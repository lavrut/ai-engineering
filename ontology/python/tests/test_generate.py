"""Tests for generate.py: the AI artefacts come from the ontology alone, and each is
compared with the course's hand-written one. A mismatch is a stated fact pinned here,
never a failure of the generator."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import generate as G  # noqa: E402
import ontology as O  # noqa: E402

ROOT = HERE.parents[2]
ONTO = O.load(O.LUMEN)
CATS = ["outage", "billing", "account", "feature_request", "bug", "how_to"]
PRIOS = ["urgent", "high", "low", "normal"]


def test_definitions_text_in_rule_order():
    lines = G.definitions_text(ONTO).splitlines()
    assert [l.split(":")[0] for l in lines] == [f"- {i}" for i in CATS + PRIOS]
    assert all(re.match(r"^- \w+: \S", l) for l in lines)
    assert "\n" not in lines[0] and "  " not in lines[0]
    assert lines[0] == "- outage: " + " ".join(ONTO.individuals["outage"]["definition"].split())


def test_json_schema_enums_match_individuals():
    s = G.json_schema(ONTO)
    assert s["type"] == "object" and s["additionalProperties"] is False
    assert s["properties"]["category"]["enum"] == CATS
    assert s["properties"]["priority"]["enum"] == PRIOS
    assert s["properties"]["reply"] == {"type": "string"}
    assert s["required"] == ["category", "priority", "reply"]


def test_tool_definition_label_enum():
    t = G.tool_definition(ONTO)
    assert t["name"] == "label_ticket"
    props = t["input_schema"]["properties"]
    assert props["id"]["pattern"] == ONTO.properties["id"]["pattern"] == r"^t\d{3}$"
    assert props["label"]["enum"] == CATS
    assert t["input_schema"]["required"] == ["id", "label"]


def test_compare_has_diff_per_artefact():
    c = G.compare(ONTO)
    assert set(c) == {"definitions", "schema", "tool"}
    for name, row in c.items():
        assert {"existing", "generated", "diff"} <= set(row), name
        assert isinstance(row["diff"], str)
    assert c["definitions"]["diff"] != ""  # numbered rule prose vs one line per individual
    assert c["definitions"]["existing"].startswith("Categories")


def test_schema_differs_from_the_lab_only_in_order():
    """Same keys, same required, same additionalProperties; the enums hold the same
    members but the lab lists categories as billing, bug, ... and priorities low..urgent."""
    c = G.compare(ONTO)
    ex, gen = c["schema"]["existing"], c["schema"]["generated"]
    assert ex["required"] == gen["required"] and ex["additionalProperties"] is gen["additionalProperties"] is False
    for k in ("category", "priority"):
        assert sorted(ex["properties"][k]["enum"]) == sorted(gen["properties"][k]["enum"])
    assert ex["properties"]["priority"]["enum"] == ["low", "normal", "high", "urgent"]
    assert c["schema"]["diff"] != ""
    assert '"billing"' in c["schema"]["diff"]


def test_tool_differs_from_the_server_by_pattern_and_enum():
    c = G.compare(ONTO)
    ex = c["tool"]["existing"]
    assert ex["input_schema"]["properties"] == {"id": {"type": "string"}, "label": {"type": "string"}}
    assert ex["error_label"] == "label must be one of: billing, bug, account, how_to, feature_request, outage"
    assert sorted(ex["label_values"]) == sorted(CATS)
    assert "pattern" in c["tool"]["diff"] and "enum" in c["tool"]["diff"]


def test_v2_rules_prompt_names_every_category_and_priority():
    prompts = json.loads((ROOT / "llms" / "data" / "prompts.json").read_text())["triage"]
    for version in ("v2_rules", "v3_examples"):
        text = prompts[version]
        for name in CATS + PRIOS:
            assert re.search(rf"\b{name}\b", text), f"{version} does not name {name}"
    # the rule lines of v3 are v2's, word for word; v3 only adds examples
    assert G.compare(ONTO)["definitions"]["v3_rule_lines_equal_v2"] is True


def test_generated_json_deterministic_and_check(tmp_path):
    out = tmp_path / "generated.json"
    a = G.write(out)
    first = out.read_text()
    G.write(out)
    assert out.read_text() == first and json.loads(first) == a
    assert G.main(["--check", "--out", str(out)]) == 0
    out.write_text(first.replace("outage", "outagE", 1))
    assert G.main(["--check", "--out", str(out)]) == 1
    assert G.main(["--check", "--out", str(tmp_path / "missing.json")]) == 1


def test_committed_generated_json_is_current():
    assert G.main(["--check"]) == 0


def test_module_values_reads_literals_and_never_runs_code(tmp_path):
    """Final review M5: other tracks' modules are read, never run: a call is skipped,
    a name defined earlier resolves, a literal reads as itself."""
    f = tmp_path / "m.py"
    marker = tmp_path / "ran"
    f.write_text(f"A = ['x', 'y']\nB = {{'enum': A, 'n': -1}}\nC = open({str(marker)!r}, 'w')\nD = A + ['z']\n",
                 encoding="utf-8")
    _, env = G._module_values(f)
    assert env["A"] == ["x", "y"] and env["B"] == {"enum": ["x", "y"], "n": -1}
    assert "C" not in env and not marker.exists()
    assert env["D"] == ["x", "y", "z"]
