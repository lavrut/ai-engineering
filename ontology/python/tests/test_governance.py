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
    # t002: accepted, last good review passed -> permitted; planted: declined -> conditional; t003: review failed
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
                              "permitted": 13, "conditional": 3, "not_permitted": 2}
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
def test_unknown_condition_source_is_conditional_never_a_pass(tmp_path):
    y = LUMEN.read_text().replace("evaluated_from: approve", "evaluated_from: a_signature")
    (tmp_path / "lumen.yaml").write_text(y)
    data = G.build(O.load(tmp_path / "lumen.yaml"), FIX)
    w = cell(data, "supervisor", "write")
    assert w["verdicts"]["permitted"] == 0
    assert w["unmet"]["human_confirmed"] == 3


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
