"""The three v1 contracts hold on the live datasets they describe.

The datasets belong to other tracks and are read in place: a red verdict here
means a producer broke its promise (or the contract is wrong), never that this
test should be loosened in passing.
"""
import re
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import contract as C  # noqa: E402
import quality as Q  # noqa: E402

ROOT = HERE.parents[2]
CONTRACTS = ROOT / "data" / "contracts"
LIVE = [
    ("tickets.v1.yaml", "llms/data/tickets.jsonl"),
    ("complaints.v1.yaml", "ml/data/complaints.jsonl"),
    ("agent-traces.v1.yaml", "agents/data/traces/desk-sdk/sdk-single-t001.json"),
]
AS_OF = date(2026, 10, 28)  # pinned (ruling D10): these tests never read the real clock
TRACE_FILES = sorted((ROOT / "agents" / "data" / "traces").glob("*/*.json"))


def failed(verdicts):
    return [x for x in verdicts if not x["ok"]]


@pytest.mark.parametrize("name,dataset", LIVE)
def test_three_contracts_hold(name, dataset):
    verdicts = Q.run(CONTRACTS / name, ROOT / dataset, as_of=AS_OF)
    assert verdicts and not failed(verdicts), failed(verdicts)


def test_every_trace_file_holds():
    assert len(TRACE_FILES) > 100  # six sets of recorded runs
    for path in TRACE_FILES:
        bad = failed(Q.run(CONTRACTS / "agent-traces.v1.yaml", path, as_of=AS_OF))
        assert not bad, (path.name, bad)


def test_row_counts_are_the_live_ones():
    rc = {name: next(x for x in Q.run(CONTRACTS / name, ROOT / ds, as_of=AS_OF) if x["rule"] == "row_count")["detail"]
          for name, ds in LIVE}
    assert rc == {"tickets.v1.yaml": "60 rows (min 60, max 60)",
                  "complaints.v1.yaml": "3600 rows (min 3600, max 3600)",
                  "agent-traces.v1.yaml": "1 row (min 1, max 1)"}


def test_contract_values():
    t = C.load_contract(CONTRACTS / "tickets.v1.yaml")
    fields = {f["name"]: f for f in t["schema"]["fields"]}
    assert list(fields) == ["id", "text", "category", "priority", "tags", "split"]
    assert fields["id"]["pattern"] == r"^t\d{3}$"
    assert len(fields["category"]["enum"]) == 6 and len(fields["priority"]["enum"]) == 4
    assert fields["split"]["enum"] == ["dev", "test"]
    assert t["quality"] == {"row_count": {"min": 60, "max": 60}, "unique": ["id"],
                            "enum_coverage": ["category", "priority"]}
    assert t["info"]["owner"] == "llms"
    assert [(c["name"], c["version"]) for c in t["consumers"]] == [("mcp-read-server", "1")]  # agents-eval moved to v2
    assert t["info"]["status"] == "deprecated" and t["info"]["sunset"] == "2027-01-31"
    c = C.load_contract(CONTRACTS / "complaints.v1.yaml")
    assert c["info"]["owner"] == "ml" and c["quality"]["row_count"] == {"min": 3600, "max": 3600}
    assert [(x["name"], x["version"]) for x in c["consumers"]] == [("mcp-read-server", "1")]
    a = C.load_contract(CONTRACTS / "agent-traces.v1.yaml")
    assert a["info"]["owner"] == "agents" and a["quality"]["row_count"] == {"min": 1, "max": 1}
    assert {"engine", "desk", "ticket", "trace", "proxy_calls", "errors"} <= {f["name"] for f in a["schema"]["fields"]}
    assert [(x["name"], x["version"]) for x in a["consumers"]] == [("agents-eval", "1")]
    for doc in (t, c, a):
        assert doc["servicing"]["freshness"]["max_age_days"] == 365
    assert c["info"]["status"] == a["info"]["status"] == "active"


@pytest.mark.parametrize("name", [n for n, _ in LIVE])
def test_contract_sections_are_numbered(name):
    """Lessons quote a contract by `# ── N. Section`, so each top-level key has one, in order."""
    text = (CONTRACTS / name).read_text(encoding="utf-8")
    heads = re.findall(r"^# ── (\d)\. (\w+) ─+$", text, re.M)
    assert heads == [(str(i), s) for i, s in enumerate(C.SECTIONS, 1)]
    for _, s in heads:
        assert re.search(rf"^# ── \d\. {s} ─+\n{s}:", text, re.M), s


def test_exports_are_current(capsys):
    assert C.export_check() == 0
    assert "exports: 4 current, 0 stale" in capsys.readouterr().out
