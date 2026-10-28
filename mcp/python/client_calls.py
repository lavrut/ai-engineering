"""Lumen MCP client, part 3 of 3: calls, resources and prompts (section 1), and answering
input-required rounds (2). client.py re-exports every name here; the Go twin is mcp/go/client/calls.go.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Awaitable, Callable, TextIO

from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types import ElicitResult


# ── 1. Calls, resources and prompts ─────────────────────────────────────────
NO_TEXT = "<no text>"


async def call(client: Client, name: str, arguments: dict) -> dict:
    result = await client.call_tool(name, arguments)
    if result.is_error:
        texts = [getattr(c, "text", None) for c in result.content]
        return {"error": texts[0] if texts and isinstance(texts[0], str) else NO_TEXT}
    return {"structured": result.structured_content}


def resource_entry(c: Any) -> dict:
    """{"uri", "mimeType", "json"}; a non-JSON (or non-text) body prints "text" instead of "json"."""
    entry = {"uri": str(c.uri), "mimeType": c.mime_type}
    text = getattr(c, "text", None)
    try:
        entry["json"] = json.loads(text)
    except (TypeError, ValueError):
        entry["text"] = text
    return entry


async def read(client: Client, uri: str) -> dict:
    try:
        result = await client.read_resource(uri)
    except MCPError as e:
        return {"error": str(e)}
    return {"contents": [resource_entry(c) for c in result.contents]}


async def prompt(client: Client, name: str, arguments: dict) -> dict:
    try:
        result = await client.get_prompt(name, arguments)
    except MCPError as e:
        return {"error": str(e)}
    return {"contents": [{"role": m.role, "text": getattr(m.content, "text", None)} for m in result.messages]}


# ── 2. Answering input-required rounds ──────────────────────────────────────
# A server that needs a confirmation answers a call with an InputRequiredResult holding an
# elicitation request. The Client drives the retry; this callback supplies the answer. The answer
# is the client's own assertion: the server cannot tell a human from a script (--yes).
Answerer = Callable[[str], Awaitable[bool]]


async def deny(message: str) -> bool:
    return False


def always(value: bool) -> Answerer:
    async def answer(message: str) -> bool:
        return value
    return answer


def ask_terminal(stdin: TextIO | None = None, stderr: TextIO | None = None) -> Answerer:
    """Prompt on stderr (stdout stays clean JSON), read the answer from stdin. Only y/yes accepts."""
    async def answer(message: str) -> bool:
        err, inp = stderr or sys.stderr, stdin if stdin is not None else sys.stdin
        if inp is None:  # stdin is closed: nobody can answer, so decline
            return False
        err.write(f"{message} [y/N] ")
        err.flush()
        line = await asyncio.to_thread(inp.readline)
        return line.strip().lower() in ("y", "yes")
    return answer


def elicitation_callback(answer: Answerer):
    async def callback(context: Any, params: Any) -> ElicitResult:
        if not await answer(params.message):
            return ElicitResult(action="decline")
        # Accept: set every boolean field the form asks for to true. Any other field type is
        # not something a y/N prompt can fill, so it is declined rather than guessed.
        props = (params.requested_schema or {}).get("properties", {})
        if any(p.get("type") != "boolean" for p in props.values()):
            return ElicitResult(action="decline")
        return ElicitResult(action="accept", content={name: True for name in props})
    return callback
