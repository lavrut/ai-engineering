"""Tests for the shared desk core. A scripted fake model stands in for Claude: no proxy, no API."""
import json
from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

import desk_core as core

ALL_TOOLS = {"classify_complaint", "get_ticket", "label_ticket", "search_tickets"}


def rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


# ── tickets ─────────────────────────────────────────────────────────────────
def test_eval_tickets_13_balanced_plus_t061():
    ts = core.load_eval_tickets()
    assert len(ts) == 13 and ts[-1]["id"] == "t061"
    cats = ["billing", "bug", "account", "how_to", "feature_request", "outage"]
    every = [json.loads(l) for l in core.LUMEN_TICKETS.read_text().splitlines() if l.strip()]
    for c in cats:
        want = sorted(t["id"] for t in every if t["category"] == c)[:2]
        assert [t["id"] for t in ts if t["id"] in want] == want
    assert [t["category"] for t in ts[:12]] == [c for c in cats for _ in range(2)]
    assert core.TICKETS == ts


def test_committed_tickets_file_matches_selection_rule():
    assert core.check_eval_tickets() is True


def test_check_detects_drift(tmp_path):
    p = tmp_path / "t.jsonl"
    core.write_eval_tickets(p)
    assert core.check_eval_tickets(p)
    p.write_text(p.read_text().replace("t061", "t999"))
    assert not core.check_eval_tickets(p)


# ── trace ───────────────────────────────────────────────────────────────────
def test_trace_shape_and_validation():
    t = core.Trace()
    t.add(agent="a", kind="tool", name="get_ticket")
    assert t.to_list() == [{"agent": "a", "kind": "tool", "name": "get_ticket",
                            "tokens_in": None, "tokens_out": None, "ms": None}]
    with pytest.raises(ValueError):
        t.add(agent="a", kind="bogus", name="x")
    with pytest.raises(ValueError):
        t.add(agent="a", kind="model", name="x", extra=1)
    assert len(t.to_list()) == 1
    t.to_list().clear()  # a copy: the trace is append-only
    assert len(t.to_list()) == 1


# ── claude() ────────────────────────────────────────────────────────────────
class FakeClient:
    def __init__(self, reply=None, exc=None):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)
        self._reply, self._exc = reply, exc

    def _create(self, **kw):
        self.calls.append(kw)
        if self._exc:
            raise self._exc
        return self._reply


def test_trace_records_tokens(monkeypatch):
    reply = SimpleNamespace(usage=SimpleNamespace(input_tokens=11, output_tokens=7), content=[])
    fake = FakeClient(reply)
    monkeypatch.setattr(core, "make_client", lambda: fake)
    trace = core.Trace()
    out = core.claude([{"role": "user", "content": "hi"}], system="s", trace=trace, agent="triage")
    assert out is reply
    [rec] = trace.to_list()
    assert (rec["agent"], rec["kind"], rec["name"]) == ("triage", "model", core.MODEL)
    assert (rec["tokens_in"], rec["tokens_out"]) == (11, 7) and isinstance(rec["ms"], int)
    assert fake.calls[0]["max_tokens"] == core.MAX_TOKENS == 4000
    assert "tools" not in fake.calls[0]


def test_client_points_at_proxy(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = core.make_client()
    assert str(c.base_url).startswith(core.PROXY_URL) and c.api_key == "sk-ant-dummy"


def test_client_never_reads_the_real_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SENTINEL")
    c = core.make_client()
    assert c.api_key == "sk-ant-dummy" and c.max_retries == 0


@pytest.mark.parametrize("status,body", [
    (402, {"type": "error", "error": {"type": "cost_cap", "message": "proxy: cap reached; not called"}}),
    (404, {"type": "error", "error": {"type": "replay_miss", "message": "no recording for abcd1234; record with: CMD"}}),
    (500, {"type": "error", "error": {"type": "api_error", "message": "boom"}}),
    (404, {"error": "not a dict"}),
])
def test_one_request_and_clear_error(monkeypatch, capsys, status, body):
    """The real anthropic client behind a mock transport: exactly one request, whatever the status."""
    import anthropic
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=body)

    monkeypatch.setattr(core, "make_client", lambda: anthropic.Anthropic(
        base_url=core.PROXY_URL, api_key=core.DUMMY_KEY, max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    expected = core.ProxyError if status in (402, 404) else anthropic.APIStatusError
    with pytest.raises(expected) as info:
        core.claude([{"role": "user", "content": "hi"}], system="s")
    assert len(seen) == 1
    if status == 404 and isinstance(body["error"], dict):
        assert "record with: CMD" in str(info.value) and "replay miss" in str(info.value)
        assert "record with: CMD" in capsys.readouterr().err
    if status == 404 and not isinstance(body["error"], dict):
        assert "replay miss" not in str(info.value)


def test_make_client_really_has_no_retries(monkeypatch):
    """make_client itself (not a stand-in) must not retry a 500."""
    import anthropic
    seen = []
    real = anthropic.Anthropic

    def factory(**kw):
        return real(http_client=httpx.Client(transport=httpx.MockTransport(
            lambda r: (seen.append(r), httpx.Response(500, json={}))[1])), **kw)

    monkeypatch.setattr(anthropic, "Anthropic", factory)
    with pytest.raises(anthropic.APIStatusError):
        core.claude([], system="s")
    assert len(seen) == 1


# ── registry ────────────────────────────────────────────────────────────────
def test_register_and_refuse_duplicates():
    fn = lambda *a, **k: None
    core.register("zz_test", fn)
    try:
        assert core.ENGINES["zz_test"] is fn
        with pytest.raises(ValueError):
            core.register("zz_test", fn)
    finally:
        core.ENGINES.pop("zz_test", None)


# ── tools ───────────────────────────────────────────────────────────────────
def test_tools_are_mcp_only(tmp_path):
    with core.open_desk_tools(("read", "ops"), guard=True, approve="decline", elicit="accept",
                              labels_path=tmp_path / "l.jsonl") as (schema, run_tool, session):
        assert {t["name"] for t in schema} == ALL_TOOLS
        assert all(set(t) == {"name", "description", "input_schema"} for t in schema)
        assert not {"list_files", "read_file", "write_file"} & {t["name"] for t in schema}
        out, err = run_tool("write_file", {"path": "x", "content": "y"})
        assert out.startswith("Unknown tool") and err is True


def run_script(run_tool, script):
    """A scripted fake model: each step is one (tool, args) call, results collected."""
    return [run_tool(name, args) for name, args in script]


def test_guard_applies(tmp_path):
    labels = tmp_path / "l.jsonl"
    with core.open_desk_tools(("read", "ops"), guard=True, approve="decline", elicit="accept",
                              labels_path=labels) as (_, run_tool, session):
        out = run_script(run_tool, [("get_ticket", {"id": "t061"}),
                                    ("label_ticket", {"id": "t001", "label": "outage"})])
        assert session.read_untrusted.is_set()
        assert "denied" in out[1][0].lower() and out[1][1] is True and out[0][1] is False
    assert rows(labels) == []


def test_write_allowed_when_person_accepts(tmp_path):
    labels = tmp_path / "l.jsonl"
    with core.open_desk_tools(("read", "ops"), guard=True, approve="accept", elicit="accept",
                              labels_path=labels) as (_, run_tool, _s):
        run_script(run_tool, [("get_ticket", {"id": "t061"}),
                              ("label_ticket", {"id": "t001", "label": "outage"})])
    assert [r["label"] for r in rows(labels)] == ["outage"]


def test_tool_errors_become_text(tmp_path):
    with core.open_desk_tools(("read",), guard=True, approve="decline", elicit="accept",
                              labels_path=tmp_path / "l.jsonl") as (_, run_tool, _s):
        text, err = run_tool("get_ticket", {"id": "nope"})
        assert isinstance(text, str)


def test_constants_match_proxy():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("proxy_mod", Path(core.REPO) / "agents" / "proxy" / "proxy.py")
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    assert core.MAX_TOKENS == proxy.MAX_TOKENS and core.MODEL == proxy.DEFAULT_MODEL
    assert core.PROXY_URL == f"http://{proxy.HOST}:{proxy.DEFAULT_PORT}"


def test_parallel_calls_are_serialized_and_guarded(tmp_path):
    """A write arriving while a read is in flight waits for the read, then sees its taint."""
    import threading
    import time
    labels = tmp_path / "l.jsonl"
    with core.open_desk_tools(("read", "ops"), guard=True, approve="decline", elicit="accept",
                              labels_path=labels) as (_, run_tool, session):
        entered, release = threading.Event(), threading.Event()
        active, overlap, times = [0], [], {}

        def wrap(tool):
            real = tool.fn

            def inner(**kw):
                active[0] += 1
                overlap.append(active[0])
                times[tool.name + "_start"] = time.perf_counter()
                try:
                    if tool.name == "get_ticket":
                        entered.set()
                        assert release.wait(10)       # the read stays in flight, inside the lock
                    return real(**kw)
                finally:
                    times[tool.name + "_end"] = time.perf_counter()
                    active[0] -= 1
            tool.fn = inner
        for t in session.tools:
            wrap(t)
        res = {}

        def reader():
            res["read"] = run_tool("get_ticket", {"id": "t061"})

        def writer():
            assert entered.wait(10)
            res["write"] = run_tool("label_ticket", {"id": "t001", "label": "outage"})

        ths = [threading.Thread(target=reader), threading.Thread(target=writer)]
        [t.start() for t in ths]
        assert entered.wait(10)
        time.sleep(0.3)                 # the writer is now blocked on the lock (or, without it, has run)
        release.set()
        [t.join(30) for t in ths]
        assert not any(t.is_alive() for t in ths)
    assert res["read"][1] is False
    assert res["write"] == ("The user denied this action.", True)
    assert rows(labels) == []
    assert max(overlap) == 1
    assert "label_ticket_start" not in times or times["label_ticket_start"] > times["get_ticket_end"]
