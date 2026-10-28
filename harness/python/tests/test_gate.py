"""The money gate in the REPL (section 9): the estimate, y/N, --yes and --max-cost. No API calls:
every test passes a fake in place of call_model."""
import io
import re
import sys
from pathlib import Path

import pytest

import harness
from conftest import block, reply, text

ESTIMATE = ("Estimate: typical $0.10 a task (4 calls of ~4,000 input and ~400 output tokens); "
            "worst case $23.00 (MAX_TURNS 25 calls of 150,000 input and 16,000 output tokens, "
            "plus any compaction summaries), at $4.00 / $20.00 per million tokens.")


class Terminal(io.StringIO):
    """Standard input that says it is a terminal, so the gate asks."""

    def isatty(self) -> bool:
        return True


def run_main(monkeypatch, capsys, stdin, argv=(), replies=None):
    calls = []
    answers = iter(replies or [reply("end_turn", text("done"))])

    def fake(messages, **overrides):
        calls.append(overrides)
        return next(answers)

    monkeypatch.setattr(sys, "stdin", stdin)
    code = harness.main(list(argv), call=fake)
    return code, calls, capsys.readouterr().out


def test_prices_match_the_llms_lab():
    lab = (Path(harness.__file__).resolve().parents[2] / "llms/python/triage_lab.py").read_text()
    p_in, p_out = re.search(r'"claude-opus-5-5": \(([\d.]+), ([\d.]+)\)', lab).groups()
    assert harness.MODEL == "claude-opus-5-5"
    assert harness.PRICE_PER_MTOK == (float(p_in), float(p_out))


def test_estimate_lines(ws):
    lines = harness.estimate_lines(harness.Ledger(cap=10.0, spent=0.0))
    assert lines == [ESTIMATE, "Spent $0.0000 this session; --max-cost stops it before $10.00."]


def test_y_on_a_terminal_runs_the_task(ws, monkeypatch, capsys):
    code, calls, out = run_main(monkeypatch, capsys, Terminal("list the files\ny\nexit\n"))
    assert code == 0 and len(calls) == 1
    assert ESTIMATE in out and "Run it? [y/N] " in out
    assert "agent > done" in out
    assert "[$0.0006 this task, $0.0006 this session]" in out  # 100 in, 10 out at $4 / $20


@pytest.mark.parametrize("answer", ["", "n", "yes", "Y please"])
def test_anything_but_y_is_no(ws, monkeypatch, capsys, answer):
    code, calls, out = run_main(monkeypatch, capsys, Terminal(f"list the files\n{answer}\nexit\n"))
    assert calls == []
    assert "Not run. Nothing was spent." in out


def test_eof_at_the_question_is_no(ws, monkeypatch, capsys):
    code, calls, out = run_main(monkeypatch, capsys, Terminal("list the files\n"))
    assert calls == [] and "Not run. Nothing was spent." in out


def test_no_terminal_is_no(ws, monkeypatch, capsys):
    code, calls, out = run_main(monkeypatch, capsys, io.StringIO("list the files\ny\nexit\n"))
    assert calls == []
    assert "Not run: no terminal to ask. Nothing was spent." in out


def test_yes_flag_runs_a_scripted_task(ws, monkeypatch, capsys):
    code, calls, out = run_main(monkeypatch, capsys, io.StringIO("list the files\nexit\n"), argv=["--yes"])
    assert len(calls) == 1 and ESTIMATE in out and "agent > done" in out


def test_max_cost_stops_before_a_call(ws, monkeypatch, capsys):
    # A worst-case call is at least 16,000 output tokens x $20 / million = $0.32, so a $0.30 cap stops the first one.
    code, calls, out = run_main(monkeypatch, capsys, Terminal("list the files\ny\nexit\n"),
                                argv=["--max-cost", "0.30"])
    assert calls == []
    assert re.search(r"Stopped: spent \$0\.0000; the next call could cost up to \$0\.3\d{3}, "
                     r"over --max-cost 0\.30", out)


def test_cap_counts_what_was_spent(ws):
    ledger = harness.Ledger(cap=0.70)  # room for one call ($0.4002 spent), not for a second worst case ($0.32+)
    replies = iter([reply("tool_use", block("list_files", {"path": "."}), input_tokens=100_000),
                    reply("end_turn", text("never reached"))])
    seen = []

    def fake(messages, **overrides):
        seen.append(overrides)
        return next(replies)

    with pytest.raises(harness.CostLimit):
        harness.run_agent("hi", call=harness.metered(fake, ledger), approver=lambda *_: True)
    assert len(seen) == 1                    # the second call was refused before it was made
    assert ledger.spent == pytest.approx(0.4002)  # 100,000 in and 10 out at $4 / $20 per million


def test_summary_call_is_sized_with_its_own_max_tokens(ws):
    ledger = harness.Ledger(cap=0.30)  # under one 16,000-token call, over one 4,000-token summary
    gated = harness.metered(lambda m, **kw: reply("end_turn", text("s")), ledger)
    gated([{"role": "user", "content": "hi"}], max_tokens=4_000)
    with pytest.raises(harness.CostLimit):
        gated([{"role": "user", "content": "hi"}])


def test_missing_key_is_a_message_not_a_traceback(ws, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("hi\n"))
    assert harness.main([]) == 1
    assert "Set ANTHROPIC_API_KEY first (lesson 2). Nothing was called." in capsys.readouterr().out


def test_eof_at_the_permission_prompt_is_no(ws, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert harness.approve("write_file", {"path": "x"}) is False
