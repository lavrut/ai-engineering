#!/usr/bin/env python3
"""
quality — runs the v1 contracts against the live datasets and writes verdicts.json.

A contract is only a promise until something checks it against the real file.
This runner does that for the three datasets the course's tracks share, in
place (it never writes to another track's files), and measures freshness the
way a static repo can: from the date of the file's last git commit. A zip
download has no git history, so freshness there is "unknown", which passes
with a note rather than failing a learner who never cloned the repo.

Run (from the repo root):
    .venv-data/bin/python data/python/quality.py            # write data/data/derived/verdicts.json
    .venv-data/bin/python data/python/quality.py --check    # fail if verdicts.json drifted
Free and offline: no model calls.

The agent traces are one JSON object per file, so agent-traces.v1 validates each
file as a one-row dataset (ruling D3); verdicts.json keeps one entry per trace
set (agent-traces/<set>), rolling its files up rule by rule, which keeps the
file small and still names the first failing file.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
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


# ── 2. Freshness from git ───────────────────────────────────────────────────
def git_age_days(path: Path) -> float | None:
    """Days since the file's last commit; None without git, outside a checkout, or uncommitted.

    The age is rounded to 0.1 day so the printed detail stays readable.
    """
    path = Path(path)
    if not path.parent.is_dir():
        return None
    try:
        p = subprocess.run(["git", "log", "-1", "--format=%ct", "--", path.name],
                           cwd=path.parent, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    stamp = p.stdout.strip()
    if p.returncode != 0 or not stamp.isdigit():
        return None
    return round(max(0.0, time.time() - int(stamp)) / 86400, 1)


def run(contract_path: Path, dataset_path: Path) -> list[dict]:
    """The contract's verdicts on one live file, freshness from git."""
    return C.validate(C.load_contract(contract_path), C.read_rows(dataset_path),
                      file_age_days=git_age_days(dataset_path))


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


def build() -> dict:
    """Every dataset's verdicts, keyed by dataset id; trace sets as agent-traces/<set>."""
    datasets = {}
    for ds_id, name, rel in DATASETS:
        path, age = ROOT / rel, git_age_days(ROOT / rel)
        verdicts = C.validate(C.load_contract(CONTRACTS / name), C.read_rows(path), file_age_days=age)
        datasets[ds_id] = _entry(name, rel, verdicts, age)
    prefix, name, rel = TRACES
    contract = C.load_contract(CONTRACTS / name)
    for set_dir in sorted(p for p in (ROOT / rel).iterdir() if p.is_dir()):
        files = sorted(set_dir.glob("*.json"))
        ages = [git_age_days(f) for f in files]
        per_file = [(f.name, C.validate(contract, C.read_rows(f), file_age_days=a)) for f, a in zip(files, ages)]
        known = [a for a in ages if a is not None]
        datasets[f"{prefix}/{set_dir.name}"] = _entry(
            name, f"{rel}/{set_dir.name}/*.json", merge(per_file), max(known) if known else None, files=len(files))
    return {"generated": _head(), "datasets": datasets}


def _head() -> str | None:
    try:
        p = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() or None


def _text(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def write_verdicts(out: Path = VERDICTS) -> dict:
    doc = build()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_text(doc), encoding="utf-8")
    return doc


def _stable(doc: dict) -> dict:
    """What --check compares: everything but the clock.

    `generated` moves with every commit and the ages with every day, so the check
    leaves them and the freshness detail out; freshness ok-ness is still compared.
    """
    doc = json.loads(json.dumps(doc))
    doc.pop("generated", None)
    for entry in doc.get("datasets", {}).values():
        entry.pop("age_days", None)
        for x in entry.get("verdicts", []):
            if x.get("rule") == "freshness":
                x.pop("detail", None)
    return doc


def check(out: Path = VERDICTS) -> int:
    """0 when the file on disk says what a fresh run says, clock aside; 1 naming the file."""
    try:
        on_disk = json.loads(Path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        on_disk = None
    if isinstance(on_disk, dict) and _stable(on_disk) == _stable(build()):
        print("verdicts.json matches")
        return 0
    print("verdicts.json is stale: run data/python/quality.py")
    return 1


# ── 5. Command line ─────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the v1 contracts on the live datasets.")
    ap.add_argument("--check", action="store_true", help="fail if verdicts.json differs from a fresh run")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    doc = write_verdicts()
    for ds_id, entry in doc["datasets"].items():
        bad = sum(1 for x in entry["verdicts"] if not x["ok"])
        print(f"{'OK ' if entry['ok'] else 'FAIL'} {ds_id} ({entry['contract']}): "
              f"{len(entry['verdicts']) - bad} ok, {bad} failed")
    print(f"wrote {VERDICTS.relative_to(ROOT)}")
    return 0 if all(e["ok"] for e in doc["datasets"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
