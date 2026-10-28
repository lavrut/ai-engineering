"""Tests for the record/replay proxy. No test calls the real API: record mode always talks to an
httpx.MockTransport fake upstream, and every server binds 127.0.0.1 on a free port."""
from __future__ import annotations

import io
import json
import sys
import threading
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proxy  # noqa: E402

SECRET = "sk-ant-test-SECRET-do-not-store"
BODY = {"model": "claude-haiku-5-5", "max_tokens": 50, "messages": [{"role": "user", "content": "ping"}]}
MESSAGE = {"id": "msg_01", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
           "content": [{"type": "text", "text": "pong"}], "stop_reason": "end_turn",
           "usage": {"input_tokens": 10, "output_tokens": 2}}
# A deliberately awkward stream: a ping, non-ASCII text, a multi-line data field and a usage split
# across message_start and message_delta. Replay must give these exact bytes back.
SSE = (
    'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_02","model":"claude-haiku-5-5",'
    '"usage":{"input_tokens":12,"output_tokens":1}}}\n\n'
    'event: ping\ndata: {"type": "ping"}\n\n'
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"café — ok"}}\n\n'
    'event: odd\ndata: line one\ndata: line two\n\n'
    'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":7}}\n\n'
    'event: message_stop\ndata: {"type":"message_stop"}\n\n'
).encode()


class FakeUpstream:
    """Counts calls and remembers what the proxy sent; answers JSON, or SSE when stream is set."""

    def __init__(self):
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        body = json.loads(request.content or b"{}")
        if body.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream", "request-id": "req_x"},
                                  content=SSE)
        return httpx.Response(200, headers={"content-type": "application/json", "request-id": "req_x"},
                              json=MESSAGE)


@pytest.fixture
def running(tmp_path):
    """Start a proxy in a thread; yields a factory. Every server is shut down at the end."""
    started = []

    def start(mode="replay", upstream=None, **kw):
        upstream = upstream or FakeUpstream()
        if mode == "record":
            kw.setdefault("yes", True)            # the user's approval, as --yes gives it
        server = proxy.make_server(mode=mode, port=0, store_dir=tmp_path / "store",
                                   transport=httpx.MockTransport(upstream), **kw)
        result = {}
        t = threading.Thread(target=lambda: result.setdefault("code", proxy.serve(server)), daemon=True)
        t.start()
        started.append((server, t))
        url = f"http://127.0.0.1:{server.server_address[1]}"
        return server, url, upstream, t, result

    yield start
    for server, t in started:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


def post(url, body, headers=None):
    return httpx.post(url + "/v1/messages", json=body, headers=headers or {"x-api-key": "sk-ant-dummy"}, timeout=10)


# ── key ─────────────────────────────────────────────────────────────────────
def test_key_ignores_volatile_fields():
    a = {**BODY, "metadata": {"user_id": '{"device_id":"aaa","session_id":"s1"}'}}
    b = {**BODY, "metadata": {"user_id": '{"device_id":"bbb","session_id":"s2"}'}}
    assert proxy.request_key("POST", "/v1/messages", a) == proxy.request_key("POST", "/v1/messages", b)

    # The Agent SDK's environment block: cwd, platform, shell, OS and date differ per machine and day.
    def env(cwd, date, osv):
        text = (f"# Environment\nYou have been invoked in the following environment: \n"
                f" - Primary working directory: {cwd}\n - Is a git repository: false\n - Platform: darwin\n"
                f" - Shell: unknown\n - OS Version: {osv}\n\nToday's date is {date}.")
        return {**BODY, "messages": BODY["messages"] + [{"role": "system", "content": [{"type": "text", "text": text}]}]}

    k1 = proxy.request_key("POST", "/v1/messages?beta=true", env("/home/a/x", "2026-10-27", "Darwin 27.0.0"))
    k2 = proxy.request_key("POST", "/v1/messages?beta=true", env("/srv/b/y", "2027-01-02", "Linux 6.1"))
    assert k1 == k2
    # Real content still counts.
    assert proxy.request_key("POST", "/v1/messages", {**BODY, "model": "claude-opus-5-5"}) != \
        proxy.request_key("POST", "/v1/messages", BODY)
    # Key is order-independent (canonical JSON) and a SHA-256 hex digest.
    shuffled = dict(reversed(list(BODY.items())))
    assert proxy.request_key("POST", "/v1/messages", shuffled) == proxy.request_key("POST", "/v1/messages", BODY)
    assert len(proxy.request_key("POST", "/v1/messages", BODY)) == 64


def test_key_excludes_headers(running, tmp_path):
    import inspect
    assert list(inspect.signature(proxy.request_key).parameters) == ["method", "path", "body"]
    key = proxy.request_key("POST", "/v1/messages", BODY)
    proxy.Store(tmp_path / "store").add(key, {"method": "POST", "path": "/v1/messages", "body": BODY},
                                        {"status": 200, "headers_allowlist": {"content-type": "application/json"},
                                         "body": MESSAGE})
    _, url, *_ = running("replay")
    r1 = post(url, BODY, {"x-api-key": "sk-ant-dummy", "x-claude-code-session-id": "one"})
    r2 = post(url, BODY, {"authorization": "Bearer other", "x-claude-code-session-id": "two", "anthropic-beta": "z"})
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json() == MESSAGE


# ── replay ──────────────────────────────────────────────────────────────────
def test_replay_hit_returns_stored(running, tmp_path):
    key = proxy.request_key("POST", "/v1/messages", BODY)
    proxy.Store(tmp_path / "store").add(key, {"method": "POST", "path": "/v1/messages", "body": BODY},
                                        {"status": 200, "headers_allowlist": {"content-type": "application/json"},
                                         "body": MESSAGE})
    _, url, upstream, *_ = running("replay")
    r = post(url, BODY)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    assert r.json() == MESSAGE
    assert upstream.calls == []                      # replay never forwards


def test_replay_miss_404_with_command(running):
    _, url, upstream, *_ = running("replay", set_name="hand")
    r = post(url, BODY)
    key8 = proxy.request_key("POST", "/v1/messages", BODY)[:8]
    assert r.status_code == 404
    assert r.json() == {"type": "error", "error": {"type": "replay_miss", "message":
                        f"no recording for {key8}; record with: python agents/proxy/proxy.py --mode record --set hand"}}
    assert upstream.calls == []


def test_stream_replay_byte_exact(running, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    body = {**BODY, "stream": True}
    server, url, upstream, *_ = running("record")
    with httpx.stream("POST", url + "/v1/messages", json=body, headers={"x-api-key": "sk-ant-dummy"}) as r:
        recorded = r.read()
    assert recorded == SSE and len(upstream.calls) == 1
    server.shutdown()

    stored = proxy.Store(tmp_path / "store").find(proxy.request_key("POST", "/v1/messages", body))
    assert "sse_events" in stored["response"] and "body" not in stored["response"]
    assert len(stored["response"]["sse_events"]) == 6

    _, url2, upstream2, *_ = running("replay")
    with httpx.stream("POST", url2 + "/v1/messages", json=body, headers={"x-api-key": "sk-ant-dummy"}) as r:
        chunks = list(r.iter_raw())
        assert r.headers["content-type"] == "text/event-stream"
    assert b"".join(chunks) == SSE
    assert upstream2.calls == []


def test_rerecord_latest_wins(running, tmp_path):
    store = proxy.Store(tmp_path / "store")
    key = proxy.request_key("POST", "/v1/messages", BODY)
    summary = {"method": "POST", "path": "/v1/messages", "body": BODY}
    for text in ("first", "second"):
        store.add(key, summary, {"status": 200, "headers_allowlist": {"content-type": "application/json"},
                                 "body": {**MESSAGE, "content": [{"type": "text", "text": text}]}})
    files = list((tmp_path / "store").glob("*.json"))
    assert [f.name for f in files] == [f"{key}.json"]
    assert store.find(key)["response"]["body"]["content"][0]["text"] == "second"

    before = files[0].stat().st_mtime_ns
    _, url, *_ = running("replay")
    for _ in range(3):
        assert post(url, BODY).json()["content"][0]["text"] == "second"
    assert list((tmp_path / "store").glob("*.json")) == files      # replay never grows the store
    assert files[0].stat().st_mtime_ns == before


def test_miss_reports_closest_key_diff(running, tmp_path, capsys):
    key = proxy.request_key("POST", "/v1/messages", BODY)
    proxy.Store(tmp_path / "store").add(key, {"method": "POST", "path": "/v1/messages", "body": BODY},
                                        {"status": 200, "headers_allowlist": {"content-type": "application/json"},
                                         "body": MESSAGE})
    _, url, upstream, *_ = running("replay")
    changed = {**BODY, "metadata": {"user_id": "u", "request_id": "req_new"}}   # an SDK upgrade adds an id
    r = post(url, changed)
    assert r.status_code == 404 and r.json()["error"]["type"] == "replay_miss"   # loud, never a wrong match
    hint = r.headers["x-replay-hint"]
    assert key[:8] in hint and "body.metadata.request_id" in hint
    assert "body.metadata.request_id" in capsys.readouterr().err
    assert upstream.calls == []


# ── record mode ─────────────────────────────────────────────────────────────
def test_record_requires_confirm(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))       # piped "y" is not a terminal: counts as no
    upstream = FakeUpstream()
    code = proxy.main(["--mode", "record", "--port", "0", "--store", str(tmp_path / "s")],
                      transport=httpx.MockTransport(upstream))
    out = capsys.readouterr().out
    assert code != 0
    assert "Estimated" in out and "Not confirmed" in out
    assert upstream.calls == []
    assert not (tmp_path / "s").exists() or not any((tmp_path / "s").rglob("*.json"))


def test_cap_refuses_before_call(running, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    # Worst case for BODY: ~25 input tokens + MAX_TOKENS (4000) output tokens on Haiku, about $0.002.
    server, url, upstream, t, result = running("record", max_cost=0.00001)
    r = post(url, BODY)
    assert r.status_code == 402 and r.json()["error"]["type"] == "cost_cap"
    assert upstream.calls == []
    t.join(timeout=5)
    assert result["code"] == 3                       # the proxy stops with exit code 3

    # Under the cap the call goes through, and spend is tracked from the reply's usage.
    server2, url2, upstream2, *_ = running("record", max_cost=1.0)
    assert post(url2, BODY).status_code == 200
    assert len(upstream2.calls) == 1
    assert server2.spent == pytest.approx(proxy.call_cost("claude-haiku-5-5", 10, 2))


def test_no_key_in_store(running, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    _, url, upstream, *_ = running("record")
    assert post(url, BODY, {"x-api-key": "sk-ant-dummy", "authorization": "Bearer sk-ant-dummy"}).status_code == 200
    assert post(url, {**BODY, "stream": True}).status_code == 200
    # Upstream got the environment key, never the client's dummy one.
    assert all(c.headers["x-api-key"] == SECRET and "authorization" not in c.headers for c in upstream.calls)
    text = "\n".join(p.read_text() for p in (tmp_path / "store").rglob("*.json"))
    assert text
    for needle in (SECRET, "sk-ant-dummy", "x-api-key", "authorization", "request-id"):
        assert needle.lower() not in text.lower()
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err


def test_binds_localhost_only(tmp_path):
    server = proxy.make_server(mode="replay", port=0, store_dir=tmp_path)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
    assert proxy.HOST == "127.0.0.1" and proxy.DEFAULT_PORT == 8790
    with pytest.raises(SystemExit):
        proxy.parse_args(["--host", "0.0.0.0"])


# ── extras ──────────────────────────────────────────────────────────────────
def test_set_namespaces_store(running, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    _, url, *_ = running("record", set_name="langgraph")
    assert post(url, BODY).status_code == 200
    key = proxy.request_key("POST", "/v1/messages", BODY)
    assert (tmp_path / "store" / "langgraph" / f"{key}.json").exists()
    _, url2, *_ = running("replay", set_name="other")
    assert post(url2, BODY).status_code == 404


def test_head_probe_answered_locally(running):
    _, url, upstream, *_ = running("replay")
    assert httpx.head(url + "/api/hello").status_code == 200
    assert upstream.calls == []


def test_estimate_line(capsys):
    line = proxy.estimate_line(max_cost=10)
    assert line in capsys.readouterr().out
    assert "claude-haiku-5-5" in line and "$10.00" in line


# ── fix round 1: hardening (rulings R10-R14) ────────────────────────────────
import socket  # noqa: E402
import time  # noqa: E402


def raw_request(url: str, request_line: str, body: bytes, headers: dict[str, str] | None = None) -> int:
    """Send a hand-written HTTP request (httpx cannot send absolute-form); return the status code."""
    port = int(url.rsplit(":", 1)[1])
    hdrs = {"Host": f"127.0.0.1:{port}", "content-type": "application/json",
            "content-length": str(len(body)), "x-api-key": "sk-ant-dummy", "connection": "close", **(headers or {})}
    data = (request_line + "\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n").encode() + body
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(data)
        reply = b""
        while chunk := s.recv(65536):
            reply += chunk
    return int(reply.split(b" ", 2)[1])


def test_absolute_form_request_refused(running, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    _, url, upstream, *_ = running("record")
    body = json.dumps(BODY).encode()
    assert raw_request(url, "POST http://evil.example/steal HTTP/1.1", body) == 400
    assert raw_request(url, "POST //evil.example/v1/messages HTTP/1.1", body) == 400
    assert upstream.calls == []


def test_record_allowlist_only(running, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    _, url, upstream, *_ = running("record")
    for path in ("/v1/messages/batches", "/v1/files", "/v1/messages?beta=true&x=1", "/api/hello"):
        assert httpx.post(url + path, json=BODY).status_code == 400, path
    assert httpx.get(url + "/v1/models", headers={"content-type": "application/json"}).status_code == 400
    assert upstream.calls == []
    assert httpx.post(url + "/v1/messages?beta=true", json=BODY).status_code == 200
    assert httpx.post(url + "/v1/messages/count_tokens", json=BODY).status_code == 200
    assert [str(c.url) for c in upstream.calls] == ["https://api.anthropic.com/v1/messages?beta=true",
                                                    "https://api.anthropic.com/v1/messages/count_tokens"]


def test_origin_host_and_content_type_checked(running):
    key = proxy.request_key("POST", "/v1/messages", BODY)
    _, url, *_ = running("replay")
    port = url.rsplit(":", 1)[1]
    body = json.dumps(BODY).encode()
    assert raw_request(url, "POST /v1/messages HTTP/1.1", body, {"Origin": "https://evil.example"}) == 403
    assert raw_request(url, "POST /v1/messages HTTP/1.1", body, {"Host": f"evil.example:{port}"}) == 403
    assert raw_request(url, "POST /v1/messages HTTP/1.1", body, {"Host": "127.0.0.1:1"}) == 403
    assert raw_request(url, "POST /v1/messages HTTP/1.1", body, {"content-type": "text/plain"}) == 415
    # A normal SDK-shaped request passes the checks (and misses, since nothing is recorded): 404, not 403/415.
    assert raw_request(url, "POST /v1/messages?beta=true HTTP/1.1", body,
                       {"Host": f"localhost:{port}", "anthropic-beta": "x", "x-app": "cli"}) == 404
    assert post(url, BODY).status_code == 404 and key


def test_record_server_requires_confirm_in_process(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    with pytest.raises(proxy.NotConfirmed):
        proxy.make_server(mode="record", port=0, store_dir=tmp_path)
    out = capsys.readouterr().out
    assert "Not confirmed" in out
    # R71: the refusal never tells a learner to bypass the question
    assert "--yes" not in out and "Run it in a terminal and answer y after reading the estimate." in out
    # Nothing is listening on any port after a refusal: make_server returned no server at all.


def test_transport_error_costs_worst_case(running, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)

    def read_timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    server, url, *_ = running("record", upstream=read_timeout)
    assert post(url, BODY).status_code == 502
    assert server.spent == pytest.approx(proxy.worst_call(BODY))

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    server2, url2, *_ = running("record", upstream=refused)
    assert post(url2, BODY).status_code == 502
    assert server2.spent == 0


def test_error_stream_never_stored(running, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    error_sse = (b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":5,'
                 b'"output_tokens":1}}}\n\nevent: error\ndata: {"type":"error","error":{"type":"overloaded_error",'
                 b'"message":"Overloaded"}}\n\n')
    truncated = SSE.split(b"event: message_stop")[0]
    replies = [error_sse, truncated, SSE]
    calls = []

    def flaky(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=replies[len(calls) - 1])

    server, url, *_ = running("record", upstream=flaky)
    body = {**BODY, "stream": True}
    assert post(url, body).content == error_sse          # passed through to the client
    assert post(url, body).content == truncated          # the client's retry goes upstream again
    assert post(url, body).content == SSE                # a good stream is stored
    assert len(calls) == 3
    stored = list((tmp_path / "store").glob("*.json"))
    assert len(stored) == 1 and json.loads(stored[0].read_text())["response"]["sse_events"][-1].startswith(
        "event: message_stop")
    # Cost: the error stream had usage (5 in, 1 out); the truncated one had usage too (12 in, 7 out).
    assert server.spent == pytest.approx(proxy.call_cost("claude-haiku-5-5", 5, 1)
                                         + 2 * proxy.call_cost("claude-haiku-5-5", 12, 7))


@pytest.mark.parametrize("bad", [{"max_tokens": -5}, {"max_tokens": 10**9}, {"max_tokens": "4000"},
                                 {"max_tokens": True}, {"model": 5}, {"model": None}])
def test_bad_model_or_max_tokens_400(running, monkeypatch, bad):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    server, url, upstream, *_ = running("record")
    assert post(url, {**BODY, **bad}).status_code == 400
    assert upstream.calls == [] and server.reserved == 0 and server.spent == 0


def test_worst_case_uses_at_least_max_tokens():
    # Equal up to the one-character difference in the input estimate.
    assert proxy.worst_call({**BODY, "max_tokens": 1}) == pytest.approx(
        proxy.worst_call({**BODY, "max_tokens": proxy.MAX_TOKENS}), rel=1e-3)
    assert proxy.worst_call({**BODY, "max_tokens": 128000}) > proxy.worst_call(BODY)


def test_odd_upstream_bodies_survive(running, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    replies = {"a": (b"\xff\xfe not utf-8", "application/octet-stream"), "b": (b'"just a string"', "application/json")}

    def odd(request):
        return httpx.Response(200, headers={"content-type": replies[json.loads(request.content)["messages"][0]["content"]][1]},
                              content=replies[json.loads(request.content)["messages"][0]["content"]][0])

    server, url, *_ = running("record", upstream=odd)
    for k, (data, _) in replies.items():
        assert post(url, {**BODY, "messages": [{"role": "user", "content": k}]}).content == data
    server.shutdown()
    _, url2, *_ = running("replay")
    for k, (data, _) in replies.items():
        assert post(url2, {**BODY, "messages": [{"role": "user", "content": k}]}).content == data


def test_concurrent_identical_misses_pay_once(running, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    calls = []

    def slow(request):
        calls.append(request)
        time.sleep(0.5)
        return httpx.Response(200, headers={"content-type": "application/json"}, json=MESSAGE)

    _, url, *_ = running("record", upstream=slow)
    results = [None] * 4
    threads = [threading.Thread(target=lambda i=i: results.__setitem__(i, post(url, BODY))) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert all(r.status_code == 200 and r.json() == MESSAGE for r in results)


def test_cap_race_forwards_only_affordable(running, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    calls = []

    def slow(request):
        calls.append(request)
        time.sleep(1.0)
        return httpx.Response(200, headers={"content-type": "application/json"}, json=MESSAGE)

    bodies = [{**BODY, "messages": [{"role": "user", "content": f"p{i}"}]} for i in range(6)]
    worst = proxy.worst_call(bodies[0])
    assert all(proxy.worst_call(b) == worst for b in bodies)
    server, url, upstream, t, result = running("record", upstream=slow, max_cost=2.5 * worst)
    codes = [None] * len(bodies)

    def send(i):
        try:
            codes[i] = post(url, bodies[i]).status_code
        except httpx.HTTPError:
            codes[i] = "refused"                 # the proxy had already stopped after the cap

    threads = [threading.Thread(target=send, args=(i,)) for i in range(len(bodies))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(calls) == 2
    assert codes.count(200) == 2
    t.join(timeout=5)
    assert result["code"] == 3


# ── fix round 2: no request smuggling past the guard; non-UTF-8 streams ─────
def smuggle(url: str, outer_headers: dict[str, str]) -> tuple[bytes, int]:
    """POST whose body is a complete second request, on a kept-alive connection. Returns what came
    back before the server closed (or 2 s passed) and how many responses it held."""
    port = int(url.rsplit(":", 1)[1])
    inner_body = json.dumps(BODY).encode()
    inner = (f"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\ncontent-type: application/json\r\n"
             f"content-length: {len(inner_body)}\r\n\r\n").encode() + inner_body
    hdrs = {"Host": f"127.0.0.1:{port}", "content-type": "application/json", "connection": "keep-alive",
            "content-length": str(len(inner)), **outer_headers}
    data = ("POST /v1/messages HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())
            + "\r\n").encode() + inner
    reply = b""
    with socket.create_connection(("127.0.0.1", port), timeout=2) as s:
        s.sendall(data)
        try:
            while chunk := s.recv(65536):
                reply += chunk
        except socket.timeout:
            pass
    return reply, reply.count(b"HTTP/1.")


@pytest.mark.parametrize("outer, status", [
    ({"Origin": "https://evil.example", "content-type": "text/plain"}, b"403"),
    ({"content-type": "text/plain"}, b"415"),
    ({"Host": "evil.example"}, b"403"),
])
def test_guard_rejection_cannot_smuggle(running, monkeypatch, outer, status):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    server, url, upstream, *_ = running("record")
    reply, n = smuggle(url, outer)
    assert reply.split(b" ", 2)[1] == status
    assert n == 1 and b"connection: close" in reply.lower()
    time.sleep(0.2)
    assert upstream.calls == [] and server.paid_calls == 0


def test_non_utf8_stream_never_stored(running, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    bad = SSE.replace(b"caf\xc3\xa9", b"caf\xe9")          # latin-1 byte inside an otherwise good stream
    calls = []

    def latin(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=bad)

    _, url, *_ = running("record", upstream=latin)
    body = {**BODY, "stream": True}
    assert post(url, body).content == bad
    assert post(url, body).content == bad
    assert len(calls) == 2
    assert not list((tmp_path / "store").glob("*.json"))


# ── Task 8: the Agent SDK's per-run lines in later requests ─────────────────
HANDBACK = ("[Subagent hand-back] The text below is the final report of a subagent this session delegated to. "
            "The report follows:\n  {{\"category\": \"billing\", \"priority\": \"high\"}}\nagentId: {agent}\n"
            "<usage>subagent_tokens: 16\ntool_uses: 0\nduration_ms: {ms}</usage>")


def _handback_body(agent: str, ms: int, *, as_list: bool = True, tokens: int = 16, report: str | None = None,
                   tool: str = "Agent") -> dict:
    text = HANDBACK.format(agent=agent, ms=ms).replace("subagent_tokens: 16", f"subagent_tokens: {tokens}")
    if report is not None:
        text = text.replace('  {"category": "billing", "priority": "high"}', report)
    content = [{"type": "text", "text": text}] if as_list else text
    return {**BODY, "messages": [
        {"role": "user", "content": "ticket"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": tool, "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": content}]},
        {"role": "system", "content": [{"type": "text", "text": "<total_tokens>14999984 tokens left</total_tokens>"}]}]}


@pytest.mark.parametrize("as_list", [True, False])
def test_key_masks_subagent_handback_id_and_duration(as_list):
    """The CLI's Agent tool result carries a fresh agentId and the subagent's wall-clock duration_ms on every
    run (verified against the bundled CLI 2.1.310 through a stub): both are masked so SDK supervisor recordings
    replay. The rest of the hand-back (the report, subagent_tokens, tool_uses) stays in the key."""
    k1 = proxy.request_key("POST", "/v1/messages?beta=true", _handback_body("a2697314b701207e0", 24, as_list=as_list))
    k2 = proxy.request_key("POST", "/v1/messages?beta=true", _handback_body("a684c5feff9b656ff", 25, as_list=as_list))
    assert k1 == k2
    k3 = proxy.request_key("POST", "/v1/messages?beta=true", _handback_body("a684c5feff9b656ff", 25, as_list=as_list, tokens=17))
    assert k3 != k1


def test_handback_mask_is_narrow():
    """Only a tool_result that is a sub-agent hand-back (it has the <usage>subagent_tokens block) is masked:
    the same lines in a user's own text, or in a desk tool's result, still count."""
    plain = "agentId: a2697314b701207e0\nduration_ms: 24"
    def body(text, role="user", kind="tool_result"):
        block = {"type": kind, "tool_use_id": "toolu_1", "content": text} if kind == "tool_result" else {"type": "text", "text": text}
        return {**BODY, "messages": [{"role": role, "content": [block]}]}
    for kind in ("tool_result", "text"):
        assert proxy.request_key("POST", "/v1/messages", body(plain, kind=kind)) != \
            proxy.request_key("POST", "/v1/messages", body(plain.replace("a26", "b26").replace("24", "99"), kind=kind))


def test_token_budget_line_is_in_the_key():
    """The trailing `<total_tokens>N tokens left` system message is derived from the usage the replies
    reported (15000000 minus the tokens so far; verified), so replay reproduces it: it stays in the key, and
    a different budget is a different request."""
    a, b = _handback_body("a1", 1), _handback_body("a1", 1)
    b["messages"][-1]["content"][0]["text"] = "<total_tokens>14999000 tokens left</total_tokens>"
    assert proxy.request_key("POST", "/v1/messages", a) != proxy.request_key("POST", "/v1/messages", b)


def test_record_command_can_be_named(running):
    """run.py names its own record command in a miss (one proxy run per ticket, R25)."""
    cmd = "python agents/run.py --engine hand --desk single --tickets t001 --mode record"
    _, url, *_ = running("replay", set_name="desk-hand", record_command=cmd)
    r = post(url, BODY)
    assert r.status_code == 404
    assert r.json()["error"]["message"].endswith(f"record with: {cmd}")


def test_handback_mask_is_the_trailing_footer_only():
    """R53: only the footer at the very end of an Agent tool's result is masked. A `duration_ms:` or `agentId:` line
    inside the report itself is the sub-agent's words and stays in the key: two different bodies, two keys."""
    key = lambda b: proxy.request_key("POST", "/v1/messages?beta=true", b)
    a = _handback_body("a1", 24, report="  timing\nduration_ms: 5\nagentId: abc")
    b = _handback_body("a2", 30, report="  timing\nduration_ms: 6\nagentId: abd")
    assert key(a) != key(b)
    assert key(_handback_body("a1", 24, report="  same")) == key(_handback_body("b9", 99, report="  same"))


def test_handback_mask_needs_the_agent_tool():
    """The same footer in a result of any tool but Agent (a desk tool, say) is not a hand-back: not masked."""
    key = lambda b: proxy.request_key("POST", "/v1/messages?beta=true", b)
    assert key(_handback_body("a1", 24, tool="mcp__lumen-read__get_ticket")) != \
        key(_handback_body("a2", 30, tool="mcp__lumen-read__get_ticket"))


# ── R59: a kept-alive connection outliving its proxy run ────────────────────
def _close_like_run_py(server, t):
    server.shutdown()
    server.server_close()
    if server.client is not None:
        server.client.close()
    t.join(timeout=5)


def test_closed_run_drops_kept_alive_connections(tmp_path, monkeypatch):
    """R59: langchain-anthropic reuses one pooled httpx client per base URL, so its kept-alive connection to the
    previous ticket's proxy run outlived that run: the old handler thread read the next ticket's request and sent
    it with the closed upstream client: 500 "internal error (RuntimeError)" on every other LangGraph run. Closing a
    run now drops its open connections, so the client reconnects to the next run on the same port."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    first = FakeUpstream()
    a = proxy.make_server(mode="record", port=0, store_dir=tmp_path / "store", transport=httpx.MockTransport(first), yes=True)
    port = a.server_address[1]
    ta = threading.Thread(target=a.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    ta.start()
    pooled = httpx.Client(timeout=10)                      # one pool across both runs, as ChatAnthropic's
    url = f"http://127.0.0.1:{port}/v1/messages"
    assert pooled.post(url, json=BODY, headers={"x-api-key": "sk-ant-dummy"}).status_code == 200
    _close_like_run_py(a, ta)
    second = FakeUpstream()
    b = proxy.make_server(mode="record", port=port, store_dir=tmp_path / "store2", transport=httpx.MockTransport(second), yes=True)
    tb = threading.Thread(target=b.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    tb.start()
    try:
        r = pooled.post(url, json={**BODY, "max_tokens": 51}, headers={"x-api-key": "sk-ant-dummy"})
        assert r.status_code == 200, r.text
        assert len(second.calls) == 1 and len(first.calls) == 1   # the new run answered, not the old one
    finally:
        pooled.close()
        _close_like_run_py(b, tb)


def test_internal_error_names_the_exception(running, monkeypatch):
    """R59: a 500 carries the real exception type and message, never just its type."""
    _, url, *_ = running("replay")
    monkeypatch.setattr(proxy, "request_key", lambda *a: (_ for _ in ()).throw(RuntimeError("store exploded")))
    r = post(url, BODY)
    assert r.status_code == 500
    assert r.json()["error"]["message"] == "proxy: internal error (RuntimeError: store exploded)"


def test_log_file_keeps_the_proxy_log(running, tmp_path):
    log = tmp_path / "proxy.log"
    _, url, *_ = running("replay", set_name="s", log_file=log)
    post(url, BODY)
    assert "MISS" in log.read_text()



def test_cap_stop_line_is_in_the_log(running, monkeypatch, tmp_path):
    """Minor 7: the cap's "Stopped before calling" line reaches proxy.log too."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    log = tmp_path / "proxy.log"
    _, url, *_ = running("record", max_cost=0.0000001, log_file=log)
    assert post(url, BODY).status_code == 402
    assert "Stopped before calling" in log.read_text()


# ── R62: upstream timing in the recording, and the per-run call ledger ──────
USAGE = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def test_record_stores_upstream_timing_and_usage(running, tmp_path, monkeypatch):
    """A recording keeps the upstream's wall clock and usage beside the response (JSON and SSE); not in the key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    _, url, *_ = running("record")
    assert post(url, BODY).status_code == 200
    stream = {**BODY, "stream": True}
    assert post(url, stream).status_code == 200
    store = proxy.Store(tmp_path / "store")
    up = store.find(proxy.request_key("POST", "/v1/messages", BODY))["upstream"]
    assert set(up) == {"ms", "model", *USAGE}                            # R64: the API's fields, verbatim
    assert isinstance(up["ms"], int) and up["ms"] >= 0
    assert up["model"] == "claude-haiku-5-5"
    assert [up[k] for k in USAGE] == [10, 0, 0, 2]                       # missing cache fields are 0
    up = store.find(proxy.request_key("POST", "/v1/messages", stream))["upstream"]
    assert [up[k] for k in USAGE] == [12, 0, 0, 7]                       # message_start, then message_delta
    assert "upstream" not in proxy.request_summary("POST", "/v1/messages", BODY)


def test_ledger_live_then_replayed(running, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    rec, url, *_ = running("record")
    assert post(url, BODY).status_code == 200
    key = proxy.request_key("POST", "/v1/messages", BODY)
    stored_ms = proxy.Store(tmp_path / "store").find(key)["upstream"]["ms"]
    cost = proxy.call_cost("claude-haiku-5-5", 10, 2)
    assert rec.calls == [{"key": key, "replayed": False, "ms": stored_ms, "model": "claude-haiku-5-5",
                          "input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                          "output_tokens": 2, "cost_usd": cost}]
    rep, url2, upstream2, *_ = running("replay")
    assert post(url2, BODY).status_code == 200
    assert rep.calls == [{**rec.calls[0], "replayed": True}]            # the recording's time and tokens, served again
    assert upstream2.calls == []


def test_ledger_old_recording_count_tokens_misses_and_refusals(running, tmp_path, monkeypatch):
    """A recording that predates `upstream` gives None for ms, tokens and cost; count_tokens, misses and cap
    refusals are not in the ledger."""
    key = proxy.request_key("POST", "/v1/messages", BODY)
    proxy.Store(tmp_path / "store").add(key, {"method": "POST", "path": "/v1/messages", "body": BODY},
                                        {"status": 200, "headers_allowlist": {"content-type": "application/json"},
                                         "body": MESSAGE})
    srv, url, *_ = running("replay")
    assert post(url, BODY).status_code == 200
    assert post(url, {**BODY, "max_tokens": 7}).status_code == 404      # a miss
    assert srv.calls == [{"key": key, "replayed": True, "ms": None, "model": None, "input_tokens": None,
                          "cache_creation_input_tokens": None, "cache_read_input_tokens": None,
                          "output_tokens": None, "cost_usd": None}]
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    rec, url2, *_ = running("record")
    r = httpx.post(url2 + "/v1/messages/count_tokens", json=BODY, headers={"x-api-key": "sk-ant-dummy"}, timeout=10)
    assert r.status_code == 200
    ct = proxy.Store(tmp_path / "store").find(proxy.request_key("POST", "/v1/messages/count_tokens", BODY))
    assert "upstream" not in ct and rec.calls == []
    capped, url3, *_ = running("record", max_cost=0.0000001)
    assert post(url3, {**BODY, "max_tokens": 9}).status_code == 402
    assert capped.calls == []


def test_refresh_ignores_a_store_hit_and_overwrites(running, tmp_path, monkeypatch):
    key = proxy.request_key("POST", "/v1/messages", BODY)
    old = {**MESSAGE, "content": [{"type": "text", "text": "old"}]}
    proxy.Store(tmp_path / "store").add(key, {"method": "POST", "path": "/v1/messages", "body": BODY},
                                        {"status": 200, "headers_allowlist": {"content-type": "application/json"},
                                         "body": old})
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    plain, url, upstream, *_ = running("record")
    assert post(url, BODY).json() == old and upstream.calls == []        # without --refresh: a free store hit
    srv, url2, upstream2, *_ = running("record", refresh=True)
    assert post(url2, BODY).json() == MESSAGE and len(upstream2.calls) == 1
    assert post(url2, BODY).json() == MESSAGE and len(upstream2.calls) == 1   # staged this run: served from it
    assert [c["replayed"] for c in srv.calls] == [False, True]
    store = proxy.Store(tmp_path / "store")
    assert store.find(key)["response"]["body"] == old                   # R65: staged, not yet over the store
    assert srv.commit_staged() == 1
    found = store.find(key)
    assert found["response"]["body"] == MESSAGE and "upstream" in found
    assert srv.commit_staged() == 0                                      # committed once
    with pytest.raises(ValueError, match="refresh"):
        proxy.make_server(mode="replay", port=0, store_dir=tmp_path / "store", refresh=True)


def test_refresh_discard_keeps_the_old_recording(running, tmp_path, monkeypatch):
    """R65: a refreshed run that fails throws its staged recordings away; the store is as it was."""
    key = proxy.request_key("POST", "/v1/messages", BODY)
    old = {**MESSAGE, "content": [{"type": "text", "text": "old"}]}
    store = proxy.Store(tmp_path / "store")
    store.add(key, {"method": "POST", "path": "/v1/messages", "body": BODY},
              {"status": 200, "headers_allowlist": {"content-type": "application/json"}, "body": old})
    before = store.path(key).read_text()
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    srv, url, upstream, *_ = running("record", refresh=True)
    assert post(url, BODY).json() == MESSAGE and len(upstream.calls) == 1
    assert srv.discard_staged() == 1
    assert store.path(key).read_text() == before and srv.commit_staged() == 0


def test_ledger_prices_cache_tokens_by_their_multipliers(running, tmp_path, monkeypatch):
    """R64: cache writes at 1.25x and cache reads at 0.10x the input price, each field kept apart."""
    usage = {"input_tokens": 100, "cache_creation_input_tokens": 400, "cache_read_input_tokens": 2000,
             "output_tokens": 50}

    def cached(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, json={**MESSAGE, "usage": usage})
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    srv, url, *_ = running("record", upstream=cached)
    assert post(url, BODY).status_code == 200
    (entry,) = srv.calls
    assert {k: entry[k] for k in usage} == usage
    p_in, p_out = proxy.PRICES["claude-haiku-5-5"]
    assert proxy.CACHE_WRITE_MULTIPLIER == 1.25 and proxy.CACHE_READ_MULTIPLIER == 0.10
    want = (100 * p_in + 400 * 1.25 * p_in + 2000 * 0.10 * p_in + 50 * p_out) / 1_000_000
    assert entry["cost_usd"] == pytest.approx(want)
    assert entry["cost_usd"] < proxy.call_cost("claude-haiku-5-5", 2500, 50)   # cheaper than all at full price


# ── Polish 2026-10-28: review minors ─────────────────────────────────────────
def _straggler(running, method: str) -> bytes:
    """A request that starts on a kept-alive connection just before the run closes: its request line is on
    the wire, the rest arrives after server_close. The handler is busy from the first byte, so the close leaves
    the connection open and the straggler is answered (503), never reset."""
    server, url, *_ = running("replay")
    port = server.server_address[1]
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        s.sendall(f"{method} /v1/messages HTTP/1.1\r\n".encode())
        deadline = time.monotonic() + 5
        while not any(h.busy for h in list(server.connections.values())):
            assert time.monotonic() < deadline, "the handler never became busy on the request line"
            time.sleep(0.01)
        server.shutdown()
        server.server_close()
        body = json.dumps(BODY).encode()
        s.sendall((f"Host: 127.0.0.1:{port}\r\ncontent-type: application/json\r\n"
                   f"content-length: {len(body)}\r\n\r\n").encode() + body)
        reply = b""
        while chunk := s.recv(65536):
            reply += chunk
        return reply
    finally:
        s.close()


def test_straggler_during_close_gets_503(running):
    reply = _straggler(running, "POST")
    assert reply.startswith(b"HTTP/1.1 503"), reply[:80]
    assert b"this proxy run has ended" in reply


def test_head_straggler_during_close_gets_503(running):
    reply = _straggler(running, "HEAD")
    assert reply.startswith(b"HTTP/1.1 503"), reply[:80]


def test_idle_kept_alive_connection_is_not_busy(running):
    """Busy means a request has started: a connection waiting for its next request stays idle, so close drops it."""
    server, url, *_ = running("replay")
    with httpx.Client(timeout=10) as c:
        assert c.head(url + "/api/hello").status_code == 200
        time.sleep(0.1)
        assert server.connections and not any(h.busy for h in server.connections.values())


def test_internal_error_message_is_truncated(running, monkeypatch, tmp_path):
    log = tmp_path / "proxy.log"
    _, url, *_ = running("replay", log_file=log)
    monkeypatch.setattr(proxy, "request_key", lambda *a: (_ for _ in ()).throw(RuntimeError("x" * 5000)))
    r = post(url, BODY)
    assert r.status_code == 500
    what = r.json()["error"]["message"].removeprefix("proxy: internal error (").removesuffix(")")
    assert len(what) == 300 and what.startswith("RuntimeError: xxx")
    assert "x" * 400 not in log.read_text()


def test_log_write_does_not_take_the_server_lock(running, tmp_path):
    """The proxy.log append is serialised by its own lock: a handler logging never waits on srv.lock (the cost
    accounting's), so slow disk I/O cannot stall the cap check."""
    log = tmp_path / "proxy.log"
    server, *_ = running("replay", log_file=log)
    h = proxy.Handler.__new__(proxy.Handler)
    h.server = server
    done = threading.Event()
    with server.lock:
        t = threading.Thread(target=lambda: (h._log("while the accounting lock is held"), done.set()), daemon=True)
        t.start()
        assert done.wait(2), "_log waited on srv.lock"
    assert "while the accounting lock is held" in log.read_text()
