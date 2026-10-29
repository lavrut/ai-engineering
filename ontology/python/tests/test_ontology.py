"""Tests for ontology.py: Lumen's ontology loads clean, each defect in a fixture is
found and named, and records (the 60 tickets among them) validate against it.

The fixtures under fixtures/ are tiny ontologies with one kind of defect each, so a
failing test points at one check. A malformed file is a finding, never an exception.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ontology as O  # noqa: E402

ROOT = HERE.parents[2]
LUMEN = ROOT / "ontology" / "lumen.yaml"
TICKETS = ROOT / "llms" / "data" / "tickets.jsonl"
FIX = HERE / "fixtures"


def tickets():
    return [json.loads(line) for line in TICKETS.read_text().splitlines() if line.strip()]


def rules(findings, level=None):
    return [f["rule"] for f in findings if level is None or f["level"] == level]


# ── Loading and checking ────────────────────────────────────────────────────
def test_load_lumen_and_check_is_clean():
    onto = O.load(LUMEN)
    assert onto.source == LUMEN
    assert O.check(onto) == []
    assert set(onto.classes) == {"Ticket", "Category", "Priority", "Reply", "Complaint", "Product", "Customer", "Action", "AgentRole"}
    assert set(onto.properties) >= {"id", "text", "category", "priority", "tags", "split"}
    assert {k: (r["domain"], r["range"]) for k, r in onto.relations.items()}.items() >= {
        "has_category": ("Ticket", "Category"),
        "has_priority": ("Ticket", "Priority"),
        "answers": ("Reply", "Ticket"),
        "about": ("Complaint", "Product"),
    }.items()
    cats = sorted((i for i in onto.individuals.values() if i["class"] == "Category"), key=lambda i: i["order"])
    assert [c["id"] for c in cats] == ["outage", "billing", "account", "feature_request", "bug", "how_to"]
    assert [c["source"] for c in cats] == ["C1", "C2", "C3", "C4", "C5", "C6"]
    pris = sorted((i for i in onto.individuals.values() if i["class"] == "Priority"), key=lambda i: i["order"])
    assert [p["id"] for p in pris] == ["urgent", "high", "low", "normal"]
    products = {i["label"] for i in onto.individuals.values() if i["class"] == "Product"}
    assert products == set(json.loads((ROOT / "ml" / "data" / "category_map.json").read_text())["categories"])
    # every individual carries order, definition and source (ruling O1)
    for ind in onto.individuals.values():
        assert isinstance(ind["order"], int) and ind["definition"] and ind["source"], ind["id"]
    # every constraint names its guide rule; the checkable ones say which phrases state them (ruling O3)
    for c in onto.constraints.values():
        assert c["source"] and c["text"], c["id"]
    checkable = [c for c in onto.constraints.values() if c["kind"] == "checkable"]
    assert {c["id"] for c in checkable} == {"feature_request_never_above_normal"}
    assert all(c["phrases"] for c in checkable)
    manual_sources = {c["source"] for c in onto.constraints.values() if c["kind"] == "manual"}
    assert manual_sources >= {"R0", "C1", "C2", "C3", "C6", "U1", "U2", "U3", "U4",
                              "H1", "H2", "H3", "H4", "L1", "L2", "L3", "N", "N-F"}


def test_lumen_sections_have_numbered_headings():
    text = LUMEN.read_text()
    for n, name in enumerate(O.SECTIONS, 1):
        assert f"# ── {n}. " in text, name
        assert text.index(f"# ── {n}. ") < text.index(f"\n{name}:"), name


def test_check_reports_cycle_once():
    findings = O.check(O.load(FIX / "cycle.yaml"))
    cycles = [f for f in findings if f["rule"] == "subclass_cycle"]
    assert len(cycles) == 1
    assert cycles[0]["level"] == "error"
    assert cycles[0]["subject"] == "Animal"
    assert "Animal -> Puppy -> Dog -> Animal" in cycles[0]["detail"]


def test_check_dangling_range():
    findings = O.check(O.load(FIX / "dangling_range.yaml"))
    dangling = sorted((f["subject"], f["level"]) for f in findings if f["rule"] == "dangling_range")
    assert dangling == [("colour", "error"), ("owner", "error")]
    colour = next(f for f in findings if f["subject"] == "colour")
    assert "blue" in colour["detail"] and "red" not in colour["detail"].split(":")[-1]


def test_check_duplicate_id():
    findings = O.check(O.load(FIX / "duplicate_id.yaml"))
    dups = sorted(f["subject"] for f in findings if f["rule"] == "duplicate_id")
    assert dups == ["Ticket", "text"]
    assert all(f["level"] == "error" for f in findings if f["rule"] == "duplicate_id")


def test_check_constraint_unknown_property():
    findings = O.check(O.load(FIX / "constraint_unknown_property.yaml"))
    assert [(f["subject"], f["level"]) for f in findings if f["rule"] == "constraint_unknown_property"] == [
        ("needs_severity", "error")]
    assert "severity" in next(f for f in findings if f["rule"] == "constraint_unknown_property")["detail"]
    assert [f["subject"] for f in findings if f["rule"] == "constraint_unknown_class"] == ["about_widgets"]


def test_malformed_yaml_is_finding_not_exception():
    findings = O.check(O.load(FIX / "malformed.yaml"))
    assert rules(findings) == ["malformed"]
    assert findings[0]["level"] == "error" and findings[0]["subject"] == "malformed.yaml"
    # valid YAML in the wrong shape: each problem named, still no exception
    shape = O.check(O.load(FIX / "malformed_shape.yaml"))
    subjects = {(f["rule"], f["subject"]) for f in shape}
    assert ("malformed", "classes") in subjects        # a mapping where a list belongs
    assert ("malformed", "properties[0]") in subjects  # an entry with no id
    assert ("malformed", "constraints") in subjects    # a section missing
    # a file that is not there at all
    missing = O.check(O.load(FIX / "no_such_file.yaml"))
    assert rules(missing) == ["malformed"] and missing[0]["subject"] == "no_such_file.yaml"


def test_ids_written_as_lists_are_findings_not_exceptions():
    """Fix round 1 (I1): a list where an id belongs, in each of the seven places an id is read."""
    onto = O.load(FIX / "malformed_ids.yaml")
    findings = O.check(onto)  # must not raise
    malformed = {(f["subject"], f["detail"].split(" ")[0]) for f in findings if f["rule"] == "malformed"}
    assert malformed == {("B", "subclass_of"), ("p1", "domain"), ("p2", "range"), ("r1", "range"),
                         ("i1", "class"), ("k1", "class"), ("k2", "then[0].property")}
    assert all(f["level"] == "error" for f in findings if f["rule"] == "malformed")
    assert all("list" in f["detail"] for f in findings if f["rule"] == "malformed")
    # each bad field is reported once, as malformed, not again as a dangling reference
    assert [f for f in findings if f["subject"] in {"B", "p1", "p2", "r1", "i1", "k1", "k2"}
            and f["rule"] != "malformed"] == []
    # validating against it does not raise either
    for cls in ("A", "B"):
        for f in O.validate(onto, {"id": "x", "p1": "v", "p3": "w"}, cls):
            assert set(f) == {"level", "rule", "subject", "detail"}


def test_unexpected_exception_is_a_finding(monkeypatch):
    """The generic guard: a check or validation that raises becomes an `internal` finding."""
    onto = O.load(LUMEN)

    def boom(*a, **k):
        raise TypeError("unhashable type: 'list'")
    monkeypatch.setattr(O, "_check_constraint", boom)
    monkeypatch.setattr(O, "class_properties", boom)
    for found in (O.check(onto), O.validate(onto, {"id": "t001"}, "Ticket")):
        assert [(f["level"], f["rule"]) for f in found] == [("error", "internal")]
        assert "unhashable" in found[0]["detail"]


def test_in_with_string_value_is_malformed_not_substring():
    """Fix round 1 (M1): `in`/`not_in` need a list; a string value is never a substring match."""
    onto = O.load(FIX / "in_string_value.yaml")
    assert [(f["rule"], f["subject"]) for f in O.check(onto)] == [("constraint_malformed", "b_in_xyz")]
    found = O.validate(onto, {"b": "x"}, "T")
    assert [(f["level"], f["rule"]) for f in found] == [("manual", "b_in_xyz")]
    assert found[0]["detail"].startswith("not evaluated")


def test_missing_source_has_its_own_rule(tmp_path):
    """Fix round 1 (M2): a missing source is not counted as a missing definition."""
    f = tmp_path / "o.yaml"
    f.write_text("classes: [{id: C, definition: c}]\nproperties: []\nrelations: []\n"
                 "individuals: [{id: i, class: C, definition: d}, {id: j, class: C, source: X1}]\nconstraints: []\n")
    found = {(f["rule"], f["subject"]) for f in O.check(O.load(f))}
    assert found == {("missing_source", "i"), ("missing_definition", "j")}


# ── Validation ──────────────────────────────────────────────────────────────
def test_validate_ticket_ok():
    onto = O.load(LUMEN)
    t001 = next(t for t in tickets() if t["id"] == "t001")
    findings = O.validate(onto, t001, "Ticket")
    assert rules(findings, "error") == [] and rules(findings, "warning") == []


def test_validate_unknown_category():
    onto = O.load(LUMEN)
    rec = {"id": "t999", "text": "x", "category": "security", "priority": "high", "tags": [], "split": "dev"}
    errors = [f for f in O.validate(onto, rec, "Ticket") if f["level"] == "error"]
    assert [(f["rule"], f["subject"]) for f in errors] == [("range", "t999.category")]
    assert "security" in errors[0]["detail"]


def test_validate_feature_request_urgent_is_error():
    """The Review Focus: the guide's 'A feature_request is never above normal' is enforced."""
    onto = O.load(LUMEN)
    base = {"id": "t999", "text": "please build dark mode", "category": "feature_request", "tags": [], "split": "dev"}
    for pri, bad in (("urgent", True), ("high", True), ("normal", False), ("low", False)):
        errors = [f for f in O.validate(onto, {**base, "priority": pri}, "Ticket") if f["level"] == "error"]
        assert (rules(errors) == ["feature_request_never_above_normal"]) is bad, pri
        if bad:
            assert errors[0]["subject"] == "t999"
    # other categories may be urgent
    ok = O.validate(onto, {**base, "category": "bug", "priority": "urgent"}, "Ticket")
    assert rules(ok, "error") == []


def test_validate_partial_checks_only_present_values():
    """Ruling O2: model outputs carry only some properties; missing ones are not errors."""
    onto = O.load(LUMEN)
    assert rules(O.validate(onto, {"id": "t001", "category": "billing"}, "Ticket", partial=True), "error") == []
    full = O.validate(onto, {"id": "t001", "category": "billing"}, "Ticket")
    assert sorted(f["subject"] for f in full if f["rule"] == "required") == [
        "t001.priority", "t001.split", "t001.tags", "t001.text"]
    bad = O.validate(onto, {"id": "t001", "category": "feature_request", "priority": "urgent"}, "Ticket", partial=True)
    assert rules(bad, "error") == ["feature_request_never_above_normal"]
    # a checkable constraint whose properties are absent is not evaluated, and says so
    half = O.validate(onto, {"id": "t001", "category": "feature_request"}, "Ticket", partial=True)
    assert [f["detail"] for f in half if f["rule"] == "feature_request_never_above_normal"][0].startswith("not evaluated")


def test_validate_one_mistake_one_error():
    """A value outside its range is one error; the constraints that read it are not
    judged on it (they are listed as not evaluated), so a count never doubles."""
    onto = O.load(LUMEN)
    rec = {"id": "t001", "category": "feature_request", "priority": "critical"}
    found = O.validate(onto, rec, "Ticket", partial=True)
    assert [(f["rule"], f["subject"]) for f in found if f["level"] == "error"] == [("range", "t001.priority")]
    fr = [f for f in found if f["rule"] == "feature_request_never_above_normal"]
    assert fr[0]["level"] == "manual" and fr[0]["detail"].startswith("not evaluated: priority invalid")


def test_validate_skips_constraint_check_rejects():
    """A constraint check() rejects (it names a property the class lacks) is never
    evaluated against a record: it is listed as not evaluated, not raised as an error."""
    onto = O.load(FIX / "constraint_unknown_property.yaml")
    found = O.validate(onto, {"text": "x"}, "Ticket")
    assert [(f["level"], f["rule"]) for f in found] == [("manual", "needs_severity")]
    assert "malformed" in found[0]["detail"]


def test_validate_types_pattern_cardinality():
    onto = O.load(LUMEN)
    rec = {"id": "T1", "text": 5, "category": "bug", "priority": "low", "tags": "x", "split": "train", "extra": 1}
    found = {(f["level"], f["rule"], f["subject"]) for f in O.validate(onto, rec, "Ticket")}
    assert ("error", "pattern", "T1.id") in found
    assert ("error", "type", "T1.text") in found
    assert ("error", "cardinality", "T1.tags") in found
    assert ("error", "range", "T1.split") in found
    assert ("warning", "unknown_property", "T1.extra") in found
    assert rules(O.validate(onto, rec, "Gadget"), "error") == ["unknown_class"]


def test_validate_manual_constraints_listed_not_failed():
    onto = O.load(LUMEN)
    t001 = next(t for t in tickets() if t["id"] == "t001")
    findings = O.validate(onto, t001, "Ticket")
    manual = [f for f in findings if f["level"] == "manual"]
    manual_ids = {c["id"] for c in onto.constraints.values() if c["kind"] == "manual"}
    assert {f["rule"] for f in manual} == manual_ids
    assert all(f["subject"] == "t001" for f in manual)
    # each manual finding carries the guide rule id it encodes
    for f in manual:
        assert f["detail"].startswith(onto.constraints[f["rule"]]["source"] + ": ")
    assert rules(findings, "error") == []


def test_findings_sorted_exact_shape():
    onto = O.load(LUMEN)
    rec = {"id": "bad", "text": 1, "category": "feature_request", "priority": "urgent", "tags": [], "split": "x"}
    for findings in (O.validate(onto, rec, "Ticket"), O.check(O.load(FIX / "duplicate_id.yaml")),
                     O.check(O.load(FIX / "constraint_unknown_property.yaml"))):
        assert findings
        for f in findings:
            assert set(f) == {"level", "rule", "subject", "detail"}
            assert f["level"] in ("error", "warning", "manual")
            assert all(isinstance(v, str) for v in f.values())
        assert findings == sorted(findings, key=lambda f: (f["level"], f["rule"], f["subject"]))
    # ties on (level, rule, subject) are ordered by detail (fix round 1, M5)
    tied = [f for f in O.validate(onto, {"id": "t001", "tags": [1, "x", 2.5]}, "Ticket", partial=True)
            if f["rule"] == "type"]
    assert len(tied) == 2 and {f["subject"] for f in tied} == {"t001.tags"}
    assert [f["detail"] for f in tied] == sorted(f["detail"] for f in tied)


def test_all_60_tickets_validate():
    onto = O.load(LUMEN)
    ts = tickets()
    assert len(ts) == 60
    for t in ts:
        assert [f for f in O.validate(onto, t, "Ticket") if f["level"] != "manual"] == [], t["id"]


# ── Export and the command line ─────────────────────────────────────────────
def test_export_deterministic_and_check(tmp_path, capsys):
    onto = O.load(LUMEN)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    data = O.export(onto, a)
    O.export(O.load(LUMEN), b)
    assert a.read_bytes() == b.read_bytes()
    text = a.read_text()
    assert text.endswith("\n") and text == json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert set(data) >= set(O.SECTIONS)
    for name in O.SECTIONS:
        ids = [e["id"] for e in data[name]]
        assert ids == sorted(ids), name
    assert data["source"] == "ontology/lumen.yaml"
    assert str(ROOT) not in text
    # the committed file matches, and --check says so
    assert O.main(["export", "--check"]) == 0
    assert "ontology.json matches" in capsys.readouterr().out
    # drift is named
    assert O.main(["export", "--check", "--out", str(tmp_path / "missing.json")]) == 1
    assert "missing.json" in capsys.readouterr().out


def test_cli_check_and_validate(capsys):
    assert O.main(["check"]) == 0
    assert O.main(["validate", "Ticket", str(TICKETS)]) == 0
    out = capsys.readouterr().out
    assert "60 records" in out and "0 errors" in out
    assert O.main(["check", "--file", str(FIX / "cycle.yaml")]) == 1


# ── Final review I1: ordered ops only on ordered types (ruling O12) ─────────
NEVER_ABOVE = "then: [{property: priority, op: in, value: [normal, low]}]"


def _lumen_with(tmp_path, old, new):
    text = LUMEN.read_text(encoding="utf-8")
    assert old in text
    f = tmp_path / "lumen.yaml"
    f.write_text(text.replace(old, new, 1), encoding="utf-8")
    return O.load(f)


def test_ordered_op_on_enumeration_is_malformed(tmp_path):
    """`priority lte normal` would compare strings alphabetically ('high' < 'normal'),
    so check() rejects it and validate never runs it. `order` is a decision order, not
    a rank (O4), so no ordering is derived from it."""
    onto = _lumen_with(tmp_path, NEVER_ABOVE, "then: [{property: priority, op: lte, value: normal}]")
    bad = [f for f in O.check(onto) if f["level"] == "error"]
    assert [(f["rule"], f["subject"]) for f in bad] == [("constraint_malformed", "feature_request_never_above_normal")]
    assert "lte" in bad[0]["detail"] and "priority" in bad[0]["detail"]
    for prio in ("high", "urgent", "low"):
        found = O.validate(onto, {"id": "t1", "category": "feature_request", "priority": prio}, "Ticket", partial=True)
        fr = [f for f in found if f["rule"] == "feature_request_never_above_normal"]
        assert [f["level"] for f in fr] == ["manual"], (prio, fr)
        assert fr[0]["detail"].startswith("not evaluated: the constraint is malformed")


def test_ordered_op_on_string_is_malformed_on_integer_is_not(tmp_path):
    f = tmp_path / "o.yaml"
    f.write_text(
        "classes: [{id: T, definition: A thing.}]\n"
        "properties:\n"
        "  - {id: s, domain: T, range: string, cardinality: one}\n"
        "  - {id: n, domain: T, range: integer, cardinality: one}\n"
        "relations: []\nindividuals: []\n"
        "constraints:\n"
        "  - {id: s_gt, kind: checkable, class: T, source: X1, text: s above m., then: [{property: s, op: gt, value: m}]}\n"
        "  - {id: n_lt, kind: checkable, class: T, source: X2, text: n below 5., then: [{property: n, op: lt, value: 5}]}\n",
        encoding="utf-8")
    onto = O.load(f)
    assert [(x["rule"], x["subject"]) for x in O.check(onto)] == [("constraint_malformed", "s_gt")]
    found = O.validate(onto, {"s": "z", "n": 7}, "T")
    assert [(x["level"], x["rule"]) for x in found] == [("error", "n_lt"), ("manual", "s_gt")]


# ── Final review minors ─────────────────────────────────────────────────────
def test_then_as_mapping_reads_malformed_not_a_type_error(tmp_path):
    """M1: a `then` written as a mapping is reported as malformed, with no leaked error text."""
    onto = _lumen_with(tmp_path, NEVER_ABOVE, "then: {property: priority, op: in, value: [normal, low]}")
    found = O.validate(onto, {"id": "t1", "category": "feature_request", "priority": "high"}, "Ticket", partial=True)
    [fr] = [f for f in found if f["rule"] == "feature_request_never_above_normal"]
    assert fr["level"] == "manual" and fr["detail"].startswith("not evaluated: the constraint is malformed")


def test_validate_missing_file_is_one_line_not_a_traceback(tmp_path, capsys):
    """M2: a JSONL file that is not there is a one-line message and exit 1."""
    assert O.main(["validate", "Ticket", str(tmp_path / "missing.jsonl")]) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and "missing.jsonl" in out[0] and "cannot read" in out[0]


# ── Section 6: actions ──────────────────────────────────────────────────────
def test_lumen_actions_section():
    onto = O.load(LUMEN)
    a = onto.actions
    assert {i for i, x in onto.individuals.items() if x["class"] == "Action"} == {"read", "write", "approve", "escalate"}
    assert {i for i, x in onto.individuals.items() if x["class"] == "AgentRole"} == {
        "single", "triage", "research", "drafter", "reviewer", "supervisor", "human"}
    assert [c["id"] for c in a["conditions"]] == ["human_confirmed", "review_passed"]
    assert a["tools"]["label_ticket"] == "write" and a["tools"]["handoff"] == "escalate"
    rows = {(r["role"], r["action"]): r.get("conditions", []) for r in a["permitted"]}
    assert rows[("single", "write")] == ["human_confirmed"]
    assert rows[("supervisor", "write")] == ["review_passed", "human_confirmed"]
    assert ("research", "write") not in rows and ("drafter", "escalate") not in rows
    assert all(rows[(r, "read")] == [] for r in ("single", "triage", "research", "drafter", "reviewer", "supervisor", "human"))
    text = LUMEN.read_text()
    assert text.index("# ── 6. Actions") < text.index("\nactions:")


def test_check_actions_dangling_ids_are_errors_naming_the_id():
    findings = O.check(O.load(FIX / "actions_dangling.yaml"))
    got = sorted((f["subject"], f["level"]) for f in findings if f["rule"] == "dangling_action_ref")
    assert got == [("Bob", "error"), ("delete", "error"), ("fly", "error"), ("ghost", "error"), ("signed_off", "error")]
    detail = {f["subject"]: f["detail"] for f in findings if f["rule"] == "dangling_action_ref"}
    assert "AgentRole" in detail["Bob"] and "permitted[3]" in detail["Bob"]
    assert "delete_ticket" in detail["delete"]
    assert "condition" in detail["signed_off"]
    assert [(f["subject"], f["level"]) for f in findings if f["rule"] == "duplicate_permission"] == [("bot read", "error")]
    assert [(f["subject"], f["level"]) for f in findings if f["rule"] == "duplicate_condition"] == [("confirmed", "error")]
    assert "unknown_section" not in rules(findings)


def test_check_actions_malformed_is_a_finding_not_an_exception():
    findings = O.check(O.load(FIX / "actions_malformed.yaml"))
    assert "internal" not in rules(findings)
    bad = sorted(f["subject"] for f in findings if f["rule"] == "malformed")
    assert bad == ["actions.conditions[0]", "actions.conditions[1]", "actions.permitted[0]",
                   "actions.permitted[1]", "actions.permitted[2]", "actions.tools"]
    assert all(f["level"] == "error" for f in findings if f["rule"] == "malformed")


def test_actions_section_is_optional_and_exported():
    assert O.load(FIX / "cycle.yaml").actions == {}
    assert "actions" not in rules(O.check(O.load(FIX / "cycle.yaml")))
    data = O.to_json(O.load(LUMEN))
    assert data["actions"]["tools"]["label_ticket"] == "write"
    assert [c["id"] for c in data["actions"]["conditions"]] == ["human_confirmed", "review_passed"]
    assert "\n" not in data["actions"]["conditions"][0]["text"]
    assert len(data["actions"]["permitted"]) == 11
