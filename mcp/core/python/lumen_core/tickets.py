"""tickets — the 60 lab tickets plus the t061 injection canary, searched in memory."""
from __future__ import annotations

import json
import re

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
# Python's str.lower() and str.split() are Unicode-aware; Go's differ in the corners. Both
# cores use only ASCII rules so they agree byte for byte: A-Z -> a-z and nothing else, and
# split on space, \t, \n, \r, \v, \f and nothing else (so \x1f or U+00A0 do not split).
_ASCII_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}
_ASCII_WS = re.compile(r"[ \t\n\r\v\f]+")


def ascii_lower(s: str) -> str:
    return s.translate(_ASCII_LOWER)


def search(query: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """ASCII-case-insensitive substring match per query word; score = number of words that hit.
    Ties break by id ascending. Tickets with no hit are left out."""
    words = [w for w in _ASCII_WS.split(ascii_lower(query)) if w]
    scored = []
    for t in load_tickets():
        text = ascii_lower(t["text"])
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
