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
# Saved responses live inside a data root; the fixtures root has its own.
RESPONSES_DIR = DATA_DIR / "responses"

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


def response_path(kind, model, prompt_version, effort, root=RESPONSES_DIR) -> Path:
    """One file per configuration; all reps of it share the file."""
    return Path(root) / f"{kind}__{model}__{prompt_version}__{effort or 'default'}.jsonl"


def call_model(params: dict, *, key, config, case_id, mode=MODE, client=None, root=RESPONSES_DIR) -> dict:
    """Return one saved-response line.
    replay: read it from disk. record: call the API and append it. live: call, do not save."""
    path = response_path(config["kind"], config["model"], config["prompt_version"], config["effort"], root)
    if mode == "replay":
        if path.exists():
            for line in load_jsonl(path):
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
            block = next(b for b in response.content if b.type == "text")
            line["output"] = json.loads(block.text)
    except anthropic.APIError as e:
        line["error"] = str(e) or type(e).__name__
    line["latency_ms"] = round((time.monotonic() - start) * 1000)

    if mode == "record":
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def triage(ticket, variant, *, model=TASK_MODEL, effort=None, rep=0, mode=MODE, client=None,
           root=RESPONSES_DIR, prompts=None) -> dict:
    """Triage one ticket with one prompt version. `root` is a responses folder;
    its prompts.json sits next to it unless `prompts` is passed."""
    prompts = prompts or load_prompts(Path(root).parent / "prompts.json")
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


# ── 6. LLM judge ────────────────────────────────────────────────────────────


# ── 7. Judge agreement ──────────────────────────────────────────────────────


# ── 8. Runner ───────────────────────────────────────────────────────────────


# ── 9. Report and compare ───────────────────────────────────────────────────
