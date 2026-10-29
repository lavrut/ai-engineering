#!/usr/bin/env python3
"""
gateway: the AI gateway's HTTP server. It listens on the loopback, speaks the Messages API shape
(the same paths and bodies the agents proxy accepts) and runs every request through the chain in
stages.py: tag -> budget -> cache -> route -> limit -> retry/upstream. Then it books the answer in
the ledger (stage 7) and writes one trace line (stage 8). Bodies go upstream byte for byte.

Run (from the repo root; free and offline: the upstream is the agents replay proxy on 8791):
    .venv-agents/bin/python agents/proxy/proxy.py --port 8791 --set desk-hand
    .venv-infra/bin/python infra/python/gateway.py --run demo          # listens on 127.0.0.1:8790
Live mode (--live) forwards to api.anthropic.com: it prints the estimate, asks y/N, and stops at
--max-cost. Standard library and PyYAML only.
"""
from __future__ import annotations

import argparse
import dataclasses
import http.client
import math
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import config
import ledger
import traces
from cachefile import load_cache, save_cache
from responses import error_body
from stages import (JSON_HEADERS, Outcome, Request, State, Upstream, decision, make_request, run_chain,
                    worst_case_tokens)

ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"                       # loopback only, as the agents proxy
DEFAULT_PORT = 8790
LEDGER_PATH = ROOT / "infra" / "data" / "ledger.jsonl"
CACHE_PATH = ROOT / "infra" / "data" / "cache.json"
PATHS = ("/v1/messages", "/v1/messages/count_tokens")   # the Messages API paths (a query string is allowed)
MAX_BODY_BYTES = 32 * 1024 * 1024        # the API's own request size limit, as the proxy's
OUTCOMES = {("parse", "refuse"): "refused", ("budget", "refuse"): "refused", ("cap", "refuse"): "refused",
            ("route", "deny"): "denied", ("limit", "refuse"): "limited", ("retry", "fail"): "failed"}


# ── 1. Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    """One request: read the body, refuse what is not a Messages call, run the chain, book, trace, answer."""
    protocol_version = "HTTP/1.1"
    server: "Gateway"

    def log_message(self, format, *args) -> None:
        pass                              # the trace says what matters

    def do_HEAD(self) -> None:
        # The Agent SDK's CLI probes HEAD /api/hello before its first call; answered here, never forwarded.
        self._send(200, b"", {})

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def _send(self, status: int, data: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for k, v in {**headers, "content-length": str(len(data))}.items():
            self.send_header(k, v)
        if self.close_connection:
            self.send_header("connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _read_body(self) -> tuple[bytes, str | None]:
        """The body, or why it is refused. Framing the gateway cannot trust closes the connection,
        so nothing left on the wire is read as a second request."""
        if self.headers.get("transfer-encoding") is not None:
            self.close_connection = True
            return b"", "chunked request bodies are not accepted"
        try:
            n = int(self.headers.get("content-length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY_BYTES:
            self.close_connection = True
            return b"", f"bad or oversized content-length (the limit is {MAX_BODY_BYTES} bytes)"
        return (self.rfile.read(n) if n else b""), None

    def _guard(self) -> bool:
        """No Origin header (a web page cannot drive the gateway) and Host must be this loopback port."""
        port = self.server.server_address[1]
        if self.headers.get("origin") is not None:
            why = "requests with an Origin header are refused"
        elif self.headers.get("host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            why = "Host must be 127.0.0.1 or localhost on this port"
        else:
            return True
        self.close_connection = True
        self._send(403, error_body("permission_error", f"gateway: {why}"), dict(JSON_HEADERS))
        return False

    def _serve(self) -> None:
        started = time.monotonic()
        raw, why = self._read_body()
        if not self._guard():
            return
        req = make_request(self.command, self.path, dict(self.headers.items()), raw)
        if why is None and req.method != "POST":
            why = "only POST is accepted"
        elif why is None and req.path.split("?")[0] not in PATHS:
            why = f"unknown path {req.path.split('?')[0]}; the gateway serves {', '.join(PATHS)}"
        srv = self.server
        try:
            with srv.lock:
                out = refused(why) if why else srv.cap_check(req) or srv.run(req)
                trace = srv.book(req, out, (time.monotonic() - started) * 1000)
        except Exception as e:            # the stores failed (a full disk): answer, never drop the connection
            self.close_connection = True
            return self._send(500, error_body("api_error", f"gateway: could not book the request ({type(e).__name__})"),
                              dict(JSON_HEADERS))
        headers = {**out.headers, "x-gateway-trace": trace["id"]}
        headers.setdefault("x-cache", "skip")
        self._send(out.status, out.body, headers)


def refused(why: str) -> Outcome:
    """A request refused before any stage: 400, one parse decision, nothing forwarded."""
    return Outcome(400, dict(JSON_HEADERS), error_body("invalid_request_error", f"gateway: {why}"),
                   [decision("parse", "refuse", why)], "untagged", "none", None, None, 0.0, False, [])


def outcome_word(out: Outcome) -> str:
    """ok | cached | refused | denied | limited | failed | error, from the decision that ended the chain."""
    if out.cached:
        return "cached"
    last = out.decisions[-1] if out.decisions else {}
    if last.get("verdict") == "error":
        return "error"
    return OUTCOMES.get((last.get("stage"), last.get("verdict")), "ok")


# ── 2. Forwarding ───────────────────────────────────────────────────────────
FORWARD_REQUEST = ("content-type", "anthropic-version", "anthropic-beta", "accept")
FORWARD_RESPONSE = ("content-type", "retry-after")   # the proxy's allow-list, plus what a 429 needs
USER_AGENT = "ai-engineering-course-sample/1.0"
LIVE_UPSTREAM = "https://api.anthropic.com"
LIVE_HOST = "api.anthropic.com"
TIMEOUT_S = 600.0


def make_send(upstream: str, api_key: str | None = None, timeout: float = TIMEOUT_S):
    """send(model, req) -> Upstream over http.client: the same method, path and body bytes, the allowed
    headers only (the gateway's x-team, x-request-tag and x-parent-id never leave). A connection that
    fails or drops is Upstream(error=...), never an exception. The key goes to api.anthropic.com only."""
    u = urlsplit(upstream)
    if api_key is not None and (u.scheme != "https" or u.hostname != LIVE_HOST):
        raise ValueError(f"the key is only ever sent to {LIVE_UPSTREAM}, not {upstream}")
    conn_class = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    base = u.path.rstrip("/")

    def send(model: str, req: Request) -> Upstream:
        headers = {k: req.headers[k] for k in FORWARD_REQUEST if k in req.headers}
        headers["user-agent"] = USER_AGENT
        if api_key is not None:
            headers["x-api-key"] = api_key
        conn = conn_class(u.hostname, u.port, timeout=timeout)
        try:
            conn.request(req.method, base + req.path, body=req.body, headers=headers)
            r = conn.getresponse()
            body = r.read()
            kept = {k: v for k in FORWARD_RESPONSE if (v := r.getheader(k)) is not None}
            return Upstream(r.status, kept, body)
        except (OSError, http.client.HTTPException) as e:
            return Upstream(error=f"connection failed: {type(e).__name__}")
        finally:
            conn.close()
    return send


# ── 3. Server ───────────────────────────────────────────────────────────────
class Gateway(ThreadingHTTPServer):
    """One State for the server (spend, cache, buckets), guarded by one lock: a request runs the chain,
    books and traces while holding it, so the stores see requests one at a time, in order."""
    daemon_threads = True

    def __init__(self, cfg: config.Config, port: int, state: State, ledger_path: Path, traces_dir: Path,
                 run: str, cache_path: Path, send, live: bool, max_cost: float | None, sleep):
        super().__init__((HOST, port), Handler)
        self.cfg, self.state, self.send, self.sleep = cfg, state, send, sleep
        self.prices = ledger.resolve_prices(cfg)    # a missing lab price stops the start, not a request
        self.ledger = ledger.Ledger(ledger_path)
        self.traces = traces.TraceStore(traces_dir, run)
        self.cache_path = Path(cache_path)
        self.live, self.max_cost, self.run_spent = live, max_cost, 0.0
        self.lock = threading.Lock()
        self.url = f"http://{HOST}:{self.server_address[1]}"

    def run(self, req: Request) -> Outcome:
        before = {k: id(e) for k, e in self.state.cache.items()}
        out = run_chain(req, self.state, self.cfg, self.send, self.prices, self.sleep)
        if {k: id(e) for k, e in self.state.cache.items()} != before:
            save_cache(self.cache_path, self.state.cache)
        return out

    def cap_check(self, req: Request) -> Outcome | None:
        """Live mode only: this run's spend plus the request's worst case against --max-cost."""
        if self.max_cost is None or req.json is None or not isinstance(req.json.get("model"), str):
            return None
        model = req.json["model"]
        target = self.cfg.routes[model].to if model in self.cfg.routes else model
        worst = ledger.worst_case_usd(target, *worst_case_tokens(req), self.prices)
        if self.run_spent + worst <= self.max_cost:
            return None
        why = f"${self.run_spent:.4f} spent this run + ${worst:.4f} worst case would pass --max-cost {self.max_cost}"
        return Outcome(402, dict(JSON_HEADERS), error_body("cost_cap", f"gateway: {why}; not called"),
                       [decision("cap", "refuse", why)], "untagged", "none", None, None, 0.0, False, [])

    def book(self, req: Request, out: Outcome, ms: float) -> dict:
        """Stage 7, the ledger row, and stage 8, the trace line. Every request gets both; an answered
        one also carries the ledger and trace decisions, a refused one stops at the stage that refused."""
        word = outcome_word(out)
        team = next((d["team"] for d in out.decisions if d["stage"] == "budget" and "team" in d), out.team)
        model = next((d["to"] for d in out.decisions if d["stage"] == "retry" and d.get("to")),
                     (req.json or {}).get("model") if isinstance((req.json or {}).get("model"), str) else None)
        usage = out.usage or {}
        decisions = list(out.decisions)
        if word in ("ok", "cached"):
            cost = "unpriced (no usage)" if out.cost_usd is None else f"${out.cost_usd:.6f}"
            decisions.append(decision("ledger", "booked" if out.cost_usd is not None else "unpriced",
                                      f"{cost} to team {team}", cost_usd=out.cost_usd))
            decisions.append(decision("trace", "written", f"{self.traces.run}.jsonl"))
        if out.cost_usd:
            self.run_spent += out.cost_usd
        now = time.time()
        self.ledger.append({"ts": now, "team": team, "tag": out.tag, "model": model,
                            **{k: usage.get(k) for k in ledger.USAGE_FIELDS}, "cost_usd": out.cost_usd,
                            "replayed": not self.live, "cached": out.cached, "status": out.status})
        trace = {"id": secrets.token_hex(8), "parent_id": req.headers.get("x-parent-id") or None, "ts": now,
                 "team": team, "tag": out.tag, "model": model, "path": req.path, "status": out.status,
                 "decisions": decisions, "ms": round(ms, 1), "usage": out.usage, "cost_usd": out.cost_usd,
                 "cached": out.cached, "attempts": out.attempts, "outcome": word}
        self.traces.write(trace)
        return trace


def make_server(cfg: config.Config, port: int = 0, state: State | None = None, ledger_path: Path | None = None,
                traces_dir: Path | None = None, run: str = "run", *, cache_path: Path | None = None,
                send=None, api_key: str | None = None, live: bool = False, max_cost: float | None = None,
                sleep=time.sleep) -> Gateway:
    """Build (not start) a gateway on 127.0.0.1 (port 0 picks a free one; server.url says which).
    Without a state, the cache is loaded from cache_path and each team's spend from the ledger, so a
    restart keeps both. The upstream must be loopback unless live. `send` replaces the forwarder (tests)."""
    if not live and not config.is_loopback(cfg.upstream):
        raise ValueError(f"upstream host {urlsplit(cfg.upstream).hostname} is not loopback; only --live may "
                         "call a remote host")
    ledger_path = Path(ledger_path) if ledger_path else LEDGER_PATH
    cache_path = Path(cache_path) if cache_path else CACHE_PATH
    traces_dir = Path(traces_dir) if traces_dir else (cfg.traces_dir if cfg.traces_dir.is_absolute()
                                                      else ROOT / cfg.traces_dir)
    if state is None:
        state = State(spend=ledger.Ledger(ledger_path).spend_by_team(), cache=load_cache(cache_path))
    send = send or make_send(cfg.upstream, api_key)
    return Gateway(cfg, port, state, ledger_path, traces_dir, run, cache_path, send, live, max_cost, sleep)


# ── 4. Live mode ────────────────────────────────────────────────────────────
DEFAULT_MAX_COST = 1.00   # dollars per gateway run in live mode


def estimate_line(cfg: config.Config, max_cost: float) -> str:
    """Print and return what live mode may spend: the config's budgets and the run's cap."""
    teams = ", ".join(f"{t} ${b.limit_usd:.2f}" for t, b in cfg.budgets.items())
    untagged = "refused" if cfg.untagged == "refuse" else f"booked to {cfg.untagged}"
    line = (f"Live mode: requests go to {LIVE_UPSTREAM} and are paid. Budgets: {teams}; untagged "
            f"requests {untagged}. Cap: ${max_cost:.2f} for this run, checked before every call.")
    print(line, flush=True)
    return line


def confirm() -> bool:
    """True only after an interactive y; anything else (or no input at all) is no."""
    try:
        answer = input("Start the gateway and make paid calls? [y/N] ")
    except EOFError:
        answer = ""
    return answer.strip().lower() in ("y", "yes")


# ── 5. Command line ─────────────────────────────────────────────────────────
def cost_arg(text: str) -> float:
    """argparse type for --max-cost, as the agents proxy's: a finite number above 0."""
    try:
        value = float(text)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite number above 0")
    return value


def env_port(name: str, default: int) -> int:
    """A port from the environment when set (the lessons' Try-it test runs servers on free ports), else the
    default. A --port flag still wins: this is only its default."""
    return int(os.environ.get(name) or default)


def test_upstream(upstream: str) -> str:
    """The upstream with the course's loopback ports moved where INFRA_PROXY_PORT (8791, the agents proxy)
    or INFRA_INJECTOR_PORT (8793) say, when set; unchanged otherwise, and never for a remote host."""
    u = urlsplit(upstream)
    name = {8791: "INFRA_PROXY_PORT", 8793: "INFRA_INJECTOR_PORT"}.get(u.port or 0)
    if not name or not os.environ.get(name) or not config.is_loopback(upstream):
        return upstream
    return u._replace(netloc=f"{u.hostname}:{env_port(name, u.port)}").geturl()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="The AI gateway: a loopback HTTP server in the Messages API shape.")
    p.add_argument("--config", type=Path, default=config.GATEWAY_YAML, help="the gateway config (YAML)")
    p.add_argument("--port", type=int, default=env_port("INFRA_GATEWAY_PORT", DEFAULT_PORT))
    p.add_argument("--live", action="store_true", help=f"forward to {LIVE_UPSTREAM} (estimate, then y/N)")
    p.add_argument("--max-cost", type=cost_arg, default=DEFAULT_MAX_COST, help="dollars per run in live mode")
    p.add_argument("--run", default=None, metavar="NAME", help="trace file name (default: the start time)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    try:
        cfg = config.load(a.config, live=True)   # the loopback rule is checked below, naming the host
    except (config.ConfigError, OSError) as e:
        print(f"gateway: config error: {e}. Nothing was started.")
        return 2
    if not a.live:
        cfg = dataclasses.replace(cfg, upstream=test_upstream(cfg.upstream))
    host = urlsplit(cfg.upstream).hostname
    if not a.live and not config.is_loopback(cfg.upstream):
        print(f"gateway: upstream host {host} is not loopback; only --live may call a remote host. "
              "Nothing was started.")
        return 2
    api_key = None
    if a.live:
        if cfg.upstream.rstrip("/") != LIVE_UPSTREAM:
            print(f"gateway: live mode forwards to {LIVE_UPSTREAM} only, not {host}. Nothing was started.")
            return 2
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("gateway: live mode needs ANTHROPIC_API_KEY in the environment. Nothing was started.")
            return 2
        estimate_line(cfg, a.max_cost)
        if not confirm():
            print("Not confirmed. The gateway was not started and nothing was called.")
            return 0
    run = a.run or time.strftime("%Y%m%d-%H%M%S")
    try:
        server = make_server(cfg, a.port, run=run, api_key=api_key, live=a.live,
                             max_cost=a.max_cost if a.live else None)
    except (OSError, ValueError) as e:
        print(f"gateway: {e}. Nothing was started.")
        return 2
    print(f"gateway on {server.url} -> {cfg.upstream}  traces {server.traces.path.relative_to(ROOT)}"
          if server.traces.path.is_relative_to(ROOT) else f"gateway on {server.url} -> {cfg.upstream}", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
