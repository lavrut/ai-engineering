#!/usr/bin/env python3
"""
proxy — the record/replay proxy every engine in the AI Agents track talks to.

Engines set ANTHROPIC_BASE_URL=http://127.0.0.1:8790 and never know the difference. In replay
mode (the default) every request is answered from recordings on disk: free, offline, and the
same bytes every time, streams included. A request with no recording gets a 404 that names the
command to record it; nothing is ever forwarded. In record mode the proxy prints a cost
estimate, asks y/N, then forwards each new request to api.anthropic.com with the key from the
environment, checks the cost cap before every call, and saves the reply.

Run:
    python agents/proxy/proxy.py                                  # replay on 127.0.0.1:8790
    python agents/proxy/proxy.py --mode replay --set langgraph    # replay one recording set
    export ANTHROPIC_API_KEY=sk-ant-...                           # record mode only
    python agents/proxy/proxy.py --mode record --set hand --max-cost 2   # estimate, then y/N

Why the HTTP level: the hand-built desk, LangGraph and the Claude Agent SDK's bundled CLI all
speak the Messages API over HTTP, so one recorder covers all three without touching their code.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

# Prices live in the LLMs lab (import only, never edit).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "llms" / "python"))
from triage_lab import PRICES, call_cost  # noqa: E402

# ── 1. Configuration ────────────────────────────────────────────────────────
HOST = "127.0.0.1"                 # loopback only: the proxy is never reachable from the network
DEFAULT_PORT = 8790
UPSTREAM = "https://api.anthropic.com"   # used in record mode only
UPSTREAM_HOST = "api.anthropic.com"     # checked on every built upstream URL before sending
DEFAULT_MODEL = "claude-haiku-5-5"
MAX_TOKENS = 4000                  # the track's per-call output limit; the worst case never assumes less
MAX_TOKENS_LIMIT = 128000          # the largest max_tokens accepted (the Agent SDK's default request)
MAX_BODY_BYTES = 32 * 1024 * 1024  # the API's own request size limit; larger bodies are refused unread
MAX_CALLS = 12                     # at most this many paid calls per proxy run (one engine run)
DEFAULT_MAX_COST = 10.0            # dollars
EST_INPUT_TOKENS = 6000            # typical input per agent call, for the up-front estimate only
STORE_DIR = Path(__file__).resolve().parents[1] / "data" / "recordings"
HEADERS_ALLOWLIST = ("content-type",)    # the only response header stored; nothing auth-related
RECORD_COMMAND = "python agents/proxy/proxy.py --mode record"

# Record mode forwards only these (method, path) pairs; anything else is a 400 (ruling R10).
RECORD_ALLOWLIST = {("POST", "/v1/messages"), ("POST", "/v1/messages?beta=true"),
                    ("POST", "/v1/messages/count_tokens")}

# Request headers never forwarded as-is: the auth ones are replaced by the environment key,
# the rest are hop-by-hop or recomputed by httpx.
DROP_HEADERS = {"host", "content-length", "connection", "keep-alive", "transfer-encoding",
                "accept-encoding", "x-api-key", "authorization", "proxy-authorization"}


# ── 2. The replay key ───────────────────────────────────────────────────────
# The key is SHA-256 of canonical JSON over {method, path, body} with volatile parts removed.
# Headers are never part of it (session ids, retry counts and the key itself live there).
# Volatile, per the Task 1 spike (docs/superpowers/research/2026-10-27-agents-spike.md):
#   - metadata.user_id: the Agent SDK puts a device id and a fresh session id in it per query().
#   - the Agent SDK's "# Environment" block: working directory, git flag, platform, shell, OS
#     version and today's date differ per machine and per day. Those lines are masked.
#   - the Agent SDK's sub-agent hand-back footer: agentId and duration_ms (below, Task 8).
VOLATILE_BODY_FIELDS: list[tuple[str, ...]] = [("metadata", "user_id")]
ENV_LINES = [
    (re.compile(r"^( - Primary working directory: ).*$", re.M), r"\1<cwd>"),
    (re.compile(r"^( - Is a git repository: ).*$", re.M), r"\1<git>"),
    (re.compile(r"^( - Platform: ).*$", re.M), r"\1<platform>"),
    (re.compile(r"^( - Shell: ).*$", re.M), r"\1<shell>"),
    (re.compile(r"^( - OS Version: ).*$", re.M), r"\1<os>"),
    (re.compile(r"^(Today's date is ).*$", re.M), r"\1<date>."),
]


# Later requests of an Agent SDK supervisor run carry each sub-agent's hand-back as the Agent tool's result. Its
# footer has a fresh `agentId: <hex>` and the sub-agent's wall-clock `duration_ms: <n>` on every run (verified
# against the bundled CLI 2.1.310 through a stub, Task 8), so without a mask no supervisor recording would ever
# replay (R47). Masked narrowly (R53): only that footer, at the very end of a tool_result whose tool_use is
# `Agent`; the report itself (even a `duration_ms:` line in it), subagent_tokens and tool_uses stay in the key.
# The CLI's trailing `<total_tokens>N tokens left</total_tokens>` system message is NOT masked: N is a fixed
# budget minus the tokens the recorded replies reported, so replay reproduces it (verified; it holds no date).
HANDBACK_TOOL = "Agent"
HANDBACK_FOOTER = re.compile(r"(\nagentId: )[0-9a-f]+(\n<usage>subagent_tokens: \d+\n(?:[a-z_]+: \d+\n)*duration_ms: )\d+"
                             r"(</usage>)\Z")


def _mask_handback(text: str) -> str:
    return HANDBACK_FOOTER.sub(r"\1<agent-id>\2<ms>\3", text)


def _mask_tool_results(content: Any, agent_ids: set[str]) -> Any:
    """A user message's Agent tool_result blocks with the hand-back footer masked (content: a string or text blocks)."""
    if not isinstance(content, list):
        return content
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in agent_ids:
            inner = b.get("content")
            if isinstance(inner, str):
                b = {**b, "content": _mask_handback(inner)}
            elif isinstance(inner, list):
                b = {**b, "content": [{**c, "text": _mask_handback(c["text"])}
                                      if isinstance(c, dict) and isinstance(c.get("text"), str) else c for c in inner]}
        out.append(b)
    return out


def _agent_tool_ids(messages: list) -> set[str]:
    """The ids of every `Agent` tool_use the assistant made: only their results can be hand-backs."""
    return {b.get("id") for m in messages if isinstance(m, dict) and m.get("role") == "assistant"
            and isinstance(m.get("content"), list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == HANDBACK_TOOL}


def _mask_env(text: str) -> str:
    for pattern, repl in ENV_LINES:
        text = pattern.sub(repl, text)
    return text


def _mask_content(content: Any) -> Any:
    """System text is a string or a list of blocks; mask the environment lines in either."""
    if isinstance(content, str):
        return _mask_env(content)
    if isinstance(content, list):
        return [{**b, "text": _mask_env(b["text"])} if isinstance(b, dict) and isinstance(b.get("text"), str)
                else b for b in content]
    return content


def normalize(body: dict) -> dict:
    """A copy of the body with the volatile fields removed and environment lines masked."""
    body = copy.deepcopy(body)
    for path in VOLATILE_BODY_FIELDS:
        node: Any = body
        for part in path[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(path[-1], None)
    if "system" in body:
        body["system"] = _mask_content(body["system"])
    agent_ids = _agent_tool_ids(body.get("messages") or [])
    for msg in body.get("messages") or []:
        if isinstance(msg, dict) and msg.get("role") == "system":
            msg["content"] = _mask_content(msg.get("content"))
        elif isinstance(msg, dict) and msg.get("role") == "user":
            msg["content"] = _mask_tool_results(msg.get("content"), agent_ids)
    return body


def request_summary(method: str, path: str, body: dict) -> dict:
    """What the key is computed over, and what a recording stores to explain itself."""
    return {"method": method.upper(), "path": path, "body": normalize(body)}


def request_key(method: str, path: str, body: dict) -> str:
    """SHA-256 hex of canonical JSON (sorted keys, no spaces) over {method, path, body}."""
    canonical = json.dumps(request_summary(method, path, body), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── 3. The store ────────────────────────────────────────────────────────────
class Store:
    """One JSON file per key: <dir>/<key>.json = {request_summary, response, upstream?}. A second recording
    of the same key replaces the first (latest wins); replay only reads, so it never grows. `upstream`
    (R62) is the recorded call's wall clock and usage, stored beside the response and never part of the key;
    a recording that predates it has none, and count_tokens recordings never get one."""

    def __init__(self, dir: str | Path):
        self.dir = Path(dir)

    def path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def find(self, key: str) -> dict | None:
        p = self.path(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def add(self, key: str, request_summary: dict, response: dict, upstream: dict | None = None) -> Path:
        """Write atomically (temp file, then rename) so a reader never sees half a recording."""
        self.dir.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {"request_summary": request_summary, "response": response}
        if upstream is not None:
            record["upstream"] = upstream
        tmp = self.dir / f".{key}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path(key))
        return self.path(key)

    def all(self) -> dict[str, dict]:
        if not self.dir.is_dir():
            return {}
        return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.dir.glob("*.json"))}


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out or {prefix: {}}
    if isinstance(obj, list):
        out = {}
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}[{i}]"))
        return out or {prefix: []}
    return {prefix: obj}


def closest_diff(store: Store, summary: dict) -> str:
    """Name the nearest recording and the fields that differ, so a miss caused by a new volatile
    field (an SDK upgrade adds an id) is easy to diagnose instead of silently matching."""
    mine = _flatten(summary)
    best: tuple[int, str, list[str]] | None = None
    for key, rec in store.all().items():
        theirs = _flatten(rec.get("request_summary", {}))
        diffs = sorted(p for p in mine.keys() | theirs.keys() if mine.get(p, ...) != theirs.get(p, ...))
        if best is None or (len(diffs), key) < (best[0], best[1]):
            best = (len(diffs), key, diffs)
    if best is None:
        return "no recordings in this set"
    _, key, diffs = best
    more = f" and {len(diffs) - 5} more" if len(diffs) > 5 else ""
    return f"closest recording {key[:8]} differs at {', '.join(diffs[:5])}{more}"


# ── 4. Cost ─────────────────────────────────────────────────────────────────
class BadRequest(ValueError):
    """The request is refused before any key lookup or call (answered 400)."""


def check_body(body: dict) -> None:
    """model must be a string; max_tokens, when present, an int in 1..MAX_TOKENS_LIMIT.
    Checked up front so a bad value can never make a negative or absurd reservation."""
    if "model" in body and not isinstance(body["model"], str):
        raise BadRequest("model must be a string")
    mt = body.get("max_tokens")
    if mt is not None and (isinstance(mt, bool) or not isinstance(mt, int) or not 1 <= mt <= MAX_TOKENS_LIMIT):
        raise BadRequest(f"max_tokens must be an integer from 1 to {MAX_TOKENS_LIMIT}")


def estimate_input_tokens(body: dict) -> int:
    """About four characters per token over the request JSON: rough, and on the high side."""
    return len(json.dumps(body, ensure_ascii=False)) // 4 + 1


def worst_call(body: dict) -> float:
    """Dollars if this call used every output token it may: input estimate at the input price plus
    max(request max_tokens, MAX_TOKENS) at the output price (ruling R14), never above the API limit."""
    model = body.get("model") or DEFAULT_MODEL
    out = min(max(int(body.get("max_tokens") or 0), MAX_TOKENS), MAX_TOKENS_LIMIT)
    return call_cost(model, estimate_input_tokens(body), out)


def _sse_data(events: list[str]) -> list[dict]:
    out = []
    for ev in events:
        for line in ev.splitlines():
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:])
                except ValueError:
                    continue
                if isinstance(data, dict):
                    out.append(data)
    return out


def _sse_event_names(events: list[str]) -> list[str]:
    return [line[6:].strip() for ev in events for line in ev.splitlines() if line.startswith("event:")]


def sse_complete(events: list[str]) -> bool:
    """A stream is a good recording only if it has no error event and reaches message_stop."""
    names = _sse_event_names(events)
    types = [d.get("type") for d in _sse_data(events)]
    return "error" not in names and "error" not in types and ("message_stop" in names or "message_stop" in types)


def usage_tokens(response: dict) -> tuple[int, int] | None:
    """(input, output) tokens from the reply's own usage (JSON body, or SSE message_start then message_delta), for
    the run's spend and so the cap: cache writes and reads count as full-price input (the conservative side; the
    ledger prices them apart, R64). None if the reply carries no usage."""
    usage_in = usage_out = None
    if "sse_events" in response:
        for data in _sse_data(response["sse_events"]):
            if data.get("type") == "message_start":
                u = data.get("message", {}).get("usage", {})
                usage_in = sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens"))
                usage_out = u.get("output_tokens") or 0
            elif data.get("type") == "message_delta" and isinstance(data.get("usage"), dict):
                usage_out = data["usage"].get("output_tokens", usage_out)
    elif isinstance(response.get("body"), dict) and isinstance(response["body"].get("usage"), dict):
        u = response["body"]["usage"]
        usage_in = sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                               "cache_read_input_tokens"))
        usage_out = u.get("output_tokens") or 0
    if usage_in is None:
        return None
    return usage_in, usage_out or 0


def usage_cost(model: str, response: dict) -> float | None:
    """Dollars from the reply's own usage (JSON body or SSE events); None if it carries none."""
    used = usage_tokens(response)
    return None if used is None else call_cost(model, *used)


# R62: what one served call cost and took, from the recording. Traces carry these (proxy_calls) so the eval's
# cost and latency come from the recording, never from what a particular run happened to pay (a store hit is
# free and takes ~1 ms; the work it stands for was not).
MESSAGES_PATHS = ("/v1/messages", "/v1/messages?beta=true")


# R64: the API's usage fields, stored verbatim and apart. input_tokens is the uncached input only.
USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
# R64: prompt-cache prices as multiples of the model's input price (Anthropic's published rates: a cache write
# costs 1.25x, a cache read 0.10x). The ledger prices a recording's cached input with these, so an engine whose
# requests hit the cache (the Agent SDK's CLI does, on most calls) is costed as it is billed. The estimates and the
# cap do not use them: they assume no caching, the conservative side.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def upstream_usage(response: dict) -> dict[str, int]:
    """The reply's four usage fields (JSON body, or SSE message_start then message_delta); a missing one is 0."""
    usage: dict[str, Any] = {}
    if "sse_events" in response:
        for data in _sse_data(response["sse_events"]):
            if data.get("type") == "message_start":
                usage.update(data.get("message", {}).get("usage") or {})
            elif data.get("type") == "message_delta" and isinstance(data.get("usage"), dict):
                usage.update({k: v for k, v in data["usage"].items() if v is not None})
    elif isinstance(response.get("body"), dict) and isinstance(response["body"].get("usage"), dict):
        usage = response["body"]["usage"]
    return {k: int(usage.get(k) or 0) for k in USAGE_FIELDS}


def ledger_cost(model: str | None, usage: dict) -> float | None:
    """Dollars for one recorded call at the lab's PRICES, cache tokens at their multipliers (R64); None when the
    model has no price or a field is unknown."""
    if model not in PRICES or any(usage.get(k) is None for k in USAGE_FIELDS):
        return None
    p_in, p_out = PRICES[model]
    return (usage["input_tokens"] * p_in + usage["cache_creation_input_tokens"] * CACHE_WRITE_MULTIPLIER * p_in
            + usage["cache_read_input_tokens"] * CACHE_READ_MULTIPLIER * p_in
            + usage["output_tokens"] * p_out) / 1_000_000


def ledger_entry(key: str, replayed: bool, upstream: dict | None) -> dict:
    """One ledger row: the recording's upstream ms and usage (all None when it predates them), priced with
    ledger_cost whether the call was replayed or live."""
    up = upstream or {}
    usage = {k: up.get(k) for k in USAGE_FIELDS}
    return {"key": key, "replayed": replayed, "ms": up.get("ms"), "model": up.get("model"), **usage,
            "cost_usd": ledger_cost(up.get("model"), usage)}


def estimate_line(model: str = DEFAULT_MODEL, calls: int = MAX_CALLS, max_cost: float = DEFAULT_MAX_COST) -> str:
    """Print and return the up-front estimate for one record run."""
    typical = calls * call_cost(model, EST_INPUT_TOKENS, 700)
    worst = calls * call_cost(model, EST_INPUT_TOKENS, MAX_TOKENS)
    line = (f"Estimated cost ${typical:.2f} for up to {calls} calls on {model} (worst case ${worst:.2f} "
            f"if every reply uses {MAX_TOKENS} tokens). Cap: ${max_cost:.2f}, checked before every call.")
    print(line, flush=True)
    return line


class NotConfirmed(Exception):
    """Record mode was not approved; no server was built and nothing was called."""


def confirm(yes: bool) -> bool:
    """True only after --yes or an interactive y. Stdin that is not a terminal counts as no."""
    if yes:
        return True
    answer = ""
    if sys.stdin.isatty():
        try:
            answer = input("Make these paid calls? [y/N] ")
        except EOFError:
            pass
    if answer.strip().lower() not in ("y", "yes"):
        print("Not confirmed. Nothing was called. Run it in a terminal and answer y after reading the estimate.", flush=True)
        return False
    return True


# ── 5. Responses: record and play back ──────────────────────────────────────
# JSON bodies replay semantically (re-serialised, so spacing may differ from upstream). SSE replays
# byte-exact, sent as one write: line-oriented clients see the same events, but the upstream's
# chunk boundaries are not reproduced. A body that is not UTF-8, or not a JSON object/array, is
# stored as raw text (latin-1 when it is not UTF-8) and replayed as exactly those bytes; a
# text/event-stream reply is stored only if it is UTF-8 and complete (see sse_complete).
_SSE_SPLIT = re.compile(r"(?<=\r\n\r\n)|(?<=\n\n)")


def split_sse(text: str) -> list[str]:
    """Split a stream into events, each keeping its trailing blank line, so that
    "".join(events) == text exactly. That is what makes replay byte-exact."""
    return [part for part in _SSE_SPLIT.split(text) if part]


def to_record(status: int, headers: httpx.Headers, content: bytes) -> dict:
    """The stored form of an upstream reply: status, allow-listed headers, body or SSE events."""
    kept = {h: headers[h] for h in HEADERS_ALLOWLIST if h in headers}
    rec: dict[str, Any] = {"status": status, "headers_allowlist": kept}
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None:
        rec["body_text"], rec["encoding"] = content.decode("latin-1"), "latin-1"
    elif kept.get("content-type", "").startswith("text/event-stream"):
        rec["sse_events"] = split_sse(text)
    else:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            rec["body"] = parsed
        else:
            rec["body_text"] = text
    return rec


def playback_bytes(response: dict) -> bytes:
    if "sse_events" in response:
        return "".join(response["sse_events"]).encode("utf-8")
    if "body_text" in response:
        return response["body_text"].encode(response.get("encoding", "utf-8"))
    return json.dumps(response["body"]).encode("utf-8")


# ── 6. The server ───────────────────────────────────────────────────────────
class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, *, mode: str, store: Store, set_name: str | None, max_cost: float,
                 max_calls: int, api_key: str | None, client: httpx.Client | None, record_command: str | None = None,
                 log_file: str | Path | None = None, refresh: bool = False):
        super().__init__((HOST, port), Handler)
        self.mode, self.store, self.set_name = mode, store, set_name
        self.max_cost, self.max_calls = max_cost, max_calls
        self._api_key = api_key           # held in memory only; never printed, never stored
        self.client = client
        self.lock = threading.Lock()
        self.key_locks: dict[str, threading.Lock] = {}
        self.spent = self.reserved = 0.0
        self.paid_calls = 0
        self.misses = 0                    # replay misses this run answered (a caller may have absorbed one, R59)
        self.cap_hit = False
        self._record_command = record_command
        self.log_file = Path(log_file) if log_file else None
        self.closed = False
        self.connections: dict[socket.socket, "Handler"] = {}   # open client connections, dropped on close (R59)
        self.calls: list[dict] = []        # R62: the ledger, one row per served /v1/messages call, in service order
        self.refresh = refresh             # R62: record mode goes upstream even on a store hit (re-record)
        # R65: a refreshed run's new recordings, held until the caller commits them (its run finished ok) or
        # discards them (it failed), so a failed refresh never leaves the store half-overwritten
        self.staged: dict[str, tuple[dict, dict, dict | None]] = {}

    def server_close(self) -> None:
        """Stop listening AND drop every idle open connection; a connection mid-request answers, then closes. A
        client that pools connections (langchain-anthropic keeps one httpx client per base URL) would otherwise send
        its next request, meant for the next proxy run on this port, down a kept-alive connection to this one,
        whose upstream client is closed (R59). A busy handler's reply is only guaranteed if the caller leaves
        `client` open until it finishes: run.py waits (bounded, wait_idle) before closing it; serve() does not wait."""
        self.closed = True
        with self.lock:
            conns = [c for c, h in self.connections.items() if not h.busy]
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        super().server_close()

    def commit_staged(self) -> int:
        """Write a refreshed run's staged recordings over the store (R65). Returns how many."""
        with self.lock:
            staged, self.staged = self.staged, {}
        for key, (summary, response, upstream) in staged.items():
            self.store.add(key, summary, response, upstream)
        return len(staged)

    def discard_staged(self) -> int:
        """Drop a failed refreshed run's staged recordings (R65); the store keeps the old ones. Returns how many."""
        with self.lock:
            n, self.staged = len(self.staged), {}
        return n

    def record_command(self) -> str:
        """What a miss tells the user to run. run.py names its own command (one proxy run per ticket)."""
        if self._record_command:
            return self._record_command
        return RECORD_COMMAND + (f" --set {self.set_name}" if self.set_name else "")

    def key_lock(self, key: str) -> threading.Lock:
        """One lock per key, so two identical concurrent misses pay once: the second waits,
        then finds the first one's recording."""
        with self.lock:
            return self.key_locks.setdefault(key, threading.Lock())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ProxyServer

    def log_message(self, format: str, *args: Any) -> None:
        pass                              # the default log line is noisy; _log says what matters

    busy = False      # handling a request now (server_close lets it answer, then the connection closes)

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
            with self.server.lock, open(self.server.log_file, "a", encoding="utf-8") as f:
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
        if self._read_body() is not None and self._guard(need_json=False):
            self._send(200, b"", {})

    def do_GET(self) -> None:
        self._safely()

    def do_POST(self) -> None:
        self._safely()

    def _safely(self) -> None:
        self.busy = True
        try:
            raw = self._read_body()
            if raw is not None and self.server.closed:   # a request that reached a run already closing (R59)
                self._error(503, "api_error", "proxy: this proxy run has ended; reconnect")
            elif raw is not None and self._guard(need_json=True):
                self._handle(raw)
        except Exception as e:            # answer, never drop the connection; say what failed (R59)
            what = f"{type(e).__name__}: {e}"
            self._log(f"{self.command} {self.path} internal error: {what}")
            try:
                self._error(500, "api_error", f"proxy: internal error ({what})")
            except OSError:
                pass
        finally:
            self.busy = False
            if self.server.closed:
                self.close_connection = True

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
        if refused is not None:            # logged outside srv.lock: _log takes it to append to proxy.log
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


def make_server(mode: str = "replay", port: int = DEFAULT_PORT, store_dir: str | Path = STORE_DIR,
                set_name: str | None = None, max_cost: float = DEFAULT_MAX_COST, max_calls: int = MAX_CALLS,
                transport: httpx.BaseTransport | None = None, yes: bool = False,
                record_command: str | None = None, estimate: bool = True,
                log_file: str | Path | None = None, refresh: bool = False) -> ProxyServer:
    """Build (not start) a proxy bound to 127.0.0.1. Record mode reads the key from the environment,
    prints the estimate and asks y/N here, before anything listens, so no caller can start a paying
    server unapproved (ruling R13); `yes` is the only bypass. `transport` is for tests only.
    `record_command` replaces the command a replay miss names (run.py passes its own). `estimate=False` skips the
    printed per-run estimate (run.py prints one for the whole set); the y/N gate still applies. `log_file` also
    appends every log line there (run.py keeps one per set). `refresh` (record mode only, R62) sends even a
    request already recorded upstream; its new recording is staged (served again within the run) and written over
    the store only by commit_staged(), when the caller's run finished ok (R65)."""
    if mode not in ("replay", "record"):
        raise ValueError(f"unknown mode {mode!r}")
    if refresh and mode != "record":
        raise ValueError("refresh re-records, so it needs record mode")
    if set_name is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", set_name):
        raise ValueError("--set takes letters, digits, '-' and '_' only")
    store = Store(Path(store_dir) / set_name if set_name else store_dir)
    api_key = client = None
    if mode == "record":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("record mode needs ANTHROPIC_API_KEY in the environment")
        if estimate:
            estimate_line(max_cost=max_cost, calls=max_calls)
        if not confirm(yes):
            raise NotConfirmed()
        client = httpx.Client(base_url=UPSTREAM, transport=transport,
                              timeout=httpx.Timeout(600.0, connect=10.0))
    return ProxyServer(port, mode=mode, store=store, set_name=set_name, max_cost=max_cost,
                       max_calls=max_calls, api_key=api_key, client=client, record_command=record_command,
                       log_file=log_file, refresh=refresh)


def serve(server: ProxyServer) -> int:
    """Serve until stopped. Returns 3 if the cost cap stopped it, else 0."""
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if server.client is not None:
            server.client.close()
    return 3 if server.cap_hit else 0


# ── 7. Command line ─────────────────────────────────────────────────────────
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Record/replay proxy for the Anthropic Messages API (127.0.0.1 only).")
    p.add_argument("--mode", choices=["replay", "record"], default="replay")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--store", default=str(STORE_DIR), help="recordings directory")
    p.add_argument("--set", dest="set_name", default=None, help="recording set: <store>/<set>/")
    p.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST, help="dollars; checked before every call")
    p.add_argument("--max-calls", type=int, default=MAX_CALLS, help="paid calls allowed in this run")
    p.add_argument("--yes", action="store_true", help="skip the y/N prompt (only after the user approved)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    args = parse_args(argv)
    try:
        server = make_server(args.mode, args.port, args.store, args.set_name, args.max_cost,
                             args.max_calls, transport, yes=args.yes)
    except NotConfirmed:
        return 1
    except ValueError as e:
        print(f"proxy: {e}. Nothing was called.")
        return 2
    print(f"proxy {args.mode} on http://{HOST}:{server.server_address[1]}  store {server.store.dir}", flush=True)
    return serve(server)


if __name__ == "__main__":
    sys.exit(main())
