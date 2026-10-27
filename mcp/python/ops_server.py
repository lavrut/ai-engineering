"""lumen-ops — the Lumen write server: label a ticket, only after the user confirms (Python).

One tool, `label_ticket(id, label)`. It validates first, then asks the user one yes/no question
through a form elicitation, and appends one label row with provenance only on an accepted "yes".
The Go server (mcp/go/opsserver) answers the same shared contract, mcp/fixtures/contract.jsonl.

What the confirmation proves: only that the retry came from the same caller, for the same
arguments, within five minutes, on a state this server issued and has not seen answered before.
Whether a human or a script said "yes" is the client's claim; `confirmed=True` records the claim.

HTTP only by design (a write server is not a subprocess any host can spawn), and always as an OAuth
resource server (scope tickets:write, audience http://127.0.0.1:8766/mcp; see resource_auth.py).
The label row's client_id is the verified token's client_id. Tests use the in-memory server from
build_ops_server(), which has no HTTP and so no bearer check.

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
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.resolve import Elicit, Resolve
from mcp.server.request_state import RequestStateBoundary, RequestStateSecurity
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, field_validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "python"))
from lumen_core import CATEGORIES, LABELS_PATH, MAX_BODY_BYTES, tickets  # noqa: E402
from lumen_core.labels import write_label  # noqa: E402
from resource_auth import OPS_RESOURCE, SCOPE_WRITE, resource_server  # noqa: E402

# ── 1. Configuration and helpers ────────────────────────────────────────────
INSTRUCTIONS = (
    "Lumen ticket labelling. label_ticket writes a label only after the user confirms it; "
    "a declined or cancelled confirmation writes nothing."
)
DEFAULT_PORT = 8766
STATE_TTL_SECONDS = 300  # a confirmation round must finish within five minutes
PRUNE_MARGIN_SECONDS = 60  # a spent state is forgotten only this long after its own expiry
ANONYMOUS = "anonymous"  # client_id when no token identifies the caller (in-memory tests only)
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
    """The form: one boolean. Only the JSON boolean true confirms: anything else ("yes", 1, null)
    is read as false, so the tool answers with the fixed decline text, as the Go server does,
    instead of failing schema validation. The schema the user sees is unchanged: a boolean."""
    model_config = ConfigDict(strict=True)
    confirm: bool

    @field_validator("confirm", mode="before")
    @classmethod
    def only_true_confirms(cls, v: object) -> bool:
        return v is True


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
# reaches the tool body, and remembers it until the state's own expiry (plus a margin): after
# that the SDK rejects the state as expired, on the same wall clock (time.time) used here.
class SpentStates:
    """Digests of answered states, each kept until its state's `exp` + `margin`. In memory, one
    process: a multi-worker deployment would need a shared store (and shared state keys) instead."""

    def __init__(self, margin: float = PRUNE_MARGIN_SECONDS, clock: Callable[[], float] = time.time) -> None:
        self._margin, self._clock = margin, clock
        self._seen: dict[str, float] = {}  # digest -> the state's own exp
        self._lock = threading.Lock()

    def spend(self, state: str, expires_at: float) -> bool:
        """True the first time a state is spent; False for every replay while it is remembered."""
        key = hashlib.sha256(state.encode()).hexdigest()
        with self._lock:
            now = self._clock()
            self._seen = {k: exp for k, exp in self._seen.items() if now <= exp + self._margin}
            if key in self._seen:
                return False
            self._seen[key] = expires_at
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


def state_expiry(security: RequestStateSecurity, wire: str) -> float | None:
    """The `exp` the SDK sealed into this state, or None if it does not open. The boundary has
    already verified the state by the time the tool runs; this only reads its expiry."""
    try:
        exp = json.loads(security.codec.unseal(wire))["exp"]
    except Exception:  # noqa: BLE001 - any failure: no expiry, so the caller refuses
        return None
    return float(exp) if isinstance(exp, int | float) else None


def install_capture(mcp: MCPServer) -> None:
    """Put capture_wire_state immediately before the SDK's RequestStateBoundary (ruling R21).
    Middleware added the usual way runs inside the boundary and sees only plaintext. If the
    boundary is not there (a later SDK moved it), refuse to build: fail closed, loudly."""
    at = next((i for i, m in enumerate(mcp.middleware) if isinstance(m, RequestStateBoundary)), None)
    if at is None:
        raise RuntimeError("RequestStateBoundary not found: replay guard cannot be installed")
    mcp.middleware.insert(at, capture_wire_state)


def build_ops_server(labels_path: Path = LABELS_PATH, *, auth: bool = False) -> MCPServer:
    """auth=True makes it a resource server (required by build_http_app); in-memory tests use False."""
    security = state_security()
    mcp = MCPServer("lumen-ops", instructions=INSTRUCTIONS, request_state_security=security,
                    **(resource_server(OPS_RESOURCE, SCOPE_WRITE) if auth else {}))
    install_capture(mcp)
    spent = SpentStates()

    # ── 4. Tools ────────────────────────────────────────────────────────────
    @mcp.tool(annotations=WRITE)
    async def label_ticket(id: str, label: str,
                           answer: Annotated[ElicitationResult[Confirm], Resolve(ask_confirm)],
                           ctx: Context) -> Annotated[CallToolResult, LabelOut]:
        """Label a ticket with one of the six categories. Asks the user to confirm; writes only on yes."""
        if (message := validation_error(id, label)) is not None:  # checked first, so the fixed text wins
            return fail(message)
        wire = _wire_state.get()
        # A 2026-07-28 answer arrives with a requestState (ctx.request_state: the boundary's
        # plaintext). It must be spent here, which needs the sealed token the capture saw; if the
        # capture saw none, refuse rather than treat the round as stateless (R23.2). No state on
        # either side: a legacy-era client, answered mid-call by a real elicitation/create.
        if ctx.request_state is not None or wire is not None:
            exp = state_expiry(security, wire) if wire is not None else None
            if exp is None or not spent.spend(wire, exp):
                return fail(DECLINED)  # unseen, unreadable or already answered: nothing written
        if answer.action != "accept" or not answer.data.confirm:
            return fail(DECLINED)
        token = get_access_token()  # None only in-process (no HTTP, no bearer check)
        client_id = token.client_id if token is not None and token.client_id else ANONYMOUS
        await anyio.to_thread.run_sync(lambda: write_label(
            id, label, source="agent", client_id=client_id, model=None, confirmed=True, path=labels_path))
        out: LabelOut = {"ticket_id": id, "label": label, "written": True}
        return _ok(dict(out))

    return mcp


# ── 5. HTTP app and security ────────────────────────────────────────────────
def build_http_app(mcp: MCPServer | None = None):
    """Streamable HTTP ASGI app, always behind bearer auth (refuses a server built without it).
    host="127.0.0.1" turns on the SDK's DNS-rebinding protection (bad Host or Origin gets 403, for
    every method); the body cap is passed explicitly. The PRM is served at
    /.well-known/oauth-protected-resource/mcp. Order (ruling R9): the SDK checks the token before
    the Origin, so a bad Origin with no token is 401 and with a valid token 403 (Go: 403 both ways)."""
    mcp = mcp or build_ops_server(auth=True)
    if mcp.settings.auth is None:
        raise ValueError("lumen-ops serves HTTP only with auth: build_ops_server(auth=True)")
    return mcp.streamable_http_app(host="127.0.0.1", max_request_body_size=MAX_BODY_BYTES)


# ── 6. Entry point ──────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="lumen-ops MCP server (Streamable HTTP on 127.0.0.1 only)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--labels", type=Path, default=LABELS_PATH, help="append-only label file")
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(build_http_app(build_ops_server(args.labels, auth=True)), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
