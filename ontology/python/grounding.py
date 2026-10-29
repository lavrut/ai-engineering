#!/usr/bin/env python3
"""
grounding — the course's saved model outputs, checked against the ontology.

The LLMs track saved every triage response and the agents track saved every tool call.
Nothing is called here: each saved category and priority is read back and validated as a
`Ticket`-shaped record (`validate(..., partial=True)`, because a model output carries only
some of a Ticket's properties). The result is a count of violations per source.

Run (from the repo root):
    .venv-ontology/bin/python ontology/python/grounding.py            # rewrite validation.json
    .venv-ontology/bin/python ontology/python/grounding.py --check    # fail if it drifted
Free and offline: the other tracks' files are read, never imported or changed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ontology as O  # noqa: E402

ROOT = O.ROOT
RESPONSES = ROOT / "llms" / "data" / "responses"
TRACES = ROOT / "agents" / "data" / "traces"
VALIDATION = O.DERIVED / "validation.json"


# ── 1. Reading the saved outputs ────────────────────────────────────────────
def _response_files(responses: Path) -> list[Path]:
    return sorted([*responses.glob("triage__*.jsonl"), *responses.glob("triage__*/*.jsonl")])


def _source_names(files: list[Path], responses: Path) -> dict[Path, str]:
    """Each file's source name: its stem, or, when two files share a stem, its path under
    `responses` without the suffix, so two files never merge into one source."""
    stems = [f.stem for f in files]
    return {f: f.stem if stems.count(f.stem) == 1 else f.relative_to(responses).with_suffix("").as_posix()
            for f in files}


def triage_records(responses: Path = RESPONSES) -> list[dict]:
    """One record per saved triage line: `triage__*.jsonl` and the files inside a
    `triage__*/` subfolder (the fine-tuned Qwen run). A line with an
    `error`, or without an output or a field, still yields a record (None fields), so the
    validation counts it instead of skipping it."""
    out = []
    files = _response_files(responses)
    names = _source_names(files, responses)
    for f in files:
        for n, raw in enumerate(f.read_text(encoding="utf-8").splitlines()):
            if not raw.strip():
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                line = {"error": f"line {n + 1} is not JSON"}
            line = line if isinstance(line, dict) else {"error": f"line {n + 1} is not an object"}
            o = line.get("output")
            o = o if isinstance(o, dict) else {}
            out.append({
                "source": names[f],
                "case_id": line.get("case_id"),
                "category": o.get("category"),
                "priority": o.get("priority"),
                "error": line.get("error") or (None if o else "no output"),
            })
    return out


def agent_label_calls(traces: Path = TRACES) -> list[dict]:
    """Every `label_ticket` call in the agents traces: `{source, set, ticket, label}`. A trace
    is never dropped silently: a file that is not JSON, whose output is not a mapping, whose
    `tool_calls` is not a list (and that has no chain `calls` either), or a label call without
    `args`, yields a record with `unreadable: True` (ticket = the file name). A chain trace
    (`output.calls`, no `tool_calls`) is readable and has no label call; a set with no label
    call has no source."""
    out = []

    def bad(f: Path) -> dict:
        return {"source": f"{f.parent.name}/{f.stem}", "set": f.parent.name,
                "ticket": f.name, "label": None, "unreadable": True}

    for f in sorted(traces.glob("*/*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            out.append(bad(f))
            continue
        o = data.get("output") if isinstance(data, dict) else None
        if not isinstance(o, dict) or not isinstance(o.get("tool_calls", o.get("calls")), list):
            out.append(bad(f))
            continue
        for c in o.get("tool_calls") or []:
            if not isinstance(c, dict) or c.get("tool") != "label_ticket":
                continue
            a = c.get("args")
            if not isinstance(a, dict):
                out.append(bad(f))
                continue
            out.append({"source": f"{f.parent.name}/{f.stem}", "set": f.parent.name,
                        "ticket": a.get("id"), "label": a.get("label")})
    return out


def agent_sources(calls: list[dict]) -> dict[str, list[dict]]:
    """Group label calls per trace set as records; `kind: "label"` tells `_violations` that
    only a category is expected (a label_ticket call carries no priority)."""
    out: dict[str, list[dict]] = {}
    for c in calls:
        out.setdefault(f"agents/{c['set']}", []).append(
            {"case_id": c["ticket"], "category": c["label"], "kind": "label",
             "unreadable": bool(c.get("unreadable"))})
    return out


# ── 2. Validation ───────────────────────────────────────────────────────────
def _violations(onto: O.Ontology, rec: dict) -> tuple[list[str], list[str]]:
    """(violated rules, manual constraint ids) for one record."""
    if rec.get("unreadable"):
        return ["unreadable_trace"], []
    if rec.get("error"):
        return ["response_error"], []
    rules = []
    fields = ("category",) if rec.get("kind") == "label" else ("category", "priority")
    if any(rec.get(f) is None for f in fields):
        rules.append("missing_field")
    ticket = {"id": rec.get("case_id"), "category": rec.get("category")}
    if rec.get("kind") != "label":
        ticket["priority"] = rec.get("priority")
    found = O.validate(onto, ticket, "Ticket", partial=True)
    rules += sorted({f["rule"] for f in found if f["level"] == "error"})
    return rules, sorted({f["rule"] for f in found if f["level"] == "manual"})


def validate_sources(onto: O.Ontology, sources: dict[str, list[dict]]) -> dict:
    """Per source: record count, violations per rule (records breaking it), the sorted ids of
    violating records, and per manual constraint the number of records it applies to (never
    one finding per record). A record counts once per rule."""
    result, total, total_records = {}, {}, 0
    for name in sorted(sources):
        viol, manual, bad = {}, {}, set()
        for rec in sources[name]:
            rules, man = _violations(onto, rec)
            for r in rules:
                viol[r] = viol.get(r, 0) + 1
                total[r] = total.get(r, 0) + 1
            for m in man:
                manual[m] = manual.get(m, 0) + 1
            if rules:
                bad.add(str(rec.get("case_id")))
        result[name] = {"records": len(sources[name]), "violations": viol,
                        "violating": sorted(bad), "manual": manual}
        total_records += len(sources[name])
    return {"sources": result, "totals": {"records": total_records, "violations": total}}


# ── 3. Build, write, check ──────────────────────────────────────────────────
def build(onto: O.Ontology, root: Path = ROOT) -> dict:
    """Validate every saved output under `root` (the repo, or a fixture tree with the same layout)."""
    sources: dict[str, list[dict]] = {}
    for r in triage_records(root / "llms" / "data" / "responses"):
        sources.setdefault(r["source"], []).append(r)
    sources.update(agent_sources(agent_label_calls(root / "agents" / "data" / "traces")))
    return validate_sources(onto, sources)


def run(out: Path = VALIDATION, root: Path = ROOT) -> dict:
    data = build(O.load(O.LUMEN), root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(O.dumps(data), encoding="utf-8")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate the saved model outputs against Lumen's ontology.")
    ap.add_argument("--check", action="store_true", help="fail if validation.json drifted")
    ap.add_argument("--out", type=Path, default=VALIDATION)
    args = ap.parse_args(argv)
    if args.check:
        want = O.dumps(build(O.load(O.LUMEN)))
        have = args.out.read_text(encoding="utf-8") if args.out.exists() else None
        if have != want:
            print(f"{args.out.name}: drifted or missing; run grounding.py to rewrite it")
            return 1
        print(f"{args.out.name}: up to date")
        return 0
    data = run(args.out)
    print(f"wrote {args.out.name}: {json.dumps(data['totals'], sort_keys=True)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
