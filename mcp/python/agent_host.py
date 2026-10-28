"""agent_host — what an MCP host adds to the harness: the bridge, the tools and the guard.

Part of agent.py (which re-exports every name here): the harness imported on first use (section 1),
the bridge from the harness's threads to the async MCP client (2), MCP tools as harness tools (3) and
the untrusted-read rule (4). Configuration comes from agent_config.py. A test that patches a name
used here (connect, CALL_TIMEOUT) patches this module, where the code looks it up.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_config import CALL_TIMEOUT, HARNESS_DIR  # also puts mcp/python on sys.path
from client import Answerer, always, connect, deny
from ops_server import build_ops_server  # puts mcp/core/python on sys.path
from read_server import build_read_server


# ── 1. The harness, imported on first use ───────────────────────────────────
def load_harness() -> Any:
    """Import harness/python/harness.py. Importing it creates an anthropic client (its section 1),
    so only the paths that run the agent call this; the estimate (--dry-run) never does."""
    if str(HARNESS_DIR) not in sys.path:
        sys.path.insert(0, str(HARNESS_DIR))
    import harness
    return harness


# ── 2. The bridge: synchronous Tool.fn over the async MCP client ────────────
# harness Tool.fn is a plain function returning a str, called from the executor's worker threads.
# The MCP Client is async, and its connection must be opened and closed in one task. So the bridge
# owns a private event loop on a daemon thread: one long-lived task opens every client and holds
# them; each tool call is submitted to that loop with run_coroutine_threadsafe and waited for.
class ToolError(Exception):
    """The server answered with isError. Raised so the harness hands the model an error result."""


class Bridge:
    def __init__(self, targets: dict[str, tuple[Any, Answerer]]) -> None:
        self._targets = targets
        self.clients: dict[str, Any] = {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-bridge", daemon=True)
        self._opened: concurrent.futures.Future = concurrent.futures.Future()
        self._held: concurrent.futures.Future | None = None
        self._stop: asyncio.Event | None = None

    def __enter__(self) -> "Bridge":
        self._thread.start()
        self._held = asyncio.run_coroutine_threadsafe(self._hold(), self._loop)
        try:
            self._opened.result(timeout=CALL_TIMEOUT)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    async def _hold(self) -> None:
        self._stop = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                for name, (target, answer) in self._targets.items():
                    self.clients[name] = await stack.enter_async_context(connect(target, answer))
                self._opened.set_result(None)
                await self._stop.wait()
        except BaseException as e:
            if not self._opened.done():
                self._opened.set_exception(e)
            raise

    def run(self, coro: Any) -> Any:
        """Run a coroutine on the bridge loop from any thread and return its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=CALL_TIMEOUT)

    def __exit__(self, *exc: Any) -> None:
        if self._held is not None and not self._held.done():
            opened = self._opened.done() and self._opened.exception() is None
            if opened and self._stop is not None:
                self._loop.call_soon_threadsafe(self._stop.set)  # close the clients in their own task
            else:
                self._held.cancel()  # still opening (hung or failed): cancel rather than wait
            concurrent.futures.wait([self._held], timeout=CALL_TIMEOUT)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=CALL_TIMEOUT)
        self._loop.close()


# ── 3. MCP tools as harness tools ───────────────────────────────────────────
@dataclass
class Session:
    """What the agent holds while the servers are open."""
    tools: list[Any]                       # the harness Tools registered for this session
    instructions: dict[str, str]           # server name -> its instructions (they go in the system prompt)
    read_untrusted: threading.Event = field(default_factory=threading.Event)  # this turn read ticket text

    @property
    def names(self) -> set[str]:
        return {t.name for t in self.tools}


def carries_untrusted(value: Any) -> bool:
    """True if a tool result has an `untrusted_body` field anywhere: it carried ticket text."""
    if isinstance(value, dict):
        return "untrusted_body" in value or any(carries_untrusted(v) for v in value.values())
    if isinstance(value, list):
        return any(carries_untrusted(v) for v in value)
    return False


UNTRUSTED_TOOLS = {"search_tickets", "get_ticket"}  # their results are ticket text, even when empty


def taints(name: str, result: Any) -> bool:
    """Does this tool result put untrusted text into the context? Fails closed: a ticket tool always
    does, so does any result without structured content (its text is unchecked), and so does any
    structured result with an untrusted_body anywhere in it."""
    return (name in UNTRUSTED_TOOLS or result.structured_content is None
            or carries_untrusted(result.structured_content))


def result_text(result: Any) -> str:
    """Structured content as canonical JSON (stable, so replay keys match), else the text blocks."""
    if result.structured_content is not None and not result.is_error:
        return json.dumps(result.structured_content, ensure_ascii=False, sort_keys=True)
    return "\n".join(t for c in result.content if isinstance(t := getattr(c, "text", None), str)) or "<no text>"


def make_fn(bridge: Bridge, server: str, name: str, session: Session) -> Callable[..., str]:
    def fn(**arguments: Any) -> str:
        result = bridge.run(bridge.clients[server].call_tool(name, arguments))
        if taints(name, result):
            session.read_untrusted.set()
        if result.is_error:
            raise ToolError(result_text(result))
        return result_text(result)
    return fn


SERVER_NAMES = {"read": "lumen-read", "ops": "lumen-ops"}


@contextmanager
def open_tools(servers: tuple[str, ...], *, labels_path: Path, elicit: str) -> Iterator[Session]:
    """Open the in-memory servers and make their tools the whole of harness.REGISTRY (rulings R1, R34:
    changed at runtime, harness.py untouched). The harness's own tools (list_files, read_file,
    write_file) are hidden for the session and restored on exit. A tool not marked readOnlyHint needs
    approval, so label_ticket goes through the harness's permission gate."""
    harness = load_harness()
    targets: dict[str, tuple[Any, Answerer]] = {}
    if "read" in servers:
        targets["read"] = (build_read_server(), deny)  # lumen-read never asks anything
    if "ops" in servers:  # the scripted answer to the server's own confirmation round
        targets["ops"] = (build_ops_server(labels_path=labels_path), always(elicit == "accept"))
    with Bridge(targets) as bridge:
        session = Session(tools=[], instructions={})
        for server, client in bridge.clients.items():
            session.instructions[SERVER_NAMES[server]] = client.instructions or ""
            for t in bridge.run(client.list_tools()).tools:
                read_only = bool(t.annotations and t.annotations.read_only_hint)
                session.tools.append(harness.Tool(t.name, t.description or "", t.input_schema,
                                                  make_fn(bridge, server, t.name, session),
                                                  needs_approval=not read_only))
        session.tools.sort(key=lambda t: t.name)
        saved = dict(harness.REGISTRY)
        harness.REGISTRY.clear()
        harness.REGISTRY.update({t.name: t for t in session.tools})
        try:
            yield session
        finally:
            harness.REGISTRY.clear()
            harness.REGISTRY.update(saved)


# ── 4. The untrusted-read rule (--guard) ────────────────────────────────────
# Ticket text is written by customers, and t061 carries an instruction aimed at the agent. A model
# that has read it may act on it. The rule: once any tool result in this turn carried
# untrusted text (section 3, taints), a write (a tool that needs approval) goes ahead only on a
# person's yes.
def needs_human(tool: Any, *, read_untrusted: bool, guard: bool) -> bool:
    """A write requested after untrusted text entered the context needs a yes. The harness asks for
    every approval in a response before it runs any of that response's tools, so a write requested
    in the same response as the first untrusted read is not guarded: the model had not seen the text."""
    return guard and read_untrusted and tool.needs_approval


def make_approver(session: Session, *, guard: bool,
                  human: Callable[[str, dict], bool]) -> Callable[[str, dict], bool]:
    """The harness asks this before every tool that needs approval. Without --guard it allows the
    call: that is the demonstration of the attack (the server's confirmation round still runs)."""
    tools = {t.name: t for t in session.tools}

    def approver(name: str, args: dict) -> bool:
        if name not in tools:  # not an MCP tool of this session (e.g. the harness's own write_file)
            return False
        if needs_human(tools[name], read_untrusted=session.read_untrusted.is_set(), guard=guard):
            return human(name, args)
        return True
    return approver


def scripted_human(approve: str) -> Callable[[str, dict], bool]:
    """The person the guard asks, scripted by --approve (separate from the server's --elicit)."""
    def human(name: str, args: dict) -> bool:
        allowed = approve == "accept"
        print(f"  ! guard: untrusted ticket text was read this turn; asking the person about "
              f"{name}({json.dumps(args)[:120]}) -> {'yes' if allowed else 'no'} (scripted)")
        load_harness().emit("permission", tool=name, allowed=allowed, guard=True)
        return allowed
    return human
