#!/usr/bin/env python3
"""
quality — runs the contracts against the live datasets and writes verdicts.json.

A contract is only a promise until something checks it against the real file.
This runner does that for the three datasets the course's tracks share, in
place (it never writes to another track's files), and measures freshness the
way a static repo can: from the date of the file's last git commit, as of a
pinned date that verdicts.json stores (ruling D10), so the file stays the same
until someone moves the date; --live asks about today instead. A zip
download has no git history, and a shallow clone (CI's default checkout) has
only its tip, which would date every file to that one commit; so in both,
freshness is "unknown", which passes with a note rather than failing a
learner who never cloned the whole repo (ruling D17).

Run (from the repo root):
    .venv-data/bin/python data/python/quality.py                        # rewrite verdicts.json as of its stored date
    .venv-data/bin/python data/python/quality.py --as-of 2026-10-28     # ... as of a new date
    .venv-data/bin/python data/python/quality.py --check                # fail if verdicts.json drifted
    .venv-data/bin/python data/python/quality.py --live                 # freshness as of today; writes nothing
Free and offline: no model calls.

The agent traces are one JSON object per file, so agent-traces.v1 validates each
file as a one-row dataset (ruling D3); verdicts.json keeps one entry per trace
set (agent-traces/<set>), rolling its files up rule by rule, which keeps the
file small and still names the first failing file.

The capstone adds two entries for tickets.v2: `tickets-v2`, the migrated file
(data/data/tickets.v2.jsonl), which passes, and `tickets-v2-before`, the v1 file
judged by v2, which fails on the renamed field. That failure is the point, so the
entry carries `"expect": "fail"` and the run counts it as fine while it fails.
"""
from __future__ import annotations

import argparse
import functools
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import contract as C

# ── 1. The datasets ─────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "data" / "contracts"
VERDICTS = ROOT / "data" / "data" / "derived" / "verdicts.json"
DATASETS = [  # fixed list, never a crawl: (id, contract file, dataset path from the repo root)
    ("tickets", "tickets.v1.yaml", "llms/data/tickets.jsonl"),
    ("complaints", "complaints.v1.yaml", "ml/data/complaints.jsonl"),
]
TRACES = ("agent-traces", "agent-traces.v1.yaml", "agents/data/traces")  # every <set>/*.json
CAPSTONE = [  # (id, contract file, dataset path, expect): before and after the breaking change
    ("tickets-v2", "tickets.v2.yaml", "data/data/tickets.v2.jsonl", None),
    ("tickets-v2-before", "tickets.v2.yaml", "llms/data/tickets.jsonl", "fail"),
]
# A derived file is as fresh as the file it is derived from (migrate_tickets.py --check ties
# the two byte for byte), so its age is its source's. This also keeps verdicts.json the same
# before and after the derived file's own commit.
AGE_FROM = {"data/data/tickets.v2.jsonl": "llms/data/tickets.jsonl"}


# ── 2. Freshness from git, as of a date ─────────────────────────────────────
# Ruling D10: an age is measured as of a pinned date (the start of that day, UTC), not
# "now". verdicts.json stores its as_of, so rebuilding it next year gives the same bytes;
# --live asks the question for today instead, and that is the run that goes stale.
def today() -> date:
    """The real clock, in one place, so tests can move it."""
    return date.today()


@functools.lru_cache(maxsize=None)
def has_history(where: Path) -> bool:
    """True inside a git checkout with its full history (ruling D17).

    False without git, outside a checkout (a zip or `git archive` copy), and in a
    shallow clone, whose one commit would look like every file's last change.
    """
    try:
        p = subprocess.run(["git", "rev-parse", "--is-inside-work-tree", "--is-shallow-repository"],
                           cwd=where, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0 and p.stdout.split() == ["true", "false"]


def commit_time(path: Path) -> int | None:
    """Unix time of the file's last commit; None without full history, outside a checkout, or uncommitted."""
    path = Path(path)
    if not path.parent.is_dir() or not has_history(path.parent):
        return None
    try:
        p = subprocess.run(["git", "log", "-1", "--format=%ct", "--", path.name],
                           cwd=path.parent, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    stamp = p.stdout.strip()
    return int(stamp) if p.returncode == 0 and stamp.isdigit() else None


def git_age_days(path: Path, as_of: date | None = None) -> float | None:
    """Days from the file's last commit to as_of (default today), rounded to 0.1, never negative."""
    stamp = commit_time(path)
    if stamp is None:
        return None
    ref = datetime.combine(as_of or today(), datetime.min.time(), timezone.utc).timestamp()
    return round(max(0.0, ref - stamp) / 86400, 1)


def run(contract_path: Path, dataset_path: Path, as_of: date | None = None) -> list[dict]:
    """The contract's verdicts on one live file, freshness from git as of as_of (default today)."""
    return C.validate(C.load_contract(contract_path), C.read_rows(dataset_path),
                      file_age_days=git_age_days(dataset_path, as_of))


# ── 3. Rolling a trace set up ───────────────────────────────────────────────
def merge(per_file: list[tuple[str, list[dict]]]) -> list[dict]:
    """One verdict per (rule, field) across files: failed if any file failed, naming the first.

    A pass keeps its detail when every file agrees ("26 files: all values are string"),
    else it says how many files passed ("26 files ok").
    """
    groups: dict[tuple[str, str], list[tuple[str, dict]]] = {}
    for name, verdicts in per_file:
        for x in verdicts:
            groups.setdefault((x["rule"], x["field"] or ""), []).append((name, x))
    out = []
    for key in sorted(groups):
        items = groups[key]
        rule, field = items[0][1]["rule"], items[0][1]["field"]
        fails = [(n, x) for n, x in items if not x["ok"]]
        if fails:
            more = len(fails) - 1
            tail = f" (+{C._plural(more, 'more file')})" if more else ""
            detail = f"{fails[0][0]}: {fails[0][1]['detail']}{tail}"
        elif len({x["detail"] for _, x in items}) == 1:
            detail = f"{C._plural(len(items), 'file')}: {items[0][1]['detail']}"
        else:
            detail = f"{C._plural(len(items), 'file')} ok"
        out.append({"rule": rule, "field": field, "ok": not fails, "detail": detail})
    return out


# ── 4. verdicts.json ────────────────────────────────────────────────────────
def _contract_id(name: str) -> str:
    return name.removesuffix(".yaml")


def _entry(contract_name: str, path: str, verdicts: list[dict], age: float | None, **extra) -> dict:
    return {"contract": _contract_id(contract_name), "path": path, "age_days": age, **extra,
            "verdicts": verdicts, "ok": all(x["ok"] for x in verdicts)}


def as_expected(entry: dict) -> bool:
    """An entry is fine when it passes, or when it is a recorded break (`expect: fail`) that fails."""
    return entry["ok"] != (entry.get("expect") == "fail")


def build(as_of: date) -> dict:
    """Every dataset's verdicts as of a date, keyed by dataset id; trace sets as agent-traces/<set>.

    A trace-set entry also carries `files`, the number of trace files rolled up.
    """
    datasets = {}
    for ds_id, name, rel, expect in [(*d, None) for d in DATASETS] + CAPSTONE:
        path, age = ROOT / rel, git_age_days(ROOT / AGE_FROM.get(rel, rel), as_of)
        verdicts = C.validate(C.load_contract(CONTRACTS / name), C.read_rows(path), file_age_days=age)
        datasets[ds_id] = _entry(name, rel, verdicts, age, **({"expect": expect} if expect else {}))
    prefix, name, rel = TRACES
    contract = C.load_contract(CONTRACTS / name)
    for set_dir in sorted(p for p in (ROOT / rel).iterdir() if p.is_dir()):
        files = sorted(set_dir.glob("*.json"))
        ages = [git_age_days(f, as_of) for f in files]
        per_file = [(f.name, C.validate(contract, C.read_rows(f), file_age_days=a)) for f, a in zip(files, ages)]
        known = [a for a in ages if a is not None]
        datasets[f"{prefix}/{set_dir.name}"] = _entry(
            name, f"{rel}/{set_dir.name}/*.json", merge(per_file), max(known) if known else None, files=len(files))
    return {"as_of": as_of.isoformat(), "datasets": datasets}


def _text(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def stored_as_of(out: Path = VERDICTS) -> date | None:
    """The as_of written in an existing verdicts.json, or None."""
    try:
        return date.fromisoformat(json.loads(Path(out).read_text(encoding="utf-8"))["as_of"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_verdicts(out: Path = VERDICTS, as_of: date | None = None) -> dict:
    """Write verdicts.json as of as_of; by default the date already stored there, else today."""
    doc = build(as_of or stored_as_of(out) or today())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_text(doc), encoding="utf-8")
    return doc


NO_HISTORY = "no git history: commit dates not compared"


def _without_ages(doc: dict) -> dict:
    """Without git history there are no commit dates: compare everything but the ages and freshness."""
    doc = json.loads(json.dumps(doc))
    for entry in doc.get("datasets", {}).values():
        entry.pop("age_days", None)
        entry["verdicts"] = [x for x in entry.get("verdicts", []) if x.get("rule") != "freshness"]
        entry.pop("ok", None)
    return doc


def check(out: Path = VERDICTS) -> int:
    """0 when the file on disk is byte-identical to a rebuild as of its own as_of; 1 naming the file."""
    history = has_history(ROOT)
    if not history:
        print(NO_HISTORY)
    as_of = stored_as_of(out)
    if as_of is not None:
        fresh = build(as_of)
        text = Path(out).read_text(encoding="utf-8")
        if text == _text(fresh) or (not history and _without_ages(json.loads(text)) == _without_ages(fresh)):
            print("verdicts.json matches")
            return 0
    print("verdicts.json is stale: run data/python/quality.py")
    return 1


# ── 5. Command line ─────────────────────────────────────────────────────────
def _table(doc: dict) -> bool:
    print(f"as of {doc['as_of']}")
    for ds_id, entry in doc["datasets"].items():
        bad = sum(1 for x in entry["verdicts"] if not x["ok"])
        note = f" (expected to {entry['expect']})" if entry.get("expect") else ""
        print(f"{'OK ' if entry['ok'] else 'FAIL'} {ds_id} ({entry['contract']}): "
              f"{len(entry['verdicts']) - bad} ok, {bad} failed{note}")
    return all(as_expected(e) for e in doc["datasets"].values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the contracts on the live datasets.")
    ap.add_argument("--check", action="store_true", help="fail if verdicts.json differs from a rebuild as of its as_of")
    ap.add_argument("--live", action="store_true", help="judge freshness as of today and write nothing")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD",
                    help="the date ages are measured to (default: the stored as_of; with --live, today)")
    args = ap.parse_args(argv)
    if args.check:
        if args.live or args.as_of:
            ap.error("--check takes no other option")
        return check()
    if args.live:
        ok = _table(build(args.as_of or today()))
        print("live run: nothing written")
        return 0 if ok else 1
    doc = write_verdicts(VERDICTS, args.as_of)
    ok = _table(doc)
    print(f"wrote {VERDICTS.name}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
