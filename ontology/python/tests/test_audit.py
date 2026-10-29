"""Tests for audit.py: the ontology compared with every artefact that implies the same
facts, and the gate. Fixtures are copies of the real artefacts in tmp_path with one
planted change each (plus the planted contract under fixtures/audit/). No API calls."""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import audit as A  # noqa: E402
import ontology as O  # noqa: E402

ROOT = HERE.parents[2]
FIXTURE = HERE / "fixtures" / "audit"
ARTEFACTS = ("llms/data/labeling_guide.md", "llms/data/prompts.json", "ml/data/category_map.json")
STATUSES = {"agree", "disagree", "missing"}


def _onto():
    return O.load(O.LUMEN)


def _copy(tmp_path: Path, contracts: bool = True) -> Path:
    """A copy of the real artefacts under tmp_path, same layout as the repo."""
    for rel in ARTEFACTS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    (tmp_path / "data/contracts").mkdir(parents=True)
    if contracts:
        shutil.copy(ROOT / "data/contracts/tickets.v1.yaml", tmp_path / "data/contracts/tickets.v1.yaml")
    return tmp_path


def _rows(rows, artefact, fact=None):
    return [r for r in rows if r["artefact"] == artefact and (fact is None or r["fact"] == fact)]


def test_audit_live_rows_shape():
    data = A.audit(_onto(), ROOT)
    rows = data["rows"]
    assert rows, "the live audit has rows"
    for r in rows:
        assert set(r) == {"artefact", "fact", "status", "detail"}
        assert r["status"] in STATUSES
        assert all(isinstance(r[k], str) and r[k] for k in r)
        assert not r["artefact"].startswith("/"), "artefacts are named by repo-relative path"
    assert rows == sorted(rows, key=lambda r: (r["artefact"], r["fact"]))
    arts = {r["artefact"] for r in rows}
    assert {"llms/data/labeling_guide.md", "llms/data/prompts.json#triage.v2_rules",
            "llms/data/prompts.json#triage.v3_examples", "ml/data/category_map.json",
            "data/contracts/tickets.v1.yaml", "data/contracts/tickets.v2.yaml"} <= arts
    # Every constraint's rule id and every category's definition is audited against the guide.
    guide = _rows(rows, "llms/data/labeling_guide.md")
    onto = _onto()
    sources = {c["source"] for c in onto.constraints.values()}
    assert {f"rule:{s}" for s in sources} <= {r["fact"] for r in guide}
    cats = [i for i, v in onto.individuals.items() if v["class"] == "Category"]
    assert {f"definition:{c}" for c in cats} <= {r["fact"] for r in guide}
    # The gate reflects the data: tickets.v2 adds `security`, so it is red.
    assert data["gate"] == A.gate(rows)
    v2 = _rows(rows, "data/contracts/tickets.v2.yaml", "enum:category")
    assert v2 and v2[0]["status"] == "disagree" and "security" in v2[0]["detail"]
    assert data["gate"]["ok"] is False
    assert data["gate"]["disagree"] == sum(r["status"] == "disagree" for r in rows)


def test_live_guide_and_prompts_agree():
    """The guide's category table and the ontology's definitions are worded alike today,
    and both prompts name every category and priority and state the checkable rule."""
    rows = A.audit(_onto(), ROOT)["rows"]
    for r in rows:
        if r["artefact"] == "llms/data/labeling_guide.md" or r["artefact"].startswith("llms/data/prompts.json"):
            assert r["status"] == "agree", r
    assert all(r["status"] == "agree" for r in _rows(rows, "ml/data/category_map.json"))
    assert all(r["status"] == "agree" for r in _rows(rows, "data/contracts/tickets.v1.yaml"))


def test_reworded_heading_is_disagree_with_diff(tmp_path):
    root = _copy(tmp_path)
    guide = root / "llms/data/labeling_guide.md"
    text = guide.read_text(encoding="utf-8")
    old = "| C2 | `billing` | Money, charges, refunds, invoices,"
    assert old in text
    guide.write_text(text.replace(old, "| C2 | `billing` | Payments, charges, refunds, invoices,"), encoding="utf-8")
    rows = A.audit(_onto(), root)["rows"]
    [r] = _rows(rows, "llms/data/labeling_guide.md", "definition:billing")
    assert r["status"] == "disagree"
    assert "--- ontology" in r["detail"] and "+++ " in r["detail"]
    assert "-money, charges" in r["detail"] and "+payments, charges" in r["detail"]
    # Markdown, case and spacing alone never make a disagreement (ruling O5).
    others = [x for x in _rows(rows, "llms/data/labeling_guide.md") if x["fact"].startswith("definition:")]
    assert [x["fact"] for x in others if x["status"] != "agree"] == ["definition:billing"]
    g = A.gate(rows)
    assert g["ok"] is False and "llms/data/labeling_guide.md definition:billing" in g["reason"]


def test_markdown_case_and_whitespace_are_normalised():
    assert A.normalise("  This beats **`how_to`**:\n  \"How do I\"  ") == 'this beats how_to: "how do i"'


def test_planted_extra_enum_is_disagree(tmp_path):
    root = _copy(tmp_path)
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
    rows = A.audit(_onto(), root)["rows"]
    art = "data/contracts/tickets.planted.yaml"
    [cat] = _rows(rows, art, "enum:category")
    assert cat["status"] == "disagree" and "security" in cat["detail"]
    [pri] = _rows(rows, art, "enum:priority")
    assert pri["status"] == "disagree" and "low" in pri["detail"]
    [v1] = _rows(rows, "data/contracts/tickets.v1.yaml", "enum:category")
    assert v1["status"] == "agree"
    g = A.gate(rows)
    assert g["ok"] is False and g["disagree"] == 2
    assert f"{art} enum:category" in g["reason"]


def test_missing_artefact_is_missing_row(tmp_path):
    root = _copy(tmp_path)
    (root / "ml/data/category_map.json").unlink()
    (root / "llms/data/prompts.json").write_text("{ not json", encoding="utf-8")
    (root / "data/contracts/broken.yaml").write_text("schema: [unclosed\n", encoding="utf-8")
    rows = A.audit(_onto(), root)["rows"]
    [cm] = _rows(rows, "ml/data/category_map.json")
    assert cm["status"] == "missing" and "not found" in cm["detail"]
    [pr] = _rows(rows, "llms/data/prompts.json")
    assert pr["status"] == "missing" and "JSON" in pr["detail"]
    [br] = _rows(rows, "data/contracts/broken.yaml")
    assert br["status"] == "missing" and "YAML" in br["detail"]
    g = A.gate(rows)
    assert g["ok"] is True and g["missing"] == 3 and g["disagree"] == 0


def test_prompt_missing_a_rule_and_a_name_is_missing(tmp_path):
    root = _copy(tmp_path)
    p = root / "llms/data/prompts.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["triage"]["v2_rules"] = d["triage"]["v2_rules"].replace(" A feature_request is never above normal.", "").replace("how_to", "how-to")
    del d["triage"]["v3_examples"]
    p.write_text(json.dumps(d), encoding="utf-8")
    rows = A.audit(_onto(), root)["rows"]
    art = "llms/data/prompts.json#triage.v2_rules"
    assert _rows(rows, art, "rule:feature_request_never_above_normal")[0]["status"] == "missing"
    assert _rows(rows, art, "category:how_to")[0]["status"] == "missing"
    assert _rows(rows, art, "category:billing")[0]["status"] == "agree"
    [v3] = _rows(rows, "llms/data/prompts.json#triage.v3_examples")
    assert v3["status"] == "missing"


def test_rule_stated_by_guide_id_counts(tmp_path):
    """Ruling O3: the checkable rule is stated when its guide id appears, even without a phrase."""
    root = _copy(tmp_path)
    p = root / "llms/data/prompts.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["triage"]["v2_rules"] = d["triage"]["v2_rules"].replace("A feature_request is never above normal.", "Apply n-f.")
    p.write_text(json.dumps(d), encoding="utf-8")
    rows = A.audit(_onto(), root)["rows"]
    [r] = _rows(rows, "llms/data/prompts.json#triage.v2_rules", "rule:feature_request_never_above_normal")
    assert r["status"] == "agree" and "N-F" in r["detail"]


def test_gate_red_on_disagree():
    agree = {"artefact": "a", "fact": "f", "status": "agree", "detail": "ok"}
    missing = {"artefact": "b", "fact": "g", "status": "missing", "detail": "gone"}
    bad = {"artefact": "c", "fact": "h", "status": "disagree", "detail": "differs"}
    g = A.gate([agree, missing])
    assert g == {"ok": True, "disagree": 0, "missing": 1, "reason": g["reason"]} and g["reason"]
    g = A.gate([agree, missing, bad])
    assert g["ok"] is False and g["disagree"] == 1 and g["missing"] == 1
    assert "c h" in g["reason"]


def test_audit_json_check(tmp_path, capsys):
    out = tmp_path / "audit.json"
    assert A.main(["--check", "--out", str(out)]) == 1
    assert "audit.json" in capsys.readouterr().out
    assert A.main(["--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n") and str(ROOT) not in text
    data = json.loads(text)
    assert set(data) == {"rows", "gate"}
    assert A.main(["--check", "--out", str(out)]) == 0
    out.write_text(text.replace('"agree"', '"disagree"', 1), encoding="utf-8")
    assert A.main(["--check", "--out", str(out)]) == 1
    # The committed file is current.
    assert A.main(["--check"]) == 0
