#!/usr/bin/env python3
"""
triage_lab — a teaching-sized evaluation lab for an LLM feature.

The feature is support-ticket triage for "Lumen", a fictional cloud notes app:
the model reads a ticket and returns a category, a priority and a reply draft.
The lab is everything around that call — the dataset, the prompts, saved
responses, graders, an LLM judge, a runner and a report. This file is all of it.

Run (in the venv: from the repo root, python3 -m venv .venv-llms, then
.venv-llms/bin/pip install -r llms/python/requirements.txt and source .venv-llms/bin/activate):
    cd llms/python
    python triage_lab.py run                       # replay saved responses: free, no API key
    python triage_lab.py compare --prompt v1_basic --vs v2_rules
    python triage_lab.py judge --prompt v2_rules   # judge the saved replies
    python triage_lab.py agreement                 # judge vs. the human labels
    python triage_lab.py gate --prompt v3_examples --baseline v2_rules

    read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY   # only to record or run live; paste the key, unechoed
    python triage_lab.py record --plan all --max-cost 8   # prints the estimate, asks first
    python triage_lab.py run --mode live --model claude-sonnet-5-5 --effort high
Add --data llms/data/fixtures to use the tiny test data instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# The anthropic SDK is imported inside call_model, only when a paid mode needs it, so replay,
# the graders and llms/finetune/lora.py work without it installed.

# ── 1. Configuration ────────────────────────────────────────────────────────
TASK_MODEL = "claude-haiku-5-5"    # the model that does the triage
JUDGE_MODEL = "claude-opus-5-5"    # the model that grades the replies
MODE = "replay"                    # the CLI's default --mode: replay (saved responses) | record | live

# Dollars per million tokens: (input, output). Used to estimate cost before any paid run.
# Checked 2026-10-28 against https://platform.claude.com/docs/en/about-claude/pricing (base input and
# output; Haiku 5.5 at prompts up to 100,000 tokens). Recorded costs in llms/data use these figures.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

# Resolved from this file, not the working directory, so it works from anywhere.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# A data root holds tickets.jsonl, prompts.json and responses/; llms/data/fixtures is another root.

CATEGORIES = ["billing", "bug", "account", "how_to", "feature_request", "outage"]
PRIORITIES = ["low", "normal", "high", "urgent"]


# ── 2. Dataset ──────────────────────────────────────────────────────────────
def load_jsonl(path: str | Path) -> list[dict]:
    """Read one JSON object per line. A bad line names its file and line number."""
    path = Path(path)
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path.name}:{n}: {e.msg}") from e
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}:{n}: expected a JSON object")
            rows.append(row)
    return rows


def load_tickets(path: str | Path = DATA_DIR / "tickets.jsonl") -> list[dict]:
    """Each ticket: id, text, category, priority, tags, split ("dev" or "test")."""
    return load_jsonl(path)


def split(tickets: list[dict], name: str) -> list[dict]:
    """Tune on "dev"; touch "test" only to report a final number."""
    return [t for t in tickets if t["split"] == name]


def load_prompts(path: str | Path = DATA_DIR / "prompts.json") -> dict:
    """Triage prompt versions, the judge prompt and the judge rubric."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── 3. The task ─────────────────────────────────────────────────────────────
# The model must answer in this shape. Structured output enforces it, so the
# reply is always parseable JSON with an allowed category and priority.
TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "priority": {"type": "string", "enum": PRIORITIES},
        "reply": {"type": "string"},
    },
    "required": ["category", "priority", "reply"],
    "additionalProperties": False,
}

# The judge returns a verdict and a one-sentence reason for every rubric criterion.
RUBRIC_IDS = ["addresses_issue", "tone", "no_false_promises"]
_VERDICT = {
    "type": "object",
    "properties": {"pass": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["pass", "reason"],
    "additionalProperties": False,
}
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {rid: _VERDICT for rid in RUBRIC_IDS},
    "required": RUBRIC_IDS,
    "additionalProperties": False,
}


# ── 4. Model call with saved responses ──────────────────────────────────────
# Every model call is identified by a key. In replay mode the key is looked up
# in a saved-response file, so the lab runs free, fast and the same every time.
class ReplayMiss(Exception):
    """Replay found no saved response. It never falls back to a paid call."""


def cache_key(kind, model, effort, prompt_version, prompt_text, input, rep) -> str:
    """sha256 of the canonical JSON of everything that shapes the answer.
    Change the prompt text or the rep and the key changes, so stale answers never match."""
    request = {"kind": kind, "model": model, "effort": effort, "prompt_version": prompt_version,
               "prompt_text": prompt_text, "input": input, "rep": rep}
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def response_path(kind, model, prompt_version, effort, root=DATA_DIR) -> Path:
    """One file per configuration inside <root>/responses; all reps share the file."""
    return Path(root) / "responses" / f"{kind}__{model}__{prompt_version}__{effort or 'default'}.jsonl"


MAX_TOKENS = 2000                # the most output one call may use; the worst-case cost assumes it all
_WRITE_LOCK = threading.Lock()   # runner threads append to the same file; one write at a time


def already_recorded(path, key) -> dict | None:
    """The saved line for `key` if its latest recording succeeded (no error), else None.
    Record mode uses it to resume: an interrupted run re-records only what is missing or errored."""
    path = Path(path)
    if path.exists():
        for line in reversed(load_jsonl(path)):
            if line["key"] == key:
                return None if line.get("error") else line
    return None


def call_model(params: dict, *, key, config, case_id, mode="replay", client=None, root=DATA_DIR) -> dict:
    """Return one saved-response line (`root` is a data root).
    replay: read it from disk (the latest recording of the key wins). record: call the API and append it. live: call, do not save."""
    path = response_path(config["kind"], config["model"], config["prompt_version"], config["effort"], root)
    if mode == "replay":
        if path.exists():
            for line in reversed(load_jsonl(path)):   # latest recording wins, so re-recording fixes errors
                if line["key"] == key:
                    return line
        raise ReplayMiss(
            f"no saved response for {config['kind']} / {config['model']} / {config['prompt_version']} "
            f"/ case {case_id} (rep {config['rep']}). Record it: ../../.venv-llms/bin/python triage_lab.py record ...")
    if mode not in ("record", "live"):
        raise ValueError(f"unknown mode {mode!r}")

    if mode == "record":
        saved = already_recorded(path, key)
        if saved:                                  # resume: never pay twice for the same request
            return {**saved, "skipped": True}      # "skipped" is not saved; the runner counts it as free
    import anthropic                                         # only a paid call needs the SDK
    client = client or anthropic.Anthropic(max_retries=4)   # created only when a paid mode needs it
    line = {"key": key, "config": config, "case_id": case_id, "output": None, "stop_reason": None,
            "input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "error": None}
    start = time.monotonic()
    try:
        response = client.messages.create(**params)
        line["stop_reason"] = response.stop_reason
        line["input_tokens"] = response.usage.input_tokens
        line["output_tokens"] = response.usage.output_tokens
        # A refusal or a cut-off reply has no usable JSON; leave output null.
        if response.stop_reason not in ("refusal", "max_tokens"):
            block = next((b for b in response.content if b.type == "text"), None)
            if block is None:
                line["error"] = "no text block in response"
            else:
                try:
                    line["output"] = json.loads(block.text)
                except json.JSONDecodeError as e:
                    line["error"] = f"invalid JSON: {e.msg}"
    except anthropic.APIError as e:
        line["error"] = str(e) or type(e).__name__
    line["latency_ms"] = round((time.monotonic() - start) * 1000)

    if mode == "record":
        path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def triage_key(ticket_text, variant, model, effort, rep, prompts) -> str:
    return cache_key("triage", model, effort, variant, prompts["triage"][variant], ticket_text, rep)


def triage(ticket, variant, *, model=TASK_MODEL, effort=None, rep=0, mode="replay", client=None,
           root=DATA_DIR, prompts=None) -> dict:
    """Triage one ticket with one prompt version. `root` is a data root: prompts come from
    <root>/prompts.json (unless `prompts` is passed) and responses from <root>/responses.
    Only an explicit mode="record" or "live" can spend; the default reads saved responses."""
    prompts = prompts or load_prompts(Path(root) / "prompts.json")
    prompt_text = prompts["triage"][variant]
    output_config = {"format": {"type": "json_schema", "schema": TRIAGE_SCHEMA}}
    if effort is not None:                      # null effort means: send nothing
        output_config["effort"] = effort
    params = {"model": model, "max_tokens": MAX_TOKENS, "system": prompt_text,
              "messages": [{"role": "user", "content": ticket["text"]}],
              "output_config": output_config}   # no temperature/top_p/top_k, no tool_choice
    key = triage_key(ticket["text"], variant, model, effort, rep, prompts)
    config = {"kind": "triage", "model": model, "effort": effort, "prompt_version": variant, "rep": rep}
    return call_model(params, key=key, config=config, case_id=ticket["id"], mode=mode, client=client, root=root)


# ── 5. Code graders ─────────────────────────────────────────────────────────
# Category and priority have one right answer, so plain code grades them: exact,
# free and the same every time. Reach for a judge only where code cannot decide.
def grade(ticket: dict, line: dict) -> dict:
    """Grade one saved line against its ticket. A line with no usable output is never correct."""
    if line.get("error"):
        outcome = "error"                      # the call failed: the model was never graded
    elif line.get("stop_reason") in ("refusal", "max_tokens"):
        outcome = line["stop_reason"]          # the model answered, but not with a usable result
    else:
        outcome = "ok"
    output = line.get("output") or {}
    category_ok = outcome == "ok" and output.get("category") == ticket["category"]
    priority_ok = outcome == "ok" and output.get("priority") == ticket["priority"]
    return {"case_id": ticket["id"], "category_ok": category_ok, "priority_ok": priority_ok,
            "both_ok": category_ok and priority_ok, "outcome": outcome}


def accuracy(grades: list[dict], field: str) -> float:
    """Share of graded cases that are correct. A refusal or cut-off counts as wrong;
    an error says nothing about the model, so it is left out."""
    counted = [g for g in grades if g["outcome"] != "error"]
    return sum(g[field] for g in counted) / len(counted) if counted else 0.0


def confusion_matrix(pairs: list[tuple]) -> dict[str, dict[str, int]]:
    """Count (expected, predicted) pairs: matrix[expected][predicted]. No answer is "(none)"."""
    matrix: dict[str, dict[str, int]] = {}
    for expected, predicted in pairs:
        row = matrix.setdefault(expected, {})
        predicted = predicted or "(none)"
        row[predicted] = row.get(predicted, 0) + 1
    return matrix



# ── 6. LLM judge ────────────────────────────────────────────────────────────
# Some qualities, like tone, have no exact answer a script can check. A stronger model
# reads the ticket and the reply and grades each rubric item with a reason.
def rubric_text(rubric: list[dict]) -> str:
    """One line per rubric item: its id, then what it asks."""
    return "\n".join(f"{item['id']}: {item['text']}" for item in rubric)


def _judge_system(prompts: dict) -> str:
    return prompts["judge"].replace("{rubric}", rubric_text(prompts["rubric"]))


def judge_key(ticket_text, reply, order, model, prompts) -> str:
    """The order is the prompt version, so a saved verdict only matches the order that produced it."""
    return cache_key("judge", model, None, order, _judge_system(prompts), {"ticket": ticket_text, "reply": reply}, 0)


def judge(ticket_text, reply, *, order="after", model=JUDGE_MODEL, mode="replay", client=None,
          root=DATA_DIR, prompts=None, case_id=None) -> dict:
    """Grade one reply against the rubric. `order` is where the rubric sits in the user
    message: "before" the reply or "after" it. The two orders are different requests with
    different keys, so you can measure whether position changes the verdicts."""
    if order not in ("before", "after"):
        raise ValueError(f"order must be 'before' or 'after', not {order!r}")
    prompts = prompts or load_prompts(Path(root) / "prompts.json")
    rubric = rubric_text(prompts["rubric"])
    system = _judge_system(prompts)
    ticket_part, reply_part, rubric_part = f"Ticket:\n{ticket_text}", f"Reply:\n{reply}", f"Rubric:\n{rubric}"
    parts = [ticket_part, rubric_part, reply_part] if order == "before" else [ticket_part, reply_part, rubric_part]
    params = {"model": model, "max_tokens": MAX_TOKENS, "system": system,
              "messages": [{"role": "user", "content": "\n\n".join(parts)}],
              "output_config": {"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}}}
    # The order is the prompt version, so a saved verdict only matches the order that produced it.
    key = judge_key(ticket_text, reply, order, model, prompts)
    config = {"kind": "judge", "model": model, "effort": None, "prompt_version": order, "rep": 0}
    return call_model(params, key=key, config=config, case_id=case_id, mode=mode, client=client, root=root)


def verdicts(line: dict) -> dict[str, bool | None]:
    """Pass/fail per criterion from a saved judge line; None for all when the line has no output."""
    output = line.get("output")
    return {rid: (output[rid]["pass"] if output else None) for rid in RUBRIC_IDS}


# ── 7. Judge agreement ──────────────────────────────────────────────────────
# A judge is only useful if it agrees with a careful human. Compare its verdicts with
# hand labels, and check that merely moving the rubric does not change them.
def agreement(judge_v: list, human_v: list, criterion: str = "") -> float:
    """Share of replies where the judge and the human gave the same verdict on one criterion.
    A reply the judge could not grade (None) counts as a disagreement."""
    if len(judge_v) != len(human_v) or not judge_v:
        raise ValueError(f"{criterion or 'criterion'}: need two equal-length, non-empty verdict lists")
    return sum(j == h for j, h in zip(judge_v, human_v)) / len(judge_v)


def cohen_kappa(a: list[bool], b: list[bool]) -> float:
    """Agreement beyond chance: (po - pe) / (1 - pe), where po is the observed agreement and
    pe the agreement two raters would reach by luck given how often each says true."""
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty lists")
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1:                        # both raters always say the same thing: kappa is undefined
        return 1.0 if po == 1 else 0.0
    return (po - pe) / (1 - pe)


def position_flips(before_v: list, after_v: list) -> int:
    """How many replies get a different verdict when only the rubric's position changes."""
    if len(before_v) != len(after_v):
        raise ValueError("need two equal-length verdict lists")
    return sum(x != y for x, y in zip(before_v, after_v))


# ── 8. Runner ───────────────────────────────────────────────────────────────
# Cost first: every paid run prints an estimate before the first call, and stops
# at a cap you set. Cost = tokens x price, so it can be worked out in advance.
# Rough tokens per call, (input, output): the output includes any thinking.
EST_TOKENS = {"triage": (900, 700), "judge": (1400, 900)}


def call_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Dollars for one call, from the token counts and PRICES (dollars per million tokens)."""
    if model not in PRICES:
        raise ValueError(f'no price for model "{model}"')
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def estimate_cost(kind: str, model: str, n_calls: int) -> float:
    """Dollars for n_calls of one kind, assuming the typical token counts in EST_TOKENS."""
    tokens_in, tokens_out = EST_TOKENS[kind]
    return n_calls * call_cost(model, tokens_in, tokens_out)


def worst_case_cost(kind: str, model: str, n_calls: int) -> float:
    """Dollars if every call used all of MAX_TOKENS for output (long thinking, long reply)."""
    return n_calls * call_cost(model, EST_TOKENS[kind][0], MAX_TOKENS)


class CostLimit(Exception):
    """The actual spend passed --max-cost. `lines` holds what finished; `spent` is the dollars."""
    def __init__(self, lines: list[dict], spent: float):
        super().__init__(f"spent ${spent:.4f}, over the limit")
        self.lines, self.spent = lines, spent


def run(cases: list, fn, *, reps: int = 1, workers: int = 8, max_cost: float | None = None) -> list[dict]:
    """Call fn(case, rep) for every case and rep on a thread pool; results keep input order
    (case by case, reps in order). Cost is summed from each line's real token usage. Once it
    passes max_cost no new call starts, calls already in flight finish, and CostLimit carries
    every line that finished, so a capped run still saves its work."""
    jobs = [(case, rep) for case in cases for rep in range(reps)]
    stop, lock = threading.Event(), threading.Lock()
    spent = 0.0

    def work(job):
        nonlocal spent
        if stop.is_set():
            return None                          # the cap was hit: skip calls that have not started
        line = fn(*job)
        with lock:
            if not line.get("skipped"):              # a resumed (already recorded) line costs nothing now
                spent += call_cost(line["config"]["model"], line["input_tokens"], line["output_tokens"])
            if max_cost is not None and spent > max_cost:
                stop.set()
        return line

    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        results = list(pool.map(work, jobs))
    lines = [line for line in results if line is not None]
    if stop.is_set():
        raise CostLimit(lines, spent)
    return lines


# The recording plan: what a full paid run records, in phases. Phase 2 (pick the best
# prompt by dev accuracy) costs nothing, so it is not a step. "best" is that winner, which
# is unknown up front, but the cost is the same whichever prompt wins.
RECORDING_PLAN = [
    # 1. Haiku, every prompt on all 60 tickets; v2_rules three times to measure run-to-run noise.
    {"name": "prompts", "kind": "triage", "model": TASK_MODEL, "effort": None, "prompt": "v1_basic", "split": "all", "reps": 1},
    {"name": "prompts", "kind": "triage", "model": TASK_MODEL, "effort": None, "prompt": "v2_rules", "split": "all", "reps": 3},
    {"name": "prompts", "kind": "triage", "model": TASK_MODEL, "effort": None, "prompt": "v3_examples", "split": "all", "reps": 1},
    # 3. Bigger models and more effort, on the dev split with the best prompt.
    {"name": "models", "kind": "triage", "model": "claude-sonnet-5-5", "effort": "low", "prompt": "best", "split": "dev", "reps": 1},
    {"name": "models", "kind": "triage", "model": "claude-sonnet-5-5", "effort": "high", "prompt": "best", "split": "dev", "reps": 1},
    {"name": "models", "kind": "triage", "model": "claude-opus-5-5", "effort": "low", "prompt": "best", "split": "dev", "reps": 1},
    {"name": "models", "kind": "triage", "model": "claude-opus-5-5", "effort": "high", "prompt": "best", "split": "dev", "reps": 1},
    # 4. The judge: the 30 labelled replies in both rubric orders, then the best prompt's test replies.
    {"name": "judge", "kind": "judge", "model": JUDGE_MODEL, "effort": None, "prompt": "labels", "orders": ["before", "after"]},
    {"name": "judge", "kind": "judge", "model": JUDGE_MODEL, "effort": None, "prompt": "best", "orders": ["after"]},
    # A pilot, not part of "all": 3 of the most expensive requests, to check output_tokens and
    # stop_reason before the full run. These are the same requests the full plan makes (when v2_rules
    # wins), so recording is resumable and the full run skips them.
    {"name": "pilot", "kind": "triage", "model": "claude-opus-5-5", "effort": "high", "prompt": "v2_rules", "split": "dev", "reps": 1, "limit": 3},
    {"name": "pilot", "kind": "judge", "model": JUDGE_MODEL, "effort": None, "prompt": "labels", "orders": ["after"], "limit": 3},
]


def _label_cases(root, tickets) -> list[dict]:
    by_id = {t["id"]: t for t in tickets}
    return [{"id": l["id"], "ticket": by_id[l["ticket_id"]]["text"], "reply": l["reply"], "labels": l["labels"]}
            for l in load_jsonl(Path(root) / "reply_labels.jsonl")]


def _step_cases(step: dict, tickets: list[dict]) -> list[dict]:
    cases = tickets if step["split"] == "all" else split(tickets, step["split"])
    return cases[: step.get("limit")]


def plan_calls(step: dict, root=DATA_DIR) -> int:
    """How many API calls one plan step makes against a data root."""
    root = Path(root)
    if step["kind"] == "judge" and step["prompt"] == "labels":
        n = len(load_jsonl(root / "reply_labels.jsonl"))
        return min(n, step.get("limit") or n) * len(step["orders"])
    tickets = load_tickets(root / "tickets.jsonl")
    if step["kind"] == "judge":                                  # replies to the test split
        return len(split(tickets, "test")) * len(step["orders"])
    return len(_step_cases(step, tickets)) * step["reps"]


def _recorded_keys(path) -> set:
    """Keys whose latest saved line succeeded."""
    state: dict[str, bool] = {}
    if Path(path).exists():
        for line in load_jsonl(path):
            state[line["key"]] = not line.get("error")
    return {k for k, ok in state.items() if ok}


def remaining_calls(step: dict, root, best: str | None = None) -> int:
    """Calls this step still has to make: the plan minus requests already recorded without error.
    When the best prompt (or its replies) is not known yet, nothing can be counted as done."""
    root = Path(root)
    total = plan_calls(step, root)
    prompts, tickets = load_prompts(root / "prompts.json"), load_tickets(root / "tickets.jsonl")
    variant = best if step["prompt"] == "best" else step["prompt"]
    if variant is None:
        return total
    if step["kind"] == "triage":
        done = _recorded_keys(response_path("triage", step["model"], variant, step["effort"], root))
        keys = [triage_key(t["text"], variant, step["model"], step["effort"], rep, prompts)
                for rep in range(step["reps"]) for t in _step_cases(step, tickets)]
        return sum(k not in done for k in keys)
    done_by_order = {o: _recorded_keys(response_path("judge", step["model"], o, None, root)) for o in step["orders"]}
    if step["prompt"] == "labels":
        pairs = [(c["ticket"], c["reply"]) for c in _label_cases(root, tickets)[: step.get("limit")]]
    else:
        try:
            pairs = [(t["text"], o["reply"]) for t in split(tickets, "test")
                     if (o := triage(t, variant, mode="replay", root=root, prompts=prompts)["output"])]
        except ReplayMiss:
            return total
    return sum(judge_key(t, r, o, step["model"], prompts) not in done_by_order[o]
               for o in step["orders"] for t, r in pairs)


def estimate_plan(root=DATA_DIR, only: str | None = None) -> list[dict]:
    """One row per plan step (or only the steps of one phase; "all" leaves out the pilot).
    calls = the plan; todo = still to make (already recorded requests are skipped);
    cost / worst = dollars for the todo calls, typical and with every reply at MAX_TOKENS."""
    root = Path(root)
    try:
        best = best_prompt(root, quiet=True)
    except ReplayMiss:
        best = None
    rows = []
    for step in RECORDING_PLAN:
        if only in (None, "all") and step["name"] == "pilot" or only not in (None, "all", step["name"]):
            continue
        todo = remaining_calls(step, root, best)
        rows.append({**step, "calls": plan_calls(step, root), "todo": todo,
                     "cost": estimate_cost(step["kind"], step["model"], todo),
                     "worst": worst_case_cost(step["kind"], step["model"], todo)})
    return rows


# ── 9. Report and compare ───────────────────────────────────────────────────
# A score on a small set is noisy. The band is how far a score can move by luck alone,
# so a difference smaller than the band is not evidence of anything.
def noise_band(m: int) -> float:
    """Rough noise on an accuracy, 1/sqrt(m): about two standard errors at worst (p = 0.5),
    where m is the number of scored lines (non-error, across all reps). More scored lines shrink it."""
    if m <= 0:
        raise ValueError("no scored cases")
    return 1 / math.sqrt(m)


def report(grades: list[dict]) -> dict:
    """Scores for one configuration; `grades` holds every graded line, all reps together.
    n = distinct cases (errors included), errors = lines that errored, scored = lines the
    accuracies are computed over (so scored = n * reps when nothing errors), band = noise_band(scored)."""
    scored = sum(g["outcome"] != "error" for g in grades)
    return {"n": len({g["case_id"] for g in grades}),
            "errors": len(grades) - scored,
            "scored": scored,
            "category": accuracy(grades, "category_ok"),
            "priority": accuracy(grades, "priority_ok"),
            "both": accuracy(grades, "both_ok"),
            "band": noise_band(scored)}


def compare(grades_a: list[dict], grades_b: list[dict]) -> dict:
    """Does B beat A on `both_ok`? Pass exactly one grade per case (select one rep);
    a duplicate case_id raises ValueError. diff = b - a. `fixed` are cases A got wrong and B
    right, `broken` the reverse, sorted by case_id; an error counts as not correct, and ids
    present on only one side are ignored in the flips. band = noise_band of the smaller
    scored count (conservative); significant is strict: abs(diff) > band."""
    for grades in (grades_a, grades_b):
        ids = [g["case_id"] for g in grades]
        if len(ids) != len(set(ids)):
            raise ValueError("compare needs exactly one grade per case; select a single rep")
    a, b = accuracy(grades_a, "both_ok"), accuracy(grades_b, "both_ok")
    right_a = {g["case_id"]: g["both_ok"] for g in grades_a}
    right_b = {g["case_id"]: g["both_ok"] for g in grades_b}
    shared = sorted(right_a.keys() & right_b.keys())
    scored = min(sum(g["outcome"] != "error" for g in grades_a), sum(g["outcome"] != "error" for g in grades_b))
    band = noise_band(scored)
    return {"a": a, "b": b, "diff": b - a, "band": band,
            "fixed": [c for c in shared if right_b[c] and not right_a[c]],
            "broken": [c for c in shared if right_a[c] and not right_b[c]],
            "significant": abs(b - a) > band}


def gate(grades: list[dict], baseline_grades: list[dict], *, minimum: float = 0.7) -> dict:
    """A pass/fail check for CI: fail below the minimum, or below the baseline by more than the noise band."""
    r, base = report(grades), report(baseline_grades)   # the band comes from the scored lines
    reasons = []
    if r["both"] < minimum:
        reasons.append(f"both {r['both']:.3f} is below the minimum {minimum:.3f}")
    if r["both"] < base["both"] - r["band"]:
        reasons.append(f"both {r['both']:.3f} is more than the band {r['band']:.3f} below the baseline {base['both']:.3f}")
    return {"passed": not reasons, "reasons": reasons}


# ── 10. Command line ────────────────────────────────────────────────────────
# Replay is the default and needs no key. Paid modes (record, live) print the
# estimate first and call the API only after a yes.
def _prompt_name(prompts: dict, name: str) -> str:
    if name not in prompts["triage"]:
        raise ValueError(f"unknown prompt {name!r}; choose from {', '.join(sorted(prompts['triage']))}")
    return name


def _select(tickets: list[dict], name: str) -> list[dict]:
    return tickets if name == "all" else split(tickets, name)


def _row(kind, model, prompt, effort, calls, todo=None) -> dict:
    todo = calls if todo is None else todo
    return {"kind": kind, "model": model, "prompt": prompt, "effort": effort, "calls": calls, "todo": todo,
            "cost": estimate_cost(kind, model, todo), "worst": worst_case_cost(kind, model, todo)}


def _confirm(rows: list[dict], yes: bool, cap: float | None) -> bool:
    """Print the estimate, the worst case and the cap. True only after --yes or an interactive y;
    otherwise nothing is called."""
    for r in rows:
        done = f"  ({r['calls'] - r['todo']} already recorded)" if r["todo"] != r["calls"] else ""
        print(f"  {r['kind']:<7} {r['model']:<18} {r['prompt']:<12} {r['effort'] or 'default':<8} "
              f"{r['todo']:>4} calls  ${r['cost']:.4f}{done}")
    typical, worst = sum(r["cost"] for r in rows), sum(r["worst"] for r in rows)
    skipped = sum(r["calls"] - r["todo"] for r in rows)
    if skipped:
        print(f"{skipped} requests are already recorded and will be skipped; only the rest is counted.")
    cap_text = f"${cap:.2f}" if cap is not None else "none"
    print(f"Estimated cost ${typical:.2f} (worst case ${worst:.2f} if every reply uses max_tokens). Cap: {cap_text}.")
    if cap is not None and worst > cap:
        print(f"The worst case is over the cap; the run stops at ${cap:.2f} and keeps what finished.")
    if yes:
        return True
    answer = ""
    if sys.stdin.isatty():
        try:
            answer = input("Make these paid calls? [y/N] ")
        except EOFError:
            pass
    if answer.strip().lower() not in ("y", "yes"):
        print("Not confirmed. Nothing was called. Run it in a terminal and answer y.")
        return False
    return True


def _stopped(spent: float, cap: float, mode: str) -> None:
    saved = "Finished calls are saved." if mode == "record" else "Live mode saves nothing."
    print(f"Stopped: spent ${spent:.4f}, over --max-cost {cap}. {saved}")


def _spent(lines: list[dict]) -> float:
    return sum(call_cost(l["config"]["model"], l["input_tokens"], l["output_tokens"])
               for l in lines if not l.get("skipped"))


def _grades(args, tickets, prompts, variant, mode="replay", client=None, reps=1) -> list[dict]:
    """Triage (or replay) every ticket in the split and grade the lines; all reps together."""
    by_id = {t["id"]: t for t in tickets}
    cases = _select(tickets, args.split)
    lines = run(cases, lambda t, rep: triage(t, variant, model=args.model, effort=args.effort, rep=rep,
                                             mode=mode, client=client, root=args.data, prompts=prompts),
                reps=reps, workers=args.workers, max_cost=None if mode == "replay" else args.max_cost)
    return [grade(by_id[l["case_id"]], l) for l in lines]


def _print_report(title: str, r: dict) -> None:
    print(title)
    print(f"  cases {r['n']}  scored lines {r['scored']}  errors {r['errors']}")
    print(f"  category accuracy  {r['category']:.1%}")
    print(f"  priority accuracy  {r['priority']:.1%}")
    print(f"  both accuracy      {r['both']:.1%}   noise band +/-{r['band']:.1%}")


def cmd_run(args, client) -> int:
    prompts = load_prompts(Path(args.data) / "prompts.json")
    variant = _prompt_name(prompts, args.prompt)
    tickets = load_tickets(Path(args.data) / "tickets.jsonl")
    if args.mode != "replay":
        n = len(_select(tickets, args.split)) * args.reps
        if not _confirm([_row("triage", args.model, variant, args.effort, n)], args.yes, args.max_cost):
            return 1
    try:
        grades = _grades(args, tickets, prompts, variant, args.mode, client, args.reps)
    except CostLimit as e:
        _stopped(e.spent, args.max_cost, args.mode)
        return 2
    title = (f"{variant} / {args.model} / effort {args.effort or 'default'} / {args.split} split / "
             f"{args.reps} rep{'s' if args.reps != 1 else ''} / {args.mode}")
    _print_report(title, report(grades))
    return 0


def cmd_compare(args, client) -> int:
    prompts = load_prompts(Path(args.data) / "prompts.json")
    tickets = load_tickets(Path(args.data) / "tickets.jsonl")
    a = _grades(args, tickets, prompts, _prompt_name(prompts, args.prompt))     # one rep each (rep 0)
    b = _grades(args, tickets, prompts, _prompt_name(prompts, args.vs))
    c = compare(a, b)
    print(f"{args.prompt} -> {args.vs} on the {args.split} split")
    print(f"  {args.prompt} {c['a']:.1%}   {args.vs} {c['b']:.1%}   diff {c['diff']:+.1%}   band +/-{c['band']:.1%}")
    print(f"  {'a real difference' if c['significant'] else 'within the noise: not evidence of a difference'}")
    print(f"  fixed {', '.join(c['fixed']) or '-'}   broken {', '.join(c['broken']) or '-'}")
    return 0


def cmd_judge(args, client) -> int:
    prompts = load_prompts(Path(args.data) / "prompts.json")
    variant = _prompt_name(prompts, args.prompt)
    tickets = load_tickets(Path(args.data) / "tickets.jsonl")
    cases = _select(tickets, args.split)
    if args.mode != "replay":
        if not _confirm([_row("judge", JUDGE_MODEL, variant, None, len(cases))], args.yes, args.max_cost):
            return 1
    replies = {}
    for t in cases:        # the reply to judge is the saved triage reply
        out = triage(t, variant, model=args.model, effort=args.effort, mode="replay", root=args.data,
                     prompts=prompts)["output"]
        if out:
            replies[t["id"]] = (t["text"], out["reply"])
    ids = list(replies)
    try:
        lines = run(ids, lambda i, rep: judge(*replies[i], order=args.order, mode=args.mode, client=client,
                                              root=args.data, prompts=prompts, case_id=i),
                    workers=args.workers, max_cost=None if args.mode == "replay" else args.max_cost)
    except CostLimit as e:
        _stopped(e.spent, args.max_cost, args.mode)
        return 2
    print(f"judge ({JUDGE_MODEL}, rubric {args.order}) on {len(ids)} {variant} replies, {args.split} split")
    for rid in RUBRIC_IDS:
        v = [verdicts(l)[rid] for l in lines]
        graded = [x for x in v if x is not None]
        print(f"  {rid:<18} pass {sum(graded)}/{len(graded)}" + (f"  ({len(v) - len(graded)} not graded)" if len(v) != len(graded) else ""))
    return 0


def cmd_agreement(args, client) -> int:
    prompts = load_prompts(Path(args.data) / "prompts.json")
    cases = _label_cases(args.data, load_tickets(Path(args.data) / "tickets.jsonl"))
    by_order = {}
    for order in ("before", "after"):
        lines = [judge(c["ticket"], c["reply"], order=order, mode="replay", root=args.data, prompts=prompts,
                       case_id=c["id"]) for c in cases]
        by_order[order] = [verdicts(l) for l in lines]
        print(f"judge vs human, rubric {order} the reply ({len(cases)} replies)")
        for rid in RUBRIC_IDS:
            judge_v, human_v = [v[rid] for v in by_order[order]], [c["labels"][rid] for c in cases]
            print(f"  {rid:<18} agreement {agreement(judge_v, human_v, rid):.1%}   "
                  f"kappa {cohen_kappa([bool(x) for x in judge_v], human_v):.2f}")
    print("position flips: verdicts that change when only the rubric position changes")
    for rid in RUBRIC_IDS:
        n = position_flips([v[rid] for v in by_order["before"]], [v[rid] for v in by_order["after"]])
        print(f"  {rid:<18} {n} of {len(cases)}")
    return 0


def cmd_gate(args, client) -> int:
    prompts = load_prompts(Path(args.data) / "prompts.json")
    tickets = load_tickets(Path(args.data) / "tickets.jsonl")
    result = gate(_grades(args, tickets, prompts, _prompt_name(prompts, args.prompt)),
                  _grades(args, tickets, prompts, _prompt_name(prompts, args.baseline)), minimum=args.minimum)
    print(f"gate {args.prompt} vs baseline {args.baseline} (minimum {args.minimum}): "
          f"{'PASS' if result['passed'] else 'FAIL'}")
    for reason in result["reasons"]:
        print(f"  {reason}")
    return 0 if result["passed"] else 1


def best_prompt(root, prompts: dict | None = None, tickets: list[dict] | None = None, quiet: bool = False) -> str:
    """Phase 2: the recorded Haiku prompt with the highest dev `both` accuracy (first wins a tie).
    Raises ReplayMiss when phase 1 is not recorded."""
    prompts = prompts or load_prompts(Path(root) / "prompts.json")
    tickets = tickets or load_tickets(Path(root) / "tickets.jsonl")
    names = list(dict.fromkeys(s["prompt"] for s in RECORDING_PLAN if s["name"] == "prompts"))
    scores = {}
    for name in names:
        grades = [grade(t, triage(t, name, mode="replay", root=root, prompts=prompts)) for t in split(tickets, "dev")]
        scores[name] = report(grades)["both"]
    best = max(names, key=lambda n: scores[n])
    if not quiet:
        print("phase 2: best prompt on dev " + ", ".join(f"{n} {scores[n]:.1%}" for n in names) + f" -> {best}")
    return best


def cmd_record(args, client) -> int:
    root = Path(args.data)
    prompts = load_prompts(root / "prompts.json")
    tickets = load_tickets(root / "tickets.jsonl")
    steps = estimate_plan(root, only=args.plan)
    print(f"Recording plan '{args.plan}' into {root / 'responses'}")
    if not _confirm(steps, args.yes, args.max_cost):
        return 1
    mode, spent, best = "record", 0.0, None
    phase_of = {"prompts": "phase 1", "models": "phase 3", "judge": "phase 4", "pilot": "pilot"}

    def go(cases, fn, reps=1):
        nonlocal spent
        limit = None if args.max_cost is None else args.max_cost - spent
        try:
            lines = run(cases, fn, reps=reps, workers=args.workers, max_cost=limit)
        except CostLimit as e:
            spent += e.spent
            _stopped(spent, args.max_cost, mode)
            raise
        spent += _spent(lines)
        return lines

    try:
        for step in steps:
            if step["prompt"] == "best" and best is None:
                try:
                    best = best_prompt(root, prompts, tickets)
                except ReplayMiss:
                    print("Phase 2 needs the prompts phase recorded first: record --plan prompts", file=sys.stderr)
                    return 1
            variant = best if step["prompt"] == "best" else step["prompt"]
            print(f"{phase_of[step['name']]} ({step['name']}): {step['kind']} {step['model']} "
                  f"{variant} {step['effort'] or 'default'}, {step['todo']} calls to make")
            if step["kind"] == "triage":
                cases = _step_cases(step, tickets)
                go(cases, lambda t, rep: triage(t, variant, model=step["model"], effort=step["effort"], rep=rep,
                                                mode=mode, client=client, root=root, prompts=prompts), step["reps"])
            elif step["prompt"] == "labels":
                for order in step["orders"]:
                    go(_label_cases(root, tickets)[: step.get("limit")], lambda c, rep: judge(
                        c["ticket"], c["reply"], order=order, model=step["model"], mode=mode, client=client,
                        root=root, prompts=prompts, case_id=c["id"]))
            else:                          # the best prompt's test-split replies, saved in phase 1
                replies = {}
                for t in split(tickets, "test"):
                    out = triage(t, variant, mode="replay", root=root, prompts=prompts)["output"]
                    if out:
                        replies[t["id"]] = (t["text"], out["reply"])
                for order in step["orders"]:
                    go(list(replies), lambda i, rep: judge(*replies[i], order=order, model=step["model"], mode=mode,
                                                           client=client, root=root, prompts=prompts, case_id=i))
    except CostLimit:
        return 2
    print(f"Done. Actual cost ${spent:.4f}.")
    return 0


def cost_arg(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite number above 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="triage_lab.py", description="Evaluate LLM support-ticket triage.")
    parser.add_argument("--data", default=DATA_DIR, help="data root (default: llms/data)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default=argparse.SUPPRESS, help="data root (default: llms/data)")
    common.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    common.add_argument("--model", default=TASK_MODEL)
    common.add_argument("--effort", default=None, help="low | medium | high ...; omit to send none")
    common.add_argument("--workers", type=int, default=8)
    paid = argparse.ArgumentParser(add_help=False)
    paid.add_argument("--max-cost", type=cost_arg, default=10.0,
                      help="stop when the real spend passes this many dollars (default 10, the spec's cap)")
    paid.add_argument("--yes", action="store_true", help="approve the printed estimate without asking")
    sub = parser.add_subparsers(dest="command", required=True)

    modes = argparse.ArgumentParser(add_help=False)
    modes.add_argument("--mode", choices=["replay", "record", "live"], default=MODE)

    p = sub.add_parser("run", parents=[common, paid, modes], help="triage a split and print the report")
    p.add_argument("--prompt", default="v2_rules")
    p.add_argument("--reps", type=int, default=1)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("compare", parents=[common], help="does one prompt beat another?")
    p.add_argument("--prompt", default="v1_basic")
    p.add_argument("--vs", default="v2_rules")
    p.set_defaults(func=cmd_compare)
    p = sub.add_parser("judge", parents=[common, paid, modes], help="judge the replies of a prompt")
    p.add_argument("--prompt", default="v2_rules")
    p.add_argument("--order", choices=["before", "after"], default="after")
    p.set_defaults(func=cmd_judge)
    p = sub.add_parser("agreement", parents=[common], help="judge vs the human reply labels")
    p.set_defaults(func=cmd_agreement)
    p = sub.add_parser("gate", parents=[common], help="pass/fail check against a baseline prompt")
    p.add_argument("--prompt", default="v3_examples")
    p.add_argument("--baseline", default="v2_rules")
    p.add_argument("--minimum", type=float, default=0.7)
    p.set_defaults(func=cmd_gate)
    p = sub.add_parser("record", parents=[common, paid], help="record responses from the API (paid)")
    p.add_argument("--plan", choices=["all", "prompts", "models", "judge", "pilot"], default="all")
    p.set_defaults(func=cmd_record)
    return parser


def main(argv: list[str] | None = None, client=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, client)
    except (ReplayMiss, ValueError, KeyError) as e:   # a replay miss, a bad file or flag: no traceback
        print(f"error: {e.args[0] if isinstance(e, KeyError) and e.args else e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
