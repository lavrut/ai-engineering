"""
proxy_handler — one HTTP request to the record/replay proxy (proxy.py): the connection, the guard, then
replay or record. proxy_server.py builds the server that runs it.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any

import httpx

from proxy import (DEFAULT_MODEL, DROP_HEADERS, MAX_BODY_BYTES, MAX_ERROR_CHARS, RECORD_ALLOWLIST, UPSTREAM_HOST,
                   closest_diff, playback_bytes, request_key, request_summary, to_record)
from proxy_cost import (MESSAGES_PATHS, BadRequest, check_body, ledger_entry, sse_complete, upstream_usage,
                        usage_cost, worst_call)
from triage_lab import PRICES

if TYPE_CHECKING:
    from proxy_server import ProxyServer


# ── 1. The handler: the connection and the guard ────────────────────────────
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ProxyServer

    def log_message(self, format: str, *args: Any) -> None:
        pass                              # the default log line is noisy; _log says what matters

    busy = False      # a request has started (its first byte is here): server_close lets it answer, then closes

    def handle_one_request(self) -> None:
        """Wait for the next request's first byte, then mark the handler busy BEFORE the request line is read, so a
        straggler arriving as the run closes is answered 503 rather than reset. A connection waiting between
        requests stays idle, so server_close still drops it (R59)."""
        try:
            first = self.rfile.peek(1)
        except OSError:
            first = b""
        except Exception:                 # anything else (a ValueError on a closed file): not idle, so the base
            first = b"?"                  # handler reads the request, answers or closes, and busy covers it
        if not first:                     # the client closed (or close dropped us) between requests
            self.close_connection = True
            return
        with self.server.lock:
            self.busy = True
        try:
            super().handle_one_request()
        finally:
            with self.server.lock:
                self.busy = False
            if self.server.closed:
                self.close_connection = True

    def setup(self) -> None:
        super().setup()
        with self.server.lock:
            self.server.connections[self.connection] = self

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            with self.server.lock:
                self.server.connections.pop(self.connection, None)

    def _log(self, text: str) -> None:
        line = f"[proxy {self.server.mode}] {text}"
        print(line, file=sys.stderr, flush=True)
        if self.server.log_file is not None:
            with self.server.log_lock, open(self.server.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def _send(self, status: int, data: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _error(self, status: int, kind: str, message: str, extra: dict[str, str] | None = None) -> None:
        """Every error closes the connection, so nothing left on the wire after a refusal can be
        parsed as a second request on the same connection (no request smuggling past the guard)."""
        self.close_connection = True
        data = json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode()
        self._send(status, data, {"content-type": "application/json", "connection": "close", **(extra or {})})

    def _read_body(self) -> bytes | None:
        """Consume the declared body before any check answers, so a refused request's body is
        drained and discarded, never read as the next request. Framing the proxy does not accept
        (chunked, a bad or oversized Content-Length) is refused and the connection closed."""
        if self.headers.get("transfer-encoding") is not None:
            self._error(400, "invalid_request_error", "proxy: chunked request bodies are not accepted")
            return None
        try:
            n = int(self.headers.get("content-length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY_BYTES:
            self._error(413 if n > 0 else 400, "invalid_request_error", "proxy: bad or oversized Content-Length")
            return None
        return self.rfile.read(n) if n else b""

    def _guard(self, need_json: bool) -> bool:
        """Checks for both modes (ruling R11): origin-form request line, no Origin header (a web
        page cannot drive the proxy), Host must be this loopback port, and JSON content.
        Answers and returns False when a check fails."""
        port = self.server.server_address[1]
        if not self.path.startswith("/") or self.path.startswith("//"):
            self._error(400, "invalid_request_error", "proxy: only origin-form request paths are accepted")
            return False
        if self.headers.get("origin") is not None:
            self._error(403, "permission_error", "proxy: requests with an Origin header are refused")
            return False
        if self.headers.get("host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._error(403, "permission_error", "proxy: Host must be 127.0.0.1 or localhost on this port")
            return False
        ctype = (self.headers.get("content-type") or "").split(";")[0].strip().lower()
        if need_json and ctype != "application/json":
            self._error(415, "invalid_request_error", "proxy: content-type must be application/json")
            return False
        return True

    def do_HEAD(self) -> None:
        # The Agent SDK's CLI probes HEAD /api/hello before its first call (unless nonessential
        # traffic is off). Answered locally in both modes, never forwarded or stored.
        if self._read_body() is None:
            return
        if self.server.closed:
            self._error(503, "api_error", "proxy: this proxy run has ended; reconnect")
        elif self._guard(need_json=False):
            self._send(200, b"", {})

    def do_GET(self) -> None:
        self._safely()

    def do_POST(self) -> None:
        self._safely()

    def _safely(self) -> None:
        try:
            raw = self._read_body()
            if raw is not None and self.server.closed:   # a request that reached a run already closing (R59)
                self._error(503, "api_error", "proxy: this proxy run has ended; reconnect")
            elif raw is not None and self._guard(need_json=True):
                self._handle(raw)
        except Exception as e:            # answer, never drop the connection; say what failed (R59)
            what = f"{type(e).__name__}: {e}"[:MAX_ERROR_CHARS]   # a huge exception text never floods a reply
            self._log(f"{self.command} {self.path} internal error: {what}")
            try:
                self._error(500, "api_error", f"proxy: internal error ({what})")
            except OSError:
                pass

    # ── 2. Replay and record ────────────────────────────────────────────────
    def _handle(self, raw: bytes) -> None:
        srv = self.server
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self._error(400, "invalid_request_error", "proxy: the request body must be a JSON object")
        try:
            check_body(body)
        except BadRequest as e:
            return self._error(400, "invalid_request_error", f"proxy: {e}")
        key = request_key(self.command, self.path, body)
        if srv.mode == "replay":
            return self._replay(key, body)
        if (self.command, self.path) not in RECORD_ALLOWLIST:
            return self._error(400, "invalid_request_error",
                               f"proxy: record mode forwards only {', '.join(sorted({p for _, p in RECORD_ALLOWLIST}))}")
        with srv.key_lock(key):
            if srv.refresh:                  # R65: only this run's staged recordings count as hits
                with srv.lock:
                    staged = srv.staged.get(key)
                if staged is not None:
                    return self._serve(key, {"response": staged[1], "upstream": staged[2]})
                return self._record(key, raw, body)
            if srv.store.find(key) is not None:
                return self._replay(key, body)
            return self._record(key, raw, body)

    def _serve(self, key: str, found: dict) -> None:
        self._log(f"{self.command} {self.path} {key[:8]} replayed")
        resp = found["response"]
        self._ledger(key, True, found.get("upstream"))
        return self._send(resp["status"], playback_bytes(resp), dict(resp["headers_allowlist"]))

    def _ledger(self, key: str, replayed: bool, upstream: dict | None) -> None:
        if self.path in MESSAGES_PATHS:
            with self.server.lock:
                self.server.calls.append(ledger_entry(key, replayed, upstream))

    def _replay(self, key: str, body: dict) -> None:
        srv = self.server
        found = srv.store.find(key)
        if found is not None:
            return self._serve(key, found)
        with srv.lock:
            srv.misses += 1
        hint = closest_diff(srv.store, request_summary(self.command, self.path, body))
        self._log(f"{self.command} {self.path} {key[:8]} MISS; {hint}")
        return self._error(404, "replay_miss",
                           f"no recording for {key[:8]}; record with: {srv.record_command()}",
                           {"x-replay-hint": hint.encode("ascii", "replace").decode()})

    def _record(self, key: str, raw: bytes, body: dict) -> None:
        srv = self.server
        model = body.get("model") or DEFAULT_MODEL
        if model not in PRICES:
            return self._error(400, "invalid_request_error", f'proxy: no price for model "{model}"; not called')
        worst = worst_call(body)
        with srv.lock:
            over_calls = srv.paid_calls >= srv.max_calls
            if over_calls or srv.spent + srv.reserved + worst > srv.max_cost:
                srv.cap_hit = True
                why = (f"{srv.max_calls} paid calls already made" if over_calls else
                       f"spent ${srv.spent:.4f} + worst case ${worst:.4f} would pass --max-cost {srv.max_cost}")
                print(f"Stopped before calling: {why}. Recordings so far are saved.", flush=True)
                threading.Thread(target=srv.shutdown, daemon=True).start()
                refused = why
            else:
                refused = None
                srv.reserved += worst          # reserved under the same lock as the check (no cap race)
                srv.paid_calls += 1
        if refused is not None:            # logged outside srv.lock: no file I/O while the accounting lock is held
            self._log(f"Stopped before calling: {refused}. Recordings so far are saved.")
            return self._error(402, "cost_cap", f"proxy: {refused}; not called")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP_HEADERS}
        headers["x-api-key"] = srv._api_key or ""
        headers["accept-encoding"] = "identity"     # store the bytes exactly as sent
        cost = worst                                 # conservative unless we know better (ruling R12)
        try:
            assert srv.client is not None
            request = srv.client.build_request(self.command, self.path, content=raw, headers=headers)
            if request.url.scheme != "https" or request.url.host != UPSTREAM_HOST:
                cost = 0.0
                raise RuntimeError("upstream URL is not api.anthropic.com; refused")
            started = time.monotonic()
            r = srv.client.send(request)
            content = r.content                      # send() has read the whole body: this is the last byte
            ms = round((time.monotonic() - started) * 1000)
            rec = to_record(r.status_code, r.headers, content)
            actual = usage_cost(model, rec)
            cost = worst if actual is None else actual
            ctype = {"content-type": r.headers.get("content-type", "application/json")}
            if r.status_code >= 400:
                # Errors are passed through and never stored, so they are never replayed silently.
                self._log(f"{self.command} {self.path} {key[:8]} upstream {r.status_code}; not recorded")
                return self._send(r.status_code, content, ctype)
            is_stream = r.headers.get("content-type", "").startswith("text/event-stream")
            if is_stream and not ("sse_events" in rec and sse_complete(rec["sse_events"])):
                self._log(f"{self.command} {self.path} {key[:8]} stream not UTF-8, had an error or no message_stop; "
                          "not recorded")
                return self._send(r.status_code, content, ctype)
            upstream = None if self.path not in MESSAGES_PATHS else {"ms": ms, "model": model, **upstream_usage(rec)}
            summary = request_summary(self.command, self.path, body)
            if srv.refresh:
                with srv.lock:
                    srv.staged[key] = (summary, rec, upstream)
            else:
                srv.store.add(key, summary, rec, upstream)
            self._ledger(key, False, upstream)
            self._log(f"{self.command} {self.path} {key[:8]} {'staged' if srv.refresh else 'recorded'}; ${cost:.4f}")
            return self._send(r.status_code, content, dict(rec["headers_allowlist"]))
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            cost = 0.0                               # never reached the API: nothing to pay
            self._log(f"{self.command} {self.path} {key[:8]} could not connect: {type(e).__name__}")
            return self._error(502, "api_error", f"proxy: upstream unreachable ({type(e).__name__}); not recorded")
        except httpx.HTTPError as e:
            self._log(f"{self.command} {self.path} {key[:8]} upstream failed after sending: {type(e).__name__}")
            return self._error(502, "api_error", f"proxy: upstream failed ({type(e).__name__}); not recorded")
        finally:
            with srv.lock:
                srv.reserved -= worst
                srv.spent += cost
