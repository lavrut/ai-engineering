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
from auth_server import mint_test_token  # noqa: E402
from resource_auth import OPS_RESOURCE, READ_RESOURCE, SCOPE_READ, SCOPE_WRITE  # noqa: E402

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
def http_url(tmp_path_factory):
    """lumen-read over HTTP, with its own random key file. Tests mint tokens with `token_for`."""
    import socket
    import time
    from conftest import write_key_file
    key = write_key_file(tmp_path_factory.mktemp("rs-key") / "signing.key")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, str(ROOT / "mcp" / "python" / "read_server.py"), "--http",
                             "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
                            env={**os.environ, "LUMEN_SIGNING_KEY_FILE": str(key)})
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("read_server --http did not start")
        http_url.key = key
        http_url.audience = f"http://127.0.0.1:{port}/mcp"
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def token_for(monkeypatch, scope: str = SCOPE_READ, audience: str | None = None) -> str:
    """A token signed with the running server's key (lumen-read's own scope and, by default, its audience
    on the random port it runs on: R46)."""
    monkeypatch.setenv("LUMEN_SIGNING_KEY_FILE", str(http_url.key))
    return mint_test_token(scope, audience or http_url.audience)


HTTP_COMMANDS = [("tools",), ("call", "search_tickets", '{"query": "refund duplicate", "limit": 3}')]
# The one-line warning both clients print when a token is passed on the command line (R26).
WARNING = ("client: warning: a token on the command line is visible in the process list and shell history; "
           "use a token file or LUMEN_TOKEN\n")


def clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "LUMEN_TOKEN"}
    return {**env, **extra}


def run_py(*args: str, env: dict | None = None):
    return subprocess.run([sys.executable, str(CLIENT_PY), *args], capture_output=True, text=True, cwd=ROOT,
                          env=env or clean_env())


def run_go(binary, *args: str, env: dict | None = None):
    return subprocess.run([str(binary), *args], capture_output=True, text=True, cwd=ROOT, env=env or clean_env())


@pytest.mark.parametrize("command", HTTP_COMMANDS)
def test_python_client_url_matches_stdio(http_url, command, monkeypatch):
    over_http = run_py("--url", http_url, *command, env=clean_env(LUMEN_TOKEN=token_for(monkeypatch)))
    over_stdio = run_py("--stdio", *command)
    assert over_http.returncode == 0, over_http.stderr
    assert over_http.stdout == over_stdio.stdout
    if command[0] == "tools":
        assert [t["name"] for t in json.loads(over_http.stdout)] == ["classify_complaint", "get_ticket",
                                                                      "search_tickets"]


@pytest.mark.parametrize("command", HTTP_COMMANDS)
def test_go_client_url_same_output(go_client, http_url, command, monkeypatch):
    env = clean_env(LUMEN_TOKEN=token_for(monkeypatch))
    py, go = run_py("--url", http_url, *command, env=env), run_go(go_client, "-url", http_url, *command, env=env)
    assert py.returncode == 0 and go.returncode == 0, (py.stderr, go.stderr)
    assert go.stdout == py.stdout and go.stderr == py.stderr == ""


def test_token_sources_and_warning_match_in_both_clients(go_client, http_url, monkeypatch, tmp_path):
    """R26: --token-file, then --token, then LUMEN_TOKEN. --token works but warns on stderr; the
    warning is the same line in both clients. A token file or the env var prints no warning."""
    good, bad = token_for(monkeypatch), "not-a-token"
    good_file, bad_file = tmp_path / "good.tok", tmp_path / "bad.tok"
    good_file.write_text(good + "\n")
    bad_file.write_text(bad + "\n")
    cases = [  # (python args, go args, env token, expect success, expect warning)
        (["--token-file", str(good_file)], ["-token-file", str(good_file)], None, True, False),
        (["--token", good], ["-token", good], None, True, True),
        ([], [], good, True, False),
        (["--token-file", str(good_file), "--token", bad], ["-token-file", str(good_file), "-token", bad], bad,
         True, True),  # the file wins over both
        (["--token-file", str(bad_file), "--token", good], ["-token-file", str(bad_file), "-token", good], good,
         False, True),
        (["--token", good], ["-token", good], bad, True, True),  # --token wins over the env
        (["--token", bad], ["-token", bad], good, False, True),
        ([], [], None, False, False),  # no token at all: 401
    ]
    for py_args, go_args, env_token, ok, warns in cases:
        env = clean_env(**({"LUMEN_TOKEN": env_token} if env_token else {}))
        py = run_py("--url", http_url, *py_args, "tools", env=env)
        go = run_go(go_client, "-url", http_url, *go_args, "tools", env=env)
        assert (py.returncode == 0) is ok and (go.returncode == 0) is ok, (py_args, py.stderr, go.stderr)
        assert py.stdout == go.stdout
        want = (WARNING if warns else "") + ("" if ok else REFUSED_401)
        assert py.stderr == go.stderr == want, (py_args, py.stderr, go.stderr)


PLAIN_HTTP_WARNING = ("client: warning: the token goes over plain http to a host that is not this machine, "
                      "so anyone on the network path can read it; use https\n")


@pytest.mark.parametrize("url", ["http://lumen.invalid:8765/mcp", "http://192.0.2.1.invalid/mcp"])
def test_both_clients_warn_before_sending_a_token_over_plain_http_off_this_machine(go_client, url, tmp_path):
    """M7: a token to a non-loopback http URL gets one warning line, the same in both clients, before any
    request (the .invalid hosts never resolve, so nothing is sent). Loopback and https get none."""
    env = clean_env(LUMEN_TOKEN="t0ken")
    py, go = run_py("--url", url, "tools", env=env), run_go(go_client, "-url", url, "tools", env=env)
    assert py.returncode != 0 and go.returncode != 0
    assert py.stderr.startswith(PLAIN_HTTP_WARNING) and go.stderr.startswith(PLAIN_HTTP_WARNING), (py.stderr, go.stderr)
    for quiet in ("http://127.0.0.1:1/mcp", "http://localhost:1/mcp", "https://lumen.invalid/mcp"):
        py, go = run_py("--url", quiet, "tools", env=env), run_go(go_client, "-url", quiet, "tools", env=env)
        assert "plain http" not in py.stderr and "plain http" not in go.stderr, quiet
    no_token = run_py("--url", url, "tools")
    assert "plain http" not in no_token.stderr  # no token, nothing to warn about


REFUSED_401 = "client: the server refused the request: HTTP 401 Unauthorized\n"
REFUSED_403 = "client: the server refused the request: HTTP 403 Forbidden\n"


def test_refusal_is_one_line_in_both_clients(go_client, http_url, monkeypatch):
    """A 401 or 403 is one stderr line naming the HTTP status, identical in both clients (no traceback)."""
    for env, want in [(clean_env(), REFUSED_401),  # no token
                      (clean_env(LUMEN_TOKEN=token_for(monkeypatch, scope=SCOPE_WRITE)), REFUSED_403),  # wrong scope
                      (clean_env(LUMEN_TOKEN=token_for(monkeypatch, audience=OPS_RESOURCE)), REFUSED_401)]:  # audience
        py = run_py("--url", http_url, "tools", env=env)
        go = run_go(go_client, "-url", http_url, "tools", env=env)
        assert py.returncode == go.returncode == 1
        assert py.stdout == go.stdout == ""
        assert py.stderr == go.stderr == want, (py.stderr[-300:], go.stderr)


def test_token_file_errors_match_in_both_clients(go_client, http_url, tmp_path):
    empty = tmp_path / "empty.tok"
    empty.write_text("\n")
    missing = tmp_path / "missing.tok"
    for name, want in [(empty, f"client: token file is empty: {empty}\n"),
                       (missing, f"client: token file not readable: {missing}\n")]:
        py = run_py("--url", http_url, "--token-file", str(name), "tools")
        go = run_go(go_client, "-url", http_url, "-token-file", str(name), "tools")
        assert py.returncode == go.returncode == 1
        assert py.stderr == go.stderr == want


def test_token_needs_url_in_both_clients(go_client, tmp_path):
    """A token is an HTTP credential: with --stdio, --token or --token-file is a usage error.
    (LUMEN_TOKEN in the environment is ambient and is simply not used for stdio.)"""
    f = tmp_path / "t.tok"
    f.write_text("x\n")
    for py_args, go_args in [(["--token", "x"], ["-token", "x"]), (["--token-file", str(f)], ["-token-file", str(f)])]:
        py = run_py("--stdio", *py_args, "tools")
        go = run_go(go_client, "-stdio", *go_args, "tools")
        assert py.returncode == go.returncode == 1
        assert py.stderr.endswith("client: a token needs --url / -url\n") and py.stderr == go.stderr
    ok = run_py("--stdio", "tools", env=clean_env(LUMEN_TOKEN="x"))
    assert ok.returncode == 0 and ok.stderr == ""


def test_resolve_token_precedence(tmp_path):
    f = tmp_path / "t.tok"
    f.write_text("  from-file \n")
    rt = lumen_client.resolve_token
    assert rt(str(f), "from-flag", "from-env") == ("from-file", True)
    assert rt(None, "from-flag", "from-env") == ("from-flag", True)
    assert rt(None, None, "from-env") == ("from-env", False)
    assert rt(None, None, None) == (None, False)
    assert rt(None, None, "") == (None, False)


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


def test_patching_the_facade_reaches_the_parts(monkeypatch):
    """M3: client.py re-exports client_connect's and client_calls' names. Patching one on `client` sets it in
    every part that holds it (connect looks deny up in client_connect); undoing the patch restores them."""
    import client_calls
    import client_connect

    async def fake(message: str) -> bool:
        return True

    real = lumen_client.deny
    monkeypatch.setattr(lumen_client, "deny", fake)
    assert lumen_client.deny is client_connect.deny is client_calls.deny is fake
    monkeypatch.undo()
    assert lumen_client.deny is client_connect.deny is client_calls.deny is real
