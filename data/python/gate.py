#!/usr/bin/env python3
"""
gate — is every consumer green on the contract version it declares?

A breaking change is safe to ship when no reader is left behind: each consumer
names the version it reads (in the `consumers` section of that version's
contract), that version's contract file exists, and the data it governs passes
it. During the capstone the tickets readers sit on two majors at once (the MCP
read server and the agents eval's own loader on tickets.v1 until its sunset, the
shim that stands in for the agents eval's move, agents-eval-v2, on tickets.v2),
and the gate is green only if all of them are. It writes gate.json for the app.

Run (from the repo root):
    .venv-data/bin/python data/python/gate.py           # rewrite gate.json; exit 1 when the gate is red
    .venv-data/bin/python data/python/gate.py --check   # fail if gate.json drifted
Free and offline: no model calls.

The gate judges for itself (ruling D12): for each consumer it loads the declared
contract, finds the file that contract governs (`info.dataset`, a path or glob from
the repo root) and validates it again, freshness included. It never reads a verdict
from verdicts.json, so a hand-edited verdicts.json cannot turn it green; it takes
only the pinned `as_of` from there, so its ages agree with quality.py's (and with
quality.AGE_FROM for the derived v2 file). A consumer on a deprecated version past
its sunset gets a warning, not a failure: the sunset is a promise to move, and the
gate shows who has not moved yet.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import catalog as K
import contract as C
import quality as Q

# ── 1. Paths ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "data" / "contracts"
VERDICTS = ROOT / "data" / "data" / "derived" / "verdicts.json"  # only its as_of is read
GATE = ROOT / "data" / "data" / "derived" / "gate.json"


# ── 2. The gate ─────────────────────────────────────────────────────────────
def judge(doc: dict, root: Path, as_of: date) -> str | None:
    """None when every file the contract governs passes it as of as_of; else what failed, in words."""
    pattern = doc["info"].get("dataset")
    if not pattern:
        return "which names no dataset"
    found = K.files(root, pattern)
    if not found:
        return f"but {pattern} has no files"
    for rel in found:
        age = Q.git_age_days(root / Q.AGE_FROM.get(rel, rel), as_of)
        bad = [x for x in C.validate(doc, C.read_rows(root / rel), file_age_days=age) if not x["ok"]]
        if bad:
            x, more = bad[0], f" (+{len(bad) - 1} more failed)" if len(bad) > 1 else ""
            return f"which fails on {rel}: {x['rule']} {x['field'] if x['field'] is not None else '-'}: {x['detail']}{more}"
    return None


def gate(contracts: Path = CONTRACTS, verdicts: Path = VERDICTS, root: Path = ROOT) -> dict:
    """{"consumers": [{name, declared, contract_exists, verdicts_ok, sunset, warning}], "ok", "reason"}.

    One row per consumer entry in every contract file, in file order; `declared` is the
    contract id the consumer reads (tickets.v2). ok only when every declared version
    exists and validates green; `warning` (deprecated and as_of past the sunset) never
    turns it red. The reason names each red row, or says how many are green, then any warning.
    """
    as_of = Q.stored_as_of(Path(verdicts))
    if as_of is None:
        raise SystemExit(f"{Path(verdicts).name} has no as_of: run data/python/quality.py")
    rows, problems, warnings, judged = [], [], [], {}
    for path in sorted(Path(contracts).glob("*.yaml")):
        doc = C.load_contract(path)
        for c in doc["consumers"]:
            declared = f"{K.base_id(doc['info']['id'])}.v{c['version']}"
            target = Path(contracts) / f"{declared}.yaml"
            exists = target.is_file()
            sunset, warning, failure = None, False, "which has no contract file"
            if exists:
                version = C.load_contract(target)
                if declared not in judged:
                    judged[declared] = judge(version, Path(root), as_of)
                failure = judged[declared]
                sunset = version["info"].get("sunset")
                warning = version["info"]["status"] == "deprecated" and sunset is not None and as_of > date.fromisoformat(sunset)
            rows.append({"name": c["name"], "declared": declared, "contract_exists": exists,
                         "verdicts_ok": failure is None, "sunset": sunset, "warning": warning})
            if failure is not None:
                problems.append(f"{c['name']} declares {declared}, {failure}")
            if warning:
                warnings.append(f"warning: {c['name']} is on {declared}, past its sunset {sunset}")
    head = problems or [f"every consumer is green on the version it declares ({len(rows)} of {len(rows)})"]
    return {"consumers": rows, "ok": not problems, "reason": "; ".join(head + warnings)}


# ── 3. gate.json ────────────────────────────────────────────────────────────
def _text(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def write(out: Path = GATE) -> dict:
    doc = gate()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_text(doc), encoding="utf-8")
    return doc


def check(out: Path = GATE) -> int:
    """0 when gate.json is byte-identical to a fresh gate; 1 naming the file."""
    try:
        ok = Path(out).read_text(encoding="utf-8") == _text(gate())
    except OSError:
        ok = False
    if ok:
        print(f"{Path(out).name} matches")
        return 0
    print(f"{Path(out).name} is stale: run data/python/gate.py")
    return 1


# ── 4. Command line ─────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check every consumer against the contract version it declares.")
    ap.add_argument("--check", action="store_true", help="fail if gate.json differs from a fresh gate")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    doc = write()
    for r in doc["consumers"]:
        green = r["contract_exists"] and r["verdicts_ok"]
        note = f" (warning: past its sunset {r['sunset']})" if r["warning"] else ""
        print(f"{'OK ' if green else 'FAIL'} {r['name']} on {r['declared']}{note}")
    print(f"gate: {'green' if doc['ok'] else 'red'}: {doc['reason']}")
    print(f"wrote {GATE.name}")
    return 0 if doc["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
