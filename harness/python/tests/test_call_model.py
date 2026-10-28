"""call_model against a fake Messages API on 127.0.0.1: a dummy key, no network, nothing paid."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic
import pytest

import harness

OK = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
      "content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn", "stop_sequence": None,
      "usage": {"input_tokens": 12, "output_tokens": 3}}
BAD = {"type": "error", "error": {"type": "invalid_request_error", "message": "max_tokens: too large"}}


@pytest.fixture
def upstream(monkeypatch):
    """A local server that records each request and answers with the next (status, body)."""
    seen, answers = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            status, payload = answers.pop(0)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(harness, "client", anthropic.Anthropic(base_url=url, api_key="test-key", max_retries=0))
    yield seen, answers
    server.shutdown()
    server.server_close()


def events():
    return [json.loads(line) for line in harness.TRACE_FILE.read_text().splitlines()]


def test_request_shape_and_reply(ws, upstream):
    seen, answers = upstream
    answers.append((200, OK))
    response = harness.call_model([{"role": "user", "content": "hi"}])

    assert response.content[0].text == "hello" and response.stop_reason == "end_turn"
    (req,) = seen
    assert req["path"] == "/v1/messages"
    assert req["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert req["headers"]["authorization"] == "Bearer test-key"  # anthropic 2.3 sends the key this way
    assert "x-api-key" not in req["headers"]
    body = req["body"]
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16_000
    assert body["system"] == harness.SYSTEM_PROMPT
    assert [t["name"] for t in body["tools"]] == ["list_files", "read_file", "write_file"]
    assert body["fallbacks"] == "default"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert [e["event"] for e in events()] == ["model_request", "model_response"]
    assert events()[1]["input_tokens"] == 12 and events()[1]["output_tokens"] == 3


def test_overrides_win(ws, upstream):
    seen, answers = upstream
    answers.append((200, OK))
    harness.call_model([{"role": "user", "content": "hi"}], max_tokens=4_000, tool_choice={"type": "none"})
    body = seen[0]["body"]
    assert body["max_tokens"] == 4_000 and body["tool_choice"] == {"type": "none"}


def test_error_status_raises_and_logs_no_response(ws, upstream):
    seen, answers = upstream
    answers.append((400, BAD))
    with pytest.raises(anthropic.APIStatusError) as err:
        harness.call_model([{"role": "user", "content": "hi"}])
    assert err.value.status_code == 400
    assert [e["event"] for e in events()] == ["model_request"]
