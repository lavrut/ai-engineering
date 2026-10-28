#!/usr/bin/env python3
"""
gate — is every consumer green on the contract version it declares?

A breaking change is safe to ship when no reader is left behind: each consumer
names the version it reads (in the `consumers` section of that version's
contract), that version's contract file exists, and the data it governs passes
it. During the capstone the tickets readers sit on two majors at once (the MCP
read server on tickets.v1 until its sunset, the agents eval on tickets.v2), and
the gate is green only if both are. It writes gate.json for the app.

Run (from the repo root):
    .venv-data/bin/python data/python/gate.py           # rewrite gate.json; exit 1 when the gate is red
    .venv-data/bin/python data/python/gate.py --check   # fail if gate.json drifted
Free and offline: no model calls.

"Passes" is read from verdicts.json (quality.py writes it, as of a pinned date,
and quality.py --check proves it current), not recomputed: one place judges the
data, and the gate only asks the question. An entry recorded as an expected
failure (`expect: fail`, the v1 rows under v2) is evidence of the break, not of a
consumer's data, so the gate leaves it out.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import contract as C

# ── 1. Paths ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "data" / "contracts"
VERDICTS = ROOT / "data" / "data" / "derived" / "verdicts.json"
GATE = ROOT / "data" / "data" / "derived" / "gate.json"


# ── 2. The gate ─────────────────────────────────────────────────────────────
def _green(datasets: dict, contract_id: str) -> bool:
    """True when the contract has verdicts and every governed entry passed (expected breaks aside)."""
    entries = [e for e in datasets.values() if e["contract"] == contract_id and e.get("expect") != "fail"]
    return bool(entries) and all(e["ok"] for e in entries)


def gate(contracts: Path = CONTRACTS, verdicts: Path = VERDICTS) -> dict:
    """{"consumers": [{name, declared, contract_exists, verdicts_ok}], "ok", "reason"}.

    One row per consumer entry in every contract file, in file order; `declared` is the
    contract id the consumer reads (tickets.v2). ok only when every row is green; the
    reason names each red row, or says how many are green.
    """
    datasets = json.loads(Path(verdicts).read_text(encoding="utf-8"))["datasets"]
    rows, problems = [], []
    for path in sorted(Path(contracts).glob("*.yaml")):
        doc = C.load_contract(path)
        base = doc["info"]["id"].rsplit(".v", 1)[0]
        for c in doc["consumers"]:
            declared = f"{base}.v{c['version']}"
            exists = (Path(contracts) / f"{declared}.yaml").is_file()
            ok = exists and _green(datasets, declared)
            rows.append({"name": c["name"], "declared": declared, "contract_exists": exists, "verdicts_ok": ok})
            if not exists:
                problems.append(f"{c['name']} declares {declared}, which has no contract file")
            elif not ok:
                problems.append(f"{c['name']} declares {declared}, whose verdicts fail")
    if problems:
        reason = "; ".join(problems)
    else:
        reason = f"every consumer is green on the version it declares ({len(rows)} of {len(rows)})"
    return {"consumers": rows, "ok": not problems, "reason": reason}


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
        print(f"{'OK ' if green else 'FAIL'} {r['name']} on {r['declared']}")
    print(f"gate: {'green' if doc['ok'] else 'red'}: {doc['reason']}")
    print(f"wrote {GATE.name}")
    return 0 if doc["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
