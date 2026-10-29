#!/usr/bin/env python3
"""
injector: a fixture upstream for the gateway, scripted per request. Each request takes the next
step of the script: 200 (a fixed Messages answer, end_turn, usage 10 in and 5 out), 429 (with
retry-after: 0), any other status (an error body), or "drop" (the socket is closed with no
answer). At the end of the script it starts again. Standard library only; it calls nothing.

Run (from the repo root; then point a config's upstream at http://127.0.0.1:8793):
    .venv-infra/bin/python infra/python/injector.py --script infra/data/fixtures/injector/flaky.json --port 8793
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from responses import canonical, error_body

# ── 1. Script ───────────────────────────────────────────────────────────────
HOST = "127.0.0.1"
REPLY = canonical({"id": "msg_injector", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                   "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "stop_sequence": None,
                   "usage": {"input_tokens": 10, "output_tokens": 5}})
JSON = {"content-type": "application/json"}


def check_script(script) -> list[int | str]:
    """A non-empty list of HTTP statuses (100 to 599) and "drop"; anything else is a ValueError naming it."""
    if not isinstance(script, list) or not script:
        raise ValueError("a script is a non-empty JSON list of statuses and \"drop\"")
    for step in script:
        ok = step == "drop" or (isinstance(step, int) and not isinstance(step, bool) and 100 <= step <= 599)
        if not ok:
            raise ValueError(f"script step {step!r} is neither a status (100-599) nor \"drop\"")
    return list(script)


def load_script(path: Path | str) -> list[int | str]:
    return check_script(json.loads(Path(path).read_text(encoding="utf-8")))


def answer(step: int | str) -> tuple[int, dict[str, str], bytes] | None:
    """What one step answers: (status, headers, body), or None to drop the connection."""
    if step == "drop":
        return None
    if step == 200:
        return 200, dict(JSON), REPLY
    if step == 429:
        return 429, {**JSON, "retry-after": "0"}, error_body("rate_limit_error", "injector: scripted 429")
    kind = "api_error" if step >= 500 else "invalid_request_error"
    return step, dict(JSON), error_body(kind, f"injector: scripted {step}")


# ── 2. Server ───────────────────────────────────────────────────────────────
class Injector(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, script: list[int | str], port: int):
        super().__init__((HOST, port), Handler)
        self.script = check_script(script)
        self.served = 0          # requests taken so far (drops included)
        self.last: dict = {}     # the last request: method, path, headers (lower-cased), body
        self.lock = threading.Lock()
        self.url = f"http://{HOST}:{self.server_address[1]}"

    def take(self, request: dict) -> int | str:
        with self.lock:
            step = self.script[self.served % len(self.script)]
            self.served += 1
            self.last = request
            return step


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Injector

    def log_message(self, format, *args) -> None:
        pass

    def do_POST(self) -> None:
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n > 0 else b""
        step = self.server.take({"method": self.command, "path": self.path, "body": body,
                                 "headers": {k.lower(): v for k, v in self.headers.items()}})
        out = answer(step)
        if out is None:                   # "drop": close the socket, answer nothing
            self.close_connection = True
            return
        status, headers, data = out
        self.send_response(status)
        for k, v in {**headers, "content-length": str(len(data))}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST


def make_server(script: list[int | str], port: int = 0) -> Injector:
    """Build (not start) an injector on the loopback; serve it with serve_forever()."""
    return Injector(script, port)


# ── 3. Command line ─────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A scripted fixture upstream for the gateway (127.0.0.1 only).")
    ap.add_argument("--script", required=True, type=Path, help="a JSON list such as [429, 500, 200]")
    ap.add_argument("--port", type=int, default=8793)
    a = ap.parse_args(argv)
    try:
        server = make_server(load_script(a.script), a.port)
    except (OSError, ValueError) as e:
        print(f"injector: {e}", file=sys.stderr)
        return 2
    print(f"injector on {server.url}  script {server.script}", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
