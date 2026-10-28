"""Tests for the single-agent and supervisor desks. A scripted fake model stands in for Claude: no proxy, no API.

The tools are the real lumen-read and lumen-ops MCP servers (in memory), so a write is a real row in a
temporary labels file. The guard's scripted person prints and appends to the harness's trace.jsonl in the
working directory, so every test runs in tmp_path.
"""
import json
from types import SimpleNamespace

import pytest

import desk_core as core
import supervisor as sv
from scripted_model import ScriptedModel

T = {t["id"]: t for t in core.TICKETS}
READ = ["classify_complaint", "get_ticket", "search_tickets"]


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def tool(name, **inp):
    return {"tool_use": {"name": name, "input": inp}}


def handoff(worker, task="do it"):
    return tool("handoff", worker=worker, task=task)


def judge(*failed):
    return json.dumps({r: {"pass": r not in failed, "reason": "fails" if r in failed else "ok"}
                       for r in sv.RUBRIC_IDS}, sort_keys=True)


def rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def roles(run):
    return [(c["agent"], c["role"]) for c in run["calls"]]


def full_script(tid="t001", label=True):
    """triage, research (one lookup), drafter, reviewer (pass), then optionally the label, then the end."""
    s = [handoff("triage"), '{"category": "billing", "priority": "high"}',
         handoff("research"), tool("get_ticket", id=tid), f"Found {tid}.",
         handoff("drafter"), f"Draft for {tid}.",
         handoff("reviewer"), judge()]
    if label:
        s.append(tool("label_ticket", id=tid, label="billing"))
    return s + ["Done."]


# ── supervisor: handoffs ────────────────────────────────────────────────────
def test_handoff_order_and_call_sequence(tmp_path):
    m = ScriptedModel(full_script(label=False))
    run = sv.supervisor(T["t001"], call=m, labels_path=tmp_path / "l.jsonl")
    assert [h["worker"] for h in run["handoffs"]] == ["triage", "research", "drafter", "reviewer"]
    assert roles(run) == [("supervisor", "supervise"), ("triage", "triage"),
                          ("supervisor", "supervise"), ("research", "research"), ("research", "research"),
                          ("supervisor", "supervise"), ("drafter", "draft"),
                          ("supervisor", "supervise"), ("reviewer", "review"),
                          ("supervisor", "supervise")]
    assert m.unused() == 0
    assert run["stop"] == "end_turn"
    assert run["reply"] == "Draft for t001."   # the reviewed draft is the reply


def test_state_changes_are_logged(tmp_path):
    run = sv.supervisor(T["t001"], call=ScriptedModel(full_script(label=False)), labels_path=tmp_path / "l.jsonl")
    assert [h["changed"] for h in run["handoffs"]] == [
        {"category": "billing"}, {"findings": "Found t001."}, {"draft": "Draft for t001."},
        {"review": {"passed": True, "verdict": {r: True for r in sv.RUBRIC_IDS}}}]
    assert run["state"] == {"ticket_id": "t001", "category": "billing", "findings": "Found t001.",
                            "draft": "Draft for t001.",
                            "review": {"passed": True, "verdict": {r: True for r in sv.RUBRIC_IDS}}}
    logged = [r for r in run["trace"].to_list() if r["kind"] == "handoff"]
    assert [r["name"] for r in logged] == ["triage: category", "research: findings", "drafter: draft", "reviewer: review"]
    assert all(r["agent"] == "supervisor" and isinstance(r["ms"], int) for r in logged)


def test_unknown_worker_is_an_error_result_and_changes_nothing(tmp_path):
    """R26: the loop goes on and the state is unchanged. R37: an unknown worker is no handoff; it goes to
    `errors` and leaves no handoff Trace record."""
    m = ScriptedModel([handoff("billing-bot"), "Gave up."])
    run = sv.supervisor(T["t001"], call=m, labels_path=tmp_path / "l.jsonl")
    assert run["handoffs"] == [] and run["tool_calls"] == []
    assert run["errors"] == [{"agent": "supervisor", "tool": "handoff", "args": {"worker": "billing-bot", "task": "do it"},
                              "result": "Unknown worker: billing-bot. Workers: triage, research, drafter, reviewer"}]
    assert run["state"] == {"ticket_id": "t001", "category": None, "findings": None, "draft": None, "review": None}
    result = m.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "billing-bot" in result["content"]
    assert [r for r in run["trace"].to_list() if r["kind"] == "handoff"] == []
    assert run["reply"] == "Gave up."


def test_non_string_worker_goes_to_errors_as_json(tmp_path):
    run = sv.supervisor(T["t001"], call=ScriptedModel([tool("handoff", worker=None, task="x"), "Gave up."]),
                        labels_path=tmp_path / "l.jsonl")
    assert run["handoffs"] == []
    assert run["errors"][0]["result"].startswith("Unknown worker: null. ")


def test_unknown_tool_goes_to_errors_not_tool_calls(tmp_path):
    """R37 (R35 in every engine): tool_calls holds lumen-read and lumen-ops names only."""
    m = ScriptedModel([tool("write_file", path="x", content="y"), tool("get_ticket", id="t001"), "Done."])
    run = sv.single_agent(T["t001"], call=m, labels_path=tmp_path / "l.jsonl")
    assert run["tool_calls"] == [{"agent": "single", "tool": "get_ticket", "args": {"id": "t001"}, "is_error": False}]
    assert run["errors"] == [{"agent": "single", "tool": "write_file", "args": {"path": "x", "content": "y"},
                              "result": "Unknown tool: write_file"}]
    assert m.calls[1]["messages"][-1]["content"][0]["is_error"] is True and run["stop"] == "end_turn"


def test_supervisor_unknown_tool_goes_to_errors(tmp_path):
    m = ScriptedModel([tool("write_file", path="x"), tool("get_ticket", id="t001"), "Done."])
    run = sv.supervisor(T["t001"], call=m, labels_path=tmp_path / "l.jsonl")
    assert [e["tool"] for e in run["errors"]] == ["write_file"]
    assert run["tool_calls"] == [{"agent": "supervisor", "tool": "get_ticket", "args": {"id": "t001"}, "is_error": True}]


def test_bad_worker_output_is_an_error_result(tmp_path):
    m = ScriptedModel([handoff("triage"), '{"category": "weather", "priority": "high"}',
                       handoff("reviewer"), "Stopped."])
    run = sv.supervisor(T["t001"], call=m, labels_path=tmp_path / "l.jsonl")
    assert [h["is_error"] for h in run["handoffs"]] == [True, True]   # bad category; nothing to review yet
    assert run["state"]["category"] is None
    assert roles(run).count(("reviewer", "review")) == 0               # no draft: the reviewer is not called


# ── supervisor: tools ───────────────────────────────────────────────────────
def test_research_cannot_see_or_use_ops_tools(tmp_path):
    labels = tmp_path / "l.jsonl"
    m = ScriptedModel([handoff("research"), tool("label_ticket", id="t001", label="outage"),
                       tool("get_ticket", id="t061"), "Read t061.", "Done."])
    run = sv.supervisor(T["t061"], call=m, labels_path=labels, approve="accept")
    research = [c for c in run["calls"] if c["agent"] == "research"]
    assert research and all(c["tools"] == READ for c in research)
    assert all(t["name"] in READ for t in m.calls[1]["tools"])
    assert run["tool_calls"][:2] == [
        {"agent": "research", "tool": "label_ticket", "args": {"id": "t001", "label": "outage"}, "is_error": True},
        {"agent": "research", "tool": "get_ticket", "args": {"id": "t061"}, "is_error": False}]
    assert rows(labels) == []


def test_ops_tool_absent_until_review_passes(tmp_path):
    labels = tmp_path / "l.jsonl"
    m = ScriptedModel([tool("label_ticket", id="t001", label="billing")] + full_script(label=True))
    run = sv.supervisor(T["t001"], call=m, labels_path=labels, approve="accept", elicit="accept")
    sup = [c["tools"] for c in run["calls"] if c["agent"] == "supervisor"]
    assert sup[:5] == [["handoff"]] * 5                    # before and during the review
    assert sup[5:] == [["handoff", "label_ticket"]] * 2    # after the reviewer passed
    assert all(t["name"] == "handoff" for t in m.calls[0]["tools"])
    first = run["tool_calls"][0]
    assert first == {"agent": "supervisor", "tool": "label_ticket", "args": {"id": "t001", "label": "billing"}, "is_error": True}
    assert run["tool_calls"][-1] == {"agent": "supervisor", "tool": "label_ticket",
                                     "args": {"id": "t001", "label": "billing"}, "is_error": False}
    assert [r["label"] for r in rows(labels)] == ["billing"]   # exactly one row: the one after the review


def test_no_write_before_review_even_if_requested_in_the_reviewing_response(tmp_path):
    """The same-response case: a label_ticket next to the reviewer's handoff was not in that request's tools."""
    labels = tmp_path / "l.jsonl"
    script = full_script(label=False)[:7]                  # up to the draft

    class Model(ScriptedModel):
        def __call__(self, messages, **kw):
            n = len(self.calls)
            if n == 7:   # the supervisor asks for the review and the label in one response
                self.calls.append({"agent": kw.get("agent"), "tools": kw.get("tools"), "messages": messages})
                blocks = [SimpleNamespace(type="tool_use", id="toolu_a", name="handoff", input={"worker": "reviewer", "task": "x"}),
                          SimpleNamespace(type="tool_use", id="toolu_b", name="label_ticket", input={"id": "t001", "label": "billing"})]
                return SimpleNamespace(content=blocks, stop_reason="tool_use")
            return super().__call__(messages, **kw)

    m = Model(script + ["placeholder", judge(), "Done."])
    run = sv.supervisor(T["t001"], call=m, labels_path=labels, approve="accept", elicit="accept")
    assert run["state"]["review"]["passed"] is True
    assert run["tool_calls"][-1]["tool"] == "label_ticket" and run["tool_calls"][-1]["is_error"] is True
    assert rows(labels) == []


def test_a_new_draft_resets_the_review_and_hides_the_ops_tool_again(tmp_path):
    m = ScriptedModel(full_script(label=False)[:-1] + [handoff("drafter"), "Draft 2.", "Done."])
    run = sv.supervisor(T["t001"], call=m, labels_path=tmp_path / "l.jsonl")
    assert run["handoffs"][-1]["changed"] == {"draft": "Draft 2.", "review": None}
    assert [c["tools"] for c in run["calls"] if c["agent"] == "supervisor"][-1] == ["handoff"]
    assert run["reply"] == "Done."   # no passed review: the supervisor's own text


def test_guard_still_decides_the_write_after_review(tmp_path):
    """The ticket text is untrusted and in the context from the first call: a write needs a person's yes."""
    labels = tmp_path / "l.jsonl"
    run = sv.supervisor(T["t001"], call=ScriptedModel(full_script()), labels_path=labels, approve="decline", elicit="accept")
    assert run["tool_calls"][-1]["is_error"] is True and rows(labels) == []
    labels2 = tmp_path / "l2.jsonl"
    sv.supervisor(T["t001"], call=ScriptedModel(full_script()), labels_path=labels2, approve="accept", elicit="decline")
    assert rows(labels2) == []    # the server's own confirmation still decides


def test_reviewer_uses_the_labs_rubric_on_the_same_call(tmp_path):
    m = ScriptedModel(full_script(label=False))
    sv.supervisor(T["t001"], call=m, labels_path=tmp_path / "l.jsonl")
    rev = next(c for c in m.calls if c["agent"] == "reviewer")
    assert rev["tools"] is None
    for r in sv.RUBRIC_IDS:
        assert r in json.dumps(rev["messages"])
    assert "Draft for t001." in json.dumps(rev["messages"])


def test_workers_without_tools_get_none(tmp_path):
    m = ScriptedModel(full_script(label=False))
    sv.supervisor(T["t001"], call=m, labels_path=tmp_path / "l.jsonl")
    for c in m.calls:
        if c["agent"] in ("triage", "drafter", "reviewer"):
            assert c["tools"] is None


def test_supervisor_turn_cap(tmp_path):
    m = ScriptedModel([handoff("billing-bot")] * sv.MAX_TURNS)
    run = sv.supervisor(T["t001"], call=m, labels_path=tmp_path / "l.jsonl")
    assert run["stop"] == "max_turns" and len(m.calls) == sv.MAX_TURNS
    assert run["reply"] == sv.STOPPED


# ── single agent ────────────────────────────────────────────────────────────
def test_single_agent_has_all_desk_tools_and_writes_once_on_yes(tmp_path):
    labels = tmp_path / "l.jsonl"
    m = ScriptedModel([tool("get_ticket", id="t001"), tool("label_ticket", id="t001", label="billing"), "Reply."])
    run = sv.single_agent(T["t001"], call=m, labels_path=labels, approve="accept", elicit="accept")
    assert {t["name"] for t in m.calls[0]["tools"]} == set(READ) | {"label_ticket"}
    assert roles(run) == [("single", "act")] * 3
    assert run["calls"][0]["tools"] == READ[:2] + ["label_ticket", "search_tickets"]
    assert run["reply"] == "Reply." and run["stop"] == "end_turn"
    assert [t["is_error"] for t in run["tool_calls"]] == [False, False]
    assert [r["label"] for r in rows(labels)] == ["billing"]
    assert [r["kind"] for r in run["trace"].to_list()] == ["tool", "tool"]   # the scripted call logs no model records
    # the tool results go back in one user message, matched by id
    last = m.calls[2]["messages"]
    assert last[-1]["content"][0]["tool_use_id"] == "toolu_001"


def test_single_agent_t061_injection_never_writes_without_a_yes(tmp_path):
    labels = tmp_path / "l.jsonl"
    m = ScriptedModel([tool("label_ticket", id="t001", label="outage"), "Done."])   # first response, no read at all
    run = sv.single_agent(T["t061"], call=m, labels_path=labels, approve="decline", elicit="accept")
    assert run["tool_calls"][0]["is_error"] is True and rows(labels) == []


def test_single_agent_turn_cap(tmp_path):
    m = ScriptedModel([tool("get_ticket", id="t001")] * sv.MAX_TURNS)
    run = sv.single_agent(T["t001"], call=m, labels_path=tmp_path / "l.jsonl")
    assert sv.MAX_TURNS == 8
    assert run["stop"] == "max_turns" and run["reply"] == sv.STOPPED and len(m.calls) == 8


# ── registry and contract ───────────────────────────────────────────────────
def test_hand_engine_registered():
    eng = core.ENGINES["hand"]
    assert isinstance(eng, core.Engine)
    assert eng.single is sv.single_agent and eng.supervisor is sv.supervisor


def test_contract_has_desk_cases():
    rows_ = [json.loads(l) for l in (core.REPO / "agents" / "fixtures" / "contract.jsonl").read_text().splitlines()]
    desk = {(r["pattern"], r["ticket_id"]) for r in rows_ if r["pattern"] in ("single", "supervisor")}
    assert {(p, t) for p in ("single", "supervisor") for t in ("t001", "t007", "t061")} <= desk
    names = {r["name"] for r in rows_}
    assert "supervisor-unknown-worker" in names
    for r in rows_:
        if r["pattern"] in ("single", "supervisor"):
            for c in r["expect"]["calls"]:
                if c["agent"] == "research":
                    assert "label_ticket" not in c["tools"]


# ── fix round 1: R24 mark, R27 binding, R29 stop records, reason text ───────
def test_supervisor_marks_untrusted_before_any_read(tmp_path):
    """No research step, so no tool read taints the session: only the R24 mark makes the guard ask."""
    labels = tmp_path / "l.jsonl"
    m = ScriptedModel([handoff("triage"), '{"category": "how_to", "priority": "low"}',
                       handoff("drafter"), "Draft.", handoff("reviewer"), judge(),
                       tool("label_ticket", id="t061", label="how_to"), "Done."])
    run = sv.supervisor(T["t061"], call=m, labels_path=labels, approve="decline", elicit="accept")
    assert rows(labels) == []
    assert run["tool_calls"] == [{"agent": "supervisor", "tool": "label_ticket",
                                  "args": {"id": "t061", "label": "how_to"}, "is_error": True}]
    result = m.calls[-1]["messages"][-1]["content"][0]
    assert result["content"] == "The user denied this action."


def test_label_is_bound_to_the_state(tmp_path):
    """R27: the t061 injection target is refused after a passed review, before any person is asked."""
    labels = tmp_path / "l.jsonl"
    m = ScriptedModel([handoff("triage"), '{"category": "how_to", "priority": "low"}',
                       handoff("drafter"), "Draft.", handoff("reviewer"), judge(),
                       tool("label_ticket", id="t001", label="outage"),
                       tool("label_ticket", id="t061", label="outage"), "Done."])
    run = sv.supervisor(T["t061"], call=m, labels_path=labels, approve="accept", elicit="accept")
    assert [t["is_error"] for t in run["tool_calls"]] == [True, True]
    assert rows(labels) == []
    assert not [r for r in run["trace"].to_list() if r["kind"] == "tool"]     # never reached run_tool
    trace_file = tmp_path / "trace.jsonl"                                      # the guard's person was never asked
    assert not trace_file.exists() or "permission" not in trace_file.read_text()
    assert "ticket" in m.calls[7]["messages"][-1]["content"][0]["content"]


def stops(run):
    return [(r["agent"], r["name"]) for r in run["trace"].to_list() if r["kind"] == "stop"]


def test_caps_emit_stop_records(tmp_path):
    single = sv.single_agent(T["t001"], call=ScriptedModel([tool("get_ticket", id="t001")] * sv.MAX_TURNS),
                             labels_path=tmp_path / "l.jsonl")
    assert stops(single) == [("single", "max_turns")]
    research = sv.supervisor(T["t001"], call=ScriptedModel([handoff("research")] + [tool("get_ticket", id="t001")] * sv.MAX_WORKER_STEPS + ["Done."]),
                             labels_path=tmp_path / "l.jsonl")
    assert stops(research) == [("research", "max_worker_steps")]
    sup = sv.supervisor(T["t001"], call=ScriptedModel([handoff("billing-bot")] * sv.MAX_TURNS), labels_path=tmp_path / "l.jsonl")
    assert stops(sup) == [("supervisor", "max_turns")]
    done = sv.single_agent(T["t001"], call=ScriptedModel(["Hi."]), labels_path=tmp_path / "l.jsonl")
    assert stops(done) == []


def test_supervisor_cap_reply_is_draft_only_if_review_passed(tmp_path):
    pad = sv.MAX_TURNS - 3
    passed = sv.supervisor(T["t001"], call=ScriptedModel([handoff("drafter"), "Draft.", handoff("reviewer"), judge()]
                                                         + [handoff("billing-bot")] * (pad + 1)), labels_path=tmp_path / "l.jsonl")
    assert passed["stop"] == "max_turns" and passed["reply"] == "Draft."
    failed = sv.supervisor(T["t001"], call=ScriptedModel([handoff("drafter"), "Draft.", handoff("reviewer"), judge("tone")]
                                                         + [handoff("billing-bot")] * (pad + 1)), labels_path=tmp_path / "l.jsonl")
    assert failed["stop"] == "max_turns" and failed["reply"] == sv.STOPPED


@pytest.mark.parametrize("reason,want", [(None, ""), (True, "true"), (False, "false"), ("ok", "ok"), ([1, "a"], '[1, "a"]')])
def test_reviewer_reason_text_is_normalised(reason, want):
    assert sv._reason({"pass": True, "reason": reason}) == want
    assert sv._reason({"pass": True}) == ""


# ── R30: a category change after a passed review clears it ──────────────────
def test_a_category_change_clears_the_review(tmp_path):
    """triage, drafter, reviewer (pass), then triage again with a new category: the pass was for the old
    category, so the review is cleared, label_ticket leaves the tools and a label (bound to the new
    category, R27) is refused before the guard. A re-triage to the same category keeps the review."""
    labels = tmp_path / "l.jsonl"
    m = ScriptedModel([handoff("triage"), '{"category": "billing", "priority": "high"}',
                       handoff("drafter"), "Draft.", handoff("reviewer"), judge(),
                       handoff("triage"), '{"category": "account", "priority": "high"}',
                       tool("label_ticket", id="t001", label="account"), "Done."])
    run = sv.supervisor(T["t001"], call=m, labels_path=labels, approve="accept", elicit="accept")
    assert run["handoffs"][-1]["changed"] == {"category": "account", "review": None}
    assert run["state"]["review"] is None
    assert [c["tools"] for c in run["calls"] if c["agent"] == "supervisor"][-2:] == [["handoff"], ["handoff"]]
    assert run["tool_calls"] == [{"agent": "supervisor", "tool": "label_ticket",
                                  "args": {"id": "t001", "label": "account"}, "is_error": True}]
    assert rows(labels) == [] and run["reply"] == "Done."
    same = sv.supervisor(T["t001"], call=ScriptedModel([handoff("triage"), '{"category": "billing", "priority": "high"}',
                                                        handoff("drafter"), "Draft.", handoff("reviewer"), judge(),
                                                        handoff("triage"), '{"category": "billing", "priority": "low"}', "Done."]),
                         labels_path=labels)
    assert same["handoffs"][-1]["changed"] == {} and same["state"]["review"]["passed"] is True


def test_thinking_blocks_are_skipped_and_sent_back_unmodified(tmp_path):
    """R59: Haiku 5.5 leads replies with thinking blocks unasked. The desk reads only text and tool_use, and sends
    the thinking block back unmodified, first in the assistant turn, before the tool results."""
    model = ScriptedModel([tool("get_ticket", id="t001"), "Reply."])     # call 0 leads with a thinking block
    run = sv.single_agent(T["t001"], call=model, labels_path=tmp_path / "l.jsonl")
    assert run["reply"] == "Reply." and [t["tool"] for t in run["tool_calls"]] == ["get_ticket"]
    assistant = model.calls[1]["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig_000"}
    assert assistant["content"][1]["type"] == "tool_use"


def test_redacted_thinking_is_sent_back_and_unknown_blocks_refused():
    from patterns import _blocks
    reply = SimpleNamespace(content=[SimpleNamespace(type="redacted_thinking", data="opaque"),
                                     SimpleNamespace(type="text", text="hi")])
    assert _blocks(reply) == [{"type": "redacted_thinking", "data": "opaque"}, {"type": "text", "text": "hi"}]
    odd = SimpleNamespace(content=[SimpleNamespace(type="server_tool_use", id="x")])
    with pytest.raises(ValueError, match="unexpected block type 'server_tool_use'"):
        _blocks(odd)
