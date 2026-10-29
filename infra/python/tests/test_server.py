"""Server tests: the gateway in-process against the injector (in-process) and the agents replay proxy."""
import dataclasses
import http.client
import json
import sys
import threading
from pathlib import Path

import pytest
from conftest import FIXTURES, ROOT

import config
import gateway
import injector
import ledger
import traces
from responses import canonical

HAIKU = "claude-haiku-5-5"
BODY = {"model": HAIKU, "max_tokens": 64, "messages": [{"role": "user", "content": "Say ok. Ticket t008."}]}
SCRIPTS = FIXTURES / "injector"


class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body)


def post(url, path, body, headers=None):
    host, port = url.removeprefix("http://").split(":")
    data = body if isinstance(body, bytes) else canonical(body)
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    try:
        conn.request("POST", path, body=data, headers={"content-type": "application/json",
                                                       "anthropic-version": "2023-06-01", **(headers or {})})
        r = conn.getresponse()
        return Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    finally:
        conn.close()


def last_trace(gw):
    return traces.read_run(gw.traces.path)[-1]


def run_in_thread(server):
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server


@pytest.fixture
def servers():
    started = []
    yield started
    for s in started:
        s.shutdown()
        s.server_close()


def base_cfg(upstream, **budgets):
    cfg = config.load(config.GATEWAY_YAML)
    cfg = dataclasses.replace(cfg, upstream=upstream, retry=config.RetryCfg(cfg.retry.attempts, 1, 2000))
    if budgets:
        cfg = dataclasses.replace(cfg, budgets={**cfg.budgets, **budgets})
    return cfg


def start_gateway(servers, tmp_path, cfg):
    gw = gateway.make_server(cfg, port=0, ledger_path=tmp_path / "ledger.jsonl", traces_dir=tmp_path / "traces",
                             run="test", cache_path=tmp_path / "cache.json")
    servers.append(run_in_thread(gw))
    return gw


def start_injector(servers, script):
    inj = injector.make_server(script, port=0)
    servers.append(run_in_thread(inj))
    return inj


def gateway_on(servers, tmp_path, script, **budgets):
    inj = start_injector(servers, script)
    gw = start_gateway(servers, tmp_path, base_cfg(inj.url, **budgets))
    gw.injector = inj
    return gw


@pytest.fixture
def gw(servers, tmp_path):
    return gateway_on(servers, tmp_path, [200])


@pytest.fixture
def injector_ok(gw):
    """The gw fixture's own injector, scripted [200]."""
    return gw.injector


@pytest.fixture
def gw_flaky(servers, tmp_path):
    return gateway_on(servers, tmp_path, injector.load_script(SCRIPTS / "flaky.json"))


@pytest.fixture
def gw_dead(servers, tmp_path):
    return gateway_on(servers, tmp_path, injector.load_script(SCRIPTS / "dead.json"))


@pytest.fixture
def gw_small_budget(servers, tmp_path):
    return gateway_on(servers, tmp_path, [200], desk=config.Budget(0.00001, None))


# ── The injector ────────────────────────────────────────────────────────────
def test_injector_fixtures_are_the_three_scripts():
    assert injector.load_script(SCRIPTS / "flaky.json") == [429, 500, 200]
    assert injector.load_script(SCRIPTS / "dead.json") == [500]
    assert injector.load_script(SCRIPTS / "ratelimited.json") == [429, 429, 200]


def test_injector_answers_in_script_order_and_cycles(servers):
    inj = start_injector(servers, [429, 200, "drop"])
    a, b = post(inj.url, "/v1/messages", BODY), post(inj.url, "/v1/messages", BODY)
    assert (a.status, a.headers["retry-after"]) == (429, "0")
    assert b.status == 200 and b.json()["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert b.json()["stop_reason"] == "end_turn"
    with pytest.raises((http.client.RemoteDisconnected, ConnectionError)):
        post(inj.url, "/v1/messages", BODY)
    assert post(inj.url, "/v1/messages", BODY).status == 429  # cycled back to the start
    assert inj.served == 4


def test_injector_refuses_a_bad_script(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('[200, "explode"]')
    with pytest.raises(ValueError, match="explode"):
        injector.load_script(p)


# ── The gateway against the injector ────────────────────────────────────────
def test_passthrough_answers_and_traces(gw, injector_ok):
    r = post(gw.url, "/v1/messages", BODY, {"x-team": "desk", "x-request-tag": "t008", "x-parent-id": "abc"})
    assert r.status == 200 and r.headers["x-cache"] == "miss" and "x-gateway-trace" in r.headers
    t = last_trace(gw)
    assert t["decisions"][0]["stage"] == "tag"
    assert [d["stage"] for d in t["decisions"]] == ["tag", "budget", "cache", "route", "limit", "retry", "ledger", "trace"]
    assert t["id"] == r.headers["x-gateway-trace"] and len(t["id"]) == 16 and t["parent_id"] == "abc"
    assert (t["team"], t["tag"], t["outcome"], t["status"]) == ("desk", "t008", "ok", 200)
    # the body went upstream byte for byte, without the gateway's own headers
    seen = gw.injector.last
    assert seen["body"] == canonical(BODY)
    assert not any(k.startswith("x-") for k in seen["headers"])
    assert seen["headers"]["user-agent"] == "ai-engineering-course-sample/1.0"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    row = gw.ledger.rows()[-1]
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 5}
    assert row["cost_usd"] == ledger.cost_of(HAIKU, usage, ledger.resolve_prices(gw.cfg)) == t["cost_usd"]
    assert (row["team"], row["tag"], row["model"], row["status"], row["cached"]) == ("desk", "t008", HAIKU, 200, False)


def test_repeat_is_a_cache_hit_and_the_cache_is_saved(gw, tmp_path):
    post(gw.url, "/v1/messages", BODY, {"x-team": "desk"})
    r = post(gw.url, "/v1/messages", BODY, {"x-team": "desk"})
    assert r.status == 200 and r.headers["x-cache"] == "hit" and gw.injector.served == 1
    assert last_trace(gw)["outcome"] == "cached" and gw.ledger.rows()[-1]["cost_usd"] == 0.0
    assert (tmp_path / "cache.json").exists()


def test_flaky_upstream_is_retried(gw_flaky):               # [429, 500, 200]
    r = post(gw_flaky.url, "/v1/messages", BODY, {"x-team": "desk"})
    assert r.status == 200 and [a["status"] for a in last_trace(gw_flaky)["attempts"]] == [429, 500, 200]


def test_dropped_connection_is_retried(servers, tmp_path):
    g = gateway_on(servers, tmp_path, ["drop", 200])
    r = post(g.url, "/v1/messages", BODY, {"x-team": "desk"})
    assert r.status == 200 and [a["status"] for a in last_trace(g)["attempts"]] == [None, 200]


def test_dead_upstream_is_502_after_attempts(gw_dead):
    r = post(gw_dead.url, "/v1/messages", BODY, {"x-team": "desk"})
    t = last_trace(gw_dead)
    assert r.status == 502 and len(t["attempts"]) == 3 and t["outcome"] == "failed"
    assert r.json()["type"] == "error"


def test_budget_refusal_is_402_and_traced(gw_small_budget):
    r = post(gw_small_budget.url, "/v1/messages", BODY, {"x-team": "desk"})
    msg = r.json()["error"]["message"]
    assert r.status == 402 and "team desk" in msg and "limit" in msg
    t = last_trace(gw_small_budget)
    assert t["outcome"] == "refused" and [d["stage"] for d in t["decisions"]] == ["tag", "budget"]
    assert gw_small_budget.injector.served == 0
    assert gw_small_budget.ledger.rows()[-1]["status"] == 402 and gw_small_budget.ledger.rows()[-1]["cost_usd"] is None


def test_unknown_model_is_404(gw):
    r = post(gw.url, "/v1/messages", {**BODY, "model": "claude-unknown-1"}, {"x-team": "desk"})
    assert r.status == 404 and last_trace(gw)["outcome"] == "denied" and gw.injector.served == 0


def test_no_team_is_refused(gw):                              # untagged: refuse -> 402
    r = post(gw.url, "/v1/messages", BODY)
    t = last_trace(gw)
    assert r.status == 402 and t["team"] == "untagged" and t["outcome"] == "refused"
    assert gw.injector.served == 0


def test_malformed_body_is_400_before_any_stage(gw):         # Review Focus 1
    r = post(gw.url, "/v1/messages", b"{not json", {"x-team": "desk"})
    t = last_trace(gw)
    assert r.status == 400 and r.json()["error"]["type"] == "invalid_request_error"
    assert [d["stage"] for d in t["decisions"]] == ["parse"] and t["decisions"][0]["verdict"] == "refuse"
    assert gw.injector.served == 0


def test_unknown_path_is_400_before_any_stage(gw):
    r = post(gw.url, "/v1/other", BODY, {"x-team": "desk"})
    assert r.status == 400 and [d["stage"] for d in last_trace(gw)["decisions"]] == ["parse"]
    assert gw.injector.served == 0


def test_spend_survives_a_restart_through_the_ledger(servers, tmp_path):
    g = gateway_on(servers, tmp_path, [200])
    post(g.url, "/v1/messages", BODY, {"x-team": "desk"})
    again = gateway.make_server(g.cfg, port=0, ledger_path=tmp_path / "ledger.jsonl", traces_dir=tmp_path / "traces",
                                cache_path=tmp_path / "cache.json")
    try:
        assert again.state.spend["desk"] == pytest.approx(g.state.spend["desk"]) and again.state.cache
    finally:
        again.server_close()


# ── The command line ────────────────────────────────────────────────────────
def write_cfg(tmp_path, upstream):
    text = config.GATEWAY_YAML.read_text().replace("upstream: http://127.0.0.1:8791", f"upstream: {upstream}")
    p = tmp_path / "gateway.yaml"
    p.write_text(text)
    return p


def test_remote_upstream_refused_without_live(tmp_path, capsys):  # Review Focus 5
    p = write_cfg(tmp_path, "http://example.com:8791")
    assert gateway.main(["--config", str(p), "--port", "0"]) == 2
    out = capsys.readouterr()
    assert "example.com" in out.out + out.err


def test_live_asks_and_declines(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-key")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    monkeypatch.setattr(gateway, "make_server", lambda *a, **k: pytest.fail("served without a yes"))
    p = write_cfg(tmp_path, "https://api.anthropic.com")
    assert gateway.main(["--config", str(p), "--live", "--max-cost", "0.50", "--port", "0"]) == 0
    out = capsys.readouterr().out
    assert "desk $1.00" in out and "$0.50" in out and "not started" in out
    assert "dummy-not-a-key" not in out


def test_live_needs_the_anthropic_host(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-key")
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("asked before checking the host"))
    p = write_cfg(tmp_path, "https://example.com")
    assert gateway.main(["--config", str(p), "--live"]) == 2
    assert "example.com" in capsys.readouterr().out


# ── Through the agents replay proxy ─────────────────────────────────────────
def test_replay_proxy_passthrough(servers, tmp_path):
    sys.path.insert(0, str(ROOT / "agents" / "proxy"))
    import proxy
    import proxy_cost
    import proxy_server
    store = ROOT / "agents" / "data" / "recordings" / "desk-hand"
    px = proxy_server.make_server(mode="replay", port=0, store_dir=store)
    servers.append(run_in_thread(px))
    recording = json.loads(sorted(store.glob("*.json"))[0].read_text(encoding="utf-8"))
    summary = recording["request_summary"]
    gw = start_gateway(servers, tmp_path, base_cfg(f"http://127.0.0.1:{px.server_address[1]}"))
    data = json.dumps(summary["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    r = post(gw.url, summary["path"], data, {"x-team": "desk"})
    assert r.status == 200 and r.body == proxy.playback_bytes(recording["response"])
    up = recording["upstream"]
    usage = {k: up[k] for k in proxy_cost.USAGE_FIELDS}
    assert gw.ledger.rows()[-1]["cost_usd"] == proxy_cost.ledger_cost(up["model"], usage)
    assert last_trace(gw)["outcome"] == "ok"


def test_a_stage_that_raises_is_500_and_traced(servers, tmp_path):
    def broken(model, req):
        raise RuntimeError("bug")
    g = gateway.make_server(base_cfg("http://127.0.0.1:9"), ledger_path=tmp_path / "l.jsonl", traces_dir=tmp_path,
                            cache_path=tmp_path / "c.json", send=broken)
    servers.append(run_in_thread(g))
    r = post(g.url, "/v1/messages", BODY, {"x-team": "desk"})
    t = last_trace(g)
    assert r.status == 500 and t["outcome"] == "error"
    assert t["decisions"][-1] == {"stage": "retry", "verdict": "error", "detail": "RuntimeError"}


def test_live_cap_stops_before_calling(servers, tmp_path):     # a fake upstream: no key, no network
    calls = []

    def fake(model, req):
        calls.append(model)
        return gateway.Upstream(200, {"content-type": "application/json"}, injector.REPLY)
    g = gateway.make_server(base_cfg("https://api.anthropic.com"), ledger_path=tmp_path / "l.jsonl",
                            traces_dir=tmp_path, cache_path=tmp_path / "c.json", send=fake, live=True,
                            max_cost=0.00001)
    servers.append(run_in_thread(g))
    r = post(g.url, "/v1/messages", BODY, {"x-team": "desk"})
    assert r.status == 402 and r.json()["error"]["type"] == "cost_cap" and calls == []
    assert last_trace(g)["outcome"] == "refused" and g.ledger.rows()[-1]["replayed"] is False


def test_the_key_never_goes_to_another_host():
    with pytest.raises(ValueError, match="only ever sent"):
        gateway.make_send("http://127.0.0.1:8791", api_key="dummy-not-a-key")
