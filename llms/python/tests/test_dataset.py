import json
from collections import Counter

import pytest

import triage_lab as lab


@pytest.fixture(scope="module")
def tickets():
    return lab.load_tickets()


def test_real_tickets_are_well_formed(tickets):
    assert len(tickets) == 60
    assert len({t["id"] for t in tickets}) == 60
    for t in tickets:
        assert t["category"] in lab.CATEGORIES
        assert t["priority"] in lab.PRIORITIES
        assert t["split"] in {"dev", "test"}
        assert t["text"].strip()
    counts = Counter(t["category"] for t in tickets)
    assert all(counts[c] >= 6 for c in lab.CATEGORIES)
    assert sum(1 for t in tickets if t["tags"]) >= 15
    tags = Counter(g for t in tickets for g in t["tags"])
    assert tags["non_english"] >= 2 and tags["injection"] >= 2


def test_split_counts(tickets):
    dev, test = lab.split(tickets, "dev"), lab.split(tickets, "test")
    assert len(dev) + len(test) == len(tickets)
    assert len(test) >= 18


def test_malformed_line_names_file_and_line(tmp_path):
    p = tmp_path / "broken.jsonl"
    p.write_text('{"id": "a"}\n{bad\n', encoding="utf-8")
    with pytest.raises(ValueError) as e:
        lab.load_jsonl(p)
    assert "broken.jsonl" in str(e.value) and ":2:" in str(e.value)


def test_fixture_root_loads():
    fx = lab.DATA_DIR / "fixtures"
    assert len(lab.load_tickets(fx / "tickets.jsonl")) == 8
    assert set(lab.load_prompts(fx / "prompts.json")["triage"]) == {
        "v1_basic", "v2_rules", "v3_examples"}


def test_prompts_have_three_versions_and_rubric():
    prompts = lab.load_prompts()
    assert set(prompts["triage"]) == {"v1_basic", "v2_rules", "v3_examples"}
    assert [r["id"] for r in prompts["rubric"]] == [
        "addresses_issue", "tone", "no_false_promises"]
    assert "{rubric}" in prompts["judge"]


def test_examples_do_not_leak_test_tickets(tickets):
    v3 = lab.load_prompts()["triage"]["v3_examples"]
    for t in lab.split(tickets, "test"):
        assert t["text"] not in v3, t["id"]
