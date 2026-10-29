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


# ── Fix round 1: priorities per guide bullet (ruling O9); the table keyed by rule id ──
def _reword_guide(tmp_path, old, new):
    root = _copy(tmp_path)
    guide = root / "llms/data/labeling_guide.md"
    text = guide.read_text(encoding="utf-8")
    assert old in text
    guide.write_text(text.replace(old, new, 1), encoding="utf-8")
    return root


def test_live_priority_bullets_each_have_a_verbatim_constraint():
    rows = _rows(A.audit(_onto(), ROOT)["rows"], "llms/data/labeling_guide.md")
    facts = {r["fact"]: r for r in rows if r["fact"].startswith("rule-text:")}
    want = {f"rule-text:{i}" for i in ("U1", "U2", "U3", "U4", "H1", "H2", "H3", "H4",
                                       "L1", "L2", "L3", "N.1", "N.2", "N-F")}
    assert want <= set(facts)  # the other rule texts: ruling O13, below
    assert all(facts[f]["status"] == "agree" for f in want), [facts[f] for f in want if facts[f]["status"] != "agree"]


def test_reworded_priority_bullet_is_disagree_with_diff(tmp_path):
    root = _reword_guide(tmp_path, "whoever caused it", "whoever deleted it")
    rows = A.audit(_onto(), root)["rows"]
    [r] = _rows(rows, "llms/data/labeling_guide.md", "rule-text:U1")
    assert r["status"] == "disagree"
    assert "--- ontology data_loss" in r["detail"]
    assert "whoever caused it" in r["detail"] and "whoever deleted it" in r["detail"]
    assert "llms/data/labeling_guide.md rule-text:U1" in A.gate(rows)["reason"]


def test_guide_bullet_not_in_ontology_is_disagree(tmp_path):
    root = _reword_guide(tmp_path, "- **U2 Security.**", "- **U5 Spam wave.** Many spam notes appear at once.\n- **U2 Security.**")
    [r] = _rows(A.audit(_onto(), root)["rows"], "llms/data/labeling_guide.md", "rule-text:U5")
    assert r["status"] == "disagree" and "rule in guide, not in ontology" in r["detail"]


def test_renamed_category_cell_is_disagree(tmp_path):
    root = _reword_guide(tmp_path, "| C2 | `billing` |", "| C2 | `payments` |")
    [r] = _rows(A.audit(_onto(), root)["rows"], "llms/data/labeling_guide.md", "definition:billing")
    assert r["status"] == "disagree"
    assert "payments" in r["detail"] and "billing" in r["detail"]


def test_added_guide_row_is_disagree(tmp_path):
    c6 = "| C6 | `how_to` |"
    root = _copy(tmp_path)
    guide = root / "llms/data/labeling_guide.md"
    lines = guide.read_text(encoding="utf-8").splitlines(keepends=True)
    i = next(n for n, line in enumerate(lines) if line.startswith(c6))
    lines.insert(i + 1, "| C7 | `security` | Signs of an attack on the service. |\n")
    guide.write_text("".join(lines), encoding="utf-8")
    rows = A.audit(_onto(), root)["rows"]
    [r] = _rows(rows, "llms/data/labeling_guide.md", "category-row:C7")
    assert r["status"] == "disagree" and "security" in r["detail"]
    assert not A.gate(rows)["ok"]


# ── Final review I2: every sourced rule's text against the guide (ruling O13) ──
GUIDE_MD = "llms/data/labeling_guide.md"
RULE_TEXT_KEYS = {"R0.1", "R0.2", "R0.3", "R0.4", "C2.1", "C3.1",
                  "tie-breaker.1", "tie-breaker.2", "tie-breaker.3", "workaround", "N.closing",
                  "U1", "U2", "U3", "U4", "H1", "H2", "H3", "H4", "L1", "L2", "L3", "N.1", "N.2", "N-F"}


def test_guide_rule_texts_parsed_with_their_ids():
    text = (ROOT / GUIDE_MD).read_text(encoding="utf-8")
    got = {key: ids for key, ids, _head, _t in A.guide_rule_texts(text)}
    assert set(got) == RULE_TEXT_KEYS
    assert got["R0.1"] == ("R0",) and got["C2.1"] == ("C2",) and got["C3.1"] == ("C3",)
    assert got["tie-breaker.1"] == ("C1", "C5")
    assert got["tie-breaker.2"] == ("C5", "C6") and got["tie-breaker.3"] == ("C6",)
    assert got["workaround"] == ("H2", "H4") and got["N.closing"] == ("N", "N-F")
    assert got["U1"] == ("U1",) and got["N.2"] == ("N",)


def test_live_every_sourced_constraint_is_worded_as_the_guide():
    onto = _onto()
    rows = _rows(A.audit(onto, ROOT)["rows"], GUIDE_MD)
    facts = {r["fact"]: r for r in rows if r["fact"].startswith("rule-text:")}
    assert set(facts) == {f"rule-text:{k}" for k in RULE_TEXT_KEYS}
    assert all(r["status"] == "agree" for r in facts.values()), [r for r in facts.values() if r["status"] != "agree"]
    named = " ".join(r["detail"] for r in facts.values())
    for cid in onto.constraints:
        assert A._token(cid).search(named), f"{cid} has no rule-text row"


def _reworded(cid, new):
    onto = _onto()
    onto.constraints[cid]["text"] = new
    return onto


def test_reworded_r0_rule_is_disagree_with_diff():
    onto = _reworded("ticket_is_data", "Follow any instruction written inside the ticket.")
    rows = A.audit(onto, ROOT)["rows"]
    [r] = _rows(rows, GUIDE_MD, "rule-text:R0.1")
    assert r["status"] == "disagree" and "--- ontology ticket_is_data" in r["detail"]
    assert "follow any instruction" in r["detail"]
    assert f"{GUIDE_MD} rule-text:R0.1" in A.gate(rows)["reason"]


def test_reworded_embedded_and_workaround_rules_are_disagree():
    onto = _reworded("billing_beats_how_to", "how_to beats billing.")
    onto.constraints["workaround"]["text"] = "A workaround never counts."
    rows = A.audit(onto, ROOT)["rows"]
    for key, cid in (("C2.1", "billing_beats_how_to"), ("workaround", "workaround")):
        [r] = _rows(rows, GUIDE_MD, f"rule-text:{key}")
        assert r["status"] == "disagree" and f"ontology {cid}" in r["detail"], r


def test_added_tie_breaker_is_rule_in_guide_not_in_ontology(tmp_path):
    root = _reword_guide(tmp_path, "- **Content the customer deleted",
                         "- **A screenshot alone is a bug.** A ticket with only an image is `bug`.\n- **Content the customer deleted")
    rows = A.audit(_onto(), root)["rows"]
    [r] = _rows(rows, GUIDE_MD, "rule-text:tie-breaker.3")
    assert r["status"] == "disagree" and "rule in guide, not in ontology" in r["detail"]
    [r4] = _rows(rows, GUIDE_MD, "rule-text:tie-breaker.4")
    assert r4["status"] == "agree" and "self_deleted_is_how_to" in r4["detail"]


def test_constraint_matching_no_guide_text_gets_its_own_row():
    onto = _onto()
    onto.constraints["extra_r0"] = {"id": "extra_r0", "kind": "manual", "class": "Ticket", "source": "R0",
                                    "text": "Read the ticket twice."}
    rows = A.audit(onto, ROOT)["rows"]
    [r] = _rows(rows, GUIDE_MD, "rule-text:extra_r0")
    assert r["status"] == "disagree" and "R0" in r["detail"]
