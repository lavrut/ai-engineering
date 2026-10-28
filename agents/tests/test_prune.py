"""Tests for agents/prune.py (ruling R73). No API calls, ever.

Every trace and store file here is a labelled test fixture written into tmp_path (never agents/data): one ticket
per set, so a set is complete with a handful of runs. The store files hold "{}": prune reads only their names.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "agents" / "evals"))

import derive  # noqa: E402
import prune  # noqa: E402
import run_evals  # noqa: E402

FIXTURE_NOTE = "test fixture (agents/tests/test_prune.py), not a recording"


def key(n: int) -> str:
    return f"{n:064x}"


def write_trace(traces: Path, set_name: str, engine: str, desk: str, ticket: str, keys: list[str], reply: str) -> None:
    doc = {"set": set_name, "engine": engine, "desk": desk, "ticket": ticket, "fixture": FIXTURE_NOTE,
           "trace": [], "output": {"reply": reply, "tool_calls": []},
           "proxy_calls": [{"key": k, "cost_usd": 0.001, "ms": 10} for k in keys]}
    path = traces / set_name / f"{engine}-{desk.removeprefix('pattern:')}-{ticket}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def store_file(store: Path, set_name: str, k: str) -> Path:
    (store / set_name).mkdir(parents=True, exist_ok=True)
    p = store / set_name / f"{k}.json"
    p.write_text("{}", encoding="utf-8")
    return p


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Every desk set complete on one ticket (t001), the patterns sets complete on t001, patterns-go incomplete.
    Each complete set's store holds its used keys plus one orphan; the judge store holds the used judge requests
    plus one orphan."""
    monkeypatch.setattr(derive, "DESK_TICKETS", ("t001",))
    monkeypatch.setattr(derive, "PATTERN_TICKETS", ("t001",))
    for e in run_evals.ENGINES:
        monkeypatch.setitem(run_evals.DESK_SETS, e, derive.run.SetSpec(f"desk-{e}", e, run_evals.DESKS, ("t001",)))
    traces, store = tmp_path / "traces", tmp_path / "recordings"
    n = 0
    files: dict[str, dict[str, list[Path]]] = {}
    for name, spec in derive.run.SETS.items():
        files[name] = {"used": [], "orphan": []}
        if name == "patterns-go":                       # incomplete: one pattern trace only
            write_trace(traces, name, spec.engine, spec.desks[0], "t001", [key(900)], "x")
            files[name]["kept"] = [store_file(store, name, key(900)), store_file(store, name, key(901))]
            continue
        for desk in spec.desks:
            n += 1
            write_trace(traces, name, spec.engine, desk, "t001", [key(n), key(n)], f"reply {n}")
            files[name]["used"].append(store_file(store, name, key(n)))
        files[name]["orphan"].append(store_file(store, name, key(10_000 + n)))
    judge = {"used": [store_file(store, "judge", k) for k in sorted(run_evals.judge_keys(traces))],
             "orphan": [store_file(store, "judge", key(99_999))]}
    files["judge"] = judge
    return {"traces": traces, "store": store, "files": files}


def test_plan_names_only_unreferenced_files(world):
    plan = prune.plan(world["traces"], world["store"])
    for name, f in world["files"].items():
        if name == "patterns-go":
            continue
        assert sorted(plan[name]["delete"]) == sorted(f["orphan"]), name
        assert plan[name]["keep"] == len(f["used"]), name
    assert len(world["files"]["judge"]["used"]) == 8      # 4 engines x 2 desks, one distinct reply each


def test_an_incomplete_set_is_never_pruned(world):
    plan = prune.plan(world["traces"], world["store"])
    assert plan["patterns-go"]["delete"] == []
    assert "incomplete" in plan["patterns-go"]["skip"]
    for p in world["files"]["patterns-go"]["kept"]:
        assert p.exists()


def test_the_judge_set_waits_for_every_desk_set(world):
    (world["traces"] / "desk-sdk" / "sdk-supervisor-t001.json").unlink()
    plan = prune.plan(world["traces"], world["store"])
    assert plan["judge"]["delete"] == [] and "desk-sdk" in plan["judge"]["skip"]


def test_dry_run_prints_and_deletes_nothing(world, capsys):
    assert prune.main([], traces_dir=world["traces"], store_dir=world["store"]) == 0
    out = capsys.readouterr().out
    for name, f in world["files"].items():
        for p in f["orphan"]:
            assert p.exists() and p.name in out
    assert "dry run: nothing deleted; delete with: python agents/prune.py --delete" in out


def test_delete_removes_the_orphans_and_keeps_every_used_file(world, capsys):
    assert prune.main(["--delete"], traces_dir=world["traces"], store_dir=world["store"]) == 0
    for name, f in world["files"].items():
        assert not any(p.exists() for p in f["orphan"]), name
        assert all(p.exists() for p in f["used"] + f.get("kept", [])), name
    assert "deleted 6 files" in capsys.readouterr().out    # one orphan in each of the 5 complete sets and the judge
    assert prune.main([], traces_dir=world["traces"], store_dir=world["store"]) == 0
    assert "0 to delete" in capsys.readouterr().out


def test_sets_json_reports_judge_orphans(world):
    doc = derive.build_sets(world["traces"], world["store"])
    assert doc["judge"] == {"recordings": 8, "missing": 0, "orphans": 1, "record": "python agents/evals/run_evals.py --record-judge"}
    assert doc["sets"]["desk-hand"]["orphans"] == 1
    prune.main(["--delete"], traces_dir=world["traces"], store_dir=world["store"])
    doc = derive.build_sets(world["traces"], world["store"])
    assert doc["judge"]["orphans"] == 0 and doc["sets"]["desk-hand"]["orphans"] == 0
    assert doc["sets"]["patterns-go"]["orphans"] == 1                    # incomplete: left alone
