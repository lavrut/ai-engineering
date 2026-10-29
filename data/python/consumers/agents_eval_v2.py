"""
agents_eval_v2 — what the agents eval's ticket loader looks like on tickets.v2: a shim.

The agents eval (agents/desk/python/desk_core.py) scores the desk on 13 tickets:
2 per Lumen category plus the injection canary t061. Its loader reads the v1 file
and hands on whole rows in the v1 shape it was written against (the eval scores
`category`; `priority` rides along). The agents track has not migrated: desk_core
is still a declared tickets.v1 consumer (`agents-eval`). This module is the
stand-in for its move, declared in tickets.v2 as `agents-eval-v2`: it reads the
v2 file and maps `urgency` back to `priority` at the edge (the shim), so every
row comes out in the v1 shape. The agents track is never edited; the capstone
test proves the two loaders agree, and the gate shows both green on the version
each declares.

Which 13 tickets: the ids in agents/evals/tickets.jsonl, the eval set the agents
track committed, in its order. t061 comes from mcp/data/injection.jsonl, as it
does in desk_core: it is a canary planted on purpose, outside any contract, so
it is taken as is and never validated or mapped.
"""
from __future__ import annotations

import json
from pathlib import Path

# ── 1. Paths ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "data" / "data" / "tickets.v2.jsonl"  # governed by tickets.v2
EVAL_IDS = ROOT / "agents" / "evals" / "tickets.jsonl"  # which tickets, and in what order
INJECTION = ROOT / "mcp" / "data" / "injection.jsonl"  # t061, outside any contract
INJECTION_ID = "t061"
SHIM = {"urgency": "priority"}  # v2 name -> the v1 name the agents code was written against


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── 2. The shim ─────────────────────────────────────────────────────────────
def to_v1_shape(row: dict) -> dict:
    """A v2 row in the v1 shape: `urgency` back to `priority`, in the same place."""
    return {SHIM.get(k, k): v for k, v in row.items()}


def load_eval_tickets_v2() -> list[dict]:
    """The 13 eval tickets, read from tickets.v2 and shimmed to the v1 shape desk_core returns."""
    ids = [t["id"] for t in _rows(EVAL_IDS)]
    v2 = {t["id"]: t for t in _rows(SOURCE)}
    injection = [t for t in _rows(INJECTION) if t["id"] == INJECTION_ID]
    missing = [i for i in ids if i != INJECTION_ID and i not in v2]
    if missing or len(injection) != 1:
        raise RuntimeError(f"ticket sources do not hold the eval set (missing {missing or [INJECTION_ID]})")
    return [injection[0] if i == INJECTION_ID else to_v1_shape(v2[i]) for i in ids]
