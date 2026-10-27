"""Tests for lumen-ops: the confirmed write, its provenance, single-use state, and the HTTP guards.

Every test writes to a tmp labels file, never to mcp/data/labels.jsonl.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types import ElicitResult, InputRequiredResult
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ops_server  # noqa: E402
from lumen_core import LABELS_PATH  # noqa: E402
from mcp.server.request_state import RequestStateBoundary  # noqa: E402
from ops_server import (DECLINED, ERR_LABEL, PRUNE_MARGIN_SECONDS, STATE_TTL_SECONDS, SpentStates,  # noqa: E402
                        build_http_app, build_ops_server, capture_wire_state)
from test_read_server import deep_equal  # noqa: E402
from auth_server import mint_test_token  # noqa: E402
from resource_auth import OPS_RESOURCE, SCOPE_WRITE  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "mcp" / "fixtures" / "contract.jsonl"
ACCEPT = ElicitResult(action="accept", content={"confirm": True})


def rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def answering(reply: ElicitResult | None, asked: list):
    """An elicitation callback that records what it was shown and replies `reply`
    (None: it must not be asked; if it is, it records that and declines)."""
    async def cb(context: Any, params: Any) -> ElicitResult:
        asked.append({"message": params.message, "requestedSchema": params.requested_schema})
        return reply if reply is not None else ElicitResult(action="decline")
    return cb


def call(path: Path, args: dict, reply: ElicitResult | None, **client_kw):
    async def go():
        asked: list = []
        async with Client(build_ops_server(path), elicitation_callback=answering(reply, asked), **client_kw) as c:
            return await c.call_tool("label_ticket", args), asked
    return asyncio.run(go())


# ── 1. The confirmed write and its provenance ───────────────────────────────
def test_label_accepted_writes_one_row(tmp_path):
    path = tmp_path / "labels.jsonl"
    r, asked = call(path, {"id": "t001", "label": "billing"}, ACCEPT)
    assert not r.is_error
    assert r.structured_content == {"ticket_id": "t001", "label": "billing", "written": True}
    assert json.loads(r.content[0].text) == r.structured_content
    assert asked == [{"message": 'Set ticket t001 to "billing"?', "requestedSchema": {
        "properties": {"confirm": {"title": "Confirm", "type": "boolean"}}, "required": ["confirm"],
        "type": "object"}}]
    [row] = rows(path)
    time = row.pop("time")
    assert row == {"ticket_id": "t001", "label": "billing", "source": "agent", "client_id": "anonymous",
                   "model": None, "confirmed": True}
    assert time.endswith("+00:00")


@pytest.mark.parametrize("reply", [ElicitResult(action="decline"), ElicitResult(action="cancel"),
                                   ElicitResult(action="accept", content={"confirm": False})],
                         ids=["decline", "cancel", "confirm_false"])
def test_label_declined_writes_nothing(tmp_path, reply):
    path = tmp_path / "labels.jsonl"
    r, asked = call(path, {"id": "t001", "label": "bug"}, reply)
    assert r.is_error and r.content[0].text == DECLINED == "label not written: the user declined"
    assert r.structured_content is None
    assert len(asked) == 1
    assert rows(path) == []


def test_accept_without_confirm_key_is_declined(tmp_path):
    """R27: an accepted answer with no `confirm` at all is a "no": the fixed decline, as in Go."""
    path = tmp_path / "labels.jsonl"
    for content in ({}, {"other": True}):
        r, _ = call(path, {"id": "t001", "label": "bug"}, ElicitResult(action="accept", content=content))
        assert r.is_error and r.content[0].text == DECLINED, (content, r.content[0].text)
    assert rows(path) == []


def test_label_bad_label(tmp_path):
    path = tmp_path / "labels.jsonl"
    r, asked = call(path, {"id": "t001", "label": "spam"}, ACCEPT)
    assert r.is_error and r.content[0].text == ERR_LABEL
    assert ERR_LABEL == "label must be one of: billing, bug, account, how_to, feature_request, outage"
    assert asked == []  # validated before anything is put to the user
    assert rows(path) == []


def test_label_unknown_ticket(tmp_path):
    path = tmp_path / "labels.jsonl"
    r, asked = call(path, {"id": "t999", "label": "billing"}, ACCEPT)
    assert r.is_error and r.content[0].text == "unknown ticket: t999"
    assert asked == []
    assert rows(path) == []


def test_accept_with_non_boolean_confirm_writes_nothing(tmp_path):
    """Only the JSON boolean true confirms: "yes", "true", 1 and null read as "no", and the answer is
    the fixed decline text, as in Go (R23.4), not an SDK schema error."""
    path = tmp_path / "labels.jsonl"
    for bad in ("yes", "true", 1, None):
        r, _ = call(path, {"id": "t001", "label": "bug"}, ElicitResult(action="accept", content={"confirm": bad}))
        assert r.is_error and r.content[0].text == DECLINED, (bad, r.content[0].text)
    assert rows(path) == []


def test_client_without_elicitation_writes_nothing(tmp_path):
    """No elicitation capability: the SDK refuses with -32021 before the tool body runs."""
    path = tmp_path / "labels.jsonl"

    async def go():
        async with Client(build_ops_server(path)) as c:
            try:
                return await c.call_tool("label_ticket", {"id": "t001", "label": "bug"})
            except MCPError as e:
                return e

    err = asyncio.run(go())
    assert isinstance(err, MCPError) and err.error.code == -32021
    assert rows(path) == []


def test_legacy_era_client_can_confirm(tmp_path):
    """Python serves the legacy era with a real elicitation/create mid-call (sessions on), so a
    2025-11-25 host can confirm. Go's Stateless server cannot (ruling R6): see opsserver."""
    path = tmp_path / "labels.jsonl"
    r, asked = call(path, {"id": "t002", "label": "bug"}, ACCEPT, mode="legacy")
    assert not r.is_error and len(asked) == 1
    assert [x["ticket_id"] for x in rows(path)] == ["t002"]


def test_labels_path_defaults_to_labels_path():
    assert inspect.signature(build_ops_server).parameters["labels_path"].default == LABELS_PATH


def test_tool_metadata(tmp_path):
    async def go():
        async with Client(build_ops_server(tmp_path / "l.jsonl")) as c:
            return (await c.list_tools()).tools

    [tool] = asyncio.run(go())
    assert tool.name == "label_ticket"
    assert tool.annotations.read_only_hint is False and tool.annotations.destructive_hint is False
    assert set(tool.input_schema["properties"]) == {"id", "label"}  # `answer` is resolved, never an argument
    assert tool.output_schema is not None


# ── 2. requestState: forged, rebound and replayed rounds write nothing ─────
async def _round1(c: Client, args: dict) -> InputRequiredResult:
    r1 = await c.session.call_tool("label_ticket", args, allow_input_required=True)
    assert isinstance(r1, InputRequiredResult)
    return r1


def _accept_all(r1: InputRequiredResult) -> dict:
    return {key: ACCEPT for key in r1.input_requests}


def test_forged_state_writes_nothing(tmp_path):
    """Ruling R8: tampered state, state for other arguments, or an answer with no state: no row.
    (Python answers the first two with a JSON-RPC error; the third is simply asked again.)"""
    path = tmp_path / "labels.jsonl"
    args = {"id": "t003", "label": "bug"}

    async def go():
        outcomes = []
        async with Client(build_ops_server(path), elicitation_callback=answering(ACCEPT, [])) as c:
            r1 = await _round1(c, args)
            state = r1.request_state
            tampered = state[:-2] + ("A" if state[-2] != "A" else "B") + state[-1]
            for label, st in [("bug", tampered), ("outage", state), ("bug", "v1.not-a-state")]:
                try:
                    outcomes.append(await c.session.call_tool("label_ticket", {"id": "t003", "label": label},
                                                              input_responses=_accept_all(r1), request_state=st,
                                                              allow_input_required=True))
                except MCPError as e:
                    outcomes.append(e)
            outcomes.append(await c.session.call_tool("label_ticket", args, input_responses=_accept_all(r1),
                                                      allow_input_required=True))
        return outcomes

    outcomes = asyncio.run(go())
    assert all(isinstance(o, MCPError) for o in outcomes[:3]), outcomes
    assert isinstance(outcomes[3], InputRequiredResult)  # no state: the answer is discarded, asked again
    assert rows(path) == []


def test_replayed_state_writes_once(tmp_path):
    """Ruling R12: the identical accepted retry, sent twice, writes one row. The SDK's sealed state
    is not single-use by itself (the second retry passes its checks), so the server spends it."""
    path = tmp_path / "labels.jsonl"
    args = {"id": "t004", "label": "outage"}

    async def go():
        async with Client(build_ops_server(path), elicitation_callback=answering(ACCEPT, [])) as c:
            r1 = await _round1(c, args)
            retry = dict(input_responses=_accept_all(r1), request_state=r1.request_state)
            first = await c.session.call_tool("label_ticket", args, **retry)
            second = await c.session.call_tool("label_ticket", args, **retry)
            fresh = await c.call_tool("label_ticket", args)  # a new round 1 gets a new state: allowed
        return first, second, fresh

    first, second, fresh = asyncio.run(go())
    assert not first.is_error
    assert second.is_error and second.content[0].text == DECLINED
    assert not fresh.is_error
    assert len(rows(path)) == 2  # the first retry and the fresh confirmation; never the replay


def test_declined_state_cannot_be_reanswered(tmp_path):
    """A state is spent by its first answer, whatever it was: decline, then replay as accept, writes nothing."""
    path = tmp_path / "labels.jsonl"
    args = {"id": "t005", "label": "bug"}

    async def go():
        async with Client(build_ops_server(path), elicitation_callback=answering(ACCEPT, [])) as c:
            r1 = await _round1(c, args)
            no = {key: ElicitResult(action="decline") for key in r1.input_requests}
            first = await c.session.call_tool("label_ticket", args, input_responses=no, request_state=r1.request_state)
            second = await c.session.call_tool("label_ticket", args, input_responses=_accept_all(r1),
                                               request_state=r1.request_state)
        return first, second

    first, second = asyncio.run(go())
    assert first.is_error and second.is_error and second.content[0].text == DECLINED
    assert rows(path) == []


def test_spent_states_prune_by_the_states_own_expiry():
    """R23.3: an entry is dropped only after its own state's exp (+ a margin), on the same wall
    clock the SDK uses to reject an expired state. Never earlier, so a replay can't slip through."""
    now = [1000.0]
    spent = SpentStates(clock=lambda: now[0])
    assert STATE_TTL_SECONDS == 300 and PRUNE_MARGIN_SECONDS > 0
    exp1 = now[0] + STATE_TTL_SECONDS
    assert spent.spend("s1", exp1) is True
    assert spent.spend("s1", exp1) is False
    now[0] = exp1 + PRUNE_MARGIN_SECONDS  # still inside the margin: kept
    assert spent.spend("s2", now[0] + STATE_TTL_SECONDS) is True
    assert spent.spend("s1", exp1) is False and len(spent) == 2
    now[0] = exp1 + PRUNE_MARGIN_SECONDS + 1  # past exp + margin: the SDK rejects s1 anyway
    assert spent.spend("s3", now[0] + STATE_TTL_SECONDS) is True
    assert len(spent) == 2  # s1 was pruned (s2, s3 kept): memory stays bounded


def test_spent_states_follow_each_states_expiry_not_spend_time():
    """A state spent late in its life is pruned soon after; one spent early stays until its own exp."""
    now = [1000.0]
    spent = SpentStates(clock=lambda: now[0])
    spent.spend("late", now[0] + 5)  # expires 5 s from now
    spent.spend("early", now[0] + STATE_TTL_SECONDS)
    now[0] += 5 + PRUNE_MARGIN_SECONDS + 1
    spent.spend("x", now[0] + STATE_TTL_SECONDS)
    assert spent.spend("early", 0) is False  # still remembered
    assert len(spent) == 2  # "late" is gone; "early" and "x" remain


def test_state_ttl_is_five_minutes(tmp_path):
    """The sealed state carries exp = iat + 300 s (the SDK default is 600)."""
    sec = ops_server.state_security()
    assert sec.ttl == STATE_TTL_SECONDS == 300


def test_concurrent_identical_retries_write_once(tmp_path):
    """R23.5: N identical accepted retries in flight at once write exactly one row."""
    path = tmp_path / "labels.jsonl"
    args = {"id": "t006", "label": "account"}
    n = 20

    async def go():
        async with Client(build_ops_server(path), elicitation_callback=answering(ACCEPT, [])) as c:
            r1 = await _round1(c, args)
            retry = dict(input_responses=_accept_all(r1), request_state=r1.request_state)
            return await asyncio.gather(*(c.session.call_tool("label_ticket", args, **retry) for _ in range(n)))

    results = asyncio.run(go())
    assert sum(not r.is_error for r in results) == 1
    assert all(r.content[0].text == DECLINED for r in results if r.is_error)
    assert len(rows(path)) == 1


def test_capture_sits_just_before_the_boundary():
    """R21/R23.1: the capture must see the sealed wire token, so it runs immediately before the
    SDK's RequestStateBoundary (which replaces the token with plaintext)."""
    mw = build_ops_server().middleware
    boundary = next(i for i, m in enumerate(mw) if isinstance(m, RequestStateBoundary))
    assert boundary >= 1
    assert getattr(mw[boundary - 1], "__name__", "") == capture_wire_state.__name__
    assert sum(getattr(m, "__name__", "") == capture_wire_state.__name__ for m in mw) == 1


def test_build_fails_when_the_boundary_is_missing(monkeypatch):
    """R23.1: if a later SDK moves or renames the boundary, the build fails loudly (fail closed)."""
    class NotTheBoundary:
        pass

    monkeypatch.setattr(ops_server, "RequestStateBoundary", NotTheBoundary)
    with pytest.raises(RuntimeError) as e:
        build_ops_server()
    assert str(e.value) == "RequestStateBoundary not found: replay guard cannot be installed"


def test_state_the_capture_did_not_see_writes_nothing(tmp_path):
    """R23.2: a round 2 that carried a requestState the capture never saw is refused, not treated
    as a legacy (stateless) answer. Simulated by removing the capture middleware."""
    path = tmp_path / "labels.jsonl"
    args = {"id": "t007", "label": "bug"}
    mcp = build_ops_server(path)
    mcp.middleware[:] = [m for m in mcp.middleware if getattr(m, "__name__", "") != capture_wire_state.__name__]

    async def go():
        async with Client(mcp, elicitation_callback=answering(ACCEPT, [])) as c:
            r1 = await _round1(c, args)
            return await c.session.call_tool("label_ticket", args, input_responses=_accept_all(r1),
                                             request_state=r1.request_state)

    r = asyncio.run(go())
    assert r.is_error and r.content[0].text == DECLINED
    assert rows(path) == []


# ── 3. Shared contract (the Go server must pass the same file) ──────────────
def _ops_cases() -> list[dict]:
    rows_ = [json.loads(line) for line in CONTRACT.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [c for c in rows_ if c["server"] == "ops"]


def _run_ops_case(case: dict, path: Path) -> dict:
    """One case on a fresh server and labels file, in the README's ops `expect` shape."""
    answer = case["answer"]
    reply = ElicitResult(**answer) if answer is not None else None
    r, asked = call(path, case["params"]["arguments"], reply)
    out: dict = {"error": r.content[0].text} if r.is_error else {"structured": r.structured_content}
    if r.is_error:
        assert r.structured_content is None
    out["asked"] = asked[0] if asked else None
    assert len(asked) <= 1
    out["rows"] = [{k: v for k, v in row.items() if k != "time"} for row in rows(path)]
    return out


def test_contract_ops(tmp_path):
    cases = _ops_cases()
    names = {c["name"] for c in cases}
    assert {"ops_accept", "ops_decline", "ops_cancel", "ops_bad_label", "ops_unknown_ticket"} <= names
    for i, case in enumerate(cases):
        got = _run_ops_case(case, tmp_path / f"labels-{i}.jsonl")
        assert deep_equal(got, case["expect"]), (case["name"], got)


# ── 4. HTTP app and security (same hardening as lumen-read) ─────────────────
META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "Host": "127.0.0.1:8766", "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "server/discover"}


def _discover(pad: int = 0) -> bytes:
    params: dict = {"_meta": META}
    if pad:
        params["pad"] = "x" * pad
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": params}).encode()


def _http(tmp_path) -> TestClient:
    """HTTP needs a token since Task 8: lumen-ops's own (tickets:write, aud = ops URI), minted with
    this test's key."""
    c = TestClient(build_http_app(build_ops_server(tmp_path / "labels.jsonl", auth=True)),
                   base_url="http://127.0.0.1:8766")
    c.headers["Authorization"] = f"Bearer {mint_test_token(SCOPE_WRITE, OPS_RESOURCE)}"
    return c


def test_server_discover(tmp_path):
    with _http(tmp_path) as c:
        r = c.post("/mcp", content=_discover(), headers=HEADERS)
    assert r.status_code == 200
    assert "2026-07-28" in r.json()["result"]["supportedVersions"]


def test_origin_rejected_http(tmp_path):
    with _http(tmp_path) as c:
        assert c.post("/mcp", content=_discover(), headers=HEADERS).status_code == 200
        bad = c.post("/mcp", content=_discover(), headers={**HEADERS, "Origin": "http://evil.invalid"})
    assert bad.status_code == 403


def test_body_cap(tmp_path):
    base = len(_discover(pad=1)) - 1
    exact, over = _discover(pad=131_072 - base), _discover(pad=131_072 - base + 1)
    assert len(exact) == 131_072 and len(over) == 131_073
    with _http(tmp_path) as c:
        assert c.post("/mcp", content=over, headers=HEADERS).status_code == 413
        assert c.post("/mcp", content=exact, headers=HEADERS).status_code == 200


def test_default_port_is_8766():
    assert ops_server.DEFAULT_PORT == 8766
