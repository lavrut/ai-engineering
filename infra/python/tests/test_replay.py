"""The replay driver, the capstone gate and the derived files (Task 6). Free and offline: the agents
replay proxy and the gateway run in-process, on free ports, over the committed recordings."""
import copy
import json
import time

import pytest

import capstone_extras
import config
import replay
from conftest import ROOT

DERIVED = ROOT / "infra" / "data" / "derived"
AGENTS_TRACES = ROOT / "agents" / "data" / "traces"


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    """The whole capstone, once: 957 calls through the gateway, the injector timelines, the demo run."""
    out = tmp_path_factory.mktemp("capstone")
    t0 = time.monotonic()
    cap, files = replay.produce(config.GATEWAY_YAML, out)
    return cap, files, time.monotonic() - t0


def trace_costs(set_name):
    return [c["cost_usd"] for f in sorted((AGENTS_TRACES / set_name).glob("*.json"))
            for c in json.loads(f.read_text(encoding="utf-8"))["proxy_calls"]]


# ── Calls ───────────────────────────────────────────────────────────────────
def test_calls_are_957_in_trace_order():
    calls = replay.calls(ROOT)
    assert len(calls) == 957
    counts = {s: sum(c.set == s for c in calls) for s in replay.SETS}
    assert counts == {"desk-hand": 183, "desk-hand-go": 188, "desk-langgraph": 179, "desk-sdk": 190,
                      "patterns": 60, "patterns-go": 56, "judge": 101}
    first = json.loads(sorted((AGENTS_TRACES / "desk-hand").glob("*.json"))[0].read_text(encoding="utf-8"))
    assert calls[0].set == "desk-hand" and calls[0].key == first["proxy_calls"][0]["key"]
    assert [c.order for c in calls] == list(range(957))
    assert calls[-1].set == "judge"


def test_team_and_tag_mapping():
    calls = replay.calls(ROOT)
    assert {(c.set, c.team) for c in calls} == {
        ("desk-hand", "desk"), ("desk-hand-go", "desk"), ("desk-langgraph", "desk"), ("desk-sdk", "desk"),
        ("patterns", "patterns"), ("patterns-go", "patterns"), ("judge", "judge")}
    hand = [c for c in calls if c.set == "desk-hand"]
    tagged = [c for c in hand if c.tag != "none"]
    assert len(tagged) == 170 and all(len(c.tag) == 4 and c.tag[0] == "t" for c in tagged)
    assert {c.request[1] for c in calls if c.set == "desk-sdk"} == {"/v1/messages?beta=true"}


# ── Run ─────────────────────────────────────────────────────────────────────
def test_run_over_two_sets_writes_capstone(tmp_path):
    cfg = config.load(config.GATEWAY_YAML)
    calls = replay.calls(ROOT, sets=("patterns", "judge"))
    cap = replay.replay_sets(cfg, tmp_path, calls)
    assert [r.status for r in cap.results] == [200] * 161
    figs = replay.capstone_json(cap, replay.gate(cap), [], {})
    assert set(figs["sets"]) == {"patterns", "judge"}
    assert figs["sets"]["patterns"]["hit_rate"] >= 0
    assert figs["sets"]["patterns"]["calls"] == 60 and figs["sets"]["judge"]["calls"] == 101
    judge = sum(c.cost_recorded for c in calls if c.set == "judge")
    assert abs(figs["sets"]["judge"]["cost_usd"] - judge) <= 1e-9
    assert abs(figs["sets"]["patterns"]["cost_usd"] - sum(trace_costs("patterns"))) <= 1e-9
    assert (tmp_path / "capstone.jsonl").exists() and (tmp_path / "ledger.jsonl").exists()


def test_a_second_run_starts_from_empty_stores(tmp_path):
    cfg = config.load(config.GATEWAY_YAML)
    calls = replay.calls(ROOT, sets=("patterns",))
    a = replay.replay_sets(cfg, tmp_path, calls)
    b = replay.replay_sets(cfg, tmp_path, calls)
    assert len(b.rows) == len(b.traces) == 60
    assert [r.cache for r in a.results] == [r.cache for r in b.results]


# ── Gate ────────────────────────────────────────────────────────────────────
def test_gate_ledger_condition_matches_desk_traces(tmp_path):
    cfg = config.load(config.GATEWAY_YAML)
    cap = replay.replay_sets(cfg, tmp_path, replay.calls(ROOT, sets=("desk-hand",)))
    name, ok, detail = replay.gate(cap)[0]
    assert name == "ledger" and ok, detail
    gw = sum(r["cost_usd"] for r in cap.rows)
    assert abs(gw - sum(trace_costs("desk-hand"))) <= 1e-9


def test_gate_goes_red_on_a_planted_overspend(full):
    cap = copy.copy(full[0])
    assert all(ok for _, ok, _ in replay.gate(cap))
    planted = dict(cap.rows[-1], team="judge", cost_usd=0.75, cached=False)
    cap.rows = [*cap.rows, planted]
    lines = {name: (ok, detail) for name, ok, detail in replay.gate(cap)}
    assert lines["budgets"][0] is False and "judge" in lines["budgets"][1]


def test_full_replay_gate_is_green_and_fast(full):
    cap, files, seconds = full
    lines = replay.gate(cap)
    assert [name for name, _, _ in lines] == ["ledger", "budgets", "stages", "figures"]
    assert all(ok for _, ok, _ in lines), lines
    assert files["capstone.json"]["gate"]["ok"] is True
    assert files["capstone.json"]["calls"] == 957 and seconds < 60


def test_every_trace_names_its_stages(full):
    for t in full[0].traces:
        names = [d["stage"] for d in t["decisions"]]
        if t["outcome"] == "ok":
            assert names == list(replay.STAGES)
        elif t["outcome"] == "cached":
            assert names == ["tag", "budget", "cache", "ledger", "trace"]


# ── Review Focus 4: the pattern repeats and the cache ───────────────────────
def test_pattern_repeats_hit_only_when_end_turn_without_tools(full):
    cap = full[0]
    seen, hits, repeats_with_tools = {}, {}, 0
    for r in cap.results:
        body = json.loads(r.call.request[2])
        k = (r.call.request[1], r.call.request[2])
        if r.cache == "hit":
            first = seen[k]
            assert not body.get("tools") and first.cache == "miss" and first.status == 200
            assert capstone_extras.recorded_stop(first.call) == "end_turn"
            hits[r.call.set] = hits.get(r.call.set, 0) + 1
        elif k in seen:
            assert body.get("tools") or capstone_extras.recorded_stop(seen[k].call) != "end_turn"
            repeats_with_tools += bool(body.get("tools"))
        seen.setdefault(k, r)
    # what the data says: the patterns sets' own repeats are tool calls (never cached); patterns-go
    # repeats 19 tool-free end_turn calls patterns made first; desk-hand-go repeats one of desk-hand's
    assert hits == {"desk-hand-go": 1, "patterns-go": 19}
    within = [r for r in cap.results if r.call.set in ("patterns", "patterns-go")]
    assert sum(1 for r in within if r.cache == "skip" and json.loads(r.call.request[2]).get("tools")) > 0
    assert repeats_with_tools > 0


# ── Derived files ───────────────────────────────────────────────────────────
def test_derived_files_shape(full):
    files = full[1]
    c = files["capstone.json"]
    for key in ("about", "config", "calls", "all", "sets", "teams", "cases", "injector", "gate"):
        assert key in c
    assert set(c["sets"]) == set(replay.SETS) and set(c["teams"]) == {"desk", "patterns", "judge"}
    for figs in [*c["sets"].values(), *c["teams"].values(), c["all"]]:
        assert set(figs) == set(replay.FIGURES)
    assert len(c["cases"]) == 14 and set(c["cases"][0]) == {"id", "decisions", "status"}
    assert c["injector"]["flaky"]["outcome"] == "ok" and c["injector"]["flaky"]["status"] == 200
    assert [a["status"] for a in c["injector"]["flaky"]["attempts"]] == [429, 500, 200]
    assert c["injector"]["dead"]["status"] == 502 and c["injector"]["dead"]["outcome"] == "failed"
    assert [a["status"] for a in c["injector"]["ratelimited"]["attempts"]] == [429, 429, 200]
    led = files["ledger.json"]
    assert set(led) == {"about", "teams", "tags", "top_tags", "agents"}
    assert abs(led["agents"]["desk-hand"] - sum(trace_costs("desk-hand"))) <= 1e-9
    s = files["traces.sample.json"]
    assert [x["label"] for x in s["traces"]] == ["cached", "retried", "refused"]
    assert [x["run"] for x in s["traces"]] == ["capstone", "flaky", "demo"]
    cached, retried, refused = (x["trace"] for x in s["traces"])
    assert cached["outcome"] == "cached" and retried["outcome"] == "ok" and len(retried["attempts"]) == 3
    assert refused["status"] == 402 and refused["decisions"][-1]["stage"] == "budget"


def test_sample_traces_carry_no_bodies(full):
    def strings(x):
        if isinstance(x, dict):
            assert "body" not in x
            for v in x.values():
                yield from strings(v)
        elif isinstance(x, list):
            for v in x:
                yield from strings(v)
        elif isinstance(x, str):
            yield x
    assert max(len(s) for s in strings(full[1]["traces.sample.json"]["traces"])) <= 200


def test_check_detects_drift(full, tmp_path):
    files = full[1]
    replay.write_files(files, tmp_path)
    assert replay.drift(files, tmp_path) == []
    c = json.loads((tmp_path / "capstone.json").read_text())
    c["sets"]["patterns"]["cache_hits"] += 1
    (tmp_path / "capstone.json").write_text(json.dumps(c))
    assert replay.drift(files, tmp_path) == ["capstone.json"]


def test_sample_drift_ignores_the_gateway_clock_and_ids(full, tmp_path):
    files = copy.deepcopy(full[1])
    replay.write_files(files, tmp_path)
    for x in files["traces.sample.json"]["traces"]:
        x["trace"].update(id="0" * 16, ts=1.0, ms=99.9)
    assert replay.drift(files, tmp_path) == []
    files["traces.sample.json"]["traces"][0]["trace"]["status"] = 500
    assert replay.drift(files, tmp_path) == ["traces.sample.json"]


def test_committed_derived_files_are_up_to_date(full):
    assert replay.drift(full[1], DERIVED) == []


# ── Command line ────────────────────────────────────────────────────────────
def test_bad_config_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("upstream: http://127.0.0.1:8791\n")
    assert replay.main(["--config", str(bad), "--start", "--out", str(tmp_path / "run")]) == 2
    assert "config error" in capsys.readouterr().out


def test_sets_run_writes_only_under_out(tmp_path, capsys):
    before = {p.name: p.stat().st_mtime for p in DERIVED.glob("*.json")}
    assert replay.main(["--start", "--sets", "judge", "--gate", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "gate" in out and (tmp_path / "capstone.json").exists()
    assert {p.name: p.stat().st_mtime for p in DERIVED.glob("*.json")} == before


def test_check_exits_1_on_drift_and_names_the_file(monkeypatch, tmp_path, capsys):
    stale = tmp_path / "derived"
    stale.mkdir()
    for p in DERIVED.glob("*.json"):
        (stale / p.name).write_text(p.read_text())
    led = json.loads((stale / "ledger.json").read_text())
    led["teams"]["desk"]["requests"] += 1
    (stale / "ledger.json").write_text(json.dumps(led))
    monkeypatch.setattr(replay, "DERIVED", stale)
    assert replay.main(["--start", "--check", "--out", str(tmp_path / "run")]) == 1
    out = capsys.readouterr().out
    assert "ledger.json" in out and "capstone.json" not in out.split("drifted:")[1]
