#!/usr/bin/env python3
"""desk_core — what every Lumen desk engine (hand-built, LangGraph, Agent SDK) shares.

One ticket set, one Claude call through the record/replay proxy, one trace shape, one tool set
(lumen-read + lumen-ops over MCP) behind the Track 1 permission gate and the MCP track's
untrusted-read rule, and one registry the engines add themselves to. Keeping these here is what
lets the eval score three engines on equal terms.

Run:
    python agents/desk/python/desk_core.py --write    # regenerate agents/evals/tickets.jsonl
    python agents/desk/python/desk_core.py --check    # fail if the committed file has drifted
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "mcp" / "python"))

import agent as mcp_agent  # noqa: E402  (mcp/python/agent.py: import only, never edited)

# ── 1. Configuration ────────────────────────────────────────────────────────
PROXY_URL = "http://127.0.0.1:8790"   # agents/proxy/proxy.py: HOST and DEFAULT_PORT
DUMMY_KEY = "sk-ant-dummy"
MODEL = "claude-haiku-5-5"            # the track's default model
MAX_TOKENS = 4000                     # per call; matches the proxy's MAX_TOKENS
LUMEN_TICKETS = REPO / "llms" / "data" / "tickets.jsonl"
INJECTION_TICKETS = REPO / "mcp" / "data" / "injection.jsonl"
EVAL_TICKETS = REPO / "agents" / "evals" / "tickets.jsonl"
CATEGORIES = ("billing", "bug", "account", "how_to", "feature_request", "outage")
PER_CATEGORY = 2
READ_TOOLS = {"classify_complaint", "get_ticket", "search_tickets"}   # lumen-read
OPS_TOOLS = {"label_ticket"}                                          # lumen-ops


# ── 2. The eval tickets ─────────────────────────────────────────────────────
def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_eval_tickets() -> list[dict]:
    """The 2 lowest-id tickets of each Lumen category, in category order, then the injection
    ticket t061. Derived from the source files, never edited by hand."""
    lumen = sorted(_rows(LUMEN_TICKETS), key=lambda t: t["id"])
    picked = [t for c in CATEGORIES for t in [x for x in lumen if x["category"] == c][:PER_CATEGORY]]
    injection = [t for t in _rows(INJECTION_TICKETS) if t["id"] == "t061"]
    if len(picked) != len(CATEGORIES) * PER_CATEGORY or len(injection) != 1:
        raise RuntimeError("ticket sources do not hold 2 per category plus t061")
    return picked + injection


def _render(tickets: list[dict]) -> str:
    return "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tickets)


def write_eval_tickets(path: Path = EVAL_TICKETS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(load_eval_tickets()), encoding="utf-8")


def check_eval_tickets(path: Path = EVAL_TICKETS) -> bool:
    """True if the committed file is exactly what the selection rule produces (so it can't drift)."""
    return path.exists() and path.read_text(encoding="utf-8") == _render(load_eval_tickets())


TICKETS = load_eval_tickets()


# ── 3. The trace ────────────────────────────────────────────────────────────
KINDS = ("model", "tool", "handoff", "stop")   # stop: a loop hit its cap (R29); name says which cap
TRACE_FIELDS = ("agent", "kind", "name", "tokens_in", "tokens_out", "ms")


class Trace:
    """Append-only list of {agent, kind, name, tokens_in, tokens_out, ms}. A field an engine
    cannot fill is None, never omitted, so the eval reads every engine the same way."""

    def __init__(self) -> None:
        self._records: list[dict] = []

    def add(self, **fields: Any) -> None:
        unknown = set(fields) - set(TRACE_FIELDS)
        if unknown:
            raise ValueError(f"unknown trace field(s): {sorted(unknown)}")
        if fields.get("kind") not in KINDS:
            raise ValueError(f"trace kind must be one of {KINDS}, got {fields.get('kind')!r}")
        for required in ("agent", "name"):
            if not isinstance(fields.get(required), str):
                raise ValueError(f"trace field {required!r} must be a string")
        self._records.append({k: fields.get(k) for k in TRACE_FIELDS})

    def to_list(self) -> list[dict]:
        return [dict(r) for r in self._records]

    def __len__(self) -> int:
        return len(self._records)


# ── 4. Calling Claude through the proxy ─────────────────────────────────────
class ProxyError(RuntimeError):
    """The proxy refused (402 cost cap, 404 replay miss). Never retried: retrying cannot help."""


def make_client() -> Any:
    """Always the dummy key: the proxy injects the real one in record mode, so an engine never reads
    it. max_retries=0 because in record mode every SDK retry would be a separate paid proxy call."""
    import anthropic
    return anthropic.Anthropic(base_url=PROXY_URL, api_key=DUMMY_KEY, max_retries=0)


def claude(messages: list[dict], *, system: str, tools: list[dict] | None = None,
           max_tokens: int = MAX_TOKENS, model: str = MODEL,
           trace: Trace | None = None, agent: str = "desk", output_config: dict | None = None) -> Any:
    """One Messages API call. Tokens and latency go into `trace` when given. `output_config` (the API's
    structured output, e.g. {"format": {"type": "json_schema", "schema": ...}}) is sent only when given, so
    every other request body, and so its replay key, is unchanged."""
    import anthropic
    kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=system, messages=messages)
    if tools:
        kwargs["tools"] = tools
    if output_config is not None:
        kwargs["output_config"] = output_config
    start = time.perf_counter()
    try:
        reply = make_client().messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        if e.status_code in (402, 404):
            err = e.body.get("error") if isinstance(e.body, dict) else None
            err = err if isinstance(err, dict) else {}
            msg = err.get("message") if isinstance(err.get("message"), str) else None
            what = "replay miss" if e.status_code == 404 and err.get("type") == "replay_miss" else \
                   "cost cap" if e.status_code == 402 else "not found"
            text = f"proxy answered {e.status_code} ({what}): {msg or e}"
            print(text, file=sys.stderr)
            raise ProxyError(text) from e
        raise
    if trace is not None:
        usage = getattr(reply, "usage", None)
        trace.add(agent=agent, kind="model", name=model,
                  tokens_in=getattr(usage, "input_tokens", None),
                  tokens_out=getattr(usage, "output_tokens", None),
                  ms=round((time.perf_counter() - start) * 1000))
    return reply


# ── 5. The engine registry ──────────────────────────────────────────────────
@dataclass(frozen=True)
class Engine:
    """What an engine registers: its two desks, both `(ticket, *, call=..., approve=..., elicit=...,
    labels_path=...) -> dict`. `single` returns at least {reply, trace}; `supervisor` adds `handoffs`.
    run.py (Task 8) picks the variant by name, so every engine offers both."""
    single: Callable[..., dict]
    supervisor: Callable[..., dict]


ENGINES: dict[str, Any] = {}   # name -> Engine (a plain callable is still accepted)


def register(name: str, fn: Any) -> None:
    """Later tasks register `hand`, `langgraph` and `sdk`. A second registration is a bug."""
    if name in ENGINES:
        raise ValueError(f"engine {name!r} is already registered")
    ENGINES[name] = fn


# ── 6. Desk tools: MCP only, behind the permission gate ─────────────────────
@contextmanager
def open_desk_tools(servers: tuple[str, ...] = ("read", "ops"), *, guard: bool, approve: str = "decline",
                    elicit: str = "accept", labels_path: Path,
                    trace: Trace | None = None, agent: str = "desk") -> Iterator[tuple[list[dict], Callable[[str, dict], tuple[str, bool]], Any]]:
    """Yield (tools_schema, run_tool, session).

    The tools are the lumen-read and lumen-ops MCP tools and nothing else: open_tools hides the
    harness's file tools, and the schema is asserted to match. run_tool goes through the same gate as
    the MCP agent: a write needs approval, and with `guard` a write after an untrusted read needs a
    person's yes (`approve` scripts that person).

    run_tool returns (text, is_error): set is_error on the tool result as the harness's run_one does.
    Calls are serialized under a per-session lock, so an engine running sibling tool calls in
    parallel gets the same guard decisions every time (the order they arrive in still decides
    whether a write sees the taint, as in the MCP agent).

    One session per ticket: the untrusted-read flag is per session and never cleared, so reusing a
    session would carry one ticket's taint into the next. open_tools swaps the global harness
    REGISTRY, so sessions must not nest or overlap."""
    expected = ({t for t in READ_TOOLS} if "read" in servers else set()) | (OPS_TOOLS if "ops" in servers else set())
    with mcp_agent.open_tools(servers, labels_path=labels_path, elicit=elicit) as session:
        schema = [t.schema() for t in session.tools]
        names = {t["name"] for t in schema}
        if names != expected:
            raise RuntimeError(f"desk tools must be exactly {sorted(expected)}, got {sorted(names)}")
        tools = {t.name: t for t in session.tools}
        approver = mcp_agent.make_approver(session, guard=guard, human=mcp_agent.scripted_human(approve))

        lock = threading.Lock()

        def run_tool(name: str, args: dict) -> tuple[str, bool]:
            with lock:
                start = time.perf_counter()
                tool = tools.get(name)
                is_error = True
                if tool is None:
                    out = f"Unknown tool: {name}"
                elif tool.needs_approval and not approver(name, args):
                    out = "The user denied this action."
                else:
                    try:
                        out, is_error = tool.fn(**args), False
                    except Exception as e:  # a tool failure is information for the model, not a crash
                        out = f"{type(e).__name__}: {e}"
                if trace is not None:
                    trace.add(agent=agent, kind="tool", name=name, ms=round((time.perf_counter() - start) * 1000))
                return out, is_error

        yield schema, run_tool, session


# ── 7. Entry point ──────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help="regenerate agents/evals/tickets.jsonl")
    g.add_argument("--check", action="store_true", help="exit 1 if the committed file has drifted")
    a = p.parse_args(argv)
    if a.write:
        write_eval_tickets()
        print(f"wrote {EVAL_TICKETS.relative_to(REPO)} ({len(TICKETS)} tickets)")
        return 0
    ok = check_eval_tickets()
    print("tickets.jsonl matches the selection rule" if ok else "tickets.jsonl has drifted; run --write")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
