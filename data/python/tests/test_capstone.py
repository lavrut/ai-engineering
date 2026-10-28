"""The capstone: a breaking change to the tickets dataset, carried through to a gate.

tickets.v2 renames `priority` to `urgency` and adds the category `security`. A
rename is a breaking change, so it is a new major: the v1 rows fail v2, the
migration script writes a v2 file that passes, the agents eval moves to v2
through a shim, the MCP read server stays on the deprecated v1 until its sunset,
and the gate says whether every consumer is green on the version it declares.
"""
import importlib
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import catalog as K  # noqa: E402
import contract as C  # noqa: E402
import gate as G  # noqa: E402
import migrate_tickets as M  # noqa: E402
import quality as Q  # noqa: E402
from consumers import agents_eval_v2 as S  # noqa: E402

ROOT = HERE.parents[2]
CONTRACTS = ROOT / "data" / "contracts"
V1_ROWS = ROOT / "llms" / "data" / "tickets.jsonl"
V2_ROWS = ROOT / "data" / "data" / "tickets.v2.jsonl"


def contract(version: int) -> dict:
    return C.load_contract(CONTRACTS / f"tickets.v{version}.yaml")


def verdict(verdicts, rule, field):
    return next(x for x in verdicts if x["rule"] == rule and x["field"] == field)


def failed(verdicts):
    return [x for x in verdicts if not x["ok"]]


# ── 1. The break and the migration ──────────────────────────────────────────
def test_v1_rows_fail_v2_contract():
    out = C.validate(contract(2), C.read_rows(V1_ROWS))
    assert verdict(out, "required", "urgency") == {
        "rule": "required", "field": "urgency", "ok": False, "detail": "row 1: missing (+59 more)"}
    assert verdict(out, "extra_field", "priority")["detail"] == "not in schema (60 rows)"


def test_migrated_rows_pass_v2():
    out = C.validate(contract(2), C.read_rows(V2_ROWS))
    assert out and not failed(out), failed(out)
    assert verdict(out, "row_count", None)["detail"] == "60 rows (min 60, max 60)"


def test_v1_still_passes_v1():
    out = C.validate(contract(1), C.read_rows(V1_ROWS))
    assert out and not failed(out), failed(out)


def test_migrate_renames_one_field_in_place():
    row = {"id": "t001", "text": "x", "category": "bug", "priority": "high", "tags": [], "split": "dev"}
    got = M.migrate(row)
    assert list(got) == ["id", "text", "category", "urgency", "tags", "split"]
    assert got["urgency"] == "high" and "priority" not in got
    assert row["priority"] == "high"  # the input row is not changed


def test_migrated_file_is_the_v1_file_renamed():
    v1, v2 = C.read_rows(V1_ROWS), C.read_rows(V2_ROWS)
    assert [r["id"] for r in v2] == [r["id"] for r in v1]
    for a, b in zip(v1, v2):
        assert {k: v for k, v in a.items() if k != "priority"} == {k: v for k, v in b.items() if k != "urgency"}
        assert a["priority"] == b["urgency"]


def test_migrate_check(capsys, tmp_path):
    assert M.main(["--check"]) == 0
    assert capsys.readouterr().out.strip() == "tickets.v2.jsonl matches"
    stale = tmp_path / "tickets.v2.jsonl"
    stale.write_text(V2_ROWS.read_text(encoding="utf-8").replace('"urgent"', '"high"', 1), encoding="utf-8")
    assert M.check(stale) == 1
    assert "stale" in capsys.readouterr().out


# ── 2. The two contracts ────────────────────────────────────────────────────
def test_v1_is_deprecated_with_a_sunset():
    t = contract(1)
    assert t["info"]["status"] == "deprecated" and t["info"]["sunset"] == "2027-01-31"
    assert [(c["name"], c["version"]) for c in t["consumers"]] == [("mcp-read-server", "1")]


def test_v2_contract_values():
    t = contract(2)
    assert t["info"]["id"] == "tickets.v2" and t["info"]["version"] == "2" and t["info"]["status"] == "active"
    note = t["info"]["migration"]
    assert "priority" in note and "urgency" in note and "security" in note
    fields = {f["name"]: f for f in t["schema"]["fields"]}
    assert list(fields) == ["id", "text", "category", "urgency", "tags", "split"]
    assert fields["urgency"]["enum"] == contract(1)["schema"]["fields"][3]["enum"]
    assert fields["category"]["enum"] == contract(1)["schema"]["fields"][2]["enum"] + ["security"]
    # no ticket is labelled security yet, so category's coverage is not promised in v2
    assert t["quality"] == {"row_count": {"min": 60, "max": 60}, "unique": ["id"], "enum_coverage": ["urgency"]}
    assert [(c["name"], c["version"]) for c in t["consumers"]] == [("agents-eval", "2")]
    assert t["consumers"][0]["reads"].split(" via ")[0] == "data/data/tickets.v2.jsonl"


def test_v2_sections_are_numbered():
    import re
    text = (CONTRACTS / "tickets.v2.yaml").read_text(encoding="utf-8")
    heads = re.findall(r"^# ── (\d)\. (\w+) ─+$", text, re.M)
    assert heads == [(str(i), s) for i, s in enumerate(C.SECTIONS, 1)]


@pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")
@pytest.mark.parametrize("rows", [V1_ROWS, V2_ROWS], ids=["before", "after"])
def test_go_twin_agrees_on_v2(rows):
    py = subprocess.run([sys.executable, str(HERE.parent / "contract.py"), str(CONTRACTS / "tickets.v2.yaml"), str(rows)],
                        capture_output=True, cwd=ROOT)
    go = subprocess.run(["go", "run", "./validate", str(CONTRACTS / "tickets.v2.contract.json"), str(rows)],
                        capture_output=True, cwd=ROOT / "data" / "go")
    assert go.stdout == py.stdout
    assert (py.returncode == 0) == (rows == V2_ROWS)


# ── 3. The shim ─────────────────────────────────────────────────────────────
@pytest.fixture
def desk_core(monkeypatch):
    """The real agents loader, imported the way test_consumers does (ruling D11).

    desk_core imports mcp/python/agent.py at module level for the live desk runs, which
    needs the MCP SDK; the ticket loader never touches it, so a stub stands in.
    """
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    before = set(sys.modules)
    sys.path.insert(0, str(ROOT / "agents" / "desk" / "python"))
    yield importlib.import_module("desk_core")
    for name in set(sys.modules) - before:
        del sys.modules[name]


def test_shim_matches_v1_loader(desk_core):
    v1 = desk_core.load_eval_tickets()
    v2 = S.load_eval_tickets_v2()
    assert [t["id"] for t in v2] == [t["id"] for t in v1] and len(v2) == 13
    by_id = {r["id"]: r for r in C.read_rows(V2_ROWS)}
    for old, new in zip(v1, v2):
        assert {k: v for k, v in new.items() if k != "priority"} == {k: v for k, v in old.items() if k != "priority"}
        if new["id"] != S.INJECTION_ID:
            assert new["priority"] == by_id[new["id"]]["urgency"]  # the shim's one job


def test_shim_reads_the_path_v2_governs():
    entry = contract(2)["consumers"][0]
    assert S.SOURCE == ROOT / entry["reads"].split(" via ")[0]
    assert entry["reads"].endswith("via data/python/consumers/agents_eval_v2.py")


# ── 4. Verdicts and lineage, before and after ───────────────────────────────
def test_verdicts_record_before_and_after():
    ds = json.loads(Q.VERDICTS.read_text(encoding="utf-8"))["datasets"]
    before, after = ds["tickets-v2-before"], ds["tickets-v2"]
    assert before["contract"] == after["contract"] == "tickets.v2"
    assert before["path"] == "llms/data/tickets.jsonl" and after["path"] == "data/data/tickets.v2.jsonl"
    assert before["expect"] == "fail" and before["ok"] is False
    assert [(x["rule"], x["field"]) for x in before["verdicts"] if not x["ok"]] == [
        ("enum_coverage", "urgency"), ("required", "urgency")]
    assert after["ok"] is True and "expect" not in after


def test_lineage_edges_carry_each_consumers_version():
    lin = json.loads(K.LINEAGE.read_text(encoding="utf-8"))
    governed = {(e["from"], e["to"]): (e["contract"], e["version"]) for e in lin["edges"]}
    assert governed[("tickets", "mcp-read-server")] == ("tickets.v1", "1")
    assert governed[("tickets", "agents-eval")] == ("tickets.v2", "2")
    assert governed[("llms", "tickets")] == ("tickets.v1", "1")


# ── 5. The gate ─────────────────────────────────────────────────────────────
def test_gate_green_with_both_versions():
    g = G.gate()
    assert g["ok"] is True, g["reason"]
    rows = {(c["name"], c["declared"]): c for c in g["consumers"]}
    for key in [("mcp-read-server", "tickets.v1"), ("agents-eval", "tickets.v2")]:
        assert rows[key] == {"name": key[0], "declared": key[1], "contract_exists": True, "verdicts_ok": True}
    assert g["reason"] == "every consumer is green on the version it declares (4 of 4)"


def test_gate_red_when_a_consumer_declares_a_missing_version(tmp_path):
    contracts = tmp_path / "contracts"
    shutil.copytree(CONTRACTS, contracts)
    v2 = contracts / "tickets.v2.yaml"
    v2.write_text(v2.read_text(encoding="utf-8").replace("  - name: agents-eval\n    version: 2",
                                                       "  - name: agents-eval\n    version: 3"), encoding="utf-8")
    g = G.gate(contracts)
    assert g["ok"] is False
    row = next(c for c in g["consumers"] if c["declared"] == "tickets.v3")
    assert row == {"name": "agents-eval", "declared": "tickets.v3", "contract_exists": False, "verdicts_ok": False}
    assert g["reason"] == "agents-eval declares tickets.v3, which has no contract file"


def test_gate_red_when_a_declared_version_fails(tmp_path):
    doc = json.loads(Q.VERDICTS.read_text(encoding="utf-8"))
    doc["datasets"]["tickets-v2"]["ok"] = False
    bad = tmp_path / "verdicts.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    g = G.gate(verdicts=bad)
    assert g["ok"] is False
    assert g["reason"] == "agents-eval declares tickets.v2, whose verdicts fail"


def test_gate_json_check(capsys):
    assert G.main(["--check"]) == 0
    assert capsys.readouterr().out.strip() == "gate.json matches"
    assert json.loads(G.GATE.read_text(encoding="utf-8")) == G.gate()
