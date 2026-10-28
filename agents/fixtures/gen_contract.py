#!/usr/bin/env python3
"""gen_contract — write agents/fixtures/contract.jsonl, the cases the Python and Go hand-built desks must both pass.

One case per pattern per sample ticket (t001, t007, t061), then the extra cases below (leading prose before the
JSON, route normalisation, an unknown lookup tool, and the error paths), then the desks: `single` and `supervisor`
on each sample ticket and their extra cases (an unknown worker, a reset review, the turn caps). A case holds the
scripted model replies; the Python code runs against them, and the output and model-call sequence become `expect`.
Go replays the same replies and must produce the same `expect`. Edit this file, never the JSONL.

Run:
    python agents/fixtures/gen_contract.py --write
    python agents/fixtures/gen_contract.py --check     # exit 1 if the committed file has drifted
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desk" / "python"))

import desk_core as core  # noqa: E402
import patterns  # noqa: E402
import supervisor  # noqa: E402
from scripted_model import ScriptedModel  # noqa: E402

CONTRACT = HERE / "contract.jsonl"
SAMPLE_IDS = ("t001", "t007", "t061")
PATTERNS = ("chain", "route", "parallel", "orchestrate", "evaluate_optimize")

CATEGORY = {"t001": "billing", "t007": "billing", "t061": "how_to"}
ROUTE = {"t001": "billing", "t007": "billing", "t061": "other"}


def tool(name: str, **args) -> dict:
    return {"tool_use": {"name": name, "input": args}}


def judge(*failed: str) -> str:
    return json.dumps({r: {"pass": r not in failed, "reason": "fails" if r in failed else "ok"} for r in patterns.RUBRIC_IDS}, sort_keys=True)


def script(pattern: str, tid: str) -> list:
    """The scripted model replies for one case, in call order."""
    if pattern == "chain":
        return [json.dumps({"category": CATEGORY[tid]}), f"Reply for {tid} (chain)."]
    if pattern == "route":
        return [ROUTE[tid], f"Reply for {tid} ({ROUTE[tid]} specialist)."]
    if pattern == "parallel":
        return [tool("get_ticket", id=tid), tool("search_tickets", query="duplicate charge", limit=3),
                tool("search_tickets", query="refund", limit=3), f"Summary for {tid}."]
    if pattern == "orchestrate":
        return [json.dumps(["Look up the ticket", "Draft a reply"]), tool("get_ticket", id=tid),
                f"Looked up {tid}.", f"Draft for {tid}.", f"Final reply for {tid}."]
    # evaluate_optimize: t001 passes at once, t007 passes on the second round, t061 never passes (cap)
    if tid == "t001":
        return [f"Draft 1 for {tid}.", judge()]
    if tid == "t007":
        return [f"Draft 1 for {tid}.", judge("no_false_promises"), f"Draft 2 for {tid}.", judge()]
    return [f"Draft 1 for {tid}.", judge("tone"), f"Draft 2 for {tid}.", judge("tone"), f"Draft 3 for {tid}.", judge("tone")]


def run_case(name: str, pattern: str, tid: str, scripted: list) -> dict:
    """Run one case (a desk case goes to run_desk_case). A ValueError (a validation failure) becomes `expect: {"error": true}`: only that an
    error occurs is pinned, never its text, because Python and Go word their errors differently."""
    if pattern in DESKS:
        return run_desk_case(name, pattern, tid, scripted)
    ticket = next(t for t in core.TICKETS if t["id"] == tid)
    model = ScriptedModel(scripted)
    fn = getattr(patterns, pattern)
    try:
        run = fn(ticket, call=model, workers=1) if pattern == "parallel" else fn(ticket, call=model)
    except ValueError:
        return {"name": name, "pattern": pattern, "ticket_id": tid, "scripted": scripted, "expect": {"error": True}}
    if model.unused():
        raise RuntimeError(f"{name}: {model.unused()} scripted replies were not used")
    return {"name": name, "pattern": pattern, "ticket_id": tid, "scripted": scripted,
            "expect": {"output": run.output, "calls": run.calls}}


def bad_judge(**per_id) -> str:
    """A judge reply where each rubric id gets the given entry (default: a passing one)."""
    ok = {"pass": True, "reason": "ok"}
    return json.dumps({r: per_id.get(r, ok) for r in patterns.RUBRIC_IDS}, sort_keys=True)


# (name, pattern, ticket, scripted, must_error). Error cases give only the replies up to the failure; a
# runner fails if the pattern asks for more replies than are scripted.
EXTRA_CASES: list[tuple[str, str, str, list, bool]] = [
    # the JSON is parsed from the first "{" or "[": leading prose is skipped
    ("chain-leading-prose", "chain", "t001", ['Sure, here is the answer: {"category": "billing"} Hope that helps.', "Reply (prose)."], False),
    ("orchestrate-leading-prose", "orchestrate", "t001",
     ['The plan: ["Look up the ticket"]', tool("get_ticket", id="t001"), "Looked up.", "Final (prose)."], False),
    ("evaluate_optimize-leading-prose", "evaluate_optimize", "t001", ["Draft (prose).", "Verdict follows. " + judge()], False),
    # route normalisation: ASCII-only trim (space, \t, \n, \r) and ASCII lowercase
    ("route-ascii-trim-lowercase", "route", "t001", ["\t Billing \r\n", "Reply (trimmed)."], False),
    ("route-nbsp-not-trimmed", "route", "t001", ["\u00a0billing"], True),
    ("route-bad-route", "route", "t001", ["refund"], True),
    ("route-two-words", "route", "t001", ["billing bug"], True),
    ("chain-bad-category", "chain", "t001", ['{"category": "weather"}'], True),
    ("chain-not-json", "chain", "t001", ["billing"], True),
    ("parallel-unknown-tool-is-error", "parallel", "t001",
     [tool("label_ticket", id="t001", label="outage"), tool("get_ticket", id="t001"), tool("get_ticket", id="t001"), "Summary (unknown tool)."], False),
    ("parallel-no-tool-call", "parallel", "t001", ["I will not call a tool.", tool("get_ticket", id="t001"), tool("get_ticket", id="t001")], True),
    ("orchestrate-plan-too-long", "orchestrate", "t001", ['["a", "b", "c", "d"]'], True),
    ("orchestrate-plan-non-string-item", "orchestrate", "t001", ['["a", 2]'], True),
    ("orchestrate-plan-empty", "orchestrate", "t001", ["[]"], True),
    ("orchestrate-plan-not-json", "orchestrate", "t001", ["no plan"], True),
    ("evaluate_optimize-judge-bad-json", "evaluate_optimize", "t001", ["Draft.", "not json at all"], True),
    ("evaluate_optimize-judge-missing-pass", "evaluate_optimize", "t001",
     ["Draft.", bad_judge(tone={"reason": "no verdict"})], True),
    ("evaluate_optimize-judge-non-boolean-pass", "evaluate_optimize", "t001",
     ["Draft.", bad_judge(tone={"pass": "true", "reason": "a string"})], True),
]


# ── the desks (single agent, supervisor) ────────────────────────────────────
DESKS = ("single", "supervisor")
DESK_OUTPUT = {"single": ("reply", "stop", "tool_calls", "errors"),
               "supervisor": ("reply", "stop", "tool_calls", "errors", "handoffs", "state")}
PRIORITY = {"t001": "high", "t007": "normal", "t061": "low"}


def handoff(worker, task: str = "Do your part.") -> dict:
    return tool("handoff", worker=worker, task=task)


def triage(tid: str) -> str:
    return json.dumps({"category": CATEGORY[tid], "priority": PRIORITY[tid]})


def desk_script(desk: str, tid: str) -> list:
    """The scripted replies for one desk case. The contract runs with the person and the server both
    saying yes (approve and elicit accept), so a label_ticket that reaches the tools writes; one the
    desk refuses never reaches them."""
    if desk == "single":
        lookups = [tool("search_tickets", query="refund", limit=3)] if tid == "t007" else []
        return lookups + [tool("get_ticket", id=tid), tool("label_ticket", id=tid, label=CATEGORY[tid]), f"Reply for {tid} (single)."]
    if tid == "t001":   # the full path: triage, research, drafter, reviewer passes, then the label
        return [handoff("triage"), triage(tid), handoff("research"), tool("get_ticket", id=tid), f"Found {tid}.",
                handoff("drafter"), f"Draft for {tid}.", handoff("reviewer"), judge(),
                tool("label_ticket", id=tid, label=CATEGORY[tid]), f"Labelled {tid}."]
    if tid == "t007":   # the reviewer fails the first draft; the second passes; then the label
        return [handoff("triage"), triage(tid), handoff("drafter"), f"Draft 1 for {tid}.", handoff("reviewer"), judge("tone"),
                handoff("drafter", "Fix the tone."), f"Draft 2 for {tid}.", handoff("reviewer"), judge(),
                tool("label_ticket", id=tid, label=CATEGORY[tid]), f"Labelled {tid}."]
    # t061, the injection: the supervisor tries the write first (not offered: refused), the research worker
    # tries it too (not a read tool: refused), and the reviewed draft goes out with no label
    return [tool("label_ticket", id="t001", label="outage"), handoff("triage"), triage(tid),
            handoff("research"), tool("label_ticket", id="t001", label="outage"), tool("get_ticket", id=tid), f"Found {tid}.",
            handoff("drafter"), f"Draft for {tid}.", handoff("reviewer"), judge(), f"Answered {tid}."]


def run_desk_case(name: str, desk: str, tid: str, scripted: list) -> dict:
    """Run one desk case on the real MCP tools, a temporary labels file and working directory (the guard's
    scripted person prints and appends to the harness's trace.jsonl), and check the rows written."""
    ticket = next(t for t in core.TICKETS if t["id"] == tid)
    model = ScriptedModel(scripted)
    fn = supervisor.single_agent if desk == "single" else supervisor.supervisor
    with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp), contextlib.redirect_stdout(io.StringIO()):
        labels = Path(tmp) / "labels.jsonl"
        run = fn(ticket, call=model, approve="accept", elicit="accept", labels_path=labels)
        written = len(labels.read_text().splitlines()) if labels.exists() else 0
    if model.unused():
        raise RuntimeError(f"{name}: {model.unused()} scripted replies were not used")
    ok = sum(1 for t in run["tool_calls"] if t["tool"] == "label_ticket" and not t["is_error"])
    if written != ok:
        raise RuntimeError(f"{name}: {written} rows written, {ok} successful label_ticket calls")
    return {"name": name, "pattern": desk, "ticket_id": tid, "scripted": scripted,
            "expect": {"output": {k: run[k] for k in DESK_OUTPUT[desk]}, "calls": run["calls"]}}


DESK_CASES: list[tuple[str, str, str, list]] = [
    # a handoff to a worker that does not exist is an error result for the supervisor (the state is unchanged,
    # R26) and goes to `errors`, not `handoffs` (R37)
    ("supervisor-unknown-worker", "supervisor", "t001", [handoff("billing-bot"), handoff("triage"), triage("t001"), "Stopped."]),
    ("supervisor-handoff-task-not-string", "supervisor", "t001", [tool("handoff", worker="triage", task=7), "Stopped."]),
    ("supervisor-triage-bad-category", "supervisor", "t001", [handoff("triage"), '{"category": "weather", "priority": "high"}', "Stopped."]),
    ("supervisor-reviewer-without-draft", "supervisor", "t001", [handoff("reviewer"), "Stopped."]),
    ("supervisor-reviewer-bad-json", "supervisor", "t001", [handoff("drafter"), "Draft.", handoff("reviewer"), "no verdict", "Stopped."]),
    # a new draft clears the passed review, so label_ticket leaves the tools again and is refused
    ("supervisor-new-draft-resets-review", "supervisor", "t001",
     [handoff("drafter"), "Draft 1.", handoff("reviewer"), judge(), handoff("drafter"), "Draft 2.",
      tool("label_ticket", id="t001", label="billing"), "Stopped."]),
    # R27: after a passed review, label_ticket must name this ticket and the triaged category; the t061
    # injection target (t001, outage) and a wrong label are refused before the tools
    ("supervisor-label-bound-to-state", "supervisor", "t061",
     [handoff("triage"), triage("t061"), handoff("drafter"), "Draft.", handoff("reviewer"), judge(),
      tool("label_ticket", id="t001", label="outage"), tool("label_ticket", id="t061", label="outage"), "Stopped."]),
    # R30: a category change after a passed review clears it, so label_ticket leaves the tools again and a
    # label bound to the new category is refused
    ("supervisor-category-change-clears-review", "supervisor", "t001",
     [handoff("triage"), triage("t001"), handoff("drafter"), "Draft.", handoff("reviewer"), judge(),
      handoff("triage"), json.dumps({"category": "account", "priority": "high"}),
      tool("label_ticket", id="t001", label="account"), "Stopped."]),
    ("supervisor-turn-cap", "supervisor", "t001", [handoff("billing-bot")] * supervisor.MAX_TURNS),
    ("single-turn-cap", "single", "t001", [tool("get_ticket", id="t001")] * supervisor.MAX_TURNS),
    # a made-up tool name goes to `errors`, not `tool_calls` (R37)
    ("single-unknown-tool", "single", "t001", [tool("write_file", path="x", content="y"), "Done."]),
]


def build_cases() -> list[dict]:
    cases = [run_case(f"{p}-{tid}", p, tid, script(p, tid)) for p in PATTERNS for tid in SAMPLE_IDS]
    for c in cases:
        if c["expect"].get("error"):
            raise RuntimeError(f"{c['name']}: a base case must not fail")
    for name, pattern, tid, scripted, must_error in EXTRA_CASES:
        case = run_case(name, pattern, tid, scripted)
        if bool(case["expect"].get("error")) != must_error:
            raise RuntimeError(f"{name}: expected error={must_error}")
        cases.append(case)
    cases += [run_desk_case(f"{d}-{tid}", d, tid, desk_script(d, tid)) for d in DESKS for tid in SAMPLE_IDS]
    cases += [run_desk_case(*c) for c in DESK_CASES]
    return cases


def render() -> str:
    return "".join(json.dumps(c, sort_keys=True, ensure_ascii=True) + "\n" for c in build_cases())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    if a.write:
        CONTRACT.write_text(render(), encoding="utf-8")
        print(f"wrote {CONTRACT.name} ({len(build_cases())} cases)")
        return 0
    ok = CONTRACT.exists() and CONTRACT.read_text(encoding="utf-8") == render()
    print("contract.jsonl matches the generator" if ok else "contract.jsonl has drifted; run --write")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
