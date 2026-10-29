"""Tests for catalog.py: the catalogue and lineage over a tiny fixture repo, then the live repo.

The fixture repo lives in a temp dir: one dataset under a contract, one declared
but missing (it must show as missing, never be skipped), and a git-ignored file
that must not count, because a fresh clone would not have it.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from history import needs_history

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import catalog as K  # noqa: E402
import contract as C  # noqa: E402

ROOT = HERE.parents[2]

ORDERS_CONTRACT = """\
info:
  id: orders.v1
  version: 1
  status: active
  owner: shop
  description: Orders, one per line.
schema:
  fields:
    - name: id
      type: string
      required: true
      meaning: the order's key
quality:
  row_count: {min: 1, max: 10}
  unique: [id]
  enum_coverage: []
servicing:
  freshness:
    max_age_days: 365
consumers:
  - name: shop-report
    version: 1
    reads: shop/orders/*.jsonl via report.py
"""

FIXTURE = [
    {"id": "orders", "path": "shop/orders/*.jsonl", "producer": "shop", "contract": "orders.v1",
     "description": "Orders, one per line."},
    {"id": "ghost", "path": "shop/ghost.json", "producer": "shop", "consumers": ["app-lessons"],
     "description": "Declared but never written."},
]


@pytest.fixture
def repo(tmp_path):
    """A two-dataset repo: orders (two files, one under a contract), ghost (missing)."""
    (tmp_path / "shop" / "orders").mkdir(parents=True)
    (tmp_path / "shop" / "orders" / "a.jsonl").write_text(
        '{"id": "o1", "total": 3.5, "paid": true, "lines": [1], "meta": {"x": 1}, "note": null}\n'
        '{"id": "o2", "total": 4, "paid": false, "lines": [], "meta": {}, "note": null}\n')
    (tmp_path / "shop" / "orders" / "b.jsonl").write_text('{"id": "o3", "total": 1, "extra": "y"}\n')
    (tmp_path / "data" / "contracts").mkdir(parents=True)
    (tmp_path / "data" / "contracts" / "orders.v1.yaml").write_text(ORDERS_CONTRACT)
    return tmp_path


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=root, check=True, capture_output=True)


# ── 1. The catalogue ────────────────────────────────────────────────────────
def test_catalog_records_size_rows_and_schema(repo):
    cat = K.catalog(repo, FIXTURE)
    orders = cat["orders"]
    assert orders["path"] == "shop/orders/*.jsonl"
    assert orders["files"] == 2
    assert orders["rows"] == 3
    a, b = (repo / "shop" / "orders" / n for n in ("a.jsonl", "b.jsonl"))
    assert orders["bytes"] == a.stat().st_size + b.stat().st_size
    assert orders["missing"] is False
    assert orders["producer"] == "shop"
    assert orders["contract"] == "orders.v1"


def test_schema_sketch_uses_the_first_file_and_the_six_types(repo):
    schema = K.catalog(repo, FIXTURE)["orders"]["schema"]
    # b.jsonl's "extra" is not in the first file, so it is not sketched
    assert schema == {"id": "string", "total": "number", "paid": "boolean",
                      "lines": "array", "meta": "object", "note": "string"}
    assert set(schema.values()) <= set(C.FIELD_TYPES)


def test_schema_sketch_reads_at_most_50_rows(tmp_path):
    lines = [json.dumps({"id": i}) for i in range(50)] + [json.dumps({"id": 50, "late": True})]
    (tmp_path / "d.jsonl").write_text("\n".join(lines) + "\n")
    ds = [{"id": "d", "path": "d.jsonl", "producer": "x", "consumers": [], "description": "d"}]
    entry = K.catalog(tmp_path, ds)["d"]
    assert entry["rows"] == 51
    assert entry["schema"] == {"id": "integer"}


def test_mixed_types_sketch_as_the_most_common(tmp_path):
    rows = [{"a": 1, "b": "x"}, {"a": 2.5, "b": 1}, {"a": 3, "b": "y"}]
    (tmp_path / "d.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    ds = [{"id": "d", "path": "d.jsonl", "producer": "x", "consumers": [], "description": "d"}]
    assert K.catalog(tmp_path, ds)["d"]["schema"] == {"a": "number", "b": "string"}


def test_missing_dataset_is_recorded_not_skipped(repo):
    ghost = K.catalog(repo, FIXTURE)["ghost"]
    assert ghost == {"path": "shop/ghost.json", "files": 0, "bytes": 0, "rows": 0, "schema": {},
                     "producer": "shop", "consumers": ["app"], "contract": None,
                     "last_commit": None, "missing": True, "description": "Declared but never written."}


def test_consumers_come_from_the_contract_as_tracks(repo):
    assert K.catalog(repo, FIXTURE)["orders"]["consumers"] == ["shop"]


def test_last_commit_none_without_git(repo):
    assert K.catalog(repo, FIXTURE)["orders"]["last_commit"] is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_last_commit_and_ignored_files_in_a_git_repo(repo):
    (repo / ".gitignore").write_text("b.jsonl\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "one")
    sha = subprocess.run(["git", "log", "-1", "--format=%H"], cwd=repo, capture_output=True, text=True).stdout.strip()
    orders = K.catalog(repo, FIXTURE)["orders"]
    assert orders["last_commit"] == sha and len(sha) == 40  # ruling D18: the full sha, whatever the repo's size
    assert orders["files"] == 1 and orders["rows"] == 2  # the ignored b.jsonl is not catalogued


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_last_commit_none_in_a_shallow_clone(repo, tmp_path_factory):
    """Ruling D17: a shallow clone would date every dataset to its one commit."""
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "one")
    (repo / "shop" / "ghost.json").write_text("{}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "two")
    shallow = tmp_path_factory.mktemp("clone") / "shallow"
    git(shallow.parent, "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow))
    assert K.catalog(shallow, FIXTURE)["orders"]["last_commit"] is None
    assert K.catalog(repo, FIXTURE)["orders"]["last_commit"] is not None


def test_check_without_history_compares_all_but_last_commit(monkeypatch, capsys):
    monkeypatch.setattr(K.Q, "has_history", lambda where: False)
    assert K.check() == 0
    assert capsys.readouterr().out.splitlines() == ["no git history: commit dates not compared",
                                                    "catalog.json and lineage.json match"]


# ── 2. Lineage ──────────────────────────────────────────────────────────────
def test_lineage_edges_from_producers_and_consumers(repo):
    cat = K.catalog(repo, FIXTURE)
    contracts = [C.load_contract(repo / "data" / "contracts" / "orders.v1.yaml")]
    lin = K.lineage(cat, contracts, FIXTURE)
    assert lin["edges"] == [
        {"from": "ghost", "to": "app-lessons", "contract": None, "version": None},
        {"from": "orders", "to": "shop-report", "contract": "orders.v1", "version": "1"},
        {"from": "shop", "to": "ghost", "contract": None, "version": None},
        {"from": "shop", "to": "orders", "contract": "orders.v1", "version": "1"},
    ]
    nodes = {n["id"]: n for n in lin["nodes"]}
    assert nodes["shop"]["kind"] == "track"
    assert nodes["orders"] == {"id": "orders", "kind": "dataset", "missing": False}
    assert nodes["ghost"]["missing"] is True
    assert nodes["shop-report"] == {"id": "shop-report", "kind": "consumer", "track": "shop"}
    assert nodes["app-lessons"] == {"id": "app-lessons", "kind": "consumer", "track": "app"}


def test_consumer_edge_carries_the_declared_version(repo):
    """Ruling D2: the version on a consumer edge is the one the consumer declares."""
    cat = K.catalog(repo, FIXTURE)
    contract = C.load_contract(repo / "data" / "contracts" / "orders.v1.yaml")
    contract["consumers"][0]["version"] = "2"
    edge = next(e for e in K.lineage(cat, [contract], FIXTURE)["edges"] if e["to"] == "shop-report")
    assert edge["version"] == "2"


# ── 3. Declared versions exist ──────────────────────────────────────────────
def test_missing_versions_names_the_version(repo):
    path = repo / "data" / "contracts" / "orders.v1.yaml"
    path.write_text(ORDERS_CONTRACT.replace("    version: 1\n    reads", "    version: 3\n    reads"))
    assert K.missing_versions(repo / "data" / "contracts") == [
        "orders.v1.yaml: consumer shop-report declares version 3, but orders.v3.yaml does not exist"]


def test_consumer_declared_version_exists():
    """Every consumers[].version in every contract has its data/contracts/<id>.v<n>.yaml."""
    missing = K.missing_versions(ROOT / "data" / "contracts")
    assert not missing, "\n".join(missing)


# ── 4. The live repo ────────────────────────────────────────────────────────
def test_live_dataset_list_is_fixed():
    assert [d["id"] for d in K.DATASETS] == [
        "tickets", "tickets_v2", "complaints", "category_map", "reply_labels", "model_responses", "ml_reports",
        "ml_registry", "mcp_recordings", "mcp_derived", "agents_traces", "agents_derived"]


def test_live_catalog_has_nothing_missing():
    cat = K.catalog(ROOT)
    assert [i for i, e in cat.items() if e["missing"]] == []
    assert cat["tickets"]["rows"] == 60 and cat["tickets"]["contract"] == "tickets.v1"
    assert cat["complaints"]["rows"] == 3600
    assert cat["tickets"]["consumers"] == ["agents", "mcp"]  # the agents eval has not moved (ruling D19)
    assert cat["tickets_v2"]["consumers"] == ["agents"]
    assert any("triage__Qwen" in f for f in K.files(ROOT, K.by_id("model_responses")["path"]))


def test_live_lineage_carries_the_contract_versions():
    lin = json.loads((K.LINEAGE).read_text(encoding="utf-8"))
    governed = {(e["from"], e["to"]): (e["contract"], e["version"]) for e in lin["edges"]}
    assert governed[("tickets", "mcp-read-server")] == ("tickets.v1", "1")
    assert governed[("tickets", "agents-eval")] == ("tickets.v1", "1")  # the agents track's own loader
    assert governed[("tickets_v2", "agents-eval-v2")] == ("tickets.v2", "2")  # the capstone's shim
    assert governed[("complaints", "mcp-read-server")] == ("complaints.v1", "1")
    assert governed[("agents_traces", "agents-eval")] == ("agent-traces.v1", "1")


@needs_history
def test_live_last_commits_are_full_shas():
    cat = json.loads(K.CATALOG.read_text(encoding="utf-8"))
    assert all(len(e["last_commit"]) == 40 and int(e["last_commit"], 16) >= 0 for e in cat.values())


def test_derived_json_is_current_and_small(capsys):
    assert K.check() == 0, capsys.readouterr().out
    assert K.CATALOG.stat().st_size + K.LINEAGE.stat().st_size < 60_000
