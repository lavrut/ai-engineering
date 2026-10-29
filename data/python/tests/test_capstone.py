"""The capstone: a breaking change to the tickets dataset, carried through to a gate.

tickets.v2 renames `priority` to `urgency` and adds the category `security`. A
rename is a breaking change, so it is a new major: the v1 rows fail v2, the
migration script writes a v2 file that passes, and a shim (agents-eval-v2) shows
what the agents eval's move to v2 looks like. The agents track's own loader and the
MCP read server have not moved: both stay on the deprecated v1 until its sunset,
and the gate says whether every consumer is green on the version it declares (ruling D19).
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
    assert [(c["name"], c["version"]) for c in t["consumers"]] == [("mcp-read-server", "1"), ("agents-eval", "1")]
    assert t["consumers"][1]["reads"] == "llms/data/tickets.jsonl via agents/desk/python/desk_core.py"


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
    assert [(c["name"], c["version"]) for c in t["consumers"]] == [("agents-eval-v2", "2")]
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


def test_lineage_shows_both_files_and_both_versions():
    """Rulings D13, D19: the migrated file is a dataset of its own; each reader hangs off the file it reads."""
    lin = json.loads(K.LINEAGE.read_text(encoding="utf-8"))
    governed = {(e["from"], e["to"]): (e["contract"], e["version"]) for e in lin["edges"]}
    assert governed[("llms", "tickets")] == ("tickets.v1", "1")
    assert governed[("tickets", "mcp-read-server")] == ("tickets.v1", "1")
    assert governed[("tickets", "tickets_v2")] == ("tickets.v1", "1")  # the migration reads the v1 file
    assert governed[("data", "tickets_v2")] == ("tickets.v2", "2")
    assert governed[("tickets", "agents-eval")] == ("tickets.v1", "1")  # the agents track has not moved
    assert governed[("tickets_v2", "agents-eval-v2")] == ("tickets.v2", "2")  # the shim that shows the move
    assert ("tickets_v2", "agents-eval") not in governed and ("tickets_v2", "mcp-read-server") not in governed
    assert len(lin["edges"]) == 32
    cat = json.loads(K.CATALOG.read_text(encoding="utf-8"))
    v2 = cat["tickets_v2"]
    assert (v2["path"], v2["producer"], v2["contract"], v2["consumers"], v2["rows"]) == (
        "data/data/tickets.v2.jsonl", "data", "tickets.v2", ["agents"], 60)
    assert cat["tickets"]["consumers"] == ["agents", "mcp"]


def test_contracts_name_the_dataset_they_govern():
    want = {"tickets.v1": "llms/data/tickets.jsonl", "tickets.v2": "data/data/tickets.v2.jsonl",
            "complaints.v1": "ml/data/complaints.jsonl", "agent-traces.v1": "agents/data/traces/*/*.json"}
    got = {C.load_contract(p)["info"]["id"]: C.load_contract(p)["info"]["dataset"] for p in CONTRACTS.glob("*.yaml")}
    assert got == want


def test_info_dataset_must_be_text():
    doc = C.load_contract(CONTRACTS / "tickets.v2.yaml")
    doc["info"]["dataset"] = 3
    with pytest.raises(C.ContractError, match=r"^info\.dataset must be a non-empty string$"):
        C.check_contract(doc)


# ── 5. The gate (ruling D12) ────────────────────────────────────────────────
def pinned(tmp_path, as_of: str, edit=None) -> Path:
    """A verdicts.json copy with another as_of; edit(doc) may change anything else in it."""
    doc = json.loads(Q.VERDICTS.read_text(encoding="utf-8"))
    doc["as_of"] = as_of
    if edit:
        edit(doc)
    out = tmp_path / "verdicts.json"
    out.write_text(json.dumps(doc), encoding="utf-8")
    return out


def copy_contracts(tmp_path, name, old, new) -> Path:
    contracts = tmp_path / "contracts"
    shutil.copytree(CONTRACTS, contracts)
    path = contracts / name
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")
    return contracts


def test_gate_green_with_both_versions():
    g = G.gate()
    assert g["ok"] is True, g["reason"]
    rows = {(c["name"], c["declared"]): c for c in g["consumers"]}
    assert rows[("mcp-read-server", "tickets.v1")] == {
        "name": "mcp-read-server", "declared": "tickets.v1", "contract_exists": True, "verdicts_ok": True,
        "sunset": "2027-01-31", "warning": False}
    assert rows[("agents-eval", "tickets.v1")] == {
        "name": "agents-eval", "declared": "tickets.v1", "contract_exists": True, "verdicts_ok": True,
        "sunset": "2027-01-31", "warning": False}
    assert rows[("agents-eval-v2", "tickets.v2")] == {
        "name": "agents-eval-v2", "declared": "tickets.v2", "contract_exists": True, "verdicts_ok": True,
        "sunset": None, "warning": False}
    assert [(c["name"], c["declared"]) for c in g["consumers"]] == [
        ("agents-eval", "agent-traces.v1"), ("mcp-read-server", "complaints.v1"), ("mcp-read-server", "tickets.v1"),
        ("agents-eval", "tickets.v1"), ("agents-eval-v2", "tickets.v2")]
    assert g["reason"] == "every consumer is green on the version it declares (5 of 5)"


def test_gate_ignores_a_hand_edited_verdicts_json(tmp_path):
    def break_all(doc):
        for entry in doc["datasets"].values():
            entry["ok"] = False
            entry["expect"] = "fail"
    assert G.gate(verdicts=pinned(tmp_path, "2026-10-28", break_all)) == G.gate()


def test_gate_red_when_the_governed_file_fails(tmp_path):
    contracts = copy_contracts(tmp_path, "tickets.v2.yaml", "  dataset: data/data/tickets.v2.jsonl",
                               "  dataset: llms/data/tickets.jsonl")
    g = G.gate(contracts)
    assert g["ok"] is False
    row = next(c for c in g["consumers"] if c["declared"] == "tickets.v2")
    assert row["contract_exists"] is True and row["verdicts_ok"] is False
    assert g["reason"] == ("agents-eval-v2 declares tickets.v2, which fails on llms/data/tickets.jsonl: "
                           "enum_coverage urgency: missing values: \"low\", \"normal\", \"high\", \"urgent\" (+1 more failed)")


def test_gate_red_when_the_governed_file_is_missing(tmp_path):
    contracts = copy_contracts(tmp_path, "tickets.v2.yaml", "  dataset: data/data/tickets.v2.jsonl",
                               "  dataset: data/data/nowhere.jsonl")
    g = G.gate(contracts)
    assert g["ok"] is False
    assert g["reason"] == "agents-eval-v2 declares tickets.v2, but data/data/nowhere.jsonl has no files"


def test_gate_red_when_a_consumer_declares_a_missing_version(tmp_path):
    contracts = copy_contracts(tmp_path, "tickets.v2.yaml", "  - name: agents-eval-v2\n    version: 2",
                               "  - name: agents-eval-v2\n    version: 3")
    g = G.gate(contracts)
    assert g["ok"] is False
    row = next(c for c in g["consumers"] if c["declared"] == "tickets.v3")
    assert row == {"name": "agents-eval-v2", "declared": "tickets.v3", "contract_exists": False, "verdicts_ok": False,
                   "sunset": None, "warning": False}
    assert g["reason"] == "agents-eval-v2 declares tickets.v3, which has no contract file"


def test_gate_warns_past_a_sunset_but_stays_green(tmp_path):
    g = G.gate(verdicts=pinned(tmp_path, "2027-02-01"))
    assert g["ok"] is True, g["reason"]
    for row in (c for c in g["consumers"] if c["declared"] == "tickets.v1"):
        assert row["warning"] is True and row["verdicts_ok"] is True and row["sunset"] == "2027-01-31"
    assert [c["name"] for c in g["consumers"] if c["warning"]] == ["mcp-read-server", "agents-eval"]
    assert g["reason"] == ("every consumer is green on the version it declares (5 of 5); "
                           "warning: mcp-read-server is on tickets.v1, past its sunset 2027-01-31; "
                           "warning: agents-eval is on tickets.v1, past its sunset 2027-01-31")
    on_the_day = G.gate(verdicts=pinned(tmp_path, "2027-01-31"))
    assert not any(c["warning"] for c in on_the_day["consumers"])


def test_gate_json_check(capsys):
    assert G.main(["--check"]) == 0
    assert capsys.readouterr().out.strip() == "gate.json matches"
    assert json.loads(G.GATE.read_text(encoding="utf-8")) == G.gate()
