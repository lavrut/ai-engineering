"""The Agent SDK desk with the REAL Claude Code CLI bundled in the pinned claude-agent-sdk wheel (R45).

Offline and free: the CLI talks to a stub of the Messages API on 127.0.0.1 (scripted SSE replies, no upstream),
with the dummy key; nothing leaves the machine. Each test starts one CLI run (a second or two). The offline fake
(fake_cli.py) cannot catch what only the CLI does: it runs a response's Agent calls concurrently (R42), and it
runs the tool when a hook raises (R43). These tests run the CLI itself on those cases, plus the single desk's
happy path and the parent environment that must not reach the CLI (R44).

If the bundled CLI cannot start, every test here is skipped with the reason (never a silent pass). After each
test, no CLI process started by this one may still be running.
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "agents" / "frameworks"))

import sdk_desk as sd  # noqa: E402  (first: it puts agents/desk/python on sys.path)
import desk_core as core  # noqa: E402
import claude_agent_sdk  # noqa: E402

CLI = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
T = {t["id"]: t for t in core.TICKETS}
LABEL = "mcp__lumen-ops__label_ticket"
GET = "mcp__lumen-read__get_ticket"
PASS = json.dumps({r: {"pass": True, "reason": "ok"} for r in ("addresses_issue", "tone", "no_false_promises")})
FAIL_TONE = json.dumps({r: {"pass": r != "tone", "reason": "x"} for r in ("addresses_issue", "tone", "no_false_promises")})


def _cli_problem() -> str | None:
    if not CLI.exists():
        return f"the bundled Claude Code CLI is missing from the wheel ({CLI.name} not found)"
    try:
        r = subprocess.run([str(CLI), "--version"], capture_output=True, text=True, timeout=60,
                           env={"PATH": os.environ.get("PATH", ""), "HOME": str(ROOT / "agents")})
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"the bundled Claude Code CLI cannot start: {type(e).__name__}: {e}"
    return None if r.returncode == 0 else f"the bundled Claude Code CLI exited {r.returncode} on --version"


PROBLEM = _cli_problem()
pytestmark = pytest.mark.skipif(PROBLEM is not None, reason=f"REAL-CLI TESTS SKIPPED: {PROBLEM}")


def _cli_children() -> list[str]:
    out = subprocess.run(["ps", "-A", "-o", "ppid=,pid=,command="], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if str(CLI) in l and l.split()[0] == str(os.getpid())]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield
    deadline = time.time() + 10
    while _cli_children() and time.time() < deadline:
        time.sleep(0.1)
    assert _cli_children() == [], "a CLI process outlived its test"


# ── a stub of the Messages API on 127.0.0.1 ─────────────────────────────────
def _sse(blocks: list[dict], mid: str) -> bytes:
    events = []

    def add(kind: str, data: dict) -> None:
        events.append(f"event: {kind}\ndata: {json.dumps(data)}\n\n")
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    add("message_start", {"type": "message_start", "message": {
        "id": mid, "type": "message", "role": "assistant", "model": core.MODEL, "content": [], "stop_reason": None,
        "stop_sequence": None, "usage": {"input_tokens": 11, "output_tokens": 1}}})
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            add("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            add("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}})
        else:
            add("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
            add("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(b["input"])}})
        add("content_block_stop", {"type": "content_block_stop", "index": i})
    add("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 5}})
    add("message_stop", {"type": "message_stop"})
    return "".join(events).encode()


class Stub:
    """Answers each POST with route(system text, messages); logs the request's header names and body."""

    def __init__(self, route) -> None:
        self.log: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a) -> None:
                pass

            def do_HEAD(self) -> None:
                self.send_response(200)
                self.end_headers()

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                system = body.get("system")
                text = " ".join(s.get("text", "") for s in system) if isinstance(system, list) else str(system)
                stub.log.append({"headers": [h.lower() for h in self.headers.keys()], "system": text, "body": body})
                data = _sse(route(text, body.get("messages", [])), f"msg_{len(stub.log)}")
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def stub(monkeypatch):
    started = []

    def start(route) -> Stub:
        s = Stub(route)
        started.append(s)
        monkeypatch.setitem(sd.SDK_ENV, "ANTHROPIC_BASE_URL", s.url)
        return s
    yield start
    for s in started:
        s.server.shutdown()
        s.server.server_close()


def tool_use(i: str, name: str, **inp) -> dict:
    return {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": inp}


def text(t: str) -> list[dict]:
    return [{"type": "text", "text": t}]


def rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


# ── the tests ───────────────────────────────────────────────────────────────
def test_single_happy_path_and_a_scrubbed_environment(stub, tmp_path, monkeypatch):
    """The single desk end to end through the real CLI, with a parent environment that would send the traffic
    elsewhere or add a credential or a header if it reached the CLI (R44)."""
    for k, v in {"HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9",
                 "ANTHROPIC_AUTH_TOKEN": "leak", "ANTHROPIC_CUSTOM_HEADERS": "X-Leak: yes",
                 "CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_API_URL": "https://elsewhere.invalid"}.items():
        monkeypatch.setenv(k, v)
    turns = iter([[tool_use("a", GET, id="t001"), tool_use("b", LABEL, id="t001", label="billing")],
                  text("Reply for t001.")])
    s = stub(lambda system, msgs: next(turns) if "You are the Lumen support desk" in system else text("?"))
    labels = tmp_path / "labels.jsonl"
    run = sd.sdk_single(T["t001"], approve="accept", elicit="accept", labels_path=labels)
    assert run["reply"] == "Reply for t001." and run["stop"] == "end_turn"
    assert [r["label"] for r in rows(labels)] == ["billing"]
    assert [(t["tool"], t["is_error"]) for t in run["tool_calls"]] == [("get_ticket", False), ("label_ticket", False)]
    assert len(s.log) == 2 and [r["kind"] for r in run["trace"].to_list()].count("model") == 2
    assert all(r["body"]["max_tokens"] == 4000 for r in s.log)
    assert all("x-leak" not in r["headers"] and "authorization" not in r["headers"] for r in s.log)


def test_parallel_handoffs_are_one_at_a_time(stub, tmp_path):
    """R42: the CLI runs [drafter, reviewer] at once. Whichever starts second is refused, so a label never goes
    out on a draft that the passing review did not read."""
    sup = iter([[tool_use("s1", "Agent", subagent_type="triage", description="t", prompt="Triage it.")],
                [tool_use("s2", "Agent", subagent_type="drafter", description="d", prompt="Draft it.")],
                [tool_use("s3", "Agent", subagent_type="reviewer", description="r", prompt="Review it.")],
                [tool_use("s4", "Agent", subagent_type="drafter", description="d", prompt="Fix the tone."),
                 tool_use("s5", "Agent", subagent_type="reviewer", description="r", prompt="Review it.")],
                [tool_use("s6", LABEL, id="t001", label="billing")]])
    count = {"drafter": 0, "reviewer": 0}
    judged: list[str] = []

    def route(system, msgs):
        if "You supervise the Lumen" in system:
            return next(sup, text("Done."))
        if "strict reviewer" in system:
            count["reviewer"] += 1
            judged.append(json.dumps(msgs[0]["content"]))
            if count["reviewer"] == 2:
                time.sleep(1.5)       # slow: the parallel drafter finishes first
            return text(FAIL_TONE if count["reviewer"] == 1 else PASS)
        if "You write the reply" in system:
            count["drafter"] += 1
            return text(f"DRAFT{count['drafter']} for t001.")
        if '"category": "<category>"' in system:
            return text('{"category": "billing", "priority": "high"}')
        return text("?")
    stub(route)
    labels = tmp_path / "labels.jsonl"
    run = sd.sdk_supervisor(T["t001"], approve="accept", elicit="accept", labels_path=labels)
    pair = run["handoffs"][3:]
    assert sorted(h["worker"] for h in pair) == ["drafter", "reviewer"]
    assert sorted(h["is_error"] for h in pair) == [False, True]          # exactly one of the two ran
    if rows(labels):   # a label went out only on the draft the passing review read
        assert run["state"]["review"]["passed"] and run["state"]["draft"] in judged[-1]
    assert run["errors"] == []


def test_a_failing_hook_refuses_the_call(stub, tmp_path, monkeypatch):
    """R43: the CLI runs the tool when a hook raises (the SDK reports a control error). The desk's hook turns
    any exception into a deny, so the label is refused."""
    async def boom(self, *a):
        raise RuntimeError("gate broke")
    monkeypatch.setattr(sd.DeskHooks, "_ops_gate", boom)
    sup = iter([[tool_use("s1", "Agent", subagent_type="triage", description="t", prompt="Triage it.")],
                [tool_use("s2", "Agent", subagent_type="drafter", description="d", prompt="Draft it.")],
                [tool_use("s3", "Agent", subagent_type="reviewer", description="r", prompt="Review it.")],
                [tool_use("s4", LABEL, id="t001", label="billing")]])

    def route(system, msgs):
        if "You supervise the Lumen" in system:
            return next(sup, text("Done."))
        if "strict reviewer" in system:
            return text(PASS)
        if "You write the reply" in system:
            return text("Draft for t001.")
        if '"category": "<category>"' in system:
            return text('{"category": "billing", "priority": "high"}')
        return text("?")
    stub(route)
    labels = tmp_path / "labels.jsonl"
    run = sd.sdk_supervisor(T["t001"], approve="accept", elicit="accept", labels_path=labels)
    assert run["state"]["review"]["passed"] is True
    assert rows(labels) == [] and run["tool_calls"] == [
        {"agent": "supervisor", "tool": "label_ticket", "args": {"id": "t001", "label": "billing"}, "is_error": True}]
