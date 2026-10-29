import json

import pytest

import cachefile
import ledger
import responses
import traces
from stages import CacheEntry


def row(team="search", tag="a", cost=0.5, **kw):
    r = dict(ts=1.0, team=team, tag=tag, model="claude-haiku-5-5", input_tokens=1, cache_creation_input_tokens=0,
             cache_read_input_tokens=0, output_tokens=1, cost_usd=cost, replayed=False, cached=False, status=200)
    return {**r, **kw}


def test_ledger_appends_and_sums(tmp_path):
    p = tmp_path / "l.jsonl"
    led = ledger.Ledger(p)
    led.append(row(cost=0.25)); led.append(row(cost=0.5)); led.append(row(team="ops", cost=None))
    assert led.spend_by_team() == {"search": 0.75}
    assert len(p.read_text().splitlines()) == 3
    again = ledger.Ledger(p)
    assert again.rows() == led.rows() and again.spend_by_team() == {"search": 0.75}


def test_half_written_last_line_is_skipped_with_a_warning(tmp_path, capsys):
    p = tmp_path / "l.jsonl"
    p.write_text(json.dumps(row()) + '\n{"ts": 2, "te')
    assert len(ledger.Ledger(p).rows()) == 1
    err = capsys.readouterr().err
    assert err == f"warning: {p}: skipped a half-written last line\n"
    p.write_text('{"ts": 1\n' + json.dumps(row()) + "\n")
    with pytest.raises(ValueError, match="line 1"):
        ledger.Ledger(p)


def test_summary_top_tags():
    rows = [row(tag=f"t{i}", cost=float(i)) for i in range(12)]
    rows += [row(tag="b", cost=5.0), row(team="ops", cost=None, tag="z", status=402, cached=True)]
    s = ledger.summary(rows)
    assert len(s["top_tags"]) == 10
    assert s["top_tags"][:3] == [["t11", 11.0], ["t10", 10.0], ["t9", 9.0]]
    assert s["top_tags"][6:8] == [["b", 5.0], ["t5", 5.0]]  # equal cost: by tag
    assert s["top_tags"][-1] == ["t3", 3.0]
    assert s["teams"]["ops"] == {"requests": 1, "cost_usd": 0.0, "cached": 1, "refused": 1}
    assert s["teams"]["search"]["requests"] == 13 and "z" not in s["tags"]


def trace(id, parent=None, **kw):
    t = dict(id=id, parent_id=parent, ts=1.0, team="search", tag="a", model="m", path="/v1/messages", status=200,
             decisions=[], ms=10.4, usage=None, cost_usd=0.001, cached=False, attempts=[], outcome="ok")
    return {**t, **kw}


def test_trace_roundtrip(tmp_traces):
    store = traces.TraceStore(tmp_traces, "run1")
    ts = [trace("a"), trace("b", cost_usd=None, outcome="refused")]
    for t in ts:
        store.write(t)
    assert traces.read_run(store.path) == ts == store.read()


def test_query_filters_and_orders(tmp_traces, capsys):
    ts = [trace("a", ms=5), trace("b", ms=50, cost_usd=0.5, team="ops"), trace("c", ms=20, cost_usd=None, outcome="refused", status=402)]
    assert [t["id"] for t in traces.by(ts, team="ops")] == ["b"]
    assert [t["id"] for t in traces.by(ts, outcome="refused", tag="a")] == ["c"]
    assert [t["id"] for t in traces.slowest(ts, 2)] == ["b", "c"]
    assert [t["id"] for t in traces.costliest(ts, 5)] == ["b", "a"]
    p = tmp_traces / "r.jsonl"
    p.write_text("".join(json.dumps(t) + "\n" for t in ts))
    assert traces.main([str(p), "--outcome", "refused"]) == 0
    assert traces.main([str(p), "--costliest", "1"]) == 0
    assert capsys.readouterr().out.splitlines() == ["c 402 search a 20 - refused", "b 200 ops a 50 0.500000 ok"]


def test_tree_follows_parent_ids(tmp_traces, capsys):
    ts = [trace("root"), trace("k2", "root", ts=3), trace("k1", "root", ts=2), trace("g", "k1", ts=4), trace("x", "other")]
    assert [t["id"] for t in traces.tree(ts, "root")] == ["k1", "g", "k2"]
    p = tmp_traces / "r.jsonl"
    p.write_text("".join(json.dumps(t) + "\n" for t in ts))
    traces.main([str(p), "--tree", "root"])
    assert [l.split()[0] for l in capsys.readouterr().out.splitlines()] == ["root", "k1", "g", "k2"]


def test_corrupt_cache_loads_empty(tmp_path, capsys):
    p = tmp_path / "cache.json"
    assert cachefile.load_cache(p) == {}
    p.write_text("{not json")
    assert cachefile.load_cache(p) == {}
    assert "warning:" in capsys.readouterr().err


def test_cache_save_is_atomic(tmp_path):
    p = tmp_path / "sub" / "cache.json"
    cache = {"k": CacheEntry(200, {"content-type": "application/json"}, b'{"a":"\xc3\xa9"}\x00', "end_turn", 12.5, True)}
    cachefile.save_cache(p, cache)
    assert sorted(f.name for f in p.parent.iterdir()) == ["cache.json"]
    assert cachefile.load_cache(p) == cache


def test_response_without_usage_gives_null_cost():
    body = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    headers = {"content-type": "text/event-stream"}
    usage = responses.response_usage(headers, body)
    assert usage is None
    cost = ledger.cost_of("claude-haiku-5-5", usage or {}, ledger.lab_prices())
    r = row(cost=cost)
    assert r["cost_usd"] is None


def test_ledger_append_after_half_written_line_keeps_the_new_row(tmp_path, capsys):
    p = tmp_path / "l.jsonl"
    p.write_text(json.dumps(row(tag="old")) + '\n{"ts": 2, "te')
    led = ledger.Ledger(p)
    assert "skipped a half-written last line" in capsys.readouterr().err
    led.append(row(tag="new"))
    assert [r["tag"] for r in ledger.Ledger(p).rows()] == ["old", "new"]
    led.append(row(tag="newer"))
    assert [r["tag"] for r in ledger.Ledger(p).rows()] == ["old", "new", "newer"]


def test_trace_write_after_half_written_line_keeps_the_new_row(tmp_traces, capsys):
    store = traces.TraceStore(tmp_traces, "r")
    store.write(trace("a"))
    with store.path.open("a") as f:
        f.write('{"id": "half')
    assert [t["id"] for t in store.read()] == ["a"]
    assert "skipped a half-written last line" in capsys.readouterr().err
    store.write(trace("b"))
    store.write(trace("c"))
    assert [t["id"] for t in store.read()] == ["a", "b", "c"]
