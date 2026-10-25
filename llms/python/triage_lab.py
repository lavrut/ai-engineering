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

import json
from pathlib import Path

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


# ── 4. Model call with saved responses ──────────────────────────────────────


# ── 5. Code graders ─────────────────────────────────────────────────────────


# ── 6. LLM judge ────────────────────────────────────────────────────────────


# ── 7. Judge agreement ──────────────────────────────────────────────────────


# ── 8. Runner ───────────────────────────────────────────────────────────────


# ── 9. Report and compare ───────────────────────────────────────────────────
