#!/usr/bin/env python3
"""
triage_lab — a teaching-sized evaluation lab for an LLM feature.

The feature is support-ticket triage for "Lumen", a fictional cloud notes app:
the model reads a ticket and returns a category, a priority and a reply draft.
The lab is everything around that call — the dataset, the prompts, saved
responses, graders, an LLM judge, a runner and a report. This file is all of it.

Run:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...      # only needed to record or run live
    python triage_lab.py run
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import anthropic

# ── 1. Configuration ────────────────────────────────────────────────────────
TASK_MODEL = "claude-haiku-5-5"    # the model that does the triage
JUDGE_MODEL = "claude-opus-5-5"    # the model that grades the replies
MODE = "replay"                    # replay (saved responses) | record | live

# Dollars per million tokens: (input, output). Used to estimate cost before any paid run.
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


def call_model(params: dict, *, key, config, case_id, mode=MODE, client=None, root=DATA_DIR) -> dict:
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
            f"/ case {case_id} (rep {config['rep']}). Record it: python triage_lab.py record ...")
    if mode not in ("record", "live"):
        raise ValueError(f"unknown mode {mode!r}")

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
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def triage(ticket, variant, *, model=TASK_MODEL, effort=None, rep=0, mode=MODE, client=None,
           root=DATA_DIR, prompts=None) -> dict:
    """Triage one ticket with one prompt version. `root` is a data root: prompts come from
    <root>/prompts.json (unless `prompts` is passed) and responses from <root>/responses."""
    prompts = prompts or load_prompts(Path(root) / "prompts.json")
    prompt_text = prompts["triage"][variant]
    output_config = {"format": {"type": "json_schema", "schema": TRIAGE_SCHEMA}}
    if effort is not None:                      # null effort means: send nothing
        output_config["effort"] = effort
    params = {"model": model, "max_tokens": 2000, "system": prompt_text,
              "messages": [{"role": "user", "content": ticket["text"]}],
              "output_config": output_config}   # no temperature/top_p/top_k, no tool_choice
    key = cache_key("triage", model, effort, variant, prompt_text, ticket["text"], rep)
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


# ── 7. Judge agreement ──────────────────────────────────────────────────────


# ── 8. Runner ───────────────────────────────────────────────────────────────


# ── 9. Report and compare ───────────────────────────────────────────────────
# A score on a small set is noisy. The band is how far a score can move by luck alone,
# so a difference smaller than the band is not evidence of anything.
def noise_band(m: int) -> float:
    """Rough one-sigma noise on an accuracy, about 1/sqrt(m), where m is the number of
    scored lines (non-error, across all reps). More scored lines shrink it."""
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
