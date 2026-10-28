"""Tests for the five patterns. A scripted fake model stands in for Claude: no proxy, no API."""
import json
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

import desk_core as core
import patterns as p
from scripted_model import ScriptedModel

T1 = next(t for t in core.TICKETS if t["id"] == "t001")


def tool(name, **inp):
    return {"tool_use": {"name": name, "input": inp}}


def roles(run):
    return [(c["agent"], c["role"]) for c in run.calls]


# ── chain ───────────────────────────────────────────────────────────────────
def test_chain_two_calls_in_order():
    m = ScriptedModel(['{"category": "billing"}', "We will look into the duplicate charge."])
    run = p.chain(T1, call=m)
    assert run.output == {"category": "billing", "reply": "We will look into the duplicate charge."}
    assert roles(run) == [("classifier", "classify"), ("writer", "reply")]
    assert "billing" in m.calls[1]["system"] + json.dumps(m.calls[1]["messages"])


def test_chain_gate_rejects_unknown_category():
    m = ScriptedModel(['{"category": "weather"}', "never reached"])
    with pytest.raises(ValueError, match="category"):
        p.chain(T1, call=m)
    assert len(m.calls) == 1


# ── route ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["billing", "bug", "account", "other"])
def test_route_four_routes(name):
    m = ScriptedModel([name, "reply"])
    run = p.route(T1, call=m)
    assert run.output == {"route": name, "reply": "reply"}
    assert roles(run) == [("router", "route"), (name, "reply")]


def test_routes_are_exactly_four():
    assert p.ROUTES == ("billing", "bug", "account", "other")


@pytest.mark.parametrize("bad", ["refund", "Billing please", "", "billing\nbug"])
def test_route_validation(bad):
    m = ScriptedModel([bad, "never"])
    with pytest.raises(ValueError, match="route"):
        p.route(T1, call=m)
    assert len(m.calls) == 1


def test_route_tolerates_case_and_whitespace():
    assert p.route(T1, call=ScriptedModel(["  Bug \n", "r"])).output["route"] == "bug"


def test_route_normalisation_is_ascii_only():
    assert p.route(T1, call=ScriptedModel(["\t BILLING\r\n", "r"])).output["route"] == "billing"
    with pytest.raises(ValueError, match="route"):
        p.route(T1, call=ScriptedModel(["\u00a0billing", "never"]))   # NBSP is not trimmed
    assert p._ascii_lower("\u0130A") == "\u0130a"


# ── parallel ────────────────────────────────────────────────────────────────
PAR = [tool("get_ticket", id="t001"), tool("search_tickets", query="charged twice", limit=3),
       tool("search_tickets", query="refund"), "Duplicate charge; refund request."]


def test_parallel_order_count_and_tools():
    m = ScriptedModel(PAR)
    run = p.parallel(T1, call=m, workers=1)
    assert len(m.calls) == 4
    assert roles(run) == [("lookup-1", "lookup"), ("lookup-2", "lookup"), ("lookup-3", "lookup"), ("summarizer", "summarize")]
    assert run.output["lookups"] == [{"tool": "get_ticket", "args": {"id": "t001"}, "is_error": False},
                                     {"tool": "search_tickets", "args": {"limit": 3, "query": "charged twice"}, "is_error": False},
                                     {"tool": "search_tickets", "args": {"query": "refund"}, "is_error": False}]
    assert run.output["summary"] == "Duplicate charge; refund request."
    assert [r["name"] for r in run.trace.to_list() if r["kind"] == "tool"] == \
        ["get_ticket", "search_tickets", "search_tickets"]
    # the summary sees the tool results
    assert "t001" in json.dumps(m.calls[3]["messages"])


def test_parallel_with_threads_still_serializes_tools():
    lock, seen = threading.Lock(), []

    def by_agent(messages, *, system, tools=None, max_tokens=4000, trace=None, agent="desk"):
        if agent == "summarizer":
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="done")], stop_reason="end_turn")
        with lock:
            seen.append(agent)
        name = {"lookup-1": "get_ticket", "lookup-2": "search_tickets", "lookup-3": "search_tickets"}[agent]
        inp = {"get_ticket": {"id": "t001"}, "search_tickets": {"query": "x"}}[name]
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="u", name=name, input=inp)], stop_reason="tool_use")

    run = p.parallel(T1, call=by_agent, workers=3)
    assert sorted(seen) == ["lookup-1", "lookup-2", "lookup-3"]
    assert [l["tool"] for l in run.output["lookups"]] == ["get_ticket", "search_tickets", "search_tickets"]  # slot order
    assert [c["agent"] for c in run.calls][:3] == ["lookup-1", "lookup-2", "lookup-3"]


def test_parallel_requires_a_tool_call():
    m = ScriptedModel(["no tool here", tool("get_ticket", id="t001"), tool("get_ticket", id="t001"), "s"])
    with pytest.raises(ValueError, match="tool"):
        p.parallel(T1, call=m, workers=1)


def test_parallel_records_lookup_is_error():
    m = ScriptedModel([tool("label_ticket", id="t001", label="outage"), tool("get_ticket", id="t001"),
                       tool("get_ticket", id="t001"), "s"])
    run = p.parallel(T1, call=m, workers=1)
    assert [l["is_error"] for l in run.output["lookups"]] == [True, False, False]


# ── orchestrate ─────────────────────────────────────────────────────────────
ORCH = ['["Look up the ticket", "Draft a reply"]', tool("get_ticket", id="t001"), "Ticket is a duplicate charge.",
        "Draft: sorry about that.", "Final reply."]


def test_orchestrate_order_and_results():
    m = ScriptedModel(ORCH)
    run = p.orchestrate(T1, call=m)
    assert roles(run) == [("orchestrator", "plan"), ("worker-1", "work"), ("worker-1", "work"),
                          ("worker-2", "work"), ("synthesizer", "synthesize")]
    assert run.output == {"plan": ["Look up the ticket", "Draft a reply"],
                          "results": ["Ticket is a duplicate charge.", "Draft: sorry about that."],
                          "reply": "Final reply."}
    assert m.unused() == 0
    assert [r["name"] for r in run.trace.to_list() if r["kind"] == "tool"] == ["get_ticket"]


def test_orchestrate_caps_the_plan():
    m = ScriptedModel(['["a","b","c","d"]'])
    with pytest.raises(ValueError, match="plan"):
        p.orchestrate(T1, call=m)


def test_orchestrate_workers_cannot_write():
    """Workers get lumen-read only: a label_ticket request is an unknown tool, never a write."""
    m = ScriptedModel(['["x"]', tool("label_ticket", id="t001", label="outage"), "gave up", "final"])
    run = p.orchestrate(T1, call=m)
    assert "label_ticket" not in {t["name"] for t in m.calls[1]["tools"]}
    assert "Unknown tool" in json.dumps(m.calls[2]["messages"])
    assert run.output["reply"] == "final"


# ── evaluate_optimize ───────────────────────────────────────────────────────
OK = json.dumps({r: {"pass": True, "reason": "ok"} for r in p.RUBRIC_IDS})
BAD = json.dumps({"addresses_issue": {"pass": True, "reason": "ok"}, "tone": {"pass": True, "reason": "ok"},
                  "no_false_promises": {"pass": False, "reason": "promises a refund"}})


def test_evaluator_stops_on_first_pass():
    m = ScriptedModel(["draft 1", OK, "never"])
    run = p.evaluate_optimize(T1, call=m)
    assert roles(run) == [("writer", "draft"), ("judge", "judge")]
    assert run.output == {"rounds": [{"draft": "draft 1", "verdict": {"addresses_issue": True, "tone": True, "no_false_promises": True}}],
                          "final": "draft 1"}
    assert m.unused() == 1


def test_evaluator_revises_with_judge_reasons():
    m = ScriptedModel(["draft 1", BAD, "draft 2", OK])
    run = p.evaluate_optimize(T1, call=m)
    assert roles(run) == [("writer", "draft"), ("judge", "judge"), ("writer", "revise"), ("judge", "judge")]
    assert "promises a refund" in json.dumps(m.calls[2]["messages"])
    assert run.output["final"] == "draft 2" and len(run.output["rounds"]) == 2
    assert run.output["rounds"][0]["verdict"]["no_false_promises"] is False


def test_evaluator_caps_at_max_rounds():
    m = ScriptedModel(["d1", BAD, "d2", BAD, "d3", BAD, "never", "never"])
    run = p.evaluate_optimize(T1, call=m)
    assert len(m.calls) == 6 and len(run.output["rounds"]) == p.MAX_ROUNDS == 3
    assert run.output["final"] == "d3"
    run2 = p.evaluate_optimize(T1, call=ScriptedModel(["d1", BAD, "d2", BAD]), max_rounds=2)
    assert len(run2.output["rounds"]) == 2


def test_judge_uses_the_labs_rubric():
    import triage_lab
    m = ScriptedModel(["d", OK])
    p.evaluate_optimize(T1, call=m)
    assert p.RUBRIC_IDS == triage_lab.RUBRIC_IDS
    assert m.calls[1]["system"] == triage_lab._judge_system(triage_lab.load_prompts())
    for rid in p.RUBRIC_IDS:
        assert rid in m.calls[1]["system"]


def test_judge_reply_is_validated():
    with pytest.raises(ValueError, match="judge"):
        p.evaluate_optimize(T1, call=ScriptedModel(["d", '{"tone": {"pass": true, "reason": "x"}}']))


# ── contract ────────────────────────────────────────────────────────────────
def test_contract_matches_generator():
    r = subprocess.run([sys.executable, str(core.REPO / "agents" / "fixtures" / "gen_contract.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_contract_shape_and_coverage():
    rows = [json.loads(l) for l in (core.REPO / "agents" / "fixtures" / "contract.jsonl").read_text().splitlines()]
    base = rows[:15]
    assert {(r["pattern"], r["ticket_id"]) for r in base} == {(pt, t) for pt in
        ("chain", "route", "parallel", "orchestrate", "evaluate_optimize") for t in ("t001", "t007", "t061")}
    assert len({r["name"] for r in rows}) == len(rows)
    for r in rows:
        assert set(r) == {"name", "pattern", "ticket_id", "scripted", "expect"}
        assert set(r["expect"]) in ({"output", "calls"}, {"error"})
        assert r["expect"] == {"error": True} or set(r["expect"]) == {"output", "calls"}
    errors = {r["name"] for r in rows if "error" in r["expect"]}
    assert {"route-bad-route", "chain-bad-category", "orchestrate-plan-too-long", "orchestrate-plan-non-string-item",
            "evaluate_optimize-judge-bad-json", "evaluate_optimize-judge-missing-pass",
            "evaluate_optimize-judge-non-boolean-pass"} <= errors
    assert not any("error" in r["expect"] for r in base)


def test_contract_replays_through_the_patterns():
    import gen_contract
    for case in gen_contract.build_cases():
        assert case == gen_contract.run_case(case["name"], case["pattern"], case["ticket_id"], case["scripted"])
