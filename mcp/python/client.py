"""Lumen MCP client (Python): discover, list tools, call tools, read resources, get prompts.

Usage:  .venv-mcp/bin/python mcp/python/client.py (--stdio | --url URL [--token-file PATH | --token TOKEN]) [--yes | --no] <command>
          --stdio       spawn mcp/python/read_server.py and talk over stdio
          --url         talk to a Streamable HTTP server, e.g. http://127.0.0.1:8765/mcp
          --token-file  read the OAuth access token for that server from a file (first line, trimmed)
          --token       the token itself: works, but warns, because a command line is visible in the
                        process list and shell history
          LUMEN_TOKEN   (environment) the token, when neither flag is given
          Precedence: --token-file, then --token, then LUMEN_TOKEN. The token is sent as
          `Authorization: Bearer` on every request, never in the URL. Only with --url.
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
import os
import sys
import types
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mcp import Client, StdioServerParameters

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))  # the two other parts sit beside this file (also when loaded by path)

# The client is three files: this one (configuration, discovery, the entry point), client_connect.py
# and client_calls.py. Their names are re-exported here, so `import client` and `client.X` work as
# before.
from client_calls import (NO_TEXT, Answerer, always, ask_terminal, call, deny,  # noqa: E402,F401
                          elicitation_callback, prompt, read, resource_entry)
from client_connect import HttpTarget, Refusal, connect, http_target, refusal_line  # noqa: E402,F401
import client_calls  # noqa: E402  (the parts themselves, for the patch rule below)
import client_connect  # noqa: E402


# A patch on this module reaches the parts (M3). The names above are copies of the parts' names, so patching one
# here (monkeypatch.setattr(client, "deny", ...)) also sets it in every part that holds that name, and the
# patched value is what the code sees wherever it looks the name up. Undoing the patch sets the old value back
# the same way. Reading a name works as before.
class _Facade(types.ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        for part in _PARTS:
            if name in vars(part):
                setattr(part, name, value)
        super().__setattr__(name, value)


_PARTS = (client_connect, client_calls)
sys.modules[__name__].__class__ = _Facade


# ── 1. Configuration ────────────────────────────────────────────────────────
READ_SERVER = HERE / "read_server.py"
# Schema keys that carry shape. Everything else (title, default, $defs, description) is SDK decoration.
FALSE_IS_ABSENT = ("readOnlyHint", "idempotentHint")
SCHEMA_SHAPE_KEYS = ("type", "properties", "required", "items", "additionalProperties")


def stdio_params() -> StdioServerParameters:
    """Spawn lumen-read with the interpreter that runs this client."""
    return StdioServerParameters(command=sys.executable, args=[str(READ_SERVER)])


# ── 2. Discovery ────────────────────────────────────────────────────────────
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


# ── 3. Entry point ──────────────────────────────────────────────────────────
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


# Printed (to stderr) whenever --token is given. The Go client prints the same line.
TOKEN_FLAG_WARNING = ("client: warning: a token on the command line is visible in the process list and shell "
                      "history; use a token file or LUMEN_TOKEN")
# Printed (to stderr) before a token goes to a plain-http URL off this machine. The Go client prints the same line.
PLAIN_HTTP_WARNING = ("client: warning: the token goes over plain http to a host that is not this machine, "
                      "so anyone on the network path can read it; use https")


def plain_http_off_machine(url: str) -> bool:
    """True for an http:// URL whose host is not 127.0.0.1, localhost or ::1."""
    u = urlsplit(url)
    return u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1")


def resolve_token(token_file: str | None, token: str | None, env: str | None) -> tuple[str | None, bool]:
    """The token to send and whether to warn (a token was given on the command line).
    Precedence: --token-file, then --token, then LUMEN_TOKEN. Empty values count as absent."""
    warn = bool(token)
    if token_file:
        try:
            text = Path(token_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise UsageError(f"token file not readable: {token_file}") from None
        lines = text.strip().splitlines()
        if not lines or not lines[0].strip():
            raise UsageError(f"token file is empty: {token_file}")
        return lines[0].strip(), warn
    if token:
        return token, warn
    return (env or None), warn


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
    ap.add_argument("--token-file", help="file holding the OAuth access token for --url")
    ap.add_argument("--token", help="OAuth access token for --url (visible to other users: prefer --token-file)")
    yn = ap.add_mutually_exclusive_group()
    yn.add_argument("--yes", action="store_true", help="accept input-required rounds")
    yn.add_argument("--no", action="store_true", help="decline input-required rounds")
    ap.add_argument("command", choices=["discover", "tools", "call", "read", "prompt"])
    ap.add_argument("args", nargs="*")
    ns = ap.parse_args(argv)
    answer = always(True) if ns.yes else always(False) if ns.no else ask_terminal()
    try:
        if ns.stdio and (ns.token_file or ns.token):
            raise UsageError("a token needs --url / -url")
        token = None
        if ns.url is not None:  # LUMEN_TOKEN is ambient: used for HTTP only
            token, warn = resolve_token(ns.token_file, ns.token, os.environ.get("LUMEN_TOKEN"))
            if warn:
                print(TOKEN_FLAG_WARNING, file=sys.stderr)
            if token is not None and plain_http_off_machine(ns.url):
                print(PLAIN_HTTP_WARNING, file=sys.stderr)
        check_args(ns.command, ns.args)
    except UsageError as e:
        print(f"client: {e}", file=sys.stderr)
        raise SystemExit(1)
    refusal = Refusal()
    try:
        async with connect(stdio_params() if ns.stdio else http_target(ns.url, token), answer, refusal) as client:
            print(dump(await run(client, ns.command, ns.args)))
    except UsageError as e:
        print(f"client: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:  # noqa: BLE001 - the SDK raises ExceptionGroups; say it in one line
        # A failure the server explained with an HTTP status (401, 403, 413, a redirect...) is said
        # in one line, the same line the Go client prints; anything else as its innermost error.
        if refusal.status is not None:
            print(refusal_line(refusal.status), file=sys.stderr)
        else:
            print(f"client: {innermost(e)}", file=sys.stderr)
        raise SystemExit(1)


def innermost(e: BaseException) -> BaseException:
    """The first leaf of a (nested) exception group."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


if __name__ == "__main__":
    asyncio.run(amain(sys.argv[1:]))
