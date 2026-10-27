"""Tests for the Python client, and for Go/Python client parity.

The stdio tests spawn read_server.py. The elicitation tests use a tiny in-test MCPServer whose
tool asks for a confirmation (the shape lumen-ops will use in Task 7), connected in-process.
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import pytest
from mcp.server import MCPServer
from mcp.server.elicitation import ElicitationResult
from mcp.server.mcpserver.resolve import Elicit, Resolve
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import client as lumen_client  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CLIENT_PY = ROOT / "mcp" / "python" / "client.py"
CONTRACT = ROOT / "mcp" / "fixtures" / "contract.jsonl"
DECLINED = "label not written: the user declined"


def run(coro):
    return asyncio.run(coro)


def contract_case(name: str) -> dict:
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        if case["name"] == name and case["server"] == "read":
            return case
    raise KeyError(name)


def same(a, b) -> bool:
    """Deep equality on parsed JSON, floats within 1e-9 (mcp/fixtures/README.md)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, abs_tol=1e-9, rel_tol=0)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


# ── 1. Stdio client against lumen-read ──────────────────────────────────────
def test_client_stdio_tools_lists_three():
    async def go():
        async with lumen_client.connect(lumen_client.stdio_params()) as c:
            return await lumen_client.list_tools(c), await lumen_client.discover(c)

    tools, found = run(go())
    assert [t["name"] for t in tools] == ["classify_complaint", "get_ticket", "search_tickets"]
    assert all(t["annotations"] == {"readOnlyHint": True} for t in tools)
    assert found["tools"] == ["classify_complaint", "get_ticket", "search_tickets"]
    assert found["server"] == "lumen-read" and found["protocol_version"] == "2026-07-28"
    search = next(t for t in tools if t["name"] == "search_tickets")
    assert search["inputSchema"] == {"type": "object", "required": ["query"],
                                     "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}
    assert "title" not in json.dumps(search["outputSchema"])  # SDK decoration is gone


@pytest.mark.parametrize("name", ["search_happy", "get_unknown", "classify_happy"])
def test_client_call_matches_contract(name):
    case = contract_case(name)

    async def go():
        async with lumen_client.connect(lumen_client.stdio_params()) as c:
            return await lumen_client.call(c, case["params"]["name"], case["params"]["arguments"])

    assert same(run(go()), case["expect"])


def test_client_read_and_prompt_errors_use_the_fixed_text():
    async def go():
        async with lumen_client.connect(lumen_client.stdio_params()) as c:
            return (await lumen_client.read(c, "lumen://tickets/zz"),
                    await lumen_client.prompt(c, "review_ticket", {"id": "zz"}),
                    await lumen_client.read(c, "lumen://tickets/t001"))

    bad_read, bad_prompt, good = run(go())
    assert bad_read == {"error": "unknown ticket: zz"} and bad_prompt == {"error": "unknown ticket: zz"}
    assert good["contents"][0]["json"]["id"] == "t001"


def test_normalise_schema_drops_decoration():
    schema = {"$defs": {"Hit": {"title": "Hit", "type": "object", "required": ["b", "a"],
                                "properties": {"a": {"title": "A", "type": "string"},
                                               "b": {"type": ["integer", "null"], "default": 1}}}},
              "title": "Out", "type": "object", "additionalProperties": False,
              "properties": {"hits": {"type": "array", "items": {"$ref": "#/$defs/Hit"}},
                             "m": {"type": "object", "additionalProperties": {"type": "number"}}}}
    assert lumen_client.normalise_schema(schema) == {
        "type": "object",
        "properties": {
            "hits": {"type": "array", "items": {"type": "object", "required": ["a", "b"],
                                                "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}},
            "m": {"type": "object", "additionalProperties": {"type": "number"}}}}


# ── 2. Go client prints the same JSON ───────────────────────────────────────
@pytest.fixture(scope="module")
def go_client(tmp_path_factory):
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    binary = tmp_path_factory.mktemp("goclient") / "client"
    subprocess.run(["go", "build", "-o", str(binary), "./client"], cwd=ROOT / "mcp" / "go", check=True)
    return binary


def run_clients(go_client, *command: str) -> tuple[str, str]:
    env = {**os.environ, "LUMEN_PYTHON": sys.executable}
    py = subprocess.run([sys.executable, str(CLIENT_PY), "--stdio", *command],
                        capture_output=True, text=True, check=True, env=env, cwd=ROOT)
    go = subprocess.run([str(go_client), "-stdio", *command],
                        capture_output=True, text=True, check=True, env=env, cwd=ROOT)
    return go.stdout, py.stdout


@pytest.mark.parametrize("command", [
    ("tools",),
    ("discover",),
    ("call", "search_tickets", '{"query": "refund duplicate", "limit": 3}'),
    ("call", "classify_complaint", '{"text": "I was charged twice"}'),
    ("call", "get_ticket", '{"id": "nope"}'),
    ("read", "lumen://tickets/t061"),
    ("read", "lumen://reports/eval"),
    ("prompt", "review_ticket", '{"id": "t001"}'),
    ("prompt", "review_ticket", '{"id": "zz"}'),
])
def test_go_client_same_output(go_client, command):
    go_out, py_out = run_clients(go_client, *command)
    assert go_out == py_out
    json.loads(go_out)  # and it is JSON
    assert go_out.isascii()


# ── 3. Input-required rounds (a test server that asks, Python MCPServer) ────
class Confirm(BaseModel):
    confirm: bool


def ask_confirm(id: str) -> Elicit[Confirm]:
    return Elicit(f"Write a label on ticket {id}?", Confirm)


def build_asking_server(asked: list[str]) -> MCPServer:
    server = MCPServer("asker")

    @server.tool()
    def label_ticket(id: str, answer: Annotated[ElicitationResult[Confirm], Resolve(ask_confirm)]) -> CallToolResult:
        asked.append(id)
        if answer.action != "accept" or not answer.data.confirm:
            return CallToolResult(content=[TextContent(type="text", text=DECLINED)], is_error=True)
        return CallToolResult(content=[TextContent(type="text", text=f"labelled {id}")])

    return server


def call_label(answerer):
    asked: list[str] = []

    async def go():
        async with lumen_client.connect(build_asking_server(asked), answerer) as c:
            result = await c.call_tool("label_ticket", {"id": "t001"})
            return result.is_error, result.content[0].text

    return run(go()), asked


def test_elicitation_yes_accepts():
    (is_error, text), asked = call_label(lumen_client.always(True))
    assert (is_error, text) == (False, "labelled t001") and asked == ["t001"]


def test_elicitation_no_declines():
    (is_error, text), _ = call_label(lumen_client.always(False))
    assert (is_error, text) == (True, DECLINED)


@pytest.mark.parametrize("typed,accepted", [("y\n", True), ("YES\n", True), ("n\n", False), ("\n", False), ("", False)])
def test_elicitation_terminal_prompt_goes_to_stderr(typed, accepted):
    err = io.StringIO()
    (is_error, _), _ = call_label(lumen_client.ask_terminal(io.StringIO(typed), err))
    assert is_error is (not accepted)
    assert err.getvalue() == "Write a label on ticket t001? [y/N] "


def test_elicitation_without_a_callback_writes_nothing():
    """The default answerer declines, so a server that asks never gets a yes."""
    (is_error, text), _ = call_label(lumen_client.deny)
    assert (is_error, text) == (True, DECLINED)


def test_unanswerable_form_is_declined():
    """A y/N prompt can only fill boolean fields; anything else is declined, not guessed."""
    class Params:
        message = "name?"
        requested_schema = {"type": "object", "properties": {"name": {"type": "string"}}}

    result = run(lumen_client.elicitation_callback(lumen_client.always(True))(None, Params()))
    assert result.action == "decline"


# ── 4. HTTP (--url) ─────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def http_url():
    import socket
    import time
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, str(ROOT / "mcp" / "python" / "read_server.py"), "--http",
                             "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("read_server --http did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


HTTP_COMMANDS = [("tools",), ("call", "search_tickets", '{"query": "refund duplicate", "limit": 3}')]


@pytest.mark.parametrize("command", HTTP_COMMANDS)
def test_python_client_url_matches_stdio(http_url, command):
    over_http = subprocess.run([sys.executable, str(CLIENT_PY), "--url", http_url, *command],
                               capture_output=True, text=True, check=True, cwd=ROOT).stdout
    over_stdio = subprocess.run([sys.executable, str(CLIENT_PY), "--stdio", *command],
                                capture_output=True, text=True, check=True, cwd=ROOT).stdout
    assert over_http == over_stdio
    if command[0] == "tools":
        assert [t["name"] for t in json.loads(over_http)] == ["classify_complaint", "get_ticket", "search_tickets"]


@pytest.mark.parametrize("command", HTTP_COMMANDS)
def test_go_client_url_same_output(go_client, http_url, command):
    py = subprocess.run([sys.executable, str(CLIENT_PY), "--url", http_url, *command],
                        capture_output=True, text=True, check=True, cwd=ROOT).stdout
    go = subprocess.run([str(go_client), "-url", http_url, *command],
                        capture_output=True, text=True, check=True, cwd=ROOT).stdout
    assert go == py


# ── 5. Edge cases (identical in both clients) ───────────────────────────────
def test_closed_stdin_declines(monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    err = io.StringIO()
    assert run(lumen_client.ask_terminal(stderr=err)("ok?")) is False


def test_error_result_without_text_prints_no_text():
    from types import SimpleNamespace

    class Fake:
        def __init__(self, content):
            self.content = content

        async def call_tool(self, name, arguments):
            return SimpleNamespace(is_error=True, content=self.content, structured_content=None)

    image = SimpleNamespace(type="image")
    assert run(lumen_client.call(Fake([]), "t", {})) == {"error": "<no text>"}
    assert run(lumen_client.call(Fake([image]), "t", {})) == {"error": "<no text>"}


def test_non_json_resource_prints_text():
    from types import SimpleNamespace
    c = SimpleNamespace(uri="x://y", mime_type="text/plain", text="not json")
    assert lumen_client.resource_entry(c) == {"uri": "x://y", "mimeType": "text/plain", "text": "not json"}


USAGE_CASES = [
    (("call",), "client: call needs 1 argument(s)"),
    (("read",), "client: read needs 1 argument(s)"),
    (("prompt",), "client: prompt needs 1 argument(s)"),
    (("call", "get_ticket", "[1]"), "client: arguments are not a JSON object"),
    (("call", "get_ticket", "nope"), "client: arguments are not a JSON object"),
    (("prompt", "review_ticket", '{"id": 1}'), "client: prompt arguments must be strings: id"),
]


@pytest.mark.parametrize("command,message", USAGE_CASES)
def test_usage_errors_match_in_both_clients(go_client, command, message):
    env = {**os.environ, "LUMEN_PYTHON": sys.executable}
    py = subprocess.run([sys.executable, str(CLIENT_PY), "--stdio", *command],
                        capture_output=True, text=True, env=env, cwd=ROOT)
    go = subprocess.run([str(go_client), "-stdio", *command], capture_output=True, text=True, env=env, cwd=ROOT)
    for r in (py, go):
        assert r.returncode == 1 and r.stdout == "" and r.stderr.strip() == message
