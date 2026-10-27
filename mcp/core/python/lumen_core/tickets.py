"""tickets — the 60 lab tickets plus the t061 injection canary, searched in memory."""
from __future__ import annotations

import json

from . import DEFAULT_LIMIT, REPO_ROOT

# ── 1. Loading ──────────────────────────────────────────────────────────────
SOURCES = [REPO_ROOT / "llms" / "data" / "tickets.jsonl", REPO_ROOT / "mcp" / "data" / "injection.jsonl"]


def load_tickets() -> list[dict]:
    """All tickets in file order: t001-t060 from the lab, then t061."""
    rows = []
    for path in SOURCES:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _hit(t: dict) -> dict:
    # The body is untrusted: it is data under this one key, never merged into the structure.
    return {"id": t["id"], "category": t["category"], "priority": t["priority"],
            "untrusted_body": t["text"]}


# ── 2. Search and get ───────────────────────────────────────────────────────
def search(query: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Case-insensitive substring match per query word; score = number of words that hit.
    Ties break by id ascending. Tickets with no hit are left out."""
    words = query.lower().split()
    scored = []
    for t in load_tickets():
        text = t["text"].lower()
        n = sum(1 for w in words if w in text)
        if n:
            scored.append((-n, t["id"], t))
    scored.sort(key=lambda s: (s[0], s[1]))
    return [_hit(t) for _, _, t in scored[:limit]]


def get(id: str) -> dict:
    """One ticket by id; KeyError when unknown."""
    for t in load_tickets():
        if t["id"] == id:
            return _hit(t)
    raise KeyError(id)
