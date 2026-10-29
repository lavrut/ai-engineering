"""Tests for grounding.py: the saved model outputs are validated against the ontology.
Fixtures are tiny files in tmp_path; the real files are only counted. No API calls."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import grounding as G  # noqa: E402
import ontology as O  # noqa: E402

ROOT = HERE.parents[2]


def _line(case_id, output, error=None):
    return json.dumps({"key": "k", "config": {"kind": "triage"}, "case_id": case_id, "output": output, "error": error})


def _responses(tmp_path, lines, name="triage__m__v1__default.jsonl"):
    d = tmp_path / "responses"
    d.mkdir(exist_ok=True)
    (d / name).write_text("\n".join(lines) + "\n")
    return d


def test_triage_records_cover_every_response_file():
    files = G._response_files(ROOT / "llms/data/responses")
    assert len(files) == 12  # 11 top-level + the Qwen file in triage__Qwen/
    assert any(f.parent.name == "triage__Qwen" for f in files)
    recs = G.triage_records()
    assert {r["source"] for r in recs} == {f.stem for f in files}
    assert len(recs) == sum(len(f.read_text().splitlines()) for f in files)


def test_missing_priority_is_counted_not_skipped(tmp_path):
    d = _responses(tmp_path, [
        _line("t001", {"category": "billing", "priority": "high", "reply": "x"}),
        _line("t002", {"category": "billing", "reply": "x"}),
        _line("t003", None, error="boom"),
        _line("t004", {"category": "nonsense", "priority": "high", "reply": "x"}),
    ])
    recs = G.triage_records(d)
    assert [r["case_id"] for r in recs] == ["t001", "t002", "t003", "t004"]
    assert recs[1]["priority"] is None and recs[2]["category"] is None
    out = G.validate_sources(O.load(O.LUMEN), {"s": recs})
    s = out["sources"]["s"]
    assert s["records"] == 4
    assert s["violations"]["missing_field"] == 1
    assert s["violations"]["response_error"] == 1
    assert "t002" in s["violating"] and "t003" in s["violating"] and "t004" in s["violating"]
    assert "t001" not in s["violating"]
    assert s["violating"] == sorted(s["violating"])
    assert out["totals"]["records"] == 4


def test_manual_constraints_are_counted_once_each(tmp_path):
    d = _responses(tmp_path, [_line(f"t00{i}", {"category": "billing", "priority": "high", "reply": "x"}) for i in range(1, 4)])
    out = G.validate_sources(O.load(O.LUMEN), {"s": G.triage_records(d)})
    manual = out["sources"]["s"]["manual"]
    assert manual and all(v == 3 for v in manual.values())
    assert "manual" not in out["totals"]["violations"]


def test_agent_label_calls_found(tmp_path):
    d = tmp_path / "traces" / "desk-x"
    d.mkdir(parents=True)
    calls = [{"tool": "get_ticket", "args": {"id": "t001"}}, {"tool": "label_ticket", "args": {"id": "t001", "label": "billing"}, "is_error": True}]
    (d / "sdk-single-t001.json").write_text(json.dumps({"output": {"tool_calls": calls}}))
    (d / "proxy.log").write_text("not json")
    (tmp_path / "traces" / "desk-y").mkdir()
    (tmp_path / "traces" / "desk-y" / "a-chain-t002.json").write_text(json.dumps({"output": {"calls": [], "result": "x"}}))
    got = G.agent_label_calls(tmp_path / "traces")
    assert got == [{"source": "desk-x/sdk-single-t001", "set": "desk-x", "ticket": "t001", "label": "billing"}]
    real = G.agent_label_calls()
    assert len(real) == 60 and all(r["label"] for r in real)


def test_agent_label_is_validated_as_category(tmp_path):
    recs = [{"source": "s/a", "set": "s", "ticket": "t001", "label": "billing"},
            {"source": "s/b", "set": "s", "ticket": "t002", "label": "wrong"}]
    out = G.validate_sources(O.load(O.LUMEN), G.agent_sources(recs))
    s = out["sources"]["agents/s"]
    assert s["records"] == 2 and s["violating"] == ["t002"] and "missing_field" not in s["violations"]


def test_validation_json_check():
    assert G.main(["--check"]) == 0
    data = json.loads(G.VALIDATION.read_text())
    assert set(data) == {"sources", "totals"}
    assert any(k.startswith("agents/") for k in data["sources"])
    assert data["totals"]["records"] == sum(s["records"] for s in data["sources"].values())
    assert G.VALIDATION.read_text() == O.dumps(G.build(O.load(O.LUMEN)))


def test_unreadable_trace_is_counted_through_run(tmp_path):
    root = tmp_path / "repo"
    r = root / "llms/data/responses"
    r.mkdir(parents=True)
    (r / "triage__m__v1__default.jsonl").write_text("\n".join([
        _line("t001", {"category": "billing", "priority": "high", "reply": "x"}),
        _line("t002", {"category": "billing", "reply": "x"}),
        _line("t003", None, error="boom"),
    ]) + "\n")
    t = root / "agents/data/traces/desk-x"
    t.mkdir(parents=True)
    ok = [{"tool": "label_ticket", "args": {"id": "t001", "label": "billing"}}]
    (t / "a-single-t001.json").write_text(json.dumps({"output": {"tool_calls": ok}}))
    (t / "a-single-t002.json").write_text('{"output": {"tool_ca')  # truncated
    (t / "a-single-t003.json").write_text(json.dumps({"output": {"tool_calls": [{"tool": "label_ticket"}]}}))
    (t / "a-chain-t004.json").write_text(json.dumps({"output": {"calls": [], "result": "x"}}))
    out = tmp_path / "validation.json"
    data = G.run(out, root)
    assert json.loads(out.read_text()) == data
    assert data["sources"]["triage__m__v1__default"]["violations"] == {"missing_field": 1, "response_error": 1}
    s = data["sources"]["agents/desk-x"]
    assert s["records"] == 3 and s["violations"] == {"unreadable_trace": 2}
    assert s["violating"] == ["a-single-t002.json", "a-single-t003.json"]
    assert data["totals"]["violations"] == {"missing_field": 1, "response_error": 1, "unreadable_trace": 2}
