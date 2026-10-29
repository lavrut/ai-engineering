#!/usr/bin/env python3
"""
governance — every recorded agents action, checked against what the ontology permits.

Section 6 of ontology/lumen.yaml says which agent role may take which action, and on what
condition. The agents track recorded every desk run: each tool call with the agent that made
it, each handoff the supervisor made, and the person's answer to the write. Nothing is called
here. Each of those becomes an event, a role taking an action, and each event gets a verdict:

    permitted      a row allows the role the action, and every condition it names is met
    conditional    a row allows it, and a condition is unmet or cannot be evaluated (named)
    not permitted  no row allows it, or the tool is not in the ontology's table

The gate is green when no event is `not permitted` and every desk trace could be read;
conditional events are counted and allowed. A condition is judged once per run, from the
trace field its `evaluated_from` names. That is coarser than the agents track's scorer,
which places each write against the review at the moment it was made (agents/evals/scorers.py).

Run (from the repo root):
    .venv-ontology/bin/python ontology/python/governance.py            # rewrite governance.json
    .venv-ontology/bin/python ontology/python/governance.py --check    # fail if it drifted
    .venv-ontology/bin/python ontology/python/governance.py --ontology /tmp/lumen.yaml --out /tmp/governance.json
Free and offline: the agents traces are read, never imported or changed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ontology as O  # noqa: E402

ROOT = O.ROOT
TRACES = ROOT / "agents" / "data" / "traces"
GOVERNANCE = O.DERIVED / "governance.json"
VERDICTS = ("permitted", "conditional", "not_permitted")


# ── 1. Reading the traces ───────────────────────────────────────────────────
def read_traces(traces: Path = TRACES) -> tuple[list[dict], list[dict]]:
    """(desk runs, skipped files). A run is {file, engine, desk, approve, tool_calls, handoffs}. A
    pattern demo has no tool calls and is skipped with that reason; a file that is not JSON, or
    whose tool calls are not a list, is skipped as `unreadable`, which the gate counts against."""
    runs, skipped = [], []
    for f in sorted(traces.glob("*/*.json")):
        name = f"{f.parent.name}/{f.name}"
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            skipped.append({"file": name, "reason": "unreadable: not JSON"})
            continue
        data = data if isinstance(data, dict) else {}
        desk, out = data.get("desk"), data.get("output")
        out = out if isinstance(out, dict) else {}
        if isinstance(desk, str) and desk.startswith("pattern:") and "tool_calls" not in out:
            skipped.append({"file": name, "reason": f"pattern demo ({desk}): no tool calls"})
            continue
        calls, handoffs = out.get("tool_calls"), out.get("handoffs", [])
        if not isinstance(calls, list) or not isinstance(handoffs, list):
            skipped.append({"file": name, "reason": "unreadable: output.tool_calls or output.handoffs is not a list"})
            continue
        runs.append({"file": name, "engine": str(data.get("engine")), "desk": str(desk), "approve": data.get("approve"),
                     "tool_calls": [c for c in calls if isinstance(c, dict)],
                     "handoffs": [h for h in handoffs if isinstance(h, dict)]})
    return runs, skipped


# ── 2. Rules, events and verdicts ───────────────────────────────────────────
def rules(onto: O.Ontology) -> tuple[dict, dict, dict]:
    """(tool -> action, (role, action) -> condition ids, condition id -> condition), from section 6.
    An entry in the wrong shape is skipped here; `ontology.py check` reports it."""
    a = onto.actions
    tools = a.get("tools") if isinstance(a.get("tools"), dict) else {}
    rows = {(r["role"], r["action"]): list(r.get("conditions") or [])
            for r in a.get("permitted") or [] if isinstance(r, dict) and O._is_id(r.get("role")) and O._is_id(r.get("action"))}
    conds = {c["id"]: c for c in a.get("conditions") or [] if isinstance(c, dict) and O._is_id(c.get("id"))}
    return {str(k): v for k, v in tools.items()}, rows, conds


def events(run: dict, tools: dict) -> list[dict]:
    """One event per tool call (the caller's role), one `escalate` by the supervisor per handoff,
    and one `approve` by the human per run, carrying the answer the trace recorded."""
    out = [{"role": str(c.get("agent")), "tool": str(c.get("tool")), "action": tools.get(str(c.get("tool")))}
           for c in run["tool_calls"]]
    out += [{"role": "supervisor", "tool": "handoff", "action": tools.get("handoff")} for _ in run["handoffs"]]
    out.append({"role": "human", "tool": "confirmation", "action": tools.get("confirmation"), "outcome": run["approve"]})
    return out


def _review_passed(run: dict) -> bool:
    """The last reviewer handoff that did not fail says the review passed."""
    reviews = [h for h in run["handoffs"] if h.get("worker") == "reviewer" and not h.get("is_error")]
    review = (reviews[-1].get("changed") or {}).get("review") if reviews else None
    return isinstance(review, dict) and review.get("passed") is True


# The trace fields a condition can be evaluated from; any other `evaluated_from` cannot be.
EVALUATE = {
    "approve": lambda run: run["approve"] == "accept",
    "handoffs.review": _review_passed,
}


def verdict(event: dict, run: dict, rows: dict, conds: dict) -> tuple[str, str, list[str]]:
    """(verdict, detail, unmet condition ids) for one event."""
    role, action = event["role"], event["action"]
    if action is None:
        return "not_permitted", "tool not in the ontology's table", []
    if (role, action) not in rows:
        return "not_permitted", f"no row permits {role} to {action}", []
    unmet, why = [], []
    for cid in rows[(role, action)]:
        fn = EVALUATE.get(str((conds.get(cid) or {}).get("evaluated_from")))
        if fn is None:
            unmet.append(cid)
            why.append(f"{cid} cannot be evaluated")
        elif not fn(run):
            unmet.append(cid)
            why.append(f"{cid} unmet")
    return ("conditional", "; ".join(why), unmet) if unmet else ("permitted", "", [])


# ── 3. Build, write, check ──────────────────────────────────────────────────
def _ids(onto: O.Ontology, cls: str) -> list[str]:
    return [i for _, i in sorted((x.get("order", 0), i) for i, x in onto.individuals.items() if x.get("class") == cls)]


def _zero() -> dict:
    return {"events": 0, **{v: 0 for v in VERDICTS}}


def gate(totals: dict, violations: list[dict]) -> dict:
    """Green when no event is not permitted and no desk trace was unreadable."""
    counts: dict[str, int] = {}
    for v in violations:
        label = f"{v['role']} {v['action'] or v['tool']}"
        counts[label] = counts.get(label, 0) + 1
    named = ", ".join(k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    ok = not violations and not totals["unreadable"]
    tail = f"{totals['conditional']} conditional, {totals['permitted']} permitted, of {totals['events']} events"
    if ok:
        return {"ok": True, "reason": f"green: 0 not permitted; {tail}"}
    bad = f"{totals['not_permitted']} not permitted" + (f" ({named})" if named else "")
    unread = f"; {totals['unreadable']} unreadable" if totals["unreadable"] else ""
    return {"ok": False, "reason": f"red: {bad}{unread}; {tail}"}


def build(onto: O.Ontology, root: Path = ROOT) -> dict:
    """Check every desk run under `root` (the repo, or a fixture tree with the same layout)."""
    tools, rows, conds = rules(onto)
    roles, actions = _ids(onto, "AgentRole"), _ids(onto, "Action")
    runs, skipped = read_traces(root / "agents" / "data" / "traces")
    engines = sorted({r["engine"] for r in runs})
    cells = {(r, a): {"role": r, "action": a,
                      "rule": ("conditional" if rows[(r, a)] else "permitted") if (r, a) in rows else "no",
                      "conditions": rows.get((r, a), []), "events": 0, "verdicts": {v: 0 for v in VERDICTS},
                      "unmet": {}, "by_engine": {e: _zero() for e in engines}}
             for r in roles for a in actions}
    by_engine: dict = {}
    totals = {"traces": len(runs) + len(skipped), "desk_runs": len(runs),
              "skipped": sum(not s["reason"].startswith("unreadable") for s in skipped),
              "unreadable": sum(s["reason"].startswith("unreadable") for s in skipped), "events": 0,
              **{v: 0 for v in VERDICTS}}
    approvals: dict[str, int] = {}
    violations = []
    for run in runs:
        desk = by_engine.setdefault(run["engine"], {}).setdefault(run["desk"], _zero())
        for ev in events(run, tools):
            v, detail, unmet = verdict(ev, run, rows, conds)
            for counts in (totals, desk):
                counts["events"] += 1
                counts[v] += 1
            if "outcome" in ev:
                approvals[str(ev["outcome"])] = approvals.get(str(ev["outcome"]), 0) + 1
            c = cells.get((ev["role"], ev["action"]))
            if c is not None:
                c["events"] += 1
                c["verdicts"][v] += 1
                c["by_engine"][run["engine"]]["events"] += 1
                c["by_engine"][run["engine"]][v] += 1
                for u in unmet:
                    c["unmet"][u] = c["unmet"].get(u, 0) + 1
            if v == "not_permitted":
                violations.append({"file": run["file"], "role": ev["role"], "tool": ev["tool"],
                                   "action": ev["action"], "detail": detail})
    violations.sort(key=lambda x: (x["file"], x["role"], x["tool"]))
    return {
        "tools": dict(sorted(tools.items())), "roles": roles, "actions": actions,
        "conditions": [{"id": cid, "text": " ".join(str(c.get("text", "")).split()),
                        "evaluated_from": c.get("evaluated_from")} for cid, c in conds.items()],
        "matrix": list(cells.values()), "by_engine": by_engine, "totals": totals, "approvals": approvals,
        "violations": violations, "skipped": sorted(skipped, key=lambda s: s["file"]),
        "gate": gate(totals, violations),
    }


def _report(data: dict) -> None:
    t, g = data["totals"], data["gate"]
    print(f"gate: {'GREEN' if g['ok'] else 'RED'} ({t['not_permitted']} not permitted, {t['conditional']} conditional)")
    for c in data["matrix"]:
        if c["rule"] == "no" and not c["events"]:
            continue
        v = c["verdicts"]
        unmet = ", ".join(f"{k} unmet {n}" for k, n in sorted(c["unmet"].items()))
        print(f"  {c['role']:<10} {c['action']:<8} {c['rule']:<11} {c['events']:>3} events: {v['permitted']} permitted, "
              f"{v['conditional']} conditional{f' ({unmet})' if unmet else ''}, {v['not_permitted']} not permitted")
    groups: dict[tuple, int] = {}
    for x in data["violations"]:
        key = (x["role"], x["tool"], x["detail"])
        groups[key] = groups.get(key, 0) + 1
    for (role, tool, detail), n in sorted(groups.items()):
        print(f"  not permitted: {role} called {tool} {n} times ({detail})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check every recorded agents action against the ontology's permissions.")
    ap.add_argument("--check", action="store_true", help="fail if governance.json drifted")
    ap.add_argument("--ontology", type=Path, default=O.LUMEN, help="the ontology YAML (default ontology/lumen.yaml)")
    ap.add_argument("--out", type=Path, default=GOVERNANCE)
    args = ap.parse_args(argv)
    onto = O.load(args.ontology)
    errors = [f for f in O.check(onto) if f["level"] == "error"]
    if errors:
        O._print(errors)
        print(f"{args.ontology.name}: {len(errors)} errors; run ontology.py check. Nothing written")
        return 1
    data = build(onto)
    if args.check:
        have = args.out.read_text(encoding="utf-8") if args.out.exists() else None
        if have != O.dumps(data):
            print(f"{args.out.name}: drifted or missing; run governance.py to rewrite it")
            return 1
        print(f"{args.out.name}: up to date")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(O.dumps(data), encoding="utf-8")
    t = data["totals"]
    print(f"wrote {args.out.name}: {t['events']} events in {t['desk_runs']} desk runs, {t['skipped']} files skipped")
    _report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
