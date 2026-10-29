#!/usr/bin/env python3
"""
traces: one JSON line per request through the gateway (what each stage decided, how long it
took, what it cost), and the questions you ask of a run afterwards. Standard library only.

Run (from the repo root; reads a file, calls nothing):
    .venv-infra/bin/python infra/python/traces.py RUN.jsonl --team search --costliest 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ledger import open_for_append, read_jsonl

# ── 1. Store ────────────────────────────────────────────────────────────────
# Keys of a trace: id, parent_id, ts, team, tag, model, path, status, decisions, ms, usage,
# cost_usd, cached, attempts, outcome.


class TraceStore:
    """One file per run, <dir>/<run>.jsonl, appended a line at a time."""

    def __init__(self, dir: Path, run: str):
        self.dir = Path(dir)
        self.run = run
        self.path = self.dir / f"{run}.jsonl"

    def write(self, trace: dict) -> None:
        with open_for_append(self.path) as f:
            f.write(json.dumps(trace, ensure_ascii=False) + "\n")

    def read(self) -> list[dict]:
        return read_run(self.path)


def read_run(path: Path) -> list[dict]:
    """A run's traces in file order; a half-written last line is skipped with a warning."""
    return read_jsonl(Path(path))


# ── 2. Query ────────────────────────────────────────────────────────────────
def by(traces: list[dict], team: str | None = None, tag: str | None = None, outcome: str | None = None) -> list[dict]:
    """The traces matching every filter given, in order."""
    return [t for t in traces if (team is None or t.get("team") == team)
            and (tag is None or t.get("tag") == tag) and (outcome is None or t.get("outcome") == outcome)]


def slowest(traces: list[dict], n: int) -> list[dict]:
    return sorted(traces, key=lambda t: (-(t.get("ms") or 0), str(t.get("id"))))[:n]


def costliest(traces: list[dict], n: int) -> list[dict]:
    """The n dearest; a trace with no cost (None) is left out."""
    priced = [t for t in traces if t.get("cost_usd") is not None]
    return sorted(priced, key=lambda t: (-t["cost_usd"], str(t.get("id"))))[:n]


def tree(traces: list[dict], parent_id: str) -> list[dict]:
    """Everything below parent_id (children, then theirs), depth first, each level by ts then id.
    The parent itself is not included."""
    kids: dict[str, list[dict]] = {}
    for t in traces:
        kids.setdefault(t.get("parent_id"), []).append(t)
    out, seen = [], {parent_id}

    def walk(pid: str) -> None:
        for t in sorted(kids.get(pid, []), key=lambda t: (t.get("ts") or 0, str(t.get("id")))):
            if t["id"] not in seen:
                seen.add(t["id"])
                out.append(t)
                walk(t["id"])
    walk(parent_id)
    return out


# ── 3. Command line ─────────────────────────────────────────────────────────
def format_line(t: dict) -> str:
    cost = "-" if t.get("cost_usd") is None else f"{t['cost_usd']:.6f}"
    return " ".join([str(t.get("id")), str(t.get("status")), str(t.get("team")), str(t.get("tag")),
                     str(int(t.get("ms") or 0)), cost, str(t.get("outcome"))])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Query a gateway trace file.")
    ap.add_argument("run", help="a RUN.jsonl trace file")
    ap.add_argument("--team")
    ap.add_argument("--tag")
    ap.add_argument("--outcome")
    ap.add_argument("--slowest", type=int, metavar="N")
    ap.add_argument("--costliest", type=int, metavar="N")
    ap.add_argument("--tree", metavar="ID")
    a = ap.parse_args(argv)
    try:
        rows = read_run(Path(a.run))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rows = by(rows, a.team, a.tag, a.outcome)
    if a.tree:
        root = [t for t in rows if t.get("id") == a.tree]
        rows = root + tree(rows, a.tree)
    if a.slowest is not None:
        rows = slowest(rows, a.slowest)
    if a.costliest is not None:
        rows = costliest(rows, a.costliest)
    for t in rows:
        print(format_line(t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
