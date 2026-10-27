"""lumen-ops — the Lumen write server: label a ticket, only after the user confirms (Python).

One tool, `label_ticket(id, label)`. It validates first, then asks the user one yes/no question
through a form elicitation, and appends one label row with provenance only on an accepted "yes".
The Go server (mcp/go/opsserver) answers the same shared contract, mcp/fixtures/contract.jsonl.

What the confirmation proves: only that the retry came from the same caller, for the same
arguments, within five minutes, on a state this server issued and has not seen answered before.
Whether a human or a script said "yes" is the client's claim; `confirmed=True` records the claim.

HTTP only by design (a write server is not a subprocess any host can spawn). Tests use the
in-memory server from build_ops_server().

Run:  python mcp/python/ops_server.py [--port 8766] [--labels PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any, TypedDict

import anyio.to_thread
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.elicitation import DeclinedElicitation, ElicitationResult
from mcp.server.mcpserver.resolve import Elicit, Resolve
from mcp.server.request_state import RequestStateBoundary, RequestStateSecurity
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "python"))
from lumen_core import CATEGORIES, LABELS_PATH, MAX_BODY_BYTES, tickets  # noqa: E402
from lumen_core.labels import write_label  # noqa: E402

# ── 1. Configuration and helpers ────────────────────────────────────────────
INSTRUCTIONS = (
    "Lumen ticket labelling. label_ticket writes a label only after the user confirms it; "
    "a declined or cancelled confirmation writes nothing."
)
DEFAULT_PORT = 8766
STATE_TTL_SECONDS = 300  # a confirmation round must finish within five minutes
ANONYMOUS = "anonymous"  # client_id when no token identifies the caller (auth arrives in Task 8)
# Appending a label changes data but destroys nothing; it is not idempotent (each call adds a row).
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)

# The fixed error texts. Identical in the Go server; the contract pins them.
ERR_LABEL = "label must be one of: " + ", ".join(CATEGORIES)
DECLINED = "label not written: the user declined"


def err_unknown(id: str) -> str:
    return f"unknown ticket: {id}"


def fail(message: str) -> CallToolResult:
    """A tool error the model can read. CallToolResult(is_error=True), not ToolError: the SDK
    prefixes a ToolError with 'Error executing tool ...', and the text must stay exactly fixed."""
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def _ok(structured: dict) -> CallToolResult:
    """Structured content plus a JSON text block (the same JSON) for clients that read only text."""
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(structured, ensure_ascii=False))],
                          structured_content=structured)


class LabelOut(TypedDict):
    ticket_id: str
    label: str
    written: bool


def validation_error(id: str, label: str) -> str | None:
    """The label first, then the ticket. Checked before anything is put to the user."""
    if label not in CATEGORIES:
        return ERR_LABEL
    try:
        tickets.get(id)
    except KeyError:
        return err_unknown(id)
    return None


# ── 2. The confirmation question ────────────────────────────────────────────
class Confirm(BaseModel):
    """The form: one boolean. strict, so "yes" or 1 is not taken for true."""
    model_config = ConfigDict(strict=True)
    confirm: bool


def ask_confirm(id: str, label: str) -> Elicit[Confirm] | DeclinedElicitation:
    """Resolver: runs before the tool body. Invalid input is never put to the user; it resolves
    to a decline without asking, and the body then returns the validation error."""
    if validation_error(id, label) is not None:
        return DeclinedElicitation()
    return Elicit(f'Set ticket {id} to "{label}"?', Confirm)


# ── 3. Single-use confirmation states ───────────────────────────────────────
# The SDK seals requestState (AES-256-GCM) and checks expiry, tool, arguments, audience and
# caller, but a sealed state stays valid for its whole TTL: sending the same accepted retry
# twice would write twice. So the server spends each state the first time an answer for it
# reaches the tool body, and remembers it for one TTL (after that the SDK rejects it as expired).
class SpentStates:
    """Digests of answered states, each kept for `ttl` seconds. In memory, one process: a
    multi-worker deployment would need a shared store (and shared state keys) instead."""

    def __init__(self, ttl: float = STATE_TTL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl, self._clock = ttl, clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def spend(self, state: str) -> bool:
        """True the first time a state is spent; False for every replay within the TTL."""
        key = hashlib.sha256(state.encode()).hexdigest()
        now = self._clock()
        with self._lock:
            self._seen = {k: t for k, t in self._seen.items() if now - t <= self._ttl}
            if key in self._seen:
                return False
            self._seen[key] = now
            return True

    def __len__(self) -> int:
        return len(self._seen)


# The wire requestState of the current request. The SDK's boundary replaces it with the decrypted
# plaintext before the tool runs, and that plaintext is the same for every call with the same
# arguments; the sealed wire token is unique per issued state (fresh nonce), so it is the one to spend.
_wire_state: ContextVar[str | None] = ContextVar("lumen_ops_wire_state", default=None)


async def capture_wire_state(ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
    raw = ctx.params.get("requestState") if ctx.params else None
    token = _wire_state.set(raw if isinstance(raw, str) else None)
    try:
        return await call_next(ctx)
    finally:
        _wire_state.reset(token)


def state_security() -> RequestStateSecurity:
    """A per-process key (a restart invalidates open confirmations) and a five-minute TTL."""
    return RequestStateSecurity.ephemeral(ttl=STATE_TTL_SECONDS)


def build_ops_server(labels_path: Path = LABELS_PATH) -> MCPServer:
    mcp = MCPServer("lumen-ops", instructions=INSTRUCTIONS, request_state_security=state_security())
    # Middleware the caller adds runs inside the state boundary (it sees plaintext), so this one
    # goes in just before the boundary, where the sealed token is still on the request.
    boundary = next(i for i, m in enumerate(mcp.middleware) if isinstance(m, RequestStateBoundary))
    mcp.middleware.insert(boundary, capture_wire_state)
    spent = SpentStates()

    # ── 4. Tools ────────────────────────────────────────────────────────────
    @mcp.tool(annotations=WRITE)
    async def label_ticket(id: str, label: str,
                           answer: Annotated[ElicitationResult[Confirm], Resolve(ask_confirm)],
                           ) -> Annotated[CallToolResult, LabelOut]:
        """Label a ticket with one of the six categories. Asks the user to confirm; writes only on yes."""
        if (message := validation_error(id, label)) is not None:  # checked first, so the fixed text wins
            return fail(message)
        state = _wire_state.get()
        # None only when no state was echoed: a legacy-era client, answered mid-call by a real
        # elicitation/create (a 2026-07-28 client with no state is asked again, never reaches here).
        if state is not None and not spent.spend(state):
            return fail(DECLINED)  # a replay of an answered round: nothing written
        if answer.action != "accept" or not answer.data.confirm:
            return fail(DECLINED)
        token = get_access_token()  # None in-process and until Task 8 adds auth
        client_id = token.client_id if token is not None and token.client_id else ANONYMOUS
        await anyio.to_thread.run_sync(lambda: write_label(
            id, label, source="agent", client_id=client_id, model=None, confirmed=True, path=labels_path))
        out: LabelOut = {"ticket_id": id, "label": label, "written": True}
        return _ok(dict(out))

    return mcp


# ── 5. HTTP app and security ────────────────────────────────────────────────
def build_http_app(mcp: MCPServer | None = None):
    """Streamable HTTP ASGI app. host="127.0.0.1" turns on the SDK's DNS-rebinding protection
    (bad Host or Origin gets 403); the body cap is passed explicitly. No auth yet: Task 8 adds it."""
    mcp = mcp or build_ops_server()
    return mcp.streamable_http_app(host="127.0.0.1", max_request_body_size=MAX_BODY_BYTES)


# ── 6. Entry point ──────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="lumen-ops MCP server (Streamable HTTP on 127.0.0.1 only)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--labels", type=Path, default=LABELS_PATH, help="append-only label file")
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(build_http_app(build_ops_server(args.labels)), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
