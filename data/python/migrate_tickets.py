#!/usr/bin/env python3
"""
migrate_tickets — writes the tickets dataset in its v2 shape, for consumers that moved to v2.

tickets.v2 renames `priority` to `urgency`. A rename breaks every reader that
looks the old name up, so it is a new major version, and the v1 file cannot
simply change under its readers: the MCP read server still reads v1 and will
until v1's sunset (2027-01-31). So the v1 file stays exactly as the LLMs track
wrote it, and this script derives the v2 file from it, row for row, renaming
the one field and keeping everything else, key order included. The output is
deterministic, so it is committed and --check proves it is still what the
script would write.

Run (from the repo root):
    .venv-data/bin/python data/python/migrate_tickets.py           # rewrite data/data/tickets.v2.jsonl
    .venv-data/bin/python data/python/migrate_tickets.py --check   # fail if it drifted from the v1 file
Free and offline: no model calls.

The other half of v2, the new category value `security`, needs no migration:
an enum that gains a value accepts every row it accepted before.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import contract as C

# ── 1. Paths ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "llms" / "data" / "tickets.jsonl"  # tickets.v1, read in place, never written
TARGET = ROOT / "data" / "data" / "tickets.v2.jsonl"  # tickets.v2, derived
RENAMES = {"priority": "urgency"}  # v1 name -> v2 name


# ── 2. One row ──────────────────────────────────────────────────────────────
def migrate(row: dict) -> dict:
    """The row in v2's shape: each renamed field keeps its value and its place; the rest is untouched.

    A new dict, so the caller's row is never changed.
    """
    return {RENAMES.get(k, k): v for k, v in row.items()}


# ── 3. The file ─────────────────────────────────────────────────────────────
def render(source: Path = SOURCE) -> str:
    """The v2 file's text: one migrated row per line, in the v1 file's order, as the v1 file writes JSON."""
    rows = C.read_rows(source)
    if getattr(rows, "problems", None):
        raise SystemExit(f"{source.name} does not parse: {rows.problems[0]}")
    return "".join(json.dumps(migrate(r), ensure_ascii=False) + "\n" for r in rows)


def write(target: Path = TARGET) -> int:
    text = render()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return text.count("\n")


def check(target: Path = TARGET) -> int:
    """0 when the file on disk is byte-identical to a fresh migration; 1 naming the file."""
    try:
        ok = Path(target).read_text(encoding="utf-8") == render()
    except OSError:
        ok = False
    if ok:
        print(f"{Path(target).name} matches")
        return 0
    print(f"{Path(target).name} is stale: run data/python/migrate_tickets.py")
    return 1


# ── 4. Command line ─────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Derive tickets.v2.jsonl from the v1 tickets file.")
    ap.add_argument("--check", action="store_true", help="fail if tickets.v2.jsonl differs from a fresh migration")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    n = write()
    print(f"wrote {TARGET.relative_to(ROOT).as_posix()} ({n} rows, {', '.join(f'{a} -> {b}' for a, b in RENAMES.items())})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
