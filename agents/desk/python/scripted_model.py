"""scripted_model — a fake `call` that replays a list of canned replies in order. No network, no proxy.

Tests and the contract generator both use it, so Python and Go replay the same script. A script entry is
either a string (one text block) or {"tool_use": {"name": ..., "input": {...}}} (one tool_use block); every
even-numbered reply starts with a thinking block (reply_of).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def thinking_block(n: int) -> SimpleNamespace:
    """A thinking block as Haiku 5.5 sends one unasked (R59): empty text and a signature."""
    return SimpleNamespace(type="thinking", thinking="", signature=f"sig_{n:03d}")


def reply_of(entry: Any, n: int) -> SimpleNamespace:
    """Build an Anthropic-shaped message from one script entry; `n` numbers the tool_use id. Every even-numbered
    reply starts with a thinking block, as real replies often do (R59), so every desk must skip it when it reads
    the reply and send it back unmodified with the tool results."""
    lead = [thinking_block(n)] if n % 2 == 0 else []
    if isinstance(entry, str):
        block = SimpleNamespace(type="text", text=entry)
        return SimpleNamespace(content=lead + [block], stop_reason="end_turn")
    use = entry["tool_use"]
    block = SimpleNamespace(type="tool_use", id=f"toolu_{n:03d}", name=use["name"], input=use["input"])
    return SimpleNamespace(content=lead + [block], stop_reason="tool_use")


class ScriptedModel:
    """Each call pops the next entry. Running past the end is a test bug and raises."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, messages, *, system, tools=None, max_tokens=4000, trace=None, agent="desk"):
        n = len(self.calls)
        self.calls.append({"agent": agent, "system": system, "messages": messages, "tools": tools})
        if n >= len(self.script):
            raise AssertionError(f"script exhausted: call {n + 1} by {agent!r}")
        return reply_of(self.script[n], n)

    def unused(self) -> int:
        return len(self.script) - len(self.calls)
