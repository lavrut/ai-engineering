"""compare_llm: the Claude comparison, replay only. No test here may reach the network:
`anthropic.Anthropic` is replaced by a counter in every test that builds a CLI run."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

import compare_llm as cl
import train

ML = Path(__file__).resolve().parent.parent.parent
V1 = json.loads((ML / "models" / "v1.json").read_text())
CLASSES = V1["classes"]


class Counter:
    """Stands in for the Anthropic client and counts every call."""
    calls = 0

    def __init__(self, *a, **k):
        pass

    @property
    def messages(self):
        return self

    def create(self, **params):
        Counter.calls += 1
        text = json.dumps({"category": "Mortgage"})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=20))


@pytest.fixture(autouse=True)
def counter(monkeypatch):
    Counter.calls = 0
    monkeypatch.setattr(anthropic, "Anthropic", Counter)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))   # non-interactive
    return Counter


@pytest.fixture
def ml(tmp_path):
    """A tiny ml/ tree: 12 complaints (2 per category), a sample of all 12, v1 and latency.json."""
    (tmp_path / "data").mkdir()
    (tmp_path / "models").mkdir()
    (tmp_path / "reports").mkdir()
    rows = [{"id": str(100 + i), "date": "2022-05-01", "product": CLASSES[i % 6], "product_raw": "x",
             "narrative": f"narrative {i} about {CLASSES[i % 6].lower()} payment account"} for i in range(12)]
    (tmp_path / "data" / "complaints.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / "data" / "llm_sample.json").write_text(json.dumps({"ids": [r["id"] for r in rows]}))
    shutil.copy(ML / "models" / "v1.json", tmp_path / "models" / "v1.json")
    shutil.copy(ML / "reports" / "latency.json", tmp_path / "reports" / "latency.json")
    return tmp_path


def record_some(ml, n):
    """Write n saved lines the way record does, using the fake client."""
    cl.main(["record", "--yes", "--limit", str(n), "--ml-dir", str(ml)])


def test_replay_miss_raises(ml):
    row = cl.load_sample_rows(ml)[0]
    with pytest.raises(cl.ReplayMiss):
        cl.classify(row, ml=ml)


def test_request_is_structured_with_the_category_enum_and_no_sampling_params():
    p = cl.build_request("my narrative")
    assert p["model"] == "claude-haiku-5-5" and p["max_tokens"] == 300
    fmt = p["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["properties"]["category"]["enum"] == CLASSES
    assert p["thinking"] == {"type": "disabled"}
    assert not {"temperature", "top_p", "top_k", "tool_choice"} & set(p)
    assert p["messages"] == [{"role": "user", "content": "my narrative"}]
    for c in CLASSES:
        assert c in p["system"]


def test_cache_key_changes_with_every_input():
    base = cl.cache_key("m", "sys", "text", 0)
    assert base == cl.cache_key("m", "sys", "text", 0)
    assert len({base, cl.cache_key("m2", "sys", "text", 0), cl.cache_key("m", "sys2", "text", 0),
                cl.cache_key("m", "sys", "text2", 0), cl.cache_key("m", "sys", "text", 1)}) == 5
    s = {"thinking": cl.THINKING, "max_tokens": cl.MAX_TOKENS, "schema": cl.SCHEMA}
    assert base == cl.cache_key("m", "sys", "text", 0, s)
    for change in ({"thinking": {"type": "adaptive"}}, {"max_tokens": 301}, {"schema": {}}):
        assert cl.cache_key("m", "sys", "text", 0, {**s, **change}) != base


def test_cost_estimate_and_worst_case():
    typical, worst = cl.estimate_cost(200), cl.worst_case_cost(200)
    assert 0 < typical < worst < cl.DEFAULT_MAX_COST
    assert cl.DEFAULT_MAX_COST == 10.0
    assert cl.call_cost("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)


def test_cap_stops_new_calls_and_keeps_finished_lines(ml, monkeypatch):
    monkeypatch.setattr(cl, "EST_TOKENS", (1000, 20))
    rows = cl.load_sample_rows(ml)
    with pytest.raises(cl.CostLimit) as e:
        cl.run_calls(rows, mode="record", client=Counter(), ml=ml, max_cost=0.0001, workers=1)
    assert 1 <= Counter.calls < len(rows) and e.value.lines


@pytest.mark.parametrize("cmd", ["record", "live"])
def test_paid_commands_without_yes_make_no_call(ml, capsys, cmd):
    assert cl.main([cmd, "--ml-dir", str(ml)]) == 1
    out = capsys.readouterr().out
    assert Counter.calls == 0 and "Not confirmed. Nothing was called. Run it in a terminal and answer y." in out and "Estimated cost" in out
    assert not (ml / "data" / "llm_responses").exists()


def test_replay_commands_make_no_call_even_if_constants_are_edited(ml, monkeypatch):
    for name in ("MODE", "MODEL", "MAX_TOKENS"):
        monkeypatch.setattr(cl, name, "record" if name == "MODE" else getattr(cl, name), raising=False)
    assert cl.main(["compare", "--ml-dir", str(ml)]) == 0
    with pytest.raises(cl.ReplayMiss):
        cl.classify(cl.load_sample_rows(ml)[0], ml=ml)
    assert Counter.calls == 0


def test_record_saves_lines_and_resumes_without_paying_twice(ml):
    record_some(ml, 4)
    assert Counter.calls == 4
    record_some(ml, 4)
    assert Counter.calls == 4          # same four already recorded, skipped
    record_some(ml, 6)
    assert Counter.calls == 6


def test_latest_line_wins_and_parse_failures_are_errors(ml):
    row = cl.load_sample_rows(ml)[0]
    key = cl.request_key(row)
    path = cl.response_path(ml)
    path.parent.mkdir(parents=True)
    bad = {"key": key, "case_id": row["id"], "output": None, "error": "invalid JSON", "input_tokens": 1,
           "output_tokens": 1, "latency_ms": 5}
    good = {**bad, "output": {"category": "Mortgage"}, "error": None}
    path.write_text(json.dumps(bad) + "\n")
    assert cl.classify(row, ml=ml)["error"] == "invalid JSON"
    with path.open("a") as f:
        f.write(json.dumps(good) + "\n")
    assert cl.classify(row, ml=ml)["output"] == {"category": "Mortgage"}


def test_unparseable_reply_is_saved_as_an_error_line(ml):
    class Bad(Counter):
        def create(self, **p):
            r = super().create(**p)
            r.content[0].text = "not json"
            return r
    row = cl.load_sample_rows(ml)[0]
    line = cl.call_model(cl.build_request(row["narrative"]), key=cl.request_key(row), case_id=row["id"],
                         mode="record", client=Bad(), ml=ml)
    assert line["error"].startswith("invalid JSON")
    assert cl.classify(row, ml=ml)["error"]


def test_no_recorded_lines_writes_nothing(ml, capsys):
    assert cl.main(["compare", "--ml-dir", str(ml)]) == 0
    assert not (ml / "reports" / "llm_compare.json").exists()
    out = capsys.readouterr().out
    assert "No recorded run yet" in out and "python compare_llm.py record" in out


def test_compare_is_written_from_replayed_lines_only(ml):
    record_some(ml, 6)
    calls = Counter.calls
    assert cl.main(["compare", "--ml-dir", str(ml)]) == 0
    assert Counter.calls == calls                       # replay: no new call
    rep = json.loads((ml / "reports" / "llm_compare.json").read_text())
    assert rep["generated_by"] == "python compare_llm.py compare"
    assert rep["n"] == 6 and rep["claude"]["model"] == "claude-haiku-5-5"
    assert rep["claude"]["cost_per_1000"] == pytest.approx(1000 * cl.call_cost("claude-haiku-5-5", 1000, 20))
    assert "latency_p50_ms" in rep["claude"] and rep["v1"]["latency_p50_ms"] > 0
    rows = {r["id"]: r for r in cl.load_sample_rows(ml)}
    first6 = [rows[i] for i in cl.load_sample_ids(ml)[:6]]
    y_true = [r["product"] for r in first6]
    want = train.per_class_metrics(y_true, [train.score(V1, r["narrative"])["label"] for r in first6], CLASSES)
    assert rep["v1"]["accuracy"] == pytest.approx(want["accuracy"])
    assert rep["v1"]["macro_f1"] == pytest.approx(want["macro"]["f1"])
    claude = train.per_class_metrics(y_true, ["Mortgage"] * 6, CLASSES)
    assert rep["claude"]["accuracy"] == pytest.approx(claude["accuracy"])


def test_committed_sample_is_200_stratified_2022_complaints():
    rows = train.load_complaints(train.PATHS["complaints"])
    test = train.split_by_time(rows)["test"]
    ids = cl.pick_sample(test)
    assert ids == cl.pick_sample(test) and len(ids) == len(set(ids)) == 200
    by_id = {r["id"]: r for r in test}
    counts = {c: sum(by_id[i]["product"] == c for i in ids) for c in CLASSES}
    assert sum(counts.values()) == 200
    for c in CLASSES:   # proportional to the 600 test complaints, within one
        assert abs(counts[c] - sum(r["product"] == c for r in test) / 3) <= 1
    saved = json.loads((ML / "data" / "llm_sample.json").read_text())
    assert saved["ids"] == ids


def test_failed_answers_count_as_wrong_and_are_still_paid_for(ml):
    record_some(ml, 4)
    path = cl.response_path(ml)
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    for x in lines[:2]:                       # two replies came back unusable
        x["output"], x["error"], x["stop_reason"] = None, "no usable reply (stop_reason max_tokens)", "max_tokens"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    rep = cl.compare(ml)
    rows = cl.load_sample_rows(ml)[:4]
    assert rep["n"] == 4 and rep["no_answer"] == 2
    right = sum(r["product"] == "Mortgage" for r in rows[2:])
    assert rep["claude"]["accuracy"] == pytest.approx(right / 4)
    assert rep["answered_only"]["n"] == 2 and rep["answered_only"]["claude_accuracy"] == pytest.approx(right / 2)
    want = train.per_class_metrics([r["product"] for r in rows],
                                   [train.score(V1, r["narrative"])["label"] for r in rows], CLASSES)
    assert rep["v1"]["accuracy"] == pytest.approx(want["accuracy"])      # v1 scored on all 4 too
    assert rep["claude"]["cost_per_1000"] == pytest.approx(1000 * cl.call_cost("claude-haiku-5-5", 1000, 20))
    assert "remote" in rep["claude"]["latency_note"] and "different things" in rep["latency_caveat"]


def test_record_limit_makes_a_pilot_of_n_calls(ml):
    record_some(ml, 5)
    assert Counter.calls == 5


@pytest.mark.parametrize("bad", ["nan", "inf", "0", "-1"])
def test_max_cost_must_be_finite_and_positive(ml, capsys, bad):
    with pytest.raises(SystemExit) as e:
        cl.main(["record", "--ml-dir", str(ml), "--max-cost", bad])
    assert e.value.code == 2 and Counter.calls == 0
    assert "must be a finite number above 0" in capsys.readouterr().err
