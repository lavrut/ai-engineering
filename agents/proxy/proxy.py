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
    .venv-agents/bin/python agents/proxy/proxy.py                                # replay on 127.0.0.1:8790
    .venv-agents/bin/python agents/proxy/proxy.py --mode replay --set desk-langgraph   # replay one recording set
    read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY                       # record mode only; the key is not echoed
    .venv-agents/bin/python agents/proxy/proxy.py --mode record --set desk-hand --max-cost 2   # estimate, then y/N

The proxy is four modules, and `import proxy` reaches all of them: this one (configuration, the replay key, the
store, recorded responses, the command line), proxy_cost.py (the cap's worst case, the reply's usage, the
ledger, the estimate and the y/N), proxy_handler.py (one HTTP request: the guard, then replay or record) and
proxy_server.py (the server, make_server and serve). The parts import this file by the name `proxy`, so run
it as a script or put agents/proxy on the path; a copy loaded under another name gets its own parts.

Why the HTTP level: the hand-built desk, LangGraph and the Claude Agent SDK's bundled CLI all
speak the Messages API over HTTP, so one recorder covers all three without touching their code.
"""
from __future__ import annotations

import argparse
import math
import copy
import hashlib
import importlib
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

import httpx

# Prices live in the LLMs lab (import only, never edit).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "llms" / "python"))
from triage_lab import PRICES, call_cost  # noqa: E402

# The other three files sit beside this one; put their folder on the path, so they import even when this file is
# loaded by path from somewhere else (M2).
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# The other three files import this one's configuration, so `proxy.<name>` loads them on first use.
ELSEWHERE = {
    "proxy_cost": ("BadRequest", "check_body", "estimate_input_tokens", "worst_call", "sse_complete", "usage_tokens",
                   "usage_cost", "MESSAGES_PATHS", "USAGE_FIELDS", "CACHE_WRITE_MULTIPLIER", "CACHE_READ_MULTIPLIER",
                   "upstream_usage", "ledger_cost", "ledger_entry", "estimate_line", "NotConfirmed", "confirm"),
    "proxy_handler": ("Handler",),
    "proxy_server": ("ProxyServer", "make_server", "serve"),
}


def __getattr__(name: str) -> Any:
    for module, names in ELSEWHERE.items():
        if name in names:
            return getattr(importlib.import_module(module), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── 1. Configuration ────────────────────────────────────────────────────────
HOST = "127.0.0.1"                 # loopback only: the proxy is never reachable from the network
DEFAULT_PORT = 8790
UPSTREAM = "https://api.anthropic.com"   # used in record mode only
UPSTREAM_HOST = "api.anthropic.com"     # checked on every built upstream URL before sending
DEFAULT_MODEL = "claude-haiku-5-5"
MAX_TOKENS = 4000                  # the track's per-call output limit; the worst case never assumes less
MAX_TOKENS_LIMIT = 128000          # the largest max_tokens accepted (the Agent SDK's default request)
MAX_BODY_BYTES = 32 * 1024 * 1024  # the API's own request size limit; larger bodies are refused unread
MAX_ERROR_CHARS = 300             # a 500 names its exception in at most this many characters (reply and log)
MAX_CALLS = 12                     # at most this many paid calls per proxy run (one engine run)
DEFAULT_MAX_COST = 10.0            # dollars
EST_INPUT_TOKENS = 6000            # typical input per agent call, for the up-front estimate only
STORE_DIR = Path(__file__).resolve().parents[1] / "data" / "recordings"
HEADERS_ALLOWLIST = ("content-type",)    # the only response header stored; nothing auth-related
RECORD_COMMAND = ".venv-agents/bin/python agents/proxy/proxy.py --mode record"

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


# ── 4. Responses: record and play back ──────────────────────────────────────
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


# ── 5. Command line ─────────────────────────────────────────────────────────
def cost_arg(text: str) -> float:
    """argparse type for --max-cost: a finite number above 0 (nan would make every cap test false)."""
    try:
        value = float(text)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite number above 0")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Record/replay proxy for the Anthropic Messages API (127.0.0.1 only).")
    p.add_argument("--mode", choices=["replay", "record"], default="replay")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--store", default=str(STORE_DIR), help="recordings directory")
    p.add_argument("--set", dest="set_name", default=None, help="recording set: <store>/<set>/")
    p.add_argument("--max-cost", type=cost_arg, default=DEFAULT_MAX_COST, help="dollars; checked before every call")
    p.add_argument("--max-calls", type=int, default=MAX_CALLS, help="paid calls allowed in this run")
    p.add_argument("--yes", action="store_true", help="skip the y/N prompt (only after the user approved)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    from proxy_cost import NotConfirmed
    from proxy_server import make_server, serve
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
