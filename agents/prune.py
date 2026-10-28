#!/usr/bin/env python3
"""prune — delete the recordings no run uses (ruling R73). Free: it reads traces and file names, and calls nothing.

    .venv-agents/bin/python agents/prune.py              # dry run: print what it would delete, delete nothing
    .venv-agents/bin/python agents/prune.py --delete     # delete those files

A re-record leaves the superseded store files behind (R67 counts them as `orphans` in sets.json). Replay never
reads them, but they ship with the repo, and one round of them held a model reply that repeated the Agent SDK's
per-user temp directory. So the store is pruned to exactly what the traces use:

- a desk or pattern set (agents/run.py SETS) keeps the keys its traces' proxy_calls name (derive.used_recordings,
  the same count sets.json reports) and loses every other `<key>.json` in agents/data/recordings/<set>/;
- the `judge` set keeps the judge requests the current traces ask for (run_evals.judge_keys: one per distinct
  ticket and reply) and loses the rest.

Only a complete set is pruned. A set with a missing trace (the cap stopped it, or a run failed) keeps every file,
since its unfinished runs may still need them, and the judge set waits until every desk set is complete. After a
prune, `.venv-agents/bin/python agents/derive.py` reports orphans 0, and replaying every set still matches its traces.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

AGENTS = Path(__file__).resolve().parent
sys.path.insert(0, str(AGENTS))
sys.path.insert(0, str(AGENTS / "evals"))

import derive  # noqa: E402  (the sets' traces and the keys they use)
import run  # noqa: E402
import run_evals  # noqa: E402  (the judge requests the traces ask for)

TRACES_DIR = run.TRACES_DIR
STORE_DIR = run.STORE_DIR


# ── 1. What to delete ───────────────────────────────────────────────────────
def _orphans(store_dir: Path, name: str, keep: set[str]) -> list[Path]:
    store = Path(store_dir) / name
    return sorted(p for p in store.glob("*.json") if p.stem not in keep) if store.is_dir() else []


def plan(traces_dir: Path = TRACES_DIR, store_dir: Path = STORE_DIR) -> dict[str, dict]:
    """{set: {"keep": n used files on disk, "delete": [paths], "skip": reason or ""}} for every set and the judge."""
    out: dict[str, dict] = {}
    incomplete = []
    for name, spec in sorted(run.SETS.items()):
        docs = derive.set_docs(traces_dir, name, spec)
        keep = set(derive.used_recordings([d for d in docs if d]))
        on_disk = derive.store_keys(store_dir, name)
        if not all(docs):
            missing = sum(d is None for d in docs)
            out[name] = {"keep": len(on_disk), "delete": [],
                         "skip": f"incomplete ({missing} of {len(docs)} traces missing): nothing pruned"}
            if name.startswith("desk-"):
                incomplete.append(name)
            continue
        out[name] = {"keep": len(on_disk & keep), "delete": _orphans(store_dir, name, keep), "skip": ""}
    judge = run_evals.JUDGE_SET
    on_disk = derive.store_keys(store_dir, judge)
    if incomplete:
        out[judge] = {"keep": len(on_disk), "delete": [],
                      "skip": f"waits for every desk set to be complete ({', '.join(incomplete)} not)"}
    else:
        keep = run_evals.judge_keys(traces_dir)
        out[judge] = {"keep": len(on_disk & keep), "delete": _orphans(store_dir, judge, keep), "skip": ""}
    return out


# ── 2. Entry point ──────────────────────────────────────────────────────────
def main(argv: list[str] | None = None, *, traces_dir: Path = TRACES_DIR, store_dir: Path = STORE_DIR) -> int:
    p = argparse.ArgumentParser(description="Delete the recordings no trace uses (free; dry run by default).")
    p.add_argument("--delete", action="store_true", help="delete the files the dry run lists")
    args = p.parse_args(argv)
    todo = plan(Path(traces_dir), Path(store_dir))
    total = 0
    for name, row in todo.items():
        if row["skip"]:
            print(f"{name}: {row['skip']}")
            continue
        print(f"{name}: keep {row['keep']}, {len(row['delete'])} to delete")
        for path in row["delete"]:
            print(f"  {'deleting' if args.delete else 'would delete'} {name}/{path.name}")
            if args.delete:
                path.unlink()
        total += len(row["delete"])
    if args.delete:
        print(f"deleted {total} files; now: .venv-agents/bin/python agents/derive.py")
    else:
        print(f"{total} to delete in all; dry run: nothing deleted; delete with: .venv-agents/bin/python agents/prune.py --delete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
