"""Lumen MCP client (Python): discover, list tools, call tools, read resources, get prompts.

Usage:  python mcp/python/client.py (--stdio | --url URL [--token TOKEN]) [--yes | --no] <command>
          --stdio   spawn mcp/python/read_server.py and talk over stdio
          --url     talk to a Streamable HTTP server, e.g. http://127.0.0.1:8765/mcp
          --token   an OAuth access token for that server, sent as `Authorization: Bearer` on every
                    request (never in the URL). Only with --url.
          --yes     answer every input-required (elicitation) round "accept" without asking
          --no      answer every round "decline" without asking
          (neither) ask on the terminal: the prompt goes to stderr, the answer is read from stdin (y/N)

Commands, and the one JSON shape each prints on stdout (sorted keys, 2-space indent, no timestamps).
The Go client (mcp/go/client) prints the same bytes for the same command:
  discover          {"instructions", "prompts": [names], "protocol_version", "resource_templates": [uriTemplates],
                     "resources": [uris], "server": name, "tools": [names]}   (lists sorted by name/URI)
  tools             [{"annotations", "description", "inputSchema", "name", "outputSchema", "title"}, ...] sorted by name.
                     Schemas are normalised to their shape (type / properties / required / items /
                     additionalProperties), so each SDK's decoration (titles, $defs, defaults) does not show.
  (integral floats print as integers: 1.0 is 1, as the contract compares them)
  call NAME JSON    {"structured": ...} or {"error": text}        (shapes of mcp/fixtures/README.md)
  read URI          {"contents": [{"uri", "mimeType", "json"}]} or {"error": message}
  prompt NAME JSON  {"contents": [{"role", "text"}]} or {"error": message}
  An error result with no text content prints {"error": "<no text>"}. A resource entry whose text is not JSON
  prints {"uri", "mimeType", "text"} instead of "json". Prompt arguments must be JSON strings (else exit 1).

Normalisations both clients apply so the output is identical (the Python side does the work here, the Go
side does the same in mcp/go/client/main.go):
  1. Schema decoration is stripped (normalise_schema): only type / properties / required / items /
     additionalProperties survive, $ref is resolved, "null" leaves type unions.
  2. Annotation hints readOnlyHint / idempotentHint are dropped when false (Go's struct cannot say "absent").
  3. Integral floats print as integers (canon): 1.0 becomes 1, because Go decodes every number to float64.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, TextIO

from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import ElicitResult

HERE = Path(__file__).resolve().parent

# ── 1. Configuration ────────────────────────────────────────────────────────
READ_SERVER = HERE / "read_server.py"
# Schema keys that carry shape. Everything else (title, default, $defs, description) is SDK decoration.
FALSE_IS_ABSENT = ("readOnlyHint", "idempotentHint")
SCHEMA_SHAPE_KEYS = ("type", "properties", "required", "items", "additionalProperties")


def stdio_params() -> StdioServerParameters:
    """Spawn lumen-read with the interpreter that runs this client."""
    return StdioServerParameters(command=sys.executable, args=[str(READ_SERVER)])


# ── 2. Connecting (stdio / HTTP) ────────────────────────────────────────────
@dataclass(frozen=True)
class HttpTarget:
    """A Streamable HTTP endpoint plus the headers every request carries."""
    url: str
    headers: dict[str, str]


def http_target(url: str, token: str | None = None) -> Any:
    """The one place the HTTP path is built. With a token, every request carries
    `Authorization: Bearer <token>`; the URL is left exactly as given (a token never goes in it)."""
    if token is None:
        return url
    return HttpTarget(url, {"Authorization": f"Bearer {token}"})


@asynccontextmanager
async def connect(target: Any, answer: "Answerer | None" = None):
    """Open a Client on a StdioServerParameters, an http(s) URL, an HttpTarget, or (in tests) an
    in-process server. The elicitation callback is what declares the `elicitation` capability, so
    a server's confirmation step only runs when the client can answer it."""
    callback = elicitation_callback(answer or deny)
    if isinstance(target, HttpTarget):
        # The SDK's own factory (MCP timeouts), from a private module verified on mcp==2.3.0.
        from mcp.shared._httpx_utils import create_mcp_http_client
        async with create_mcp_http_client(headers=target.headers) as http:
            async with Client(streamable_http_client(target.url, http_client=http),
                              elicitation_callback=callback) as client:
                yield client
        return
    async with Client(target, elicitation_callback=callback) as client:
        yield client


# ── 3. Discovery ────────────────────────────────────────────────────────────
def normalise_schema(schema: dict | None, defs: dict | None = None) -> dict | None:
    """Reduce a JSON Schema to its shape: resolve $ref, keep only SCHEMA_SHAPE_KEYS, drop "null"
    from type unions, and drop boolean additionalProperties. Python and Go print the same result."""
    if schema is None:
        return None
    defs = defs if defs is not None else schema.get("$defs", {})
    if "$ref" in schema:
        return normalise_schema(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    out: dict[str, Any] = {}
    for key in SCHEMA_SHAPE_KEYS:
        if key not in schema:
            continue
        value = schema[key]
        if key == "type" and isinstance(value, list):
            value = [t for t in value if t != "null"]
            value = value[0] if len(value) == 1 else sorted(value)
        elif key == "properties":
            value = {k: normalise_schema(v, defs) for k, v in value.items()}
        elif key == "items":
            value = normalise_schema(value, defs)
        elif key == "additionalProperties":
            if not isinstance(value, dict):
                continue
            value = normalise_schema(value, defs)
        elif key == "required":
            value = sorted(value)
        out[key] = value
    return out


def normalise_annotations(annotations: Any) -> dict | None:
    """camelCase hints as sent, minus the two plain bools when false (Go's struct cannot say "absent")."""
    if annotations is None:
        return None
    hints = annotations.model_dump(by_alias=True, exclude_none=True)
    return {k: v for k, v in hints.items() if not (k in FALSE_IS_ABSENT and v is False)}


async def discover(client: Client) -> dict:
    tools = await client.list_tools()
    resources = await client.list_resources()
    templates = await client.list_resource_templates()
    prompts = await client.list_prompts()
    return {
        "server": client.server_info.name if client.server_info else None,
        "protocol_version": client.protocol_version,
        "instructions": client.instructions,
        "tools": sorted(t.name for t in tools.tools),
        "resources": sorted(str(r.uri) for r in resources.resources),
        "resource_templates": sorted(t.uri_template for t in templates.resource_templates),
        "prompts": sorted(p.name for p in prompts.prompts),
    }


async def list_tools(client: Client) -> list[dict]:
    result = await client.list_tools()
    return [{
        "name": t.name,
        "title": t.title,
        "description": t.description,
        "annotations": normalise_annotations(t.annotations),
        "inputSchema": normalise_schema(t.input_schema),
        "outputSchema": normalise_schema(t.output_schema),
    } for t in sorted(result.tools, key=lambda t: t.name)]


# ── 4. Calls, resources and prompts ─────────────────────────────────────────
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


# ── 5. Answering input-required rounds ──────────────────────────────────────
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


# ── 6. Entry point ──────────────────────────────────────────────────────────
def canon(value: Any) -> Any:
    """Print an integral float (1.0) as an integer (1), as Go does; the contract treats them as equal."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 2**53:
        return int(value)
    if isinstance(value, dict):
        return {k: canon(v) for k, v in value.items()}
    if isinstance(value, list):
        return [canon(v) for v in value]
    return value


def dump(value: Any) -> str:
    return json.dumps(canon(value), sort_keys=True, indent=2, ensure_ascii=True)


class UsageError(Exception):
    pass


def json_object(args: list[str], i: int) -> dict:
    if len(args) <= i:
        return {}
    try:
        obj = json.loads(args[i])
    except ValueError:
        obj = None
    if not isinstance(obj, dict):
        raise UsageError("arguments are not a JSON object")
    return obj


def check_args(command: str, args: list[str]) -> None:
    """Reject bad arguments before connecting (an error raised inside the client's task group
    would surface as an ExceptionGroup, not a clean usage message)."""
    need = {"call": 1, "prompt": 1, "read": 1}.get(command, 0)
    if len(args) < need:
        raise UsageError(f"{command} needs {need} argument(s)")
    if command in ("call", "prompt"):
        arguments = json_object(args, 1)
        bad = sorted(k for k, v in arguments.items() if command == "prompt" and not isinstance(v, str))
        if bad:
            raise UsageError(f"prompt arguments must be strings: {bad[0]}")


async def run(client: Client, command: str, args: list[str]) -> Any:
    if command == "discover":
        return await discover(client)
    if command == "tools":
        return await list_tools(client)
    if command == "call":
        return await call(client, args[0], json_object(args, 1))
    if command == "read":
        return await read(client, args[0])
    if command == "prompt":
        return await prompt(client, args[0], json_object(args, 1))
    raise SystemExit(f"unknown command: {command}")


async def amain(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description="Lumen MCP client")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--stdio", action="store_true", help="spawn read_server.py and use stdio")
    where.add_argument("--url", help="Streamable HTTP endpoint, e.g. http://127.0.0.1:8765/mcp")
    ap.add_argument("--token", help="OAuth access token for --url, sent as Authorization: Bearer")
    yn = ap.add_mutually_exclusive_group()
    yn.add_argument("--yes", action="store_true", help="accept input-required rounds")
    yn.add_argument("--no", action="store_true", help="decline input-required rounds")
    ap.add_argument("command", choices=["discover", "tools", "call", "read", "prompt"])
    ap.add_argument("args", nargs="*")
    ns = ap.parse_args(argv)
    if ns.token is not None and ns.url is None:
        ap.error("--token needs --url")
    answer = always(True) if ns.yes else always(False) if ns.no else ask_terminal()
    try:
        check_args(ns.command, ns.args)
        async with connect(stdio_params() if ns.stdio else http_target(ns.url, ns.token), answer) as client:
            print(dump(await run(client, ns.command, ns.args)))
    except UsageError as e:
        print(f"client: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(amain(sys.argv[1:]))
