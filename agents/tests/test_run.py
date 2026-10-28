"""Tests for agents/run.py. No API calls, ever.

Record mode is exercised only against a fake upstream (httpx.MockTransport inside the proxy, the proxy's own test
seam) with a dummy key, writing to a temporary store: that store is the "fake proxy store" the replay tests read.
It is a labelled test fixture, never agents/data/recordings. Every proxy binds 127.0.0.1 on a free port.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
from pathlib import Path

import httpx
import pytest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents"))

import run  # noqa: E402
import run_engines  # noqa: E402  (where go_run looks up go_binary and GO_TIMEOUT)
import desk_core as core  # noqa: E402
import proxy  # noqa: E402

T = {t["id"]: t for t in core.TICKETS}
SETS = ("patterns", "patterns-go", "desk-hand", "desk-langgraph", "desk-sdk", "desk-hand-go")
PASS = json.dumps({r: {"pass": True, "reason": "ok"} for r in ("addresses_issue", "tone", "no_false_promises")})
HAVE_GO = shutil.which("go") is not None


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    """The guard's scripted person appends to the harness trace in the working directory: keep it in tmp."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ── a fake upstream: scripted Messages API replies, JSON or SSE ─────────────
def _sse(blocks: list[dict], mid: str) -> bytes:
    events = []

    def add(kind: str, data: dict) -> None:
        events.append(f"event: {kind}\ndata: {json.dumps(data)}\n\n")
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    add("message_start", {"type": "message_start", "message": {
        "id": mid, "type": "message", "role": "assistant", "model": core.MODEL, "content": [], "stop_reason": None,
        "stop_sequence": None, "usage": {"input_tokens": 11, "output_tokens": 1}}})
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            add("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            add("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}})
        else:
            add("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
            add("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(b["input"])}})
        add("content_block_stop", {"type": "content_block_stop", "index": i})
    add("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 5}})
    add("message_stop", {"type": "message_stop"})
    return "".join(events).encode()


def route(body: dict) -> list[dict]:
    """A reply from the request alone (so it is the same on every run): the desks look the ticket up once and
    answer; the chain classifies, then writes."""
    system = body.get("system")
    system = " ".join(s.get("text", "") for s in system) if isinstance(system, list) else str(system or "")
    msgs = body.get("messages", [])
    turns = sum(1 for m in msgs if m.get("role") == "assistant")
    tools = [t.get("name", "") for t in body.get("tools") or [] if isinstance(t, dict)]
    if '"category": "<category>"' in system:
        return [{"type": "text", "text": '{"category": "billing", "priority": "high"}'}]
    if "You supervise the Lumen" in system:       # the hand-built supervisor: triage, drafter, reviewer, done
        order = ["triage", "drafter", "reviewer"]
        if turns < len(order):
            return [{"type": "tool_use", "id": f"toolu_s{turns}", "name": "handoff",
                     "input": {"worker": order[turns], "task": "Do your part."}}]
        return [{"type": "text", "text": "Done."}]
    if "You write the reply" in system:
        return [{"type": "text", "text": "Draft reply."}]
    if "rubric" in system.lower() or "reviewer" in system.lower():
        return [{"type": "text", "text": PASS}]
    get = next((n for n in tools if n.endswith("get_ticket")), None)
    search = next((n for n in tools if n.endswith("search_tickets")), None)
    if (get or search) and turns == 0:
        first = msgs[0]["content"]
        text = first if isinstance(first, str) else json.dumps(first)
        tid = "t061" if 'id=\\"t061\\"' in text or 'id="t061"' in text else "t001"
        if get:
            return [{"type": "tool_use", "id": "toolu_01", "name": get, "input": {"id": tid}}]
        return [{"type": "tool_use", "id": "toolu_01", "name": search, "input": {"query": "charged twice"}}]
    return [{"type": "text", "text": "Thanks, we are on it."}]


class Upstream:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        body = json.loads(request.content)
        blocks = route(body)
        mid = "msg_" + hashlib.sha256(request.content).hexdigest()[:12]   # one id per response (the SDK trace keys on it)
        if body.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(blocks, mid))
        return httpx.Response(200, json={"id": mid, "type": "message", "role": "assistant", "model": core.MODEL,
                                         "content": blocks, "stop_reason": "tool_use" if blocks[0]["type"] == "tool_use" else "end_turn",
                                         "stop_sequence": None, "usage": {"input_tokens": 11, "output_tokens": 5}})


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """A temporary store, traces dir and labels file (record mode's "real" labels path)."""
    d = {"store": tmp_path / "store", "traces": tmp_path / "traces", "labels": tmp_path / "labels.jsonl"}
    monkeypatch.setattr(run, "LABELS_PATH", d["labels"])
    return d


def go(argv: list[str], dirs: dict, **kw) -> int:
    return run.main(argv + ["--port", "0"], store_dir=dirs["store"], traces_dir=dirs["traces"], **kw)


def record(argv: list[str], dirs: dict, monkeypatch, upstream: Upstream | None = None) -> Upstream:
    """Record through the proxy into the temporary store, against the fake upstream (dummy key, --yes)."""
    upstream = upstream or Upstream()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    assert go(argv + ["--mode", "record", "--yes"], dirs, transport=httpx.MockTransport(upstream)) == 0
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    return upstream


# ── the dry run ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", SETS)
def test_dry_run_shape(name, capsys, dirs):
    assert go(["--set", name, "--dry-run"], dirs) == 0
    out = capsys.readouterr().out
    spec = run.SETS[name]
    n_runs = len(spec.desks) * len(spec.tickets)
    assert f"Recording set {name}:" in out
    assert f"({n_runs} runs)" in out
    assert "calls: typical" in out and ", max " in out
    assert "typical cost $" in out and "worst case $" in out
    assert "cap $10.00 for the whole set" in out
    assert "--max-calls" in out
    assert "prices $0.10 input / $0.50 output per million tokens" in out
    # R71: the printed record command never carries --yes, so a pasted command still asks y/N
    assert f"  python agents/run.py --set {name} --mode record --max-cost 10\n" in out
    assert "--yes" not in out
    assert "Dry run: nothing was called." in out
    assert not dirs["store"].exists() and not dirs["traces"].exists()


def test_dry_run_numbers_add_up(capsys, dirs):
    """The set's totals are the per-run rows summed; worst >= typical; the max calls respect --max-calls."""
    est = run.estimate_set(run.SETS["desk-hand"], max_calls=None)
    assert est["typical_calls"] == 13 * (run.TYPICAL_CALLS["single"] + run.TYPICAL_CALLS["supervisor"])
    assert est["max_calls"] == 13 * (run.MAX_TURNS_SINGLE + 20)          # supervisor: R61's 20, the single agent: its MAX_TURNS 8
    assert est["worst"] > est["typical"] > 0
    capped = run.estimate_set(run.SETS["desk-hand"], max_calls=12)
    assert capped["max_calls"] == 13 * (8 + 12) and capped["worst"] < est["worst"]


def test_max_calls_limited_to_20(dirs, capsys):
    assert go(["--set", "desk-hand", "--dry-run", "--max-calls", "21"], dirs) == 2
    assert "--max-calls" in capsys.readouterr().out


# ── engines and sets ────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["hand", "hand-go", "langgraph", "sdk"])
def test_every_engine_resolves_through_ENGINES(name):
    engine = run.engine(name)
    assert core.ENGINES[name] is engine
    assert callable(engine.single) and callable(engine.supervisor)


def test_set_for_each_engine_and_desk():
    assert run.set_for("hand", "pattern:chain") == "patterns"
    assert run.set_for("hand-go", "pattern:route") == "patterns-go"
    assert run.set_for("hand", "single") == "desk-hand"
    assert run.set_for("langgraph", "supervisor") == "desk-langgraph"
    assert run.set_for("sdk", "single") == "desk-sdk"
    with pytest.raises(ValueError):
        run.set_for("sdk", "pattern:chain")      # the frameworks build the desks only
    assert run.SETS["patterns"].tickets == ("t001", "t007", "t061")
    assert len(run.SETS["desk-sdk"].tickets) == 13 and run.SETS["desk-sdk"].desks == ("single", "supervisor")


def test_engines_get_approve_elicit_and_labels_explicitly(dirs, monkeypatch):
    """R33: every engine gets approve, elicit and labels_path. Replay uses a temporary labels file; record keeps
    the real path. R9: CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 is set while the engine runs."""
    seen = []

    def fake(ticket, **kw):
        seen.append({**kw, "env": os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")})
        return {"reply": "ok", "stop": "end_turn", "trace": core.Trace(), "calls": [], "tool_calls": [], "errors": []}
    monkeypatch.setitem(core.ENGINES, "hand", core.Engine(single=fake, supervisor=fake))
    assert go(["--engine", "hand", "--desk", "single", "--tickets", "t001"], dirs) == 0
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    assert go(["--engine", "hand", "--desk", "single", "--tickets", "t001", "--mode", "record", "--yes"], dirs,
              transport=httpx.MockTransport(Upstream())) == 0
    replay, rec = seen
    for kw in seen:
        assert kw["approve"] == "decline" and kw["elicit"] == "accept" and kw["env"] == "1"
    assert replay["labels_path"] != dirs["labels"] and replay["labels_path"].name == "labels.jsonl"
    assert rec["labels_path"] == dirs["labels"]


# ── replay ──────────────────────────────────────────────────────────────────
def test_replay_miss_names_the_right_record_command(dirs, capsys):
    assert go(["--engine", "hand", "--desk", "pattern:chain", "--tickets", "t001"], dirs) == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert "replay miss" in text
    assert "record with: python agents/run.py --engine hand --desk pattern:chain --tickets t001 --mode record" in text
    assert not (dirs["traces"] / "patterns" / "hand-chain-t001.json").exists()   # an incomplete run writes no trace


def test_replay_miss_command_keeps_a_raised_max_calls(dirs, capsys):
    assert go(["--engine", "hand", "--desk", "single", "--tickets", "t007", "--max-calls", "14"], dirs) == 1
    text = "".join(capsys.readouterr())
    assert "record with: python agents/run.py --engine hand --desk single --tickets t007 --mode record --max-calls 14" in text


def _trace_files(d: Path) -> dict[str, str]:
    return {str(p.relative_to(d)): p.read_text() for p in sorted(d.rglob("*.json"))}


CASES = [("hand", "pattern:chain", "t001"), ("hand", "pattern:parallel", "t001"), ("hand", "single", "t061"),
         ("hand", "supervisor", "t001"),
         ("langgraph", "single", "t001"), ("sdk", "single", "t001")]
if HAVE_GO:
    CASES += [("hand-go", "pattern:chain", "t001"), ("hand-go", "single", "t061")]


@pytest.mark.parametrize("engine,desk,ticket", CASES)
def test_traces_deterministic_in_replay(engine, desk, ticket, dirs, monkeypatch, capsys):
    """Record once against the fake upstream (the fake proxy store), then replay twice: no upstream call, the
    same trace bytes both times, and the same run as the recording (ms aside: replay latency is local)."""
    argv = ["--engine", engine, "--desk", desk, "--tickets", ticket]
    upstream = record(argv, dirs, monkeypatch)
    made = upstream.calls
    assert made >= 1
    name = f"{engine}-{desk.removeprefix('pattern:')}-{ticket}.json"
    path = dirs["traces"] / run.set_for(engine, desk) / name
    recorded = json.loads(path.read_text())
    assert recorded["mode"] == "record" and recorded["paid_calls"] == made and recorded["spent"] > 0
    replay_dir = dirs["traces"].parent / "replay"
    firsts = []
    for _ in range(2):
        assert run.main(argv + ["--port", "0"], store_dir=dirs["store"], traces_dir=replay_dir) == 0
        firsts.append(_trace_files(replay_dir))
        shutil.rmtree(replay_dir)
    assert upstream.calls == made                     # replay never forwards
    assert firsts[0] == firsts[1]
    replayed = json.loads(next(iter(firsts[0].values())))
    assert replayed["mode"] == "replay" and replayed["spent"] == 0 and replayed["paid_calls"] == 0
    assert all(r["ms"] is None for r in replayed["trace"])
    assert run.comparable(replayed) == run.comparable(recorded)
    # >=: parallel's two search_tickets lookups are the same request, recorded once and answered twice
    assert [r["kind"] for r in replayed["trace"]].count("model") >= made
    for key in ("set", "engine", "desk", "ticket", "trace", "output", "errors", "cost_usd", "spent", "paid_calls",
                "proxy_calls"):
        assert key in replayed
    # R62: the replay's ledger is the recording's, call for call, served again
    assert replayed["proxy_calls"] == [{**c, "replayed": True} for c in recorded["proxy_calls"]]


def test_replay_keeps_a_recorded_trace_that_matches(dirs, monkeypatch, capsys):
    """A replay over a record-mode trace leaves it (and its real latency and spend) when the run matches."""
    argv = ["--engine", "hand", "--desk", "pattern:chain", "--tickets", "t001"]
    record(argv, dirs, monkeypatch)
    path = dirs["traces"] / "patterns" / "hand-chain-t001.json"
    before = path.read_text()
    assert go(argv, dirs) == 0
    assert path.read_text() == before
    assert "matches the recorded trace" in capsys.readouterr().out


def test_trace_record_shape(dirs, monkeypatch):
    argv = ["--engine", "hand", "--desk", "single", "--tickets", "t001"]
    record(argv, dirs, monkeypatch)
    doc = json.loads((dirs["traces"] / "desk-hand" / "hand-single-t001.json").read_text())
    assert doc["set"] == "desk-hand" and doc["engine"] == "hand" and doc["desk"] == "single" and doc["ticket"] == "t001"
    assert doc["approve"] == "decline" and doc["elicit"] == "accept" and doc["model"] == core.MODEL
    assert doc["cost_usd"] is None                    # the hand-built engine has no cost of its own; spent is the proxy's
    assert doc["errors"] == []
    assert doc["output"]["reply"] == "Thanks, we are on it."
    assert [r["kind"] for r in doc["trace"]] == ["model", "tool", "model"]
    assert all(set(r) == set(core.TRACE_FIELDS) for r in doc["trace"])
    # R62: the proxy's ledger, one row per model call, priced from the recording's usage
    assert [set(c) for c in doc["proxy_calls"]] == [{"key", "replayed", "ms", "model", "input_tokens",
                                                     "cache_creation_input_tokens", "cache_read_input_tokens",
                                                     "output_tokens", "cost_usd"}] * 2
    assert all(c["replayed"] is False and c["input_tokens"] == 11 and c["output_tokens"] == 5
               and c["cache_creation_input_tokens"] == 0 and c["cache_read_input_tokens"] == 0
               and c["cost_usd"] == run.call_cost(core.MODEL, 11, 5) and isinstance(c["ms"], int)
               for c in doc["proxy_calls"])
    assert "/Users/" not in json.dumps(doc) and str(ROOT) not in json.dumps(doc)


# ── record mode refuses without a key, before anything binds ────────────────
def test_record_yes_without_key_refused_before_binding(dirs, monkeypatch, capsys):
    bound = []
    real_bind = socket.socket.bind

    def spy(self, *a, **k):
        bound.append(a)
        return real_bind(self, *a, **k)
    monkeypatch.setattr(socket.socket, "bind", spy)
    built = []
    monkeypatch.setattr(proxy.ProxyServer, "__init__", lambda *a, **k: built.append(1) or (_ for _ in ()).throw(AssertionError("bound")))
    assert go(["--set", "patterns", "--mode", "record", "--yes"], dirs) == 2
    assert bound == [] and built == []
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out
    with pytest.raises(ValueError):                    # the proxy itself refuses too (it already did)
        proxy.make_server(mode="record", port=0, store_dir=dirs["store"], yes=True)
    assert bound == [] and built == []


def test_record_without_yes_on_a_pipe_is_not_confirmed(dirs, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    upstream = Upstream()
    assert go(["--engine", "hand", "--desk", "pattern:chain", "--tickets", "t001", "--mode", "record"], dirs,
              transport=httpx.MockTransport(upstream)) == 1
    assert upstream.calls == 0
    assert "Not confirmed. Nothing was called." in capsys.readouterr().out


def test_record_cost_cap_stops_the_set(dirs, monkeypatch, capsys):
    """The cap is for the whole set: each ticket's proxy gets what is left; a refused call stops the set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    upstream = Upstream()
    rc = go(["--engine", "hand", "--desk", "pattern:chain", "--tickets", "t001,t007", "--mode", "record", "--yes",
             "--max-cost", "0.0001"], dirs, transport=httpx.MockTransport(upstream))
    assert rc == 3
    assert upstream.calls == 0
    assert "incomplete" in capsys.readouterr().out


@pytest.mark.parametrize("engine", ["hand", "hand-go"] if HAVE_GO else ["hand"])
def test_t061_injected_label_is_refused(engine, dirs, monkeypatch):
    """The desk marks the session untrusted before the first call (R24), so the label t061 asks for needs the
    person's yes; run.py's person says no (APPROVE): nothing is written, in Python and in Go's own Tools."""
    class Injected(Upstream):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if sum(1 for m in body["messages"] if m["role"] == "assistant") == 0:
                blocks = [{"type": "tool_use", "id": "toolu_09", "name": "label_ticket", "input": {"id": "t001", "label": "outage"}}]
            else:
                blocks = [{"type": "text", "text": "Done."}]
            self.calls += 1
            return httpx.Response(200, json={"id": f"msg_{self.calls}", "type": "message", "role": "assistant",
                                             "model": core.MODEL, "content": blocks, "stop_reason": "end_turn",
                                             "stop_sequence": None, "usage": {"input_tokens": 11, "output_tokens": 5}})
    record(["--engine", engine, "--desk", "single", "--tickets", "t061"], dirs, monkeypatch, Injected())
    doc = json.loads((dirs["traces"] / f"desk-{engine}" / f"{engine}-single-t061.json").read_text())
    assert doc["output"]["tool_calls"] == [{"agent": "single", "tool": "label_ticket", "args": {"id": "t001", "label": "outage"},
                                            "is_error": True}]
    assert not dirs["labels"].exists() or dirs["labels"].read_text() == ""


def test_call_limit_stops_only_that_ticket(dirs, monkeypatch, capsys):
    """R25: --max-calls is per ticket. A run that needs more stops incomplete (no trace) and the set goes on;
    only the dollar cap stops the whole set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    upstream = Upstream()
    rc = go(["--engine", "hand", "--desk", "pattern:chain", "--tickets", "t001,t007", "--mode", "record", "--yes",
             "--max-calls", "1"], dirs, transport=httpx.MockTransport(upstream))
    assert rc == 1 and upstream.calls == 2            # one paid call per ticket, then each stopped
    out = capsys.readouterr().out
    assert out.count("stopped at --max-calls 1; incomplete, no trace") == 2
    assert not list((dirs["traces"] / "patterns").glob("*.json"))   # proxy.log only



# ── fix round 1 ─────────────────────────────────────────────────────────────
def _kw(dirs, **over):
    kw = dict(engine_name="hand", desk="pattern:chain", ticket=T["t001"], set_name="patterns", mode="record",
              max_cost=10.0, max_calls=None, port=0, store_dir=dirs["store"], traces_dir=dirs["traces"],
              transport=httpx.MockTransport(Upstream()))
    return {**kw, **over}


def test_run_ticket_never_self_approves(dirs, monkeypatch):
    """run_ticket takes `approved` with no default; record mode without it builds no server and binds nothing."""
    import inspect
    assert inspect.signature(run.run_ticket).parameters["approved"].default is inspect.Parameter.empty
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    bound = []
    real_bind = socket.socket.bind
    monkeypatch.setattr(socket.socket, "bind", lambda self, *a, **k: bound.append(a) or real_bind(self, *a, **k))
    built = []
    monkeypatch.setattr(proxy.ProxyServer, "__init__", lambda *a, **k: built.append(1) or (_ for _ in ()).throw(AssertionError("built")))
    with pytest.raises(proxy.NotConfirmed):
        run.run_ticket(**_kw(dirs), approved=False)
    assert bound == [] and built == []


def _absorbing(calls: int):
    """A desk that swallows the proxy's refusal and returns normally (as an engine might)."""
    def desk(ticket, **kw):
        trace = core.Trace()
        for i in range(calls):          # different requests: a repeated one would replay from the store, unpaid
            try:
                core.claude([{"role": "user", "content": f"hi {ticket['id']} {i}"}], system="s", trace=trace, agent="single")
            except core.ProxyError:
                pass
        return {"reply": "ok", "stop": "end_turn", "trace": trace, "calls": [], "tool_calls": [], "errors": []}
    return desk


def test_absorbed_cost_cap_writes_no_trace_and_stops_the_set(dirs, monkeypatch, capsys):
    monkeypatch.setitem(core.ENGINES, "hand", core.Engine(single=_absorbing(1), supervisor=_absorbing(1)))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    upstream = Upstream()
    rc = go(["--engine", "hand", "--desk", "single", "--tickets", "t001,t007", "--mode", "record", "--yes",
             "--max-cost", "0.0001"], dirs, transport=httpx.MockTransport(upstream))
    assert rc == 3 and upstream.calls == 0
    out = capsys.readouterr().out
    assert "stopped by the cost cap" in out and "t007" not in out.split("Stopped by the cost cap")[0].split("t001")[-1]
    assert not list((dirs["traces"] / "desk-hand").glob("*.json"))   # proxy.log only


def test_absorbed_call_cap_stops_only_that_ticket(dirs, monkeypatch, capsys):
    monkeypatch.setitem(core.ENGINES, "hand", core.Engine(single=_absorbing(2), supervisor=_absorbing(2)))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    upstream = Upstream()
    rc = go(["--engine", "hand", "--desk", "single", "--tickets", "t001,t007", "--mode", "record", "--yes",
             "--max-calls", "1"], dirs, transport=httpx.MockTransport(upstream))
    out = capsys.readouterr().out
    assert rc == 1 and upstream.calls == 2
    assert out.count("stopped at --max-calls 1; incomplete, no trace") == 2
    assert "Stopped by the cost cap" not in out
    assert not list((dirs["traces"] / "desk-hand").glob("*.json"))   # proxy.log only


def test_one_estimate_per_set(dirs, monkeypatch, capsys):
    """run.py prints the set's estimate once; the proxy's per-run estimate line is suppressed."""
    record(["--engine", "hand", "--desk", "pattern:chain", "--tickets", "t001,t007"], dirs, monkeypatch)
    out = capsys.readouterr().out
    assert out.count("Recording set patterns:") == 1
    assert "Estimated cost $" not in out


def test_frameworks_max_calls_is_the_cap(dirs):
    """R52: the hand-built single agent stops at MAX_TURNS; LangGraph and the SDK are counted at the per-ticket cap."""
    hand = run.estimate_set(run.SETS["desk-hand"], max_calls=None)["rows"]["single"]
    for name in ("desk-langgraph", "desk-sdk"):
        row = run.estimate_set(run.SETS[name], max_calls=None)["rows"]["single"]
        assert row["max_calls"] == 13 * 12 > hand["max_calls"] == 13 * 8


def test_sdk_overhead_is_measured_not_understated():
    """R52: the SDK estimate uses first-request sizes measured on the real CLI (stored with the date and version),
    and its first-call input is at least what the CLI really sent."""
    m = run.SDK_MEASURED
    assert m["cli"] and m["date"] and m["ticket"] == "t001"
    schemas = run._schemas()
    for desk in ("single", "supervisor"):
        e = run.estimate_run("sdk", desk, T["t001"], 12, schemas)
        assert e["first"] >= m["first_request_tokens"][desk]


def test_go_timeout_kills_the_process_group(monkeypatch, tmp_path):
    """A hung hand-go run is killed with its whole process group (the MCP servers are its children)."""
    script = tmp_path / "hang.sh"
    marker = tmp_path / "child.pid"
    script.write_text(f"#!/bin/sh\nsleep 300 &\necho $! > {marker}\nwait\n")
    script.chmod(0o755)
    monkeypatch.setattr(run_engines, "go_binary", lambda: script)
    monkeypatch.setattr(run_engines, "GO_TIMEOUT", 1)
    with pytest.raises(RuntimeError, match="timed out"):
        run.go_run("single", T["t001"], approve="decline", elicit="accept", labels_path=tmp_path / "l.jsonl")
    pid = int(marker.read_text())
    import time
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the grandchild survived the timeout")


# ── R59 ─────────────────────────────────────────────────────────────────────
def test_preflight_tool_failure_aborts_before_anything(dirs, monkeypatch, capsys):
    """A lumen-read tool that errors (fastapi missing made classify_complaint fail in real recordings) stops the run
    before any estimate, server or socket: exit 2."""
    import lumen_core.classify as lc
    monkeypatch.setattr(lc, "classify", lambda text: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'fastapi'")))
    built = []
    monkeypatch.setattr(proxy.ProxyServer, "__init__", lambda *a, **k: built.append(1) or (_ for _ in ()).throw(AssertionError("built")))
    for argv in (["--engine", "hand", "--desk", "single", "--tickets", "t001"],
                 ["--set", "desk-hand", "--mode", "record", "--yes"]):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
        assert go(argv, dirs) == 2
        out = capsys.readouterr().out
        assert "pre-flight" in out and "classify_complaint" in out and "Recording set" not in out
    assert built == []


def test_preflight_passes_with_the_real_tools(capsys):
    assert run.preflight() is None


def test_proxy_log_is_kept_per_set(dirs, capsys):
    assert go(["--engine", "hand", "--desk", "pattern:chain", "--tickets", "t001"], dirs) == 1   # a miss
    log = dirs["traces"] / "patterns" / "proxy.log"
    assert "MISS" in log.read_text()


def test_langgraph_runs_back_to_back(dirs, monkeypatch):
    """R59: LangGraph's pooled connection to one ticket's proxy run must not reach the next run's request."""
    with socket.socket() as sk:                       # one fixed port for every run, as the real 8790
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    argv = ["--engine", "langgraph", "--desk", "single", "--tickets", "t001,t007,t002", "--mode", "record", "--yes",
            "--port", str(port)]
    assert run.main(argv, store_dir=dirs["store"], traces_dir=dirs["traces"], transport=httpx.MockTransport(Upstream())) == 0
    names = sorted(p.name for p in (dirs["traces"] / "desk-langgraph").glob("*.json"))
    assert names == ["langgraph-single-t001.json", "langgraph-single-t002.json", "langgraph-single-t007.json"]


def test_absorbed_replay_miss_is_incomplete(dirs, monkeypatch, capsys):
    """R59: the Agent SDK's supervisor absorbed a replay miss (a sub-agent's call) and finished "ok". Any miss in a
    run makes it incomplete: no trace, and the record command is printed."""
    monkeypatch.setitem(core.ENGINES, "hand", core.Engine(single=_absorbing(1), supervisor=_absorbing(1)))
    assert go(["--engine", "hand", "--desk", "single", "--tickets", "t001"], dirs) == 1
    out = capsys.readouterr().out
    assert "replay miss; record with: python agents/run.py --engine hand --desk single --tickets t001 --mode record" in out
    assert not list((dirs["traces"] / "desk-hand").glob("*.json"))



# ── R59 fix round 1 ─────────────────────────────────────────────────────────
def test_preflight_message_names_the_install_command(dirs, monkeypatch, capsys):
    monkeypatch.setattr(run, "preflight", lambda: "classify_complaint({}) failed: boom")
    assert go(["--engine", "hand", "--desk", "single", "--tickets", "t001"], dirs) == 2
    assert ".venv-agents/bin/pip install -r agents/requirements.txt" in capsys.readouterr().out


def test_preflight_exception_is_a_failure_not_a_traceback(dirs, monkeypatch, capsys):
    """A pre-flight that raises (the servers will not even open) is the same clean exit 2, no traceback."""
    def broken(*a, **k):
        raise ImportError("No module named 'numpy'")
    monkeypatch.setattr(core, "open_desk_tools", broken)
    assert go(["--engine", "hand", "--desk", "single", "--tickets", "t001"], dirs) == 2
    out = capsys.readouterr().out
    assert "pre-flight tool check failed" in out and "ImportError: No module named 'numpy'" in out


def test_close_waits_for_an_in_flight_paid_call(dirs, monkeypatch):
    """Minor 2: run.py waits (bounded) for busy handlers before closing the proxy's upstream client."""
    class Busy:
        busy = True
    server = SimpleNamespace(connections={object(): Busy()}, lock=__import__("threading").Lock())
    import threading, time
    def finish():
        time.sleep(0.3)
        Busy.busy = False
    threading.Thread(target=finish).start()
    start = time.monotonic()
    assert run.wait_idle(server, timeout=5) is True
    assert 0.25 < time.monotonic() - start < 3
    Busy.busy = True
    assert run.wait_idle(server, timeout=0.2) is False


# ── R62: proxy_calls from the recordings; --refresh ─────────────────────────
def test_replay_rebuilds_proxy_calls_from_the_recordings(dirs, monkeypatch, capsys):
    """Record a set against the fake upstream, then replay it: the replay trace's ledger is the record trace's
    with replayed True. A record-mode trace written before the ledger existed gets it filled in by a matching
    replay (everything else kept)."""
    argv = ["--set", "desk-hand", "--tickets", "t001,t061"]
    record(argv, dirs, monkeypatch)
    replay_dir = dirs["traces"].parent / "replay"
    assert run.main(argv + ["--port", "0"], store_dir=dirs["store"], traces_dir=replay_dir) == 0
    recorded = _trace_files(dirs["traces"] / "desk-hand")
    replayed = _trace_files(replay_dir / "desk-hand")
    assert sorted(recorded) == sorted(replayed) and len(recorded) == 4
    for name in recorded:
        a, b = json.loads(recorded[name]), json.loads(replayed[name])
        assert a["proxy_calls"] and b["proxy_calls"] == [{**c, "replayed": True} for c in a["proxy_calls"]]
    path = dirs["traces"] / "desk-hand" / "hand-single-t001.json"
    old = json.loads(path.read_text())
    del old["proxy_calls"]
    path.write_text(run.dump(old))
    assert go(["--engine", "hand", "--desk", "single", "--tickets", "t001"], dirs) == 0
    now = json.loads(path.read_text())
    assert now["mode"] == "record" and now["spent"] == old["spent"] and now["trace"] == old["trace"]
    assert now["proxy_calls"] == json.loads(replayed["hand-single-t001.json"])["proxy_calls"]


def test_refresh_needs_record_mode(dirs, capsys):
    assert go(["--set", "patterns", "--refresh"], dirs) == 2
    assert "--refresh" in capsys.readouterr().out
    assert go(["--set", "patterns", "--dry-run", "--refresh"], dirs) == 2


def test_refresh_dry_run_says_every_call_is_paid(dirs, capsys):
    assert go(["--set", "desk-sdk", "--dry-run"], dirs) == 0
    plain = capsys.readouterr().out
    assert go(["--set", "desk-sdk", "--dry-run", "--mode", "record", "--refresh"], dirs) == 0
    out = capsys.readouterr().out
    assert "every call is paid" in out and "every call is paid" not in plain
    assert "--tickets" in out.split("--refresh:")[1].splitlines()[0]     # refresh only the failed tickets
    assert "  python agents/run.py --set desk-sdk --mode record --refresh --max-cost 10\n" in out and "--yes" not in out   # R71
    # the estimate is unchanged: it already assumes every call is paid
    pick = lambda text: [l for l in text.splitlines() if "typical cost $" in l]   # noqa: E731
    assert pick(out) == pick(plain)


def test_refresh_re_records_what_is_already_recorded(dirs, monkeypatch, capsys):
    argv = ["--engine", "hand", "--desk", "pattern:chain", "--tickets", "t001"]
    first = record(argv, dirs, monkeypatch)
    again = record(argv, dirs, monkeypatch)
    assert first.calls == 2 and again.calls == 0                   # a store hit is free
    refreshed = record(argv + ["--refresh"], dirs, monkeypatch)
    assert refreshed.calls == 2                                    # every call went upstream again
    doc = json.loads((dirs["traces"] / "patterns" / "hand-chain-t001.json").read_text())
    assert [c["replayed"] for c in doc["proxy_calls"]] == [False, False]


def test_failed_refresh_keeps_the_old_recordings_and_trace(dirs, monkeypatch, capsys):
    """R65: a refreshed ticket whose second call is refused (its --max-calls) leaves the store and its trace as
    they were, so it still replays; the stop line says so."""
    argv = ["--engine", "hand", "--desk", "pattern:chain", "--tickets", "t001"]
    record(argv, dirs, monkeypatch)
    store = dirs["store"] / "patterns"
    before = {p.name: p.read_text() for p in store.glob("*.json")}
    trace = dirs["traces"] / "patterns" / "hand-chain-t001.json"
    old_trace = trace.read_text()
    assert len(before) == 2
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    upstream = Upstream()
    rc = go(argv + ["--mode", "record", "--yes", "--refresh", "--max-calls", "1"], dirs,
            transport=httpx.MockTransport(upstream))
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    out = capsys.readouterr().out
    assert rc == 1 and upstream.calls == 1                         # the first call was paid, the second refused
    assert "1 new recording(s) discarded" in out and "old recordings and trace are kept" in out
    assert {p.name: p.read_text() for p in store.glob("*.json")} == before
    assert trace.read_text() == old_trace
    assert go(argv, dirs) == 0                                      # and it still replays


def test_run_re_exports_its_parts_and_a_patch_goes_on_the_owner(monkeypatch):
    """run.py re-exports its parts' names as plain names (run.X is the part's own X). A test patches the module
    whose code looks the name up: trace_doc in run_traces reads run_traces.MODEL."""
    import run_sets
    import run_traces
    assert run.preflight is run_engines.preflight and run.trace_doc is run_traces.trace_doc
    assert run.MODEL == run_sets.MODEL == run_traces.MODEL
    monkeypatch.setattr(run_traces, "MODEL", "claude-patched")
    doc = run.trace_doc(set_name="desk-hand", engine_name="hand", desk="single", ticket_id="t001", mode="replay",
                        result={"trace": []}, spent=0.0, paid_calls=0)
    assert doc["model"] == "claude-patched"
