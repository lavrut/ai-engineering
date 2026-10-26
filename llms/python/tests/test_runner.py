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
    with pytest.raises(ValueError, match='no price for model "claude-unknown"'):
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
def test_recording_plan_estimate_under_10(tmp_path):
    # the full plan as if nothing were recorded: the real tickets, prompts and labels, no responses/
    for name in ("tickets.jsonl", "prompts.json", "reply_labels.jsonl"):
        shutil.copy(DATA / name, tmp_path / name)
    rows = lab.estimate_plan(tmp_path)
    assert all(r["todo"] == r["calls"] for r in rows)
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
def empty_root(root):
    shutil.rmtree(root / "responses")           # nothing recorded yet: every request is still to do
    return root


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


def test_record_all_runs_phases_in_order(empty_root, capsys):
    root = empty_root
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


def test_record_stops_at_max_cost_and_saves_what_finished(empty_root, capsys):
    root = empty_root
    client = FakeClient()
    code = lab.main(["record", "--plan", "prompts", "--yes", "--max-cost", "0.001", "--data", str(root)],
                    client=client)
    assert code == 2
    out = capsys.readouterr().out
    assert "max-cost" in out and "saved" in out
    assert 0 < len(client.calls) < 40


# resume, cap default, worst case, pilot ---------------------------------------
def test_record_resumes_and_skips_recorded_requests(empty_root, capsys):
    root = empty_root
    argv = ["record", "--plan", "prompts", "--yes", "--data", str(root)]
    first = FakeClient()
    assert lab.main(argv, client=first) == 0
    assert len(first.calls) == 40
    capsys.readouterr()
    second = FakeClient()
    assert lab.main(argv, client=second) == 0
    assert second.calls == []                                   # nothing paid twice
    out = capsys.readouterr().out
    assert "40 requests are already recorded" in out and "$0.00 " in out


def test_record_rerecords_errored_lines_only(empty_root):
    root = empty_root
    argv = ["record", "--plan", "pilot", "--yes", "--data", str(root)]
    assert lab.main(argv, client=FakeClient()) == 0
    path = root / "responses" / "triage__claude-opus-5-5__v2_rules__high.jsonl"
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    lines[0]["error"] = "overloaded"
    path.write_text("".join(json.dumps(l) + "\n" for l in lines))
    again = FakeClient()
    assert lab.main(argv, client=again) == 0
    assert len(again.calls) == 1
    assert lab.already_recorded(path, lines[0]["key"]) is not None   # latest line wins and succeeded


def test_record_cap_ignores_skipped_lines(empty_root):
    root = empty_root
    assert lab.main(["record", "--plan", "prompts", "--yes", "--data", str(root)], client=FakeClient()) == 0
    # the recorded lines are worth far more than the cap, yet a resumed run is free and finishes
    assert lab.main(["record", "--plan", "prompts", "--yes", "--max-cost", "0.0001", "--data", str(root)],
                    client=FakeClient()) == 0


def test_worst_case_cost_and_confirmation_text(empty_root, capsys):
    tin, _ = lab.EST_TOKENS["triage"]
    assert lab.worst_case_cost("triage", "claude-sonnet-5-5", 2) == pytest.approx(
        2 * (tin * 2.00 + lab.MAX_TOKENS * 10.00) / 1_000_000)
    rows = lab.estimate_plan(empty_root, only="models")
    assert sum(r["worst"] for r in rows) > sum(r["cost"] for r in rows) > 0
    assert lab.main(["record", "--plan", "models", "--data", str(empty_root)]) == 1
    out = capsys.readouterr().out
    assert "worst case $" in out and "if every reply uses max_tokens" in out and "Cap: $10.00." in out


def test_paid_modes_default_to_a_10_dollar_cap():
    parser = lab.build_parser()
    for argv in (["run"], ["judge"], ["record"]):
        assert parser.parse_args(argv).max_cost == 10.0


def test_pilot_plan_is_small_and_matches_full_plan_keys(empty_root):
    rows = lab.estimate_plan(empty_root, only="pilot")
    assert [(r["kind"], r["calls"]) for r in rows] == [("triage", 3), ("judge", 3)]
    assert rows[0]["model"] == "claude-opus-5-5" and rows[0]["effort"] == "high"
    assert all(r["name"] != "pilot" for r in lab.estimate_plan(empty_root, only="all"))
    assert lab.main(["record", "--plan", "pilot", "--yes", "--data", str(empty_root)], client=FakeClient()) == 0
    opus_high = next(x for x in lab.RECORDING_PLAN if x["name"] == "models" and x["model"] == "claude-opus-5-5"
                     and x["effort"] == "high")
    labels = next(x for x in lab.RECORDING_PLAN if x["name"] == "judge" and x["prompt"] == "labels")
    # when v2_rules wins, the pilot's requests are exactly the full plan's: 3 of 4 dev tickets, 3 of 8 judge calls
    assert lab.remaining_calls(opus_high, empty_root, best="v2_rules") == 1
    assert lab.remaining_calls(labels, empty_root) == 5


# MODE is only the CLI's default --mode: replay-only lookups never spend ----------
def edited_lab(mode: str):
    """A fresh copy of triage_lab with its MODE line edited, as a learner would edit the file.
    (Setting lab.MODE at run time would not do: default arguments are bound when the file loads.)"""
    import types
    source = Path(lab.__file__).read_text(encoding="utf-8")
    line = next(l for l in source.splitlines() if l.startswith("MODE = "))
    module = types.ModuleType("triage_lab_edited")
    module.__file__ = lab.__file__
    exec(compile(source.replace(line, f'MODE = "{mode}"'), lab.__file__, "exec"), module.__dict__)
    assert module.MODE == mode
    return module


@pytest.mark.parametrize("mode", ["live", "record"])
def test_editing_mode_never_makes_unconfirmed_calls(root, monkeypatch, capsys, mode):
    lab = edited_lab(mode)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    client = FakeClient()
    monkeypatch.setattr(lab.anthropic, "Anthropic", lambda **k: client)   # a call without a client is counted too
    data = ["--data", str(root)]
    for argv in (["run"], ["run", "--mode", "replay"],
                 ["compare", "--prompt", "v1_basic", "--vs", "v2_rules"],
                 ["judge"], ["judge", "--mode", "replay"],
                 ["agreement"],
                 ["gate", "--prompt", "v2_rules", "--baseline", "v1_basic", "--minimum", "0.0"],
                 ["record", "--plan", "all"], ["record", "--plan", "judge"]):
        lab.main(argv + data, client=client)
        assert client.calls == [], argv
    out = capsys.readouterr().out
    assert "Nothing was called" in out


@pytest.mark.parametrize("mode", ["live", "record"])
def test_library_calls_default_to_replay(root, monkeypatch, mode):
    lab = edited_lab(mode)
    client = FakeClient()
    monkeypatch.setattr(lab.anthropic, "Anthropic", lambda **k: client)
    prompts = lab.load_prompts(root / "prompts.json")
    ticket = lab.load_tickets(root / "tickets.jsonl")[0]
    lab.triage(ticket, "v2_rules", client=client, root=root, prompts=prompts)
    label = lab._label_cases(root, lab.load_tickets(root / "tickets.jsonl"))[0]
    lab.judge(label["ticket"], label["reply"], client=client, root=root, prompts=prompts)
    with pytest.raises(lab.ReplayMiss):       # the fixtures hold no v3_examples lines: a miss, not a call
        lab.best_prompt(root, prompts, quiet=True)
    for step in lab.RECORDING_PLAN:
        lab.remaining_calls(step, root, best="v2_rules")
    assert client.calls == []


@pytest.mark.parametrize("mode", ["record", "live"])
def test_judge_paid_mode_refuses_without_yes(root, monkeypatch, capsys, mode):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    client = FakeClient()
    assert lab.main(["judge", "--mode", mode, "--data", str(root)], client=client) == 1
    assert client.calls == []
    assert "Nothing was called" in capsys.readouterr().out


def test_live_cap_message_says_nothing_is_saved(root, capsys):
    client = FakeClient()
    code = lab.main(["run", "--mode", "live", "--yes", "--max-cost", "0.0001", "--workers", "1",
                     "--model", "claude-opus-5-5", "--data", str(root)], client=client)
    assert code == 2
    out = capsys.readouterr().out
    assert "Live mode saves nothing." in out and "Finished calls are saved" not in out


@pytest.mark.parametrize("argv, message", [
    (["run", "--prompt", "nope"], "error: unknown prompt 'nope'; choose from v1_basic, v2_rules, v3_examples"),
    (["run", "--mode", "live", "--yes", "--model", "claude-nope"], 'error: no price for model "claude-nope"'),
])
def test_cli_errors_print_one_line_without_a_traceback(root, argv, message, capsys):
    assert lab.main(argv + ["--data", str(root)], client=FakeClient()) == 1
    assert capsys.readouterr().err.strip() == message


def test_cli_every_error_line_is_caught(root, capsys, tmp_path):
    # every saved line errored: nothing is scored, so the report cannot be computed
    path = root / "responses" / "triage__claude-haiku-5-5__v1_basic__default.jsonl"
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    path.write_text("".join(json.dumps({**l, "error": "overloaded"}) + "\n" for l in lines))
    assert lab.main(["run", "--prompt", "v1_basic", "--data", str(root)]) == 1
    assert capsys.readouterr().err.strip() == "error: no scored cases"


def test_cli_zero_workers_runs_one_at_a_time(capsys):
    assert lab.main(["run", "--workers", "0", "--data", str(FIXTURES)]) == 0
    assert "accuracy" in capsys.readouterr().out
