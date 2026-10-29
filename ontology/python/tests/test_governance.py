"""Tests for governance.py: every recorded agents action checked against section 6 of the ontology.

The fixture tree under fixtures/governance/ has the agents traces' layout: four desk runs and one pattern demo.
One run plants an unpermitted write (the research worker calls label_ticket), one calls a tool the ontology's
table lacks, and the others meet, miss or half-meet the write's conditions. The real traces are read in place.
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import governance as G  # noqa: E402
import ontology as O  # noqa: E402

ROOT = HERE.parents[2]
FIX = HERE / "fixtures" / "governance"
LUMEN = ROOT / "ontology" / "lumen.yaml"


def onto():
    return O.load(LUMEN)


def fixture() -> dict:
    return G.build(onto(), FIX)


def cell(data: dict, role: str, action: str) -> dict:
    return next(c for c in data["matrix"] if (c["role"], c["action"]) == (role, action))


# ── The planted case and the verdicts ───────────────────────────────────────
def test_planted_write_by_research_is_not_permitted_and_turns_the_gate_red():
    data = fixture()
    assert data["violations"] == [
        {"file": "desk-test/test-single-t004.json", "role": "single", "tool": "delete_ticket", "action": None,
         "detail": "tool not in the ontology's table"},
        {"file": "desk-test/test-supervisor-planted.json", "role": "research", "tool": "label_ticket", "action": "write",
         "detail": "no row permits research to write"},
    ]
    assert data["gate"]["ok"] is False
    assert data["gate"]["reason"].startswith("red: 2 not permitted (research write, single delete_ticket)")
    assert cell(data, "research", "write")["rule"] == "no"
    assert cell(data, "research", "write")["verdicts"] == {"permitted": 0, "conditional": 0, "not_permitted": 1}


def test_conditions_are_met_missed_or_half_met():
    data = fixture()
    w = cell(data, "supervisor", "write")
    assert (w["rule"], w["conditions"], w["events"]) == ("conditional", ["review_passed", "human_confirmed"], 3)
    # t002: accepted, last good review passed -> permitted; planted: declined -> conditional; t003: review failed,
    # the attempt refused -> conditional
    assert w["verdicts"] == {"permitted": 1, "conditional": 2, "not_permitted": 0}
    assert w["unmet"] == {"human_confirmed": 1, "review_passed": 1}
    s = cell(data, "single", "write")
    assert (s["rule"], s["verdicts"], s["unmet"]) == ("conditional", {"permitted": 0, "conditional": 1, "not_permitted": 0}, {"human_confirmed": 1})


def test_handoffs_are_escalations_and_each_run_has_one_approval():
    data = fixture()
    assert cell(data, "supervisor", "escalate")["verdicts"] == {"permitted": 6, "conditional": 0, "not_permitted": 0}
    assert cell(data, "human", "approve")["events"] == 4
    assert data["approvals"] == {"accept": 2, "decline": 2}
    assert data["totals"] == {"traces": 5, "desk_runs": 4, "skipped": 1, "unreadable": 0, "events": 18,
                              "permitted": 13, "conditional": 3, "not_permitted": 2, "writes_landed": 1}
    assert [cell(data, r, "write")["landed"] for r in ("single", "supervisor", "research")] == [0, 1, 0]
    assert "landed" not in cell(data, "single", "read")
    assert data["by_engine"] == {"test": {
        "single": {"events": 4, "permitted": 2, "conditional": 1, "not_permitted": 1},
        "supervisor": {"events": 14, "permitted": 11, "conditional": 2, "not_permitted": 1}}}


def test_pattern_demos_are_skipped_with_a_reason_never_counted():
    data = fixture()
    assert data["skipped"] == [{"file": "patterns/test-chain-t001.json", "reason": "pattern demo (pattern:chain): no tool calls"}]


def test_matrix_is_every_role_by_every_action_with_the_rule_word():
    data = fixture()
    assert data["roles"] == ["single", "triage", "research", "drafter", "reviewer", "supervisor", "human"]
    assert data["actions"] == ["read", "write", "approve", "escalate"]
    assert [(c["role"], c["action"]) for c in data["matrix"]] == [(r, a) for r in data["roles"] for a in data["actions"]]
    rules = {(c["role"], c["action"]): c["rule"] for c in data["matrix"]}
    assert sum(r == "permitted" for r in rules.values()) == 9
    assert sum(r == "conditional" for r in rules.values()) == 2
    assert sum(r == "no" for r in rules.values()) == 17
    assert all(set(c["by_engine"]) == {"test"} for c in data["matrix"])


# ── Edge cases: never an exception, never a silent pass ─────────────────────
def test_unknown_condition_source_is_never_a_pass(tmp_path):
    y = LUMEN.read_text().replace("evaluated_from: [approve, elicit]", "evaluated_from: [approve, a_signature]")
    (tmp_path / "lumen.yaml").write_text(y)
    data = G.build(O.load(tmp_path / "lumen.yaml"), FIX)
    w = cell(data, "supervisor", "write")
    # t002's write landed, so an unevaluable condition makes it not permitted; the two refused ones stay conditional
    assert w["verdicts"] == {"permitted": 0, "conditional": 2, "not_permitted": 1}
    assert w["unmet"]["human_confirmed"] == 2
    assert {"file": "desk-test/test-supervisor-t002.json", "role": "supervisor", "tool": "label_ticket", "action": "write",
            "detail": "landed without human_confirmed"} in data["violations"]


def test_unreadable_trace_is_skipped_and_turns_the_gate_red(tmp_path):
    traces = tmp_path / "agents" / "data" / "traces"
    shutil.copytree(FIX / "agents" / "data" / "traces", traces)
    (traces / "desk-test" / "broken.json").write_text("{not json")
    (traces / "desk-test" / "odd.json").write_text(json.dumps({"desk": "single", "output": {"tool_calls": "x"}}))
    data = G.build(onto(), tmp_path)
    unread = [s for s in data["skipped"] if s["reason"].startswith("unreadable")]
    assert [s["file"] for s in unread] == ["desk-test/broken.json", "desk-test/odd.json"]
    assert data["totals"]["unreadable"] == 2
    assert "2 unreadable" in data["gate"]["reason"]


def test_an_ontology_with_errors_writes_nothing(tmp_path, capsys):
    y = LUMEN.read_text().replace("conditions: [human_confirmed]}", "conditions: [human_confirm]}")
    (tmp_path / "lumen.yaml").write_text(y)
    out = tmp_path / "governance.json"
    assert G.main(["--ontology", str(tmp_path / "lumen.yaml"), "--out", str(out)]) == 1
    assert not out.exists()
    printed = capsys.readouterr().out
    assert "dangling_action_ref human_confirm" in printed


# ── The real traces and the committed file ──────────────────────────────────
def test_real_traces_gate_is_green_and_committed_file_matches(capsys):
    data = G.build(onto())
    assert data["gate"]["ok"] is True
    assert data["totals"]["desk_runs"] == 104 and data["totals"]["skipped"] == 30
    # one approve event per run that attempted a write (ruling A10): 18 single and 42 supervisor runs
    assert data["approvals"] == {"decline": 60}
    assert cell(data, "human", "approve")["events"] == cell(data, "single", "write")["events"] + cell(data, "supervisor", "write")["events"]
    assert data["totals"]["not_permitted"] == 0
    assert all(s["reason"].startswith("pattern demo") for s in data["skipped"])
    assert G.GOVERNANCE.read_text(encoding="utf-8") == O.dumps(data)
    assert G.main(["--check"]) == 0
    assert "governance.json: up to date" in capsys.readouterr().out


def test_check_catches_drift(tmp_path, capsys):
    out = tmp_path / "governance.json"
    out.write_text("{}\n")
    assert G.main(["--check", "--out", str(out)]) == 1
    assert "drifted or missing" in capsys.readouterr().out


def test_write_is_deterministic(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    G.main(["--out", str(a)])
    G.main(["--out", str(b)])
    assert a.read_bytes() == b.read_bytes() == G.GOVERNANCE.read_bytes()


# ── Writes that landed, and the two confirmations (rulings A7, A8) ──────────
def one_run(tmp_path, approve: str, elicit: str, is_error: bool | None, tool: str = "label_ticket") -> dict:
    call = {"agent": "single", "tool": tool, "args": {"id": "t009", "label": "bug"}}
    if is_error is not None:
        call["is_error"] = is_error
    run = {"engine": "test", "desk": "single", "approve": approve, "elicit": elicit,
           "output": {"tool_calls": [call], "handoffs": []}}
    d = tmp_path / "agents" / "data" / "traces" / "desk-test"
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps(run))
    return G.build(onto(), tmp_path)


def test_a_write_that_landed_without_a_yes_is_not_permitted_and_turns_the_gate_red(tmp_path):
    data = one_run(tmp_path, "decline", "decline", is_error=False)
    assert data["violations"] == [{"file": "desk-test/run.json", "role": "single", "tool": "label_ticket", "action": "write",
                                   "detail": "landed without human_confirmed"}]
    assert data["totals"]["writes_landed"] == 1 and cell(data, "single", "write")["landed"] == 1
    assert data["gate"]["ok"] is False
    assert data["gate"]["reason"].startswith("red: 1 not permitted (single write)")


def test_a_refused_write_without_a_yes_is_conditional_and_the_gate_stays_green(tmp_path):
    data = one_run(tmp_path, "decline", "decline", is_error=True)
    assert cell(data, "single", "write")["verdicts"] == {"permitted": 0, "conditional": 1, "not_permitted": 0}
    assert data["totals"]["writes_landed"] == 0
    assert data["gate"]["ok"] is True


def test_human_confirmed_needs_both_the_person_and_the_server(tmp_path):
    data = one_run(tmp_path, "accept", "decline", is_error=True)
    w = cell(data, "single", "write")
    assert w["verdicts"]["conditional"] == 1 and w["unmet"] == {"human_confirmed": 1}
    assert [c["evaluated_from"] for c in data["conditions"]] == [["approve", "elicit"], "handoffs.review"]


def test_both_yeses_and_a_landed_write_is_permitted(tmp_path):
    data = one_run(tmp_path, "accept", "accept", is_error=False)
    assert cell(data, "single", "write")["verdicts"] == {"permitted": 1, "conditional": 0, "not_permitted": 0}
    assert data["gate"]["ok"] is True


def test_a_write_with_no_is_error_field_counts_as_landed(tmp_path):
    """Ruling A11: an unknown outcome fails closed."""
    data = one_run(tmp_path, "decline", "accept", is_error=None)
    assert data["violations"][0]["detail"] == "landed without human_confirmed"
    assert data["totals"]["writes_landed"] == 1 and data["gate"]["ok"] is False


def test_a_run_with_no_write_attempt_has_no_approve_event(tmp_path):
    """Ruling A10: nobody was asked, so the run's scripted answer is not an event."""
    data = one_run(tmp_path, "decline", "accept", is_error=False, tool="get_ticket")
    assert data["approvals"] == {}
    assert cell(data, "human", "approve")["events"] == 0
    assert data["totals"]["events"] == 1 and data["totals"]["permitted"] == 1
