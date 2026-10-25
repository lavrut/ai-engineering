import json

import pytest

import harness
from conftest import block, reply, text

EVENTS = {"session_start", "model_request", "model_response", "tool_result",
          "permission", "compact", "session_end"}


def test_safe_path_rejects_escapes(ws):
    for bad in ["../x", "/etc/passwd", "a/../../x"]:
        with pytest.raises(PermissionError):
            harness.safe_path(bad)


def test_executor_keeps_order_and_matches_ids(ws):
    (harness.WORKSPACE / "a.txt").write_text("A")
    out = harness.execute_tools([block("read_file", {"path": "a.txt"}, "t1"),
                                 block("list_files", {"path": "."}, "t2")],
                                approver=lambda *_: True)
    assert [r["tool_use_id"] for r in out] == ["t1", "t2"]
    assert out[0]["content"] == "A" and out[0]["is_error"] is False


def test_denied_unknown_and_bad_args_are_errors(ws):
    out = harness.execute_tools([block("write_file", {"path": "x", "content": "y"}, "t1"),
                                 block("nope", {}, "t2"),
                                 block("read_file", {"file": "a"}, "t3"),
                                 block("read_file", {"path": "../../etc/passwd"}, "t4")],
                                approver=lambda *_: False)
    assert all(r["is_error"] for r in out)
    assert out[0]["content"] == "The user denied this action."
    assert out[1]["content"] == "Unknown tool: nope"
    assert not (harness.WORKSPACE / "x").exists()


def test_read_file_caps_output(ws):
    (harness.WORKSPACE / "big.txt").write_text("a" * 25_000)
    out = harness.execute_tools([block("read_file", {"path": "big.txt"})],
                                approver=lambda *_: True)
    assert len(out[0]["content"]) == 20_000


def test_loop_runs_tools_then_returns_text(ws):
    replies = iter([reply("tool_use", block("list_files", {"path": "."})),
                    reply("end_turn", text("done"))])
    seen = []

    def fake(messages, **kw):
        seen.append([m["role"] for m in messages])
        return next(replies)

    assert harness.run_agent("hi", call=fake, approver=lambda *_: True) == "done"
    assert seen[1] == ["user", "assistant", "user"]


def test_stop_reasons(ws):
    out = harness.run_agent("hi", call=lambda m, **kw: reply("max_tokens", text("part")))
    assert out == "part\n[stopped: the response hit max_tokens]"
    out = harness.run_agent("hi", call=lambda m, **kw: reply("refusal"))
    assert out == "[the model declined this request]"


def test_max_turns(ws, monkeypatch):
    monkeypatch.setattr(harness, "MAX_TURNS", 2)
    calls = []

    def fake(messages, **kw):
        calls.append(1)
        return reply("tool_use", block("list_files", {"path": "."}))

    out = harness.run_agent("hi", call=fake, approver=lambda *_: True)
    assert out == "[stopped: reached MAX_TURNS without finishing]"
    assert len(calls) == 2


def test_compaction_starts_fresh(ws):
    replies = iter([
        reply("tool_use", block("list_files", {"path": "."}),
              input_tokens=harness.CONTEXT_BUDGET + 1),
        reply("end_turn", text("the summary")),
        reply("end_turn", text("done")),
    ])
    calls = []

    def fake(messages, **kw):
        calls.append((list(messages), kw))
        return next(replies)

    assert harness.run_agent("hi", call=fake, approver=lambda *_: True) == "done"
    summary_kw = calls[1][1]
    assert summary_kw["tool_choice"] == {"type": "none"}
    assert summary_kw["max_tokens"] == 4000
    third = calls[2][0]
    assert len(third) == 1 and third[0]["role"] == "user"
    assert third[0]["content"].startswith("Summary of earlier work:")


def test_trace_written(ws):
    replies = iter([reply("tool_use", block("list_files", {"path": "."})),
                    reply("end_turn", text("done"))])
    harness.run_agent("hi", call=lambda m, **kw: next(replies), approver=lambda *_: True)
    lines = harness.TRACE_FILE.read_text().splitlines()
    events = {json.loads(line)["event"] for line in lines}
    assert events <= EVENTS
    assert {"session_start", "tool_result", "session_end"} <= events
