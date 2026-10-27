"""Tests for the MCP-using agent (mcp/python/agent.py): the Track 1 harness with MCP tools,
replay, record-mode cost gating, and the untrusted-read guard.

No test calls the Claude API. The model is a fake with hand-written responses (`scripted`) or a
fake API client (`FakeClient`); recordings written by tests go to tmp_path, never to
mcp/data/recordings/, which holds only real, approved recordings. Labels go to tmp_path too.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
harness = agent.load_harness()


@pytest.fixture(autouse=True)
def quiet_trace(tmp_path, monkeypatch):
    """The harness appends its trace to ./trace.jsonl; keep it in tmp."""
    monkeypatch.setattr(harness, "TRACE_FILE", tmp_path / "trace.jsonl")


def rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# ── Hand-written fake model responses ───────────────────────────────────────
def use(name: str, input: dict, id: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def say(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def reply(stop_reason: str, *blocks) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=1200, output_tokens=80))


SEARCH_THEN_LABEL = [
    reply("tool_use", use("search_tickets", {"query": "export notes PDF"}, "tu_1")),
    reply("tool_use", use("label_ticket", {"id": "t061", "label": "outage"}, "tu_2")),
    reply("end_turn", say("Done.")),
]


def scripted(responses: list, seen: list | None = None):
    """A model_call factory: ignores the request and returns the next hand-written response."""
    queue = list(responses)

    def factory(system: str, tools: list[dict]):
        def call(messages: list[dict], **overrides) -> SimpleNamespace:
            if seen is not None:
                seen.append({"system": system, "tools": [t["name"] for t in tools], "messages": list(messages),
                             "registry": sorted(harness.REGISTRY)})
            return queue.pop(0)
        return call
    return factory


# ── 1. MCP tools become harness tools ───────────────────────────────────────
def test_mcp_tools_registered_as_harness_tools(tmp_path):
    with agent.open_tools(("read", "ops"), labels_path=tmp_path / "labels.jsonl", elicit="accept") as session:
        names = {t.name for t in session.tools}
        assert names == {"search_tickets", "get_ticket", "classify_complaint", "label_ticket"}
        for name in names:
            assert isinstance(harness.REGISTRY[name], harness.Tool)
        assert harness.REGISTRY["label_ticket"].needs_approval is True
        assert not any(harness.REGISTRY[n].needs_approval for n in names - {"label_ticket"})
        # Tool.fn is synchronous and returns a str, bridged to the async MCP client.
        out = harness.REGISTRY["get_ticket"].fn(id="t061")
        assert isinstance(out, str) and json.loads(out)["id"] == "t061"
        assert "untrusted_body" in json.loads(out)
        assert session.read_untrusted.is_set()
        # A tool error reaches the model as an error, with the server's fixed text.
        with pytest.raises(agent.ToolError, match="unknown ticket: t999"):
            harness.REGISTRY["get_ticket"].fn(id="t999")


def test_lesson5_exposes_only_lumen_read(tmp_path):
    seen: list = []
    responses = [reply("end_turn", say("ok"))]
    agent.run_scenario("lesson5", guard=False, elicit="accept", labels_path=tmp_path / "l.jsonl",
                       model_call=scripted(responses, seen))
    assert seen[0]["tools"] == ["classify_complaint", "get_ticket", "search_tickets"]
    assert "untrusted_body" in seen[0]["system"]


def test_request_tools_are_only_this_sessions_tools(tmp_path):
    """R34: the harness's own list_files/read_file/write_file are hidden for the session, then restored."""
    seen: list = []
    agent.run_scenario("capstone", guard=False, elicit="accept", labels_path=tmp_path / "l.jsonl",
                       model_call=scripted([reply("end_turn", say("ok"))], seen))
    session_tools = ["classify_complaint", "get_ticket", "label_ticket", "search_tickets"]
    assert seen[0]["tools"] == session_tools
    assert seen[0]["registry"] == session_tools          # nothing else could be executed either
    assert {"list_files", "read_file", "write_file"} <= set(harness.REGISTRY)  # restored on exit
    assert not set(session_tools) & set(harness.REGISTRY)


def test_failed_open_does_not_stall(monkeypatch):
    """A server that never finishes opening: __enter__ times out and __exit__ cancels, not waits."""
    import asyncio
    import time
    from contextlib import asynccontextmanager

    cancelled: list = []

    @asynccontextmanager
    async def hang(target, answer=None):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        yield None

    monkeypatch.setattr(agent, "connect", hang)
    monkeypatch.setattr(agent, "CALL_TIMEOUT", 0.5)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        with agent.Bridge({"read": (None, agent.deny)}):
            pass
    assert time.monotonic() - start < 3
    assert cancelled == [True]   # the opening task was cancelled, not left to run out a timeout


# ── 2. Replay ───────────────────────────────────────────────────────────────
def test_replay_miss_names_record_command(tmp_path):
    with pytest.raises(agent.ReplayMiss) as e:
        agent.run_scenario("capstone", guard=True, elicit="decline", labels_path=tmp_path / "l.jsonl",
                           model_call=agent.replay_model(agent.Options("capstone", guard=True,
                                                                       recordings=tmp_path / "rec")))
    msg = str(e.value)
    assert "capstone-guard.jsonl" in msg
    assert ("python mcp/python/agent.py --scenario capstone --guard --approve decline --elicit accept "
            f"--recordings {tmp_path / 'rec'} --mode record --max-cost 10") in msg
    assert "--mode record --dry-run" in msg


def test_record_command_echoes_non_default_options(tmp_path):
    assert agent.record_command(agent.Options("lesson5")) == \
        "python mcp/python/agent.py --scenario lesson5 --mode record --max-cost 10"
    assert agent.record_command(agent.Options("capstone")) == \
        "python mcp/python/agent.py --scenario capstone --elicit accept --mode record --max-cost 10"
    cmd = agent.record_command(agent.Options("capstone", guard=True, labels=tmp_path / "x.jsonl",
                                             recordings=tmp_path / "r", model="claude-sonnet-5-5"), dry_run=True)
    assert cmd == ("python mcp/python/agent.py --scenario capstone --guard --approve decline --elicit accept "
                   f"--model claude-sonnet-5-5 --recordings {tmp_path / 'r'} --labels {tmp_path / 'x.jsonl'} "
                   "--mode record --dry-run")


def test_replay_key_same_for_sdk_message_and_saved_copy():
    """R10: a real anthropic Message keyed live, and keyed after the save/load round trip, match."""
    from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
    live = Message(id="msg_1", type="message", role="assistant", model="claude-haiku-5-5",
                   content=[TextBlock(type="text", text="Looking."),
                            ToolUseBlock(type="tool_use", id="toolu_1", name="get_ticket", input={"id": "t061"})],
                   stop_reason="tool_use", stop_sequence=None, usage=Usage(input_tokens=10, output_tokens=5))
    saved = agent.as_response(json.loads(json.dumps(agent.response_data(live))))
    task = {"role": "user", "content": "Review ticket t061 and act on it"}
    keys = [agent.request_key("m", "s", [task, {"role": "assistant", "content": r.content}], [{"name": "get_ticket"}])
            for r in (live, saved)]
    assert keys[0] == keys[1]


def test_unknown_block_type_fails_loudly():
    with pytest.raises(ValueError, match="unsupported content block type 'server_tool_use'"):
        agent.block_dict(SimpleNamespace(type="server_tool_use", id="x", name="web_search", input={}))


def test_replay_key_ignores_nothing_that_shapes_the_answer():
    base = dict(model="m", system="s", messages=[{"role": "user", "content": "hi"}], tools=[{"name": "a"}])
    k = agent.request_key(**base)
    assert k == agent.request_key(**base)
    for field, value in [("model", "m2"), ("system", "s2"), ("messages", []), ("tools", [])]:
        assert agent.request_key(**{**base, field: value}) != k


# ── 3. The untrusted-read guard ─────────────────────────────────────────────
def test_guard_blocks_write_after_untrusted_read(tmp_path):
    labels = tmp_path / "labels.jsonl"
    asked: list = []
    out = agent.run_scenario("capstone", guard=True, elicit="accept", labels_path=labels,
                             model_call=scripted(SEARCH_THEN_LABEL),
                             human=lambda name, args: asked.append(name) or False)
    assert asked == ["label_ticket"]   # the person was asked, after the untrusted read
    assert rows(labels) == []          # and said no: nothing written
    assert out == "Done."


def test_no_guard_write_happens(tmp_path):
    labels = tmp_path / "labels.jsonl"
    asked: list = []
    agent.run_scenario("capstone", guard=False, elicit="accept", labels_path=labels,
                       model_call=scripted(SEARCH_THEN_LABEL),
                       human=lambda name, args: asked.append(name) or False)
    assert asked == []                 # no guard: the approver allowed it without asking
    [row] = rows(labels)               # the server's own confirmation was still asked (scripted accept)
    assert (row["ticket_id"], row["label"], row["source"], row["confirmed"]) == ("t061", "outage", "agent", True)


def test_guard_alone_prevents_the_write(tmp_path):
    """R35: the person (--approve decline) says no while the server's confirmation (--elicit accept)
    would say yes. Nothing is written, and the model is told the guard's denial."""
    labels = tmp_path / "labels.jsonl"
    seen: list = []
    agent.run_scenario("capstone", guard=True, approve="decline", elicit="accept", labels_path=labels,
                       model_call=scripted(SEARCH_THEN_LABEL, seen))
    assert rows(labels) == []
    [result] = seen[-1]["messages"][-1]["content"]
    assert (result["tool_use_id"], result["content"], result["is_error"]) == \
        ("tu_2", "The user denied this action.", True)


def test_guard_approve_accept_lets_the_write_through(tmp_path):
    labels = tmp_path / "labels.jsonl"
    agent.run_scenario("capstone", guard=True, approve="accept", elicit="accept", labels_path=labels,
                       model_call=scripted(SEARCH_THEN_LABEL))
    assert [r["label"] for r in rows(labels)] == ["outage"]


def test_same_response_write_is_not_guarded(tmp_path):
    """Pinned boundary: approvals are asked before a response's tools run, so a write in the same
    response as the first untrusted read is not guarded (the model had not seen the text yet)."""
    labels = tmp_path / "labels.jsonl"
    asked: list = []
    both = [reply("tool_use", use("search_tickets", {"query": "export notes PDF"}, "tu_1"),
                  use("label_ticket", {"id": "t061", "label": "how_to"}, "tu_2")),
            reply("end_turn", say("Done."))]
    agent.run_scenario("capstone", guard=True, approve="decline", elicit="accept", labels_path=labels,
                       model_call=scripted(both), human=lambda name, args: asked.append(name) or False)
    assert asked == []
    assert [r["label"] for r in rows(labels)] == ["how_to"]


def test_taint_fails_closed():
    ok = lambda structured: SimpleNamespace(structured_content=structured, is_error=False)  # noqa: E731
    assert agent.taints("search_tickets", ok({"results": []}))          # a ticket tool, even with no hits
    assert agent.taints("get_ticket", ok({"id": "t001"}))
    assert agent.taints("classify_complaint", ok(None))                # no structured content: unchecked text
    assert agent.taints("other", ok({"a": [{"b": {"untrusted_body": "x"}}]}))  # nested untrusted_body
    assert not agent.taints("classify_complaint", ok({"label": "x", "scores": {}}))
    assert not agent.taints("label_ticket", ok({"ticket_id": "t061", "label": "how_to", "written": True}))


def test_search_with_no_hits_still_taints(tmp_path):
    with agent.open_tools(("read",), labels_path=tmp_path / "l.jsonl", elicit="accept") as session:
        assert json.loads(harness.REGISTRY["search_tickets"].fn(query="zzqqxx"))["results"] == []
        assert session.read_untrusted.is_set()


def test_classify_does_not_taint(tmp_path):
    with agent.open_tools(("read",), labels_path=tmp_path / "l.jsonl", elicit="accept") as session:
        harness.REGISTRY["classify_complaint"].fn(text="I was charged twice")
        assert not session.read_untrusted.is_set()


def test_guard_does_not_ask_before_any_untrusted_read(tmp_path):
    labels = tmp_path / "labels.jsonl"
    asked: list = []
    direct = [reply("tool_use", use("label_ticket", {"id": "t061", "label": "how_to"}, "tu_1")),
              reply("end_turn", say("Done."))]
    agent.run_scenario("capstone", guard=True, elicit="accept", labels_path=labels,
                       model_call=scripted(direct), human=lambda name, args: asked.append(name) or False)
    assert asked == []
    assert [r["label"] for r in rows(labels)] == ["how_to"]


def test_server_confirmation_never_bypassed(tmp_path):
    """No guard and the approver allows it, but the scripted elicitation answer is decline: the
    server's own round still refuses, so nothing is written."""
    labels = tmp_path / "labels.jsonl"
    agent.run_scenario("capstone", guard=False, elicit="decline", labels_path=labels,
                       model_call=scripted(SEARCH_THEN_LABEL))
    assert rows(labels) == []


def test_needs_human_rule():
    write = SimpleNamespace(needs_approval=True)
    read = SimpleNamespace(needs_approval=False)
    assert agent.needs_human(write, read_untrusted=True, guard=True) is True
    assert agent.needs_human(write, read_untrusted=False, guard=True) is False
    assert agent.needs_human(write, read_untrusted=True, guard=False) is False
    assert agent.needs_human(read, read_untrusted=True, guard=True) is False


# ── 4. Record mode: estimate first, confirm, cap ────────────────────────────
class FakeClient:
    """Stands in for anthropic.Anthropic: messages.create returns the next scripted response."""
    def __init__(self, responses: list):
        self.responses, self.requests = list(responses), []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **params):
        self.requests.append(params)
        return self.responses.pop(0)


def test_estimate_printed_before_any_call(tmp_path, capsys):
    made: list = []
    rc = agent.main(["--scenario", "lesson5", "--mode", "record", "--recordings", str(tmp_path / "rec"),
                     "--labels", str(tmp_path / "l.jsonl")],
                    client_factory=lambda: made.append(1) or FakeClient([]), ask=lambda prompt: "N")
    out = capsys.readouterr().out
    assert rc == 1
    assert made == []                  # no client was created, so no call could be made
    assert "Estimated cost $" in out and "worst case $" in out and "Cap: $10.00" in out
    assert "Not confirmed. Nothing was called." in out
    assert not (tmp_path / "rec").exists()


def test_dry_run_creates_no_api_client(tmp_path):
    """Run in a fresh interpreter: the estimate path never imports the harness (whose import
    creates an anthropic client) and never constructs anthropic.Anthropic."""
    code = (
        "import sys, anthropic\n"
        "def boom(*a, **k): raise SystemExit('an API client was created')\n"
        "anthropic.Anthropic.__init__ = boom\n"
        f"sys.path.insert(0, {str(ROOT / 'mcp' / 'python')!r})\n"
        "import agent\n"
        "rc = agent.main(['--scenario', 'capstone', '--guard', '--mode', 'record', '--dry-run'])\n"
        "assert 'harness' not in sys.modules, 'harness imported'\n"
        "sys.exit(rc)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "capstone-guard" in r.stdout and "Estimated cost $" in r.stdout


def test_record_then_replay_roundtrip(tmp_path, capsys):
    rec, labels = tmp_path / "rec", tmp_path / "l.jsonl"
    fake = FakeClient([reply("tool_use", use("classify_complaint", {"text": "I was charged twice"}, "tu_1")),
                       reply("end_turn", say("It is a billing complaint."))])
    argv = ["--scenario", "lesson5", "--recordings", str(rec), "--labels", str(labels)]
    assert agent.main(argv + ["--mode", "record"], client_factory=lambda: fake, ask=lambda p: "y") == 0
    assert len(fake.requests) == 2
    assert [t["name"] for t in fake.requests[0]["tools"]] == ["classify_complaint", "get_ticket", "search_tickets"]
    assert fake.requests[0]["model"] == "claude-haiku-5-5"
    assert fake.requests[0]["max_tokens"] == agent.MAX_TOKENS
    lines = rows(rec / "lesson5.jsonl")
    assert len(lines) == 2 and all(len(l["key"]) == 64 for l in lines)
    capsys.readouterr()
    # Replay: same answer, no client at all.
    assert agent.main(argv, client_factory=lambda: pytest.fail("replay made a client")) == 0
    assert "It is a billing complaint." in capsys.readouterr().out


def test_record_stops_at_max_cost(tmp_path, capsys):
    expensive = SimpleNamespace(content=[use("get_ticket", {"id": "t001"}, "tu_1")], stop_reason="tool_use",
                                usage=SimpleNamespace(input_tokens=50_000_000, output_tokens=0))
    fake = FakeClient([expensive, reply("end_turn", say("never reached"))])
    rc = agent.main(["--scenario", "lesson5", "--mode", "record", "--recordings", str(tmp_path / "rec"),
                     "--labels", str(tmp_path / "l.jsonl"), "--max-cost", "1", "--yes"],
                    client_factory=lambda: fake)
    assert rc == 2
    assert len(fake.requests) == 1
    assert "over --max-cost" in capsys.readouterr().out


def test_record_stops_before_a_call_whose_worst_case_passes_the_cap(tmp_path, capsys):
    """R5: spent + the next call's worst case (all of MAX_TOKENS out) over the cap: it does not start."""
    fake = FakeClient([reply("end_turn", say("never reached"))])
    rc = agent.main(["--scenario", "lesson5", "--mode", "record", "--recordings", str(tmp_path / "rec"),
                     "--max-cost", "0.001", "--yes"], client_factory=lambda: fake)
    assert rc == 2
    assert fake.requests == []
    assert "the next call could cost up to $" in capsys.readouterr().out


def test_replay_labels_default_to_a_temp_file(tmp_path, capsys, monkeypatch):
    rec = tmp_path / "rec"
    fake = FakeClient([reply("tool_use", use("label_ticket", {"id": "t061", "label": "how_to"}, "tu_1")),
                       reply("end_turn", say("Labelled."))])
    real = tmp_path / "real-labels.jsonl"
    monkeypatch.setattr(agent, "LABELS_PATH", real)       # record's default: the real store
    argv = ["--scenario", "capstone", "--recordings", str(rec)]
    assert agent.main(argv + ["--mode", "record", "--yes"], client_factory=lambda: fake) == 0
    out = capsys.readouterr().out
    assert f"labels: {real}\n" in out and len(rows(real)) == 1
    assert agent.main(argv, client_factory=lambda: pytest.fail("replay made a client")) == 0
    out = capsys.readouterr().out
    assert "(temporary: replay mode)" in out and len(rows(real)) == 1   # replay did not touch the real store
    assert (rec / "trace.jsonl").exists()                              # trace next to the recordings
