"""Tests for the LangGraph desk. A GenericFakeChatModel subclass that accepts bind_tools stands in for Claude:
no proxy, no network, no API key.

The tools are the real lumen-read and lumen-ops MCP servers (in memory) opened through desk_core's
open_desk_tools, so a write is a real row in a temporary labels file and the harness guard decides it. The
guard's scripted person prints and appends to the harness's trace.jsonl in the working directory, so every
test runs in tmp_path.
"""
import contextlib
import itertools
import json
import sys
import time
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "agents" / "frameworks"))

import langgraph_desk as lg  # noqa: E402
import desk_core as core  # noqa: E402
import supervisor as sv  # noqa: E402

T = {t["id"]: t for t in core.TICKETS}
READ = ["classify_complaint", "get_ticket", "search_tickets"]
WORKER_TOOLS = ["drafter", "research", "reviewer", "triage"]
DENIED = "The user denied this action."     # the harness guard's words (desk_core.run_tool)


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


class FakeChat(GenericFakeChatModel):
    """GenericFakeChatModel has no bind_tools, and create_agent needs one: the tools are ignored, the
    scripted messages come back in order (the facts sheet's workaround)."""

    def bind_tools(self, tools, **kwargs):
        BOUND.append(sorted(t.name for t in tools))   # what reached the model, per call
        return self


BOUND: list = []


_ids = itertools.count()


def call(name, **args):
    return {"name": name, "args": args}


def ai(*entries, text=""):
    """One scripted AIMessage: a text reply, or one or more tool calls, with usage so the trace has tokens."""
    calls = [{"name": e["name"], "args": e["args"], "id": f"call_{next(_ids)}", "type": "tool_call"} for e in entries]
    n = next(_ids)
    content = text
    if n % 2 == 0:   # R59: half the replies lead with a thinking block, as Haiku 5.5's often do
        content = [{"type": "thinking", "thinking": "", "signature": f"sig_{n}"}] + ([{"type": "text", "text": text}] if text else [])
    return AIMessage(content=content, tool_calls=calls,
                     usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})


def fake(*script):
    """A fake model over the script; a str entry is a text reply, a dict one tool call, a list several."""
    msgs = [ai(text=e) if isinstance(e, str) else ai(*e) if isinstance(e, list) else ai(e) for e in script]
    return FakeChat(messages=iter(msgs))


def judge(*failed):
    return json.dumps({r: {"pass": r not in failed, "reason": "fails" if r in failed else "ok"}
                       for r in sv.RUBRIC_IDS}, sort_keys=True)


def rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def worker(name, task="do it"):
    return call(name, task=task)


def full_script(tid="t001", category="billing", label=True):
    s = [worker("triage"), json.dumps({"category": category, "priority": "high"}),
         worker("research"), call("get_ticket", id=tid), f"Found {tid}.",
         worker("drafter"), f"Draft for {tid}.",
         worker("reviewer"), judge()]
    if label:
        s.append(call("label_ticket", id=tid, label=category))
    return s + ["Done."]


def roles(run):
    return [(c["agent"], c["role"]) for c in run["calls"]]


def sup_tools(run):
    return [c["tools"] for c in run["calls"] if c["agent"] == "supervisor"]


@pytest.fixture
def hitl_says_yes(monkeypatch):
    """The framework's interrupt answered yes whatever `approve` says, so only the harness guard decides."""
    monkeypatch.setattr(lg, "_hitl_decision", lambda approve: {"type": "approve"})


# ── the single agent ────────────────────────────────────────────────────────
def test_single_t001_tool_trajectory_and_one_write_on_yes(tmp_path):
    labels = tmp_path / "l.jsonl"
    run = lg.langgraph_single(T["t001"], model=fake(call("get_ticket", id="t001"),
                                                    call("label_ticket", id="t001", label="billing"), "Reply."),
                              approve="accept", elicit="accept", labels_path=labels)
    assert [(t["agent"], t["tool"], t["is_error"]) for t in run["tool_calls"]] == [
        ("single", "get_ticket", False), ("single", "label_ticket", False)]
    assert [r["label"] for r in rows(labels)] == ["billing"]
    assert run["reply"] == "Reply." and run["stop"] == "end_turn"
    assert roles(run) == [("single", "act")] * 3
    assert run["calls"][0]["tools"] == READ[:2] + ["label_ticket", "search_tickets"]
    # the framework's own interrupt asked about the write, and the scripted person answered it
    assert run["interrupts"] == [{"agent": "single", "tool": "label_ticket",
                                  "args": {"id": "t001", "label": "billing"}, "decision": "approve"}]


def test_single_sibling_tool_calls_run_in_the_order_asked(tmp_path, monkeypatch):
    """LangGraph runs a turn's tool calls in parallel threads unless told not to. The first call here is
    slowed before it reaches the session lock, so in parallel the second would run first (R19)."""
    real = lg.open_desk_tools

    @contextlib.contextmanager
    def slow_first(*args, **kwargs):
        with real(*args, **kwargs) as (schema, run_tool, session):
            def run_tool_(name, args_):
                if name == "search_tickets":
                    time.sleep(0.2)
                return run_tool(name, args_)
            yield schema, run_tool_, session

    monkeypatch.setattr(lg, "open_desk_tools", slow_first)
    run = lg.langgraph_single(T["t007"], model=fake([call("search_tickets", query="refund", limit=3),
                                                     call("get_ticket", id="t007")], "Reply."),
                              labels_path=tmp_path / "l.jsonl")
    assert [t["tool"] for t in run["tool_calls"]] == ["search_tickets", "get_ticket"]
    assert [r["name"] for r in run["trace"].to_list() if r["kind"] == "tool"] == ["search_tickets", "get_ticket"]


def test_single_decline_rejects_at_the_interrupt(tmp_path):
    labels = tmp_path / "l.jsonl"
    run = lg.langgraph_single(T["t001"], model=fake(call("label_ticket", id="t001", label="billing"), "Done."),
                              approve="decline", elicit="accept", labels_path=labels)
    assert rows(labels) == [] and run["interrupts"][0]["decision"] == "reject"
    assert run["tool_calls"] == [{"agent": "single", "tool": "label_ticket",
                                  "args": {"id": "t001", "label": "billing"}, "is_error": True}]


def test_guard_blocks_a_write_after_an_untrusted_read(tmp_path, hitl_says_yes):
    """Even when the framework's interrupt says yes, the harness guard (approve=decline) refuses the write."""
    labels = tmp_path / "l.jsonl"
    model = fake(call("get_ticket", id="t001"), call("label_ticket", id="t001", label="billing"), "Done.")
    run = lg.langgraph_single(T["t001"], model=model, approve="decline", elicit="accept", labels_path=labels)
    assert rows(labels) == []
    assert run["tool_calls"][-1] == {"agent": "single", "tool": "label_ticket",
                                     "args": {"id": "t001", "label": "billing"}, "is_error": True}
    assert run["tool_results"][-1] == DENIED
    assert "permission" in (tmp_path / "trace.jsonl").read_text()     # the guard's person was asked


def test_r24_first_turn_t061_label_is_denied(tmp_path, hitl_says_yes):
    """No read at all: the label is the model's first act. The session was marked before the first call
    (R24), so the guard still asks and the scripted person says no."""
    labels = tmp_path / "l.jsonl"
    run = lg.langgraph_single(T["t061"], model=fake(call("label_ticket", id="t001", label="outage"), "Done."),
                              approve="decline", elicit="accept", labels_path=labels)
    assert rows(labels) == []
    assert run["tool_results"] == [DENIED]


def test_r24_supervisor_marks_before_any_read(tmp_path, hitl_says_yes):
    labels = tmp_path / "l.jsonl"
    model = fake(worker("triage"), '{"category": "how_to", "priority": "low"}', worker("drafter"), "Draft.",
                 worker("reviewer"), judge(), call("label_ticket", id="t061", label="how_to"), "Done.")
    run = lg.langgraph_supervisor(T["t061"], model=model, approve="decline", elicit="accept", labels_path=labels)
    assert rows(labels) == []
    assert run["tool_calls"] == [{"agent": "supervisor", "tool": "label_ticket",
                                  "args": {"id": "t061", "label": "how_to"}, "is_error": True}]
    assert run["tool_results"] == [DENIED]


def test_single_turn_cap(tmp_path):
    run = lg.langgraph_single(T["t001"], model=fake(*[call("get_ticket", id="t001")] * sv.MAX_TURNS),
                              labels_path=tmp_path / "l.jsonl")
    assert run["stop"] == "max_turns" and run["reply"] == sv.STOPPED
    assert len(run["calls"]) == sv.MAX_TURNS
    assert [(r["agent"], r["name"]) for r in run["trace"].to_list() if r["kind"] == "stop"] == [("single", "max_turns")]


def test_default_model_points_at_the_proxy():
    m = lg.default_model()
    assert m.anthropic_api_url == core.PROXY_URL and m.model == core.MODEL
    assert m.max_tokens == core.MAX_TOKENS and m.max_retries == 0
    assert m.anthropic_api_key.get_secret_value() == core.DUMMY_KEY


# ── the supervisor ──────────────────────────────────────────────────────────
def test_supervisor_t001_trajectory(tmp_path):
    labels = tmp_path / "l.jsonl"
    BOUND.clear()
    run = lg.langgraph_supervisor(T["t001"], model=fake(*full_script()), approve="accept", elicit="accept",
                                  labels_path=labels)
    assert [h["worker"] for h in run["handoffs"]] == ["triage", "research", "drafter", "reviewer"]
    assert roles(run) == [("supervisor", "supervise"), ("triage", "triage"),
                          ("supervisor", "supervise"), ("research", "research"), ("research", "research"),
                          ("supervisor", "supervise"), ("drafter", "draft"),
                          ("supervisor", "supervise"), ("reviewer", "review"),
                          ("supervisor", "supervise"), ("supervisor", "supervise")]
    assert [(t["agent"], t["tool"], t["is_error"]) for t in run["tool_calls"]] == [
        ("research", "get_ticket", False), ("supervisor", "label_ticket", False)]
    assert [r["label"] for r in rows(labels)] == ["billing"]
    assert run["state"]["review"]["passed"] is True and run["reply"] == "Draft for t001."
    # the tool list each model call bound is the one logged (workers without tools bind nothing)
    assert BOUND == [c["tools"] for c in run["calls"] if c["tools"]]
    assert BOUND[0] == WORKER_TOOLS and BOUND[-1] == sorted(WORKER_TOOLS + ["label_ticket"])
    assert [h["changed"] for h in run["handoffs"]][:3] == [
        {"category": "billing"}, {"findings": "Found t001."}, {"draft": "Draft for t001."}]


def test_trace_shape_matches_r2(tmp_path):
    run = lg.langgraph_supervisor(T["t001"], model=fake(*full_script()), approve="accept", elicit="accept",
                                  labels_path=tmp_path / "l.jsonl")
    records = run["trace"].to_list()
    assert records and all(tuple(r) == core.TRACE_FIELDS for r in records)
    assert {r["kind"] for r in records} == {"model", "tool", "handoff"}
    models = [r for r in records if r["kind"] == "model"]
    assert len(models) == len(run["calls"])
    assert all(r["tokens_in"] == 10 and r["tokens_out"] == 2 and isinstance(r["ms"], int) for r in models)
    assert [r["agent"] for r in records if r["kind"] == "tool"] == ["research", "supervisor"]
    assert all(r["tokens_in"] is None and r["tokens_out"] is None for r in records if r["kind"] != "model")
    assert [r["name"] for r in records if r["kind"] == "handoff"] == [
        "triage: category", "research: findings", "drafter: draft", "reviewer: review"]
    single = lg.langgraph_single(T["t001"], model=fake("Hi."), labels_path=tmp_path / "l2.jsonl")
    assert [tuple(r) for r in single["trace"].to_list()] == [core.TRACE_FIELDS]


def test_research_cannot_see_or_use_ops_tools(tmp_path):
    labels = tmp_path / "l.jsonl"
    model = fake(worker("research"), call("label_ticket", id="t001", label="outage"), call("get_ticket", id="t061"),
                 "Read.", "Done.")
    run = lg.langgraph_supervisor(T["t061"], model=model, approve="accept", elicit="accept", labels_path=labels)
    assert {tuple(c["tools"]) for c in run["calls"] if c["agent"] == "research"} == {tuple(READ)}
    assert [(t["agent"], t["tool"], t["is_error"]) for t in run["tool_calls"]] == [
        ("research", "label_ticket", True), ("research", "get_ticket", False)]
    assert rows(labels) == []
    assert [r["name"] for r in run["trace"].to_list() if r["kind"] == "tool"] == ["get_ticket"]   # never reached run_tool


def test_no_write_before_the_review_passes(tmp_path):
    labels = tmp_path / "l.jsonl"
    label = call("label_ticket", id="t001", label="billing")
    model = fake(label, worker("triage"), '{"category": "billing", "priority": "high"}',
                 worker("drafter"), "Draft.", worker("reviewer"), judge(), label, "Done.")
    run = lg.langgraph_supervisor(T["t001"], model=model, approve="accept", elicit="accept", labels_path=labels)
    assert sup_tools(run) == [WORKER_TOOLS] * 4 + [sorted(WORKER_TOOLS + ["label_ticket"])] * 2
    assert [t["is_error"] for t in run["tool_calls"]] == [True, False]
    assert run["tool_results"][0] == "Unknown tool: label_ticket"
    assert len(rows(labels)) == 1
    assert [i["args"] for i in run["interrupts"]] == [{"id": "t001", "label": "billing"}]   # the refused one never asked


def test_label_next_to_the_reviewing_handoff_is_refused(tmp_path):
    labels = tmp_path / "l.jsonl"
    model = fake(worker("triage"), '{"category": "billing", "priority": "high"}', worker("drafter"), "Draft.",
                 [worker("reviewer"), call("label_ticket", id="t001", label="billing")], judge(), "Done.")
    run = lg.langgraph_supervisor(T["t001"], model=model, approve="accept", elicit="accept", labels_path=labels)
    assert run["state"]["review"]["passed"] is True
    assert run["tool_calls"][-1]["tool"] == "label_ticket" and run["tool_calls"][-1]["is_error"] is True
    assert rows(labels) == []


def test_r27_label_is_bound_to_the_state(tmp_path):
    labels = tmp_path / "l.jsonl"
    model = fake(worker("triage"), '{"category": "how_to", "priority": "low"}', worker("drafter"), "Draft.",
                 worker("reviewer"), judge(), call("label_ticket", id="t001", label="outage"),
                 call("label_ticket", id="t061", label="outage"), "Done.")
    run = lg.langgraph_supervisor(T["t061"], model=model, approve="accept", elicit="accept", labels_path=labels)
    assert [t["is_error"] for t in run["tool_calls"]] == [True, True]
    assert rows(labels) == [] and run["interrupts"] == []
    assert not [r for r in run["trace"].to_list() if r["kind"] == "tool"]
    assert all("may only label this ticket (t061)" in r for r in run["tool_results"])
    trace_file = tmp_path / "trace.jsonl"
    assert not trace_file.exists() or "permission" not in trace_file.read_text()


def test_r30_a_category_change_clears_the_review(tmp_path):
    labels = tmp_path / "l.jsonl"
    model = fake(worker("triage"), '{"category": "billing", "priority": "high"}', worker("drafter"), "Draft.",
                 worker("reviewer"), judge(), worker("triage"), '{"category": "account", "priority": "high"}',
                 call("label_ticket", id="t001", label="account"), "Done.")
    run = lg.langgraph_supervisor(T["t001"], model=model, approve="accept", elicit="accept", labels_path=labels)
    assert run["handoffs"][-1]["changed"] == {"category": "account", "review": None}
    assert sup_tools(run)[-2:] == [WORKER_TOOLS, WORKER_TOOLS]
    assert run["tool_calls"][-1]["is_error"] is True and rows(labels) == []
    assert run["reply"] == "Done."


def test_a_failed_handoff_is_an_error_result(tmp_path):
    model = fake(worker("reviewer"), worker("triage"), '{"category": "weather", "priority": "high"}', "Stopped.")
    run = lg.langgraph_supervisor(T["t001"], model=model, labels_path=tmp_path / "l.jsonl")
    assert [(h["worker"], h["is_error"], h["changed"]) for h in run["handoffs"]] == [
        ("reviewer", True, {}), ("triage", True, {})]
    assert run["state"]["category"] is None and run["reply"] == "Stopped."


def test_supervisor_turn_cap_and_research_cap(tmp_path):
    run = lg.langgraph_supervisor(T["t001"], model=fake(*[worker("reviewer")] * sv.MAX_TURNS),
                                  labels_path=tmp_path / "l.jsonl")
    assert run["stop"] == "max_turns" and run["reply"] == sv.STOPPED
    stops = lambda r: [(x["agent"], x["name"]) for x in r["trace"].to_list() if x["kind"] == "stop"]
    assert stops(run) == [("supervisor", "max_turns")]
    res = lg.langgraph_supervisor(T["t001"], model=fake(worker("research"), *[call("get_ticket", id="t001")] * sv.MAX_WORKER_STEPS, "Done."),
                                  labels_path=tmp_path / "l.jsonl")
    assert stops(res) == [("research", "max_worker_steps")]


def test_langgraph_engine_registered():
    eng = core.ENGINES["langgraph"]
    assert isinstance(eng, core.Engine)
    assert eng.single is lg.langgraph_single and eng.supervisor is lg.langgraph_supervisor


# ── fix round 1: R35, tool_calls holds MCP calls only ───────────────────────
def test_r35_unknown_worker_is_an_error_not_a_tool_call(tmp_path):
    run = lg.langgraph_supervisor(T["t001"], model=fake(worker("billing-bot"), "Stopped."),
                                  labels_path=tmp_path / "l.jsonl")
    assert run["tool_calls"] == [] and run["tool_results"] == [] and run["handoffs"] == []
    assert len(run["errors"]) == 1
    err = run["errors"][0]
    assert (err["agent"], err["tool"], err["args"]) == ("supervisor", "billing-bot", {"task": "do it"})
    assert "billing-bot" in err["result"]
    assert run["reply"] == "Stopped."


def test_r35_single_unknown_tool_is_an_error_not_a_tool_call(tmp_path):
    run = lg.langgraph_single(T["t001"], model=fake(call("write_file", path="x", content="y"), "Done."),
                              labels_path=tmp_path / "l.jsonl")
    assert run["tool_calls"] == [] and [e["tool"] for e in run["errors"]] == ["write_file"]
