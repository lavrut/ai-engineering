import json
import random

import pytest
from conftest import FIXTURES

import config
import ledger
import stages

CASES = FIXTURES / "cases.json"
HAIKU = "claude-haiku-5-5"
TICKET = 'Triage this.\n<ticket id="t008">My notes vanished after sync.</ticket>'


def cfg_with(overrides=None):
    base = json.loads((FIXTURES / "config.json").read_text())
    return config.from_dict(stages.merge_config(base, overrides or {}))


def req(body=None, headers=None, path="/v1/messages"):
    body = {"model": HAIKU, "max_tokens": 1024, "messages": [{"role": "user", "content": TICKET}]} if body is None else body
    return stages.make_request("POST", path, {"X-Team": "desk", **(headers or {})}, stages.canonical(body))


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def state(clock=None):
    return stages.State(spend={}, cache={}, buckets={}, clock=clock or Clock(), rng=random.Random(0))


def ok_send(model, r):
    return stages.Upstream(status=200, headers={"content-type": "application/json"}, body=stages.CASE_REPLY)


# ── tag ──
def test_tag_reads_team_and_ticket():
    d = stages.tag(req(), cfg_with())
    assert (d["stage"], d["verdict"], d["tag"]) == ("tag", "desk", "t008")


def test_tag_without_team_or_ticket():
    r = stages.make_request("POST", "/v1/messages", {}, stages.canonical({"model": HAIKU, "messages": [{"role": "user", "content": "hi"}]}))
    d = stages.tag(r, cfg_with())
    assert (d["verdict"], d["tag"]) == ("untagged", "none")


def test_tag_header_wins_over_body():
    assert stages.tag(req(headers={"x-request-tag": "t042"}), cfg_with())["tag"] == "t042"


def test_tag_reads_text_blocks():
    body = {"model": HAIKU, "messages": [{"role": "user", "content": [{"type": "text", "text": "see t123 please"}]}]}
    assert stages.tag(req(body), cfg_with())["tag"] == "t123"


# ── budget ──
def test_budget_refuses_when_worst_case_crosses_limit():
    s = state(); s.spend["desk"] = 0.95
    r = req({"model": HAIKU, "max_tokens": 120000, "messages": [{"role": "user", "content": "x"}]})
    d = stages.budget(r, "desk", s, cfg_with(), ledger.lab_prices())
    assert d["verdict"] == "refuse"
    assert d["worst_usd"] == pytest.approx(0.06, abs=1e-4) and d["spent_usd"] == 0.95 and d["limit_usd"] == 1.00


def test_budget_warns_over_the_warn_line_and_allows_below():
    s = state(); s.spend["desk"] = 0.85
    assert stages.budget(req(), "desk", s, cfg_with(), ledger.lab_prices())["verdict"] == "warn"
    s.spend["desk"] = 0.10
    assert stages.budget(req(), "desk", s, cfg_with(), ledger.lab_prices())["verdict"] == "allow"


def test_budget_refuses_untagged_and_unknown_teams():
    assert stages.budget(req(), "untagged", state(), cfg_with(), ledger.lab_prices())["verdict"] == "refuse"
    assert stages.budget(req(), "nobody", state(), cfg_with(), ledger.lab_prices())["verdict"] == "refuse"


def test_budget_books_untagged_to_a_named_team():
    d = stages.budget(req(), "untagged", state(), cfg_with({"untagged": "lab"}), ledger.lab_prices())
    assert (d["verdict"], d["team"], d["limit_usd"]) == ("allow", "lab", 0.5)


def test_worst_case_tokens():
    body = {"model": HAIKU, "max_tokens": 1024, "messages": [{"role": "user", "content": "hi"}]}
    assert stages.worst_case_tokens(req(body)) == (len(stages.canonical(body).decode()) // 4 + 1, 1024)
    assert stages.worst_case_tokens(req({"model": HAIKU, "messages": []}))[1] == 0


# ── cache ──
def test_cache_key_is_sha256_of_path_and_body():
    import hashlib
    assert stages.cache_key("/v1/messages", b"{}") == hashlib.sha256(b"/v1/messages\n{}").hexdigest()


def test_cache_skips_tool_calls():
    body = {"model": HAIKU, "max_tokens": 10, "tools": [{"name": "lookup"}], "messages": [{"role": "user", "content": "x"}]}
    assert stages.cache_lookup(req(body), state(), cfg_with())["verdict"] == "skip"
    assert stages.cache_lookup(req(body), state(), cfg_with({"cache": {"skip_tools": False}}))["verdict"] == "miss"


def test_cache_skips_count_tokens():
    assert stages.cache_lookup(req(path="/v1/messages/count_tokens"), state(), cfg_with())["verdict"] == "skip"


def test_cache_hits_then_expires():
    clock, cfg = Clock(), cfg_with()
    s, r = state(clock), req()
    out = stages.run_chain(r, s, cfg, ok_send, sleep=lambda _: None)
    assert out.status == 200 and not out.cached
    d = stages.cache_lookup(r, s, cfg)
    assert d["verdict"] == "hit" and d["key"] == stages.cache_key(r.path, r.body)
    hit = stages.run_chain(r, s, cfg, ok_send, sleep=lambda _: None)
    assert hit.cached and hit.headers["x-cache"] == "hit" and hit.body == stages.CASE_REPLY and hit.cost_usd == 0.0
    clock.t += 3600
    assert stages.cache_lookup(r, s, cfg)["verdict"] == "miss"


def test_cache_stores_only_end_turn():
    s, r = state(), req()

    def max_tokens_send(model, rq):
        body = stages.CASE_REPLY.replace(b'"end_turn"', b'"max_tokens"')
        return stages.Upstream(status=200, headers={"content-type": "application/json"}, body=body)
    stages.run_chain(r, s, cfg_with(), max_tokens_send, sleep=lambda _: None)
    assert s.cache == {}


# ── route ──
def test_route_maps_the_model():
    d = stages.route(req(), cfg_with())
    assert (d["verdict"], d["to"], d["fallback"]) == ("route", HAIKU, [])


def test_route_denies_unknown_model():
    d = stages.route(req({"model": "claude-x", "messages": []}), cfg_with())
    assert (d["stage"], d["verdict"]) == ("route", "deny")


# ── limit ──
def test_limit_waits_then_refuses():
    clock = Clock()
    s, cfg = state(clock), cfg_with()                       # rpm 60: one request a second; max_wait_ms 1000
    s.buckets[HAIKU] = stages.Bucket(tokens=200000, requests=1, at=clock.t)
    assert stages.limit(HAIKU, 100, s, cfg)["verdict"] == "allow"
    d = stages.limit(HAIKU, 100, s, cfg)
    assert (d["verdict"], d["wait_ms"]) == ("wait", 1000)
    d = stages.limit(HAIKU, 100, s, cfg)
    assert (d["verdict"], d["wait_ms"]) == ("refuse", 2000)
    clock.t += 2.0
    assert stages.limit(HAIKU, 100, s, cfg)["verdict"] == "allow"


def test_limit_refuses_a_request_bigger_than_tpm():
    assert stages.limit(HAIKU, 200001, state(), cfg_with())["verdict"] == "refuse"


# ── retry ──
def test_backoff_is_capped_and_jittered():
    cfg, rng = cfg_with(), random.Random(0)
    expect_rng = random.Random(0)
    for attempt, base in [(0, 200), (1, 400), (2, 800), (3, 1600), (4, 2000), (9, 2000)]:
        ms = stages.retry_plan(cfg, attempt, rng)
        assert ms == base + expect_rng.randrange(200) and base <= ms < base + 200


def test_retry_falls_back_and_never_edits_the_body():
    cfg = cfg_with({"models": {"claude-sonnet-5-5": {"rpm": 60, "tpm": 200000, "price": "lab"}},
                    "routes": {HAIKU: {"fallback": ["claude-sonnet-5-5"]}}})
    seen, script = [], [500, 500, 500, 200]

    def send(model, r):
        seen.append((model, r.body))
        st = script.pop(0)
        return ok_send(model, r) if st == 200 else stages.Upstream(status=st, headers={}, body=b"{}")
    r, waits = req(), []
    out = stages.run_chain(r, state(), cfg, send, sleep=waits.append)
    assert out.status == 200 and [m for m, _ in seen] == [HAIKU] * 3 + ["claude-sonnet-5-5"]
    assert all(b == r.body for _, b in seen) and len(waits) == 2
    assert out.decisions[-1]["verdict"] == "fallback" and out.decisions[-1]["to"] == "claude-sonnet-5-5"
    assert [a["status"] for a in out.attempts] == [500, 500, 500, 200]


# ── responses ──
SSE = (b'event: message_start\ndata: {"type":"message_start","message":{"model":"claude-haiku-5-5",'
       b'"usage":{"input_tokens":12,"cache_read_input_tokens":3,"output_tokens":1}}}\n\n'
       b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
       b'"usage":{"output_tokens":7}}\n\n')
SSE_HEADERS = {"content-type": "text/event-stream"}


def test_usage_from_sse():
    assert stages.response_usage(SSE_HEADERS, SSE) == {
        "input_tokens": 12, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 3, "output_tokens": 7}


def test_stop_reason_from_sse():
    assert stages.stop_reason(SSE_HEADERS, SSE) == "end_turn"


def test_usage_and_stop_reason_from_json():
    h = {"content-type": "application/json"}
    assert stages.response_usage(h, stages.CASE_REPLY)["output_tokens"] == 5
    assert stages.stop_reason(h, stages.CASE_REPLY) == "end_turn"
    assert stages.response_usage(h, b"not json") is None and stages.stop_reason(h, b"{}") is None


# ── the chain ──
def test_chain_prices_and_books_spend():
    s = state()
    out = stages.run_chain(req(), s, cfg_with(), ok_send, sleep=lambda _: None)
    assert out.usage["input_tokens"] == 10 and out.cost_usd == pytest.approx((10*0.10 + 5*0.50) / 1e6)
    assert s.spend["desk"] == out.cost_usd and (out.team, out.tag) == ("desk", "t008")


def test_chain_400_on_bad_body():
    r = stages.make_request("POST", "/v1/messages", {"x-team": "desk"}, b"{nope")
    out = stages.run_chain(r, state(), cfg_with(), ok_send)
    assert out.status == 400 and out.decisions[0]["stage"] == "parse"
    out = stages.run_chain(req({"messages": []}), state(), cfg_with(), ok_send)
    assert out.status == 400


def test_a_stage_that_raises_is_a_500_never_an_exception():
    def broken(model, r):
        raise RuntimeError("boom")
    out = stages.run_chain(req(), state(), cfg_with(), broken, sleep=lambda _: None)
    assert out.status == 500
    assert out.decisions[-1] == {"stage": "retry", "verdict": "error", "detail": "RuntimeError"}


def test_decisions_carry_string_stage_verdict_detail():
    out = stages.run_chain(req(), state(), cfg_with(), ok_send, sleep=lambda _: None)
    assert [d["stage"] for d in out.decisions] == ["tag", "budget", "cache", "route", "limit", "retry"]
    assert all(isinstance(d[k], str) for d in out.decisions for k in ("stage", "verdict", "detail"))


def test_merge_config_is_deep_and_overrides_win():
    assert stages.merge_config({"a": {"b": 1, "c": 2}, "d": 3}, {"a": {"b": 9}}) == {"a": {"b": 9, "c": 2}, "d": 3}


# ── the shared cases ──
CASE_LIST = json.loads(CASES.read_text())
RESULTS = {r["id"]: r for r in stages.run_cases(CASES)}


def test_the_case_ids():
    assert [c["id"] for c in CASE_LIST] == [
        "cacheable_miss", "cacheable_hit", "tool_call_skip", "unknown_model_deny", "team_over_budget", "team_warn",
        "no_team_refused", "limit_wait", "limit_refuse", "retry_then_ok", "fallback_after_attempts", "all_fail_502",
        "dropped_connection_retry", "budget_refusal_beats_cache_hit"]


@pytest.mark.parametrize("case", CASE_LIST, ids=[c["id"] for c in CASE_LIST])
def test_every_case_matches_its_expectation(case):
    got = RESULTS[case["id"]]
    assert got == {"id": case["id"], **case["expect"]}
    assert list(got) == ["id", "decisions", "status"]


def test_cli_prints_one_line_per_case(capsys):
    assert stages.main(["--cases", str(CASES)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 14
    assert lines[0].startswith('{"id":"cacheable_miss","decisions":[{"stage":"tag","verdict":"desk"}')
