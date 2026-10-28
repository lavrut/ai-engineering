"""
agents_eval_v2 — the agents eval's ticket loader, moved to tickets.v2 through a shim.

The agents eval (agents/desk/python/desk_core.py) scores the desk on 13 tickets:
2 per Lumen category plus the injection canary t061. It was written against
tickets.v1 and its scorer reads `priority`. tickets.v2 renamed that field
`urgency`, so this loader reads the v2 file and maps `urgency` back to
`priority` at the edge (the shim): the scorer keeps working unchanged while the
data it reads is governed by v2. The agents track is never edited; its own
loader stays as it is, and the capstone test proves the two agree.

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
SHIM = {"urgency": "priority"}  # v2 name -> the name the v1 scorer reads


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── 2. The shim ─────────────────────────────────────────────────────────────
def to_v1_shape(row: dict) -> dict:
    """A v2 row as the v1 scorer reads it: `urgency` back to `priority`, in the same place."""
    return {SHIM.get(k, k): v for k, v in row.items()}


def load_eval_tickets_v2() -> list[dict]:
    """The 13 eval tickets, read from tickets.v2 and shimmed to the shape the scorer reads."""
    ids = [t["id"] for t in _rows(EVAL_IDS)]
    v2 = {t["id"]: t for t in _rows(SOURCE)}
    injection = [t for t in _rows(INJECTION) if t["id"] == INJECTION_ID]
    missing = [i for i in ids if i != INJECTION_ID and i not in v2]
    if missing or len(injection) != 1:
        raise RuntimeError(f"ticket sources do not hold the eval set (missing {missing or [INJECTION_ID]})")
    return [injection[0] if i == INJECTION_ID else to_v1_shape(v2[i]) for i in ids]
