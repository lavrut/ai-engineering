"""Tests for sections 5 and 9: code graders, report, compare and gate. Expected numbers are in
data/fixtures/expected_scores.json, worked out by hand from the fixture files."""
import json
from pathlib import Path

import pytest

import triage_lab as lab

FIXTURES = Path(__file__).resolve().parent.parent.parent / "data" / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected_scores.json").read_text(encoding="utf-8"))
V1 = "triage__claude-haiku-5-5__v1_basic__default"
V2 = "triage__claude-haiku-5-5__v2_rules__default"
TICKETS = {t["id"]: t for t in lab.load_tickets(FIXTURES / "tickets.jsonl")}


def graded(name, rep=None):
    """Grade every saved line of one fixture file (optionally only one rep)."""
    lines = lab.load_jsonl(FIXTURES / "responses" / f"{name}.jsonl")
    return [lab.grade(TICKETS[l["case_id"]], l) for l in lines if rep is None or l["config"]["rep"] == rep]


def fake(case_id, ok):
    return {"case_id": case_id, "category_ok": ok, "priority_ok": ok, "both_ok": ok, "outcome": "ok"}


def test_grade_correct_and_wrong():
    ticket = {"id": "x", "category": "bug", "priority": "high"}
    line = {"output": {"category": "bug", "priority": "low", "reply": "r"}, "stop_reason": "end_turn", "error": None}
    assert lab.grade(ticket, line) == {"case_id": "x", "category_ok": True, "priority_ok": False,
                                       "both_ok": False, "outcome": "ok"}


def test_grade_non_ok_outcomes_are_all_false():
    ticket = {"id": "x", "category": "bug", "priority": "high"}
    for line, outcome in [({"output": None, "stop_reason": "refusal", "error": None}, "refusal"),
                          ({"output": None, "stop_reason": "max_tokens", "error": None}, "max_tokens"),
                          ({"output": None, "stop_reason": None, "error": "boom"}, "error")]:
        g = lab.grade(ticket, line)
        assert g["outcome"] == outcome
        assert not (g["category_ok"] or g["priority_ok"] or g["both_ok"])


def test_v1_scores_match_expected():
    exp = EXPECTED[V1]
    r = lab.report(graded(V1))
    assert (r["n"], r["errors"]) == (exp["n"], exp["errors"])
    for field in ("category", "priority", "both"):
        assert r[field] == pytest.approx(exp[field])


def test_v2_scores_over_two_reps_match_expected():
    exp = EXPECTED[V2]
    r = lab.report(graded(V2), reps=exp["reps"])
    assert (r["n"], r["errors"]) == (exp["n"], exp["errors"])
    for field in ("category", "priority", "both"):
        assert r[field] == pytest.approx(exp[field])
    assert r["band"] == pytest.approx(EXPECTED["band_8_2"])


def test_error_cases_excluded_from_accuracy():
    grades = graded(V1)
    assert len(grades) == 8
    assert lab.accuracy(grades, "both_ok") == pytest.approx(3 / 7)   # f008 (error) is not in the denominator
    assert lab.accuracy([g for g in grades if g["outcome"] != "refusal"], "both_ok") == pytest.approx(3 / 6)


def test_accuracy_of_nothing_is_zero():
    assert lab.accuracy([], "both_ok") == 0.0


def test_confusion_matrix_matches_expected():
    pairs = []
    for line in lab.load_jsonl(FIXTURES / "responses" / f"{V1}.jsonl"):
        if line["error"]:
            continue
        predicted = line["output"]["category"] if line["output"] else None
        pairs.append((TICKETS[line["case_id"]]["category"], predicted))
    assert lab.confusion_matrix(pairs) == EXPECTED["v1_basic_confusion_category"]


def test_noise_band():
    assert lab.noise_band(8) == pytest.approx(EXPECTED["band_8_1"])
    assert lab.noise_band(8, 2) == pytest.approx(0.25)


def test_compare_v1_v2_matches_expected():
    exp = EXPECTED["compare_v1_v2"]
    c = lab.compare(graded(V1), graded(V2, rep=0))
    assert c["a"] == pytest.approx(exp["a"])
    assert c["b"] == pytest.approx(exp["b"])
    assert c["diff"] == pytest.approx(exp["diff"])
    assert c["band"] == pytest.approx(EXPECTED["band_8_1"])
    assert c["fixed"] == exp["fixed"]
    assert c["broken"] == exp["broken"]
    assert c["significant"] is exp["significant"]


def test_gate_passes_within_band():
    baseline = [fake(f"c{i}", i < 80) for i in range(100)]     # 0.80, band 0.10
    candidate = [fake(f"c{i}", i < 75) for i in range(100)]    # 0.75, drop 0.05 is inside the band
    assert lab.gate(candidate, baseline) == {"passed": True, "reasons": []}


def test_gate_fails_on_drop_beyond_band():
    baseline = [fake(f"c{i}", i < 90) for i in range(100)]     # 0.90, band 0.10
    candidate = [fake(f"c{i}", i < 75) for i in range(100)]    # 0.75 >= minimum, but 0.15 below baseline
    result = lab.gate(candidate, baseline)
    assert result["passed"] is False
    assert len(result["reasons"]) == 1 and "baseline" in result["reasons"][0]


def test_gate_fails_below_minimum():
    grades = [fake(f"c{i}", i < 50) for i in range(100)]
    result = lab.gate(grades, grades)
    assert result["passed"] is False and "minimum" in result["reasons"][0]
