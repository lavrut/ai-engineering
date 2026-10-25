import json
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import triage_lab as lab

DATA = Path(__file__).resolve().parents[2] / "data"
FIXTURES = DATA / "fixtures"


def fake_line(model="claude-haiku-5-5", input_tokens=1_000_000, output_tokens=0, case_id="x"):
    return {"config": {"model": model}, "case_id": case_id,
            "input_tokens": input_tokens, "output_tokens": output_tokens}


# ── cost ────────────────────────────────────────────────────────────────────
def test_call_cost_uses_prices():
    assert lab.call_cost("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert lab.call_cost("claude-opus-5-5", 500_000, 0) == pytest.approx(2.00)
    with pytest.raises(KeyError):
        lab.call_cost("claude-unknown", 1, 1)


def test_estimate_matches_formula():
    tin, tout = lab.EST_TOKENS["triage"]
    expected = 10 * (tin * 2.00 + tout * 10.00) / 1_000_000
    assert lab.estimate_cost("triage", "claude-sonnet-5-5", 10) == pytest.approx(expected)
    jin, jout = lab.EST_TOKENS["judge"]
    assert lab.estimate_cost("judge", "claude-opus-5-5", 3) == pytest.approx(3 * (jin * 4 + jout * 20) / 1_000_000)
    assert lab.estimate_cost("triage", "claude-haiku-5-5", 0) == 0


# ── run ─────────────────────────────────────────────────────────────────────
def test_run_keeps_order_with_slow_cases():
    cases = list(range(6))

    def fn(case, rep):
        time.sleep((5 - case) * 0.02)           # the first case finishes last
        return {**fake_line(input_tokens=1, case_id=case), "rep": rep}

    lines = lab.run(cases, fn, workers=6)
    assert [l["case_id"] for l in lines] == cases


def test_run_repeats_each_case():
    lines = lab.run(["a", "b"], lambda c, r: {**fake_line(input_tokens=1, case_id=c), "rep": r}, reps=3)
    assert [(l["case_id"], l["rep"]) for l in lines] == [
        ("a", 0), ("a", 1), ("a", 2), ("b", 0), ("b", 1), ("b", 2)]


def test_run_stops_at_max_cost():
    calls = []

    def fn(case, rep):
        calls.append(case)
        return fake_line(input_tokens=1_000_000, case_id=case)    # $0.10 each on Haiku

    with pytest.raises(lab.CostLimit) as e:
        lab.run(list(range(10)), fn, workers=1, max_cost=0.25)
    assert [l["case_id"] for l in e.value.lines] == [0, 1, 2]     # the call that crossed the cap is kept
    assert e.value.spent == pytest.approx(0.30)
    assert len(calls) == 3                                        # nothing after the cap was started


def test_run_within_max_cost_returns_everything():
    lines = lab.run([1, 2], lambda c, r: fake_line(input_tokens=1, case_id=c), max_cost=1.0)
    assert len(lines) == 2


# ── recording plan ──────────────────────────────────────────────────────────
def test_recording_plan_estimate_under_10():
    rows = lab.estimate_plan(DATA)
    assert 0 < sum(r["cost"] for r in rows) < 10.0


def test_recording_plan_counts_follow_the_ruling():
    calls = {(r["name"], r["model"], r["prompt"], r["effort"]): r["calls"] for r in lab.estimate_plan(DATA)}
    assert calls[("prompts", "claude-haiku-5-5", "v2_rules", None)] == 180      # 60 tickets x 3 reps
    assert calls[("prompts", "claude-haiku-5-5", "v1_basic", None)] == 60
    assert calls[("models", "claude-opus-5-5", "best", "high")] == 40           # dev split only
    judge = [r for r in lab.estimate_plan(DATA) if r["name"] == "judge"]
    assert sorted(r["calls"] for r in judge) == [20, 60]                        # 30 labels x 2 orders; 20 test replies
    assert lab.estimate_plan(DATA, only="models")[0]["name"] == "models"


# ── CLI ─────────────────────────────────────────────────────────────────────
def test_cli_replay_run_prints_report(capsys):
    assert lab.main(["run", "--data", str(FIXTURES)]) == 0
    out = capsys.readouterr().out
    assert "accuracy" in out and "band" in out and "v2_rules" in out


def test_cli_data_option_works_before_the_command(capsys):
    assert lab.main(["--data", str(FIXTURES), "run", "--prompt", "v1_basic", "--split", "test"]) == 0
    assert "v1_basic" in capsys.readouterr().out


def test_cli_replay_miss_exits_without_a_traceback(capsys):
    assert lab.main(["run", "--data", str(FIXTURES), "--model", "claude-sonnet-5-5"]) == 1
    assert "no saved response" in capsys.readouterr().err


def test_cli_compare_uses_one_rep(capsys):
    # the fixture holds two reps of v2_rules; compare must still get one grade per case
    assert lab.main(["compare", "--data", str(FIXTURES), "--prompt", "v1_basic", "--vs", "v2_rules"]) == 0
    out = capsys.readouterr().out
    assert "diff" in out and "band" in out


def test_cli_judge_agreement_and_gate(capsys):
    assert lab.main(["agreement", "--data", str(FIXTURES)]) == 0
    out = capsys.readouterr().out
    assert "addresses_issue" in out and "kappa" in out and "flips" in out
    # the fixtures hold judge lines for the labelled replies only, so judging triage replies misses
    assert lab.main(["judge", "--data", str(FIXTURES), "--prompt", "v2_rules"]) == 1
    assert "no saved response" in capsys.readouterr().err
    assert lab.main(["gate", "--data", str(FIXTURES), "--prompt", "v2_rules", "--baseline", "v1_basic",
                     "--minimum", "0.0"]) == 0
    assert lab.main(["gate", "--data", str(FIXTURES), "--prompt", "v1_basic", "--baseline", "v2_rules",
                     "--minimum", "1.1"]) == 1


# paid modes ------------------------------------------------------------------
class FakeClient:
    """Stands in for anthropic.Anthropic: every call returns a valid, tiny answer."""
    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)
        self.lock = threading.Lock()

    def create(self, **params):
        with self.lock:
            self.calls.append(params)
        props = params["output_config"]["format"]["schema"]["properties"]
        if "category" in props:
            body = {"category": "billing", "priority": "high", "reply": "We are looking into it."}
        else:
            body = {k: {"pass": True, "reason": "ok"} for k in props}
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=900, output_tokens=700),
                               content=[SimpleNamespace(type="text", text=json.dumps(body))])


@pytest.fixture
def root(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "data")
    return tmp_path / "data"


@pytest.fixture
def no_api(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the API must not be called")
    monkeypatch.setattr(lab.anthropic, "Anthropic", boom)


def response_files(root):
    return sorted(p.name for p in (root / "responses").glob("*.jsonl"))


def test_paid_mode_refuses_without_confirmation(root, no_api, capsys):
    before = response_files(root)
    for argv in (["run", "--mode", "record", "--model", "claude-sonnet-5-5"],
                 ["run", "--mode", "live"],
                 ["record", "--plan", "all"]):
        assert lab.main(argv + ["--data", str(root)]) == 1
        out = capsys.readouterr().out
        assert "Estimated cost" in out and "Nothing was called" in out
    assert response_files(root) == before


def test_judge_live_with_confirmation(root, capsys):
    client = FakeClient()
    assert lab.main(["judge", "--mode", "live", "--yes", "--data", str(root), "--prompt", "v2_rules"],
                    client=client) == 0
    out = capsys.readouterr().out
    assert "addresses_issue" in out and "pass" in out
    assert len(client.calls) == 4


def test_paid_mode_accepts_interactive_y(root, monkeypatch, capsys):
    client = FakeClient()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert lab.main(["run", "--mode", "live", "--data", str(root)], client=client) == 0
    assert len(client.calls) == 4                                   # 4 dev tickets
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert lab.main(["run", "--mode", "live", "--data", str(root)], client=client) == 1
    assert len(client.calls) == 4


def test_record_all_runs_phases_in_order(root, capsys):
    client = FakeClient()
    assert lab.main(["record", "--plan", "all", "--yes", "--data", str(root)], client=client) == 0
    out = capsys.readouterr().out
    assert out.index("Estimated cost") < out.index("phase 2")
    # fixtures: 8 tickets (4 dev, 4 test). prompts 8*5, models 4*4, judge 4 labels*2 + 4 test replies
    assert len(client.calls) == 40 + 16 + 8 + 4
    assert "judge__claude-opus-5-5__after__default.jsonl" in response_files(root)
    assert "triage__claude-opus-5-5__v1_basic__high.jsonl" in response_files(root) or \
           "triage__claude-opus-5-5__v2_rules__high.jsonl" in response_files(root) or \
           "triage__claude-opus-5-5__v3_examples__high.jsonl" in response_files(root)


def test_record_stops_at_max_cost_and_saves_what_finished(root, capsys):
    client = FakeClient()
    code = lab.main(["record", "--plan", "prompts", "--yes", "--max-cost", "0.001", "--data", str(root)],
                    client=client)
    assert code == 2
    out = capsys.readouterr().out
    assert "max-cost" in out and "saved" in out
    assert 0 < len(client.calls) < 40
