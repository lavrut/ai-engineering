"""Tests for quality.py: git freshness, the trace-set roll-up and verdicts.json."""
import copy
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import quality as Q  # noqa: E402

ROOT = HERE.parents[2]
CONTRACTS = ROOT / "data" / "contracts"


def v(rule, field, ok, detail):
    return {"rule": rule, "field": field, "ok": ok, "detail": detail}


# ── 1. Freshness from git ───────────────────────────────────────────────────
def test_git_age_days_none_without_git(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"id": 1}\n')
    assert Q.git_age_days(p) is None
    assert Q.git_age_days(tmp_path / "missing.jsonl") is None


def test_git_age_days_of_a_committed_file():
    age = Q.git_age_days(ROOT / "llms" / "data" / "tickets.jsonl")
    assert age is not None and 0 <= age < 365


def test_freshness_unknown_in_zip_download(tmp_path):
    """A zip download has no .git: freshness is unknown, which passes with a note."""
    copy_ = tmp_path / "tickets.jsonl"
    shutil.copy(ROOT / "llms" / "data" / "tickets.jsonl", copy_)
    out = Q.run(CONTRACTS / "tickets.v1.yaml", copy_)
    assert v("freshness", None, True, "unknown: no git date") in out
    assert all(x["ok"] for x in out)


# ── 2. The trace-set roll-up ────────────────────────────────────────────────
def test_merge_rolls_files_up_per_rule():
    a = [v("freshness", None, True, "1 days old (max 365)"), v("type", "engine", True, "all values are string")]
    b = [v("freshness", None, True, "2 days old (max 365)"), v("type", "engine", True, "all values are string")]
    assert Q.merge([("a.json", a), ("b.json", b)]) == [
        v("freshness", None, True, "2 files ok"),
        v("type", "engine", True, "2 files: all values are string"),
    ]
    bad = [v("freshness", None, True, "1 days old (max 365)"), v("type", "engine", False, "row 1: expected string, got integer")]
    out = Q.merge([("a.json", bad), ("b.json", b), ("c.json", bad)])
    assert v("type", "engine", False, "a.json: row 1: expected string, got integer (+1 more file)") in out


# ── 3. verdicts.json ────────────────────────────────────────────────────────
def test_verdicts_json_check(capsys):
    assert Q.main(["--check"]) == 0
    assert capsys.readouterr().out.strip() == "verdicts.json matches"


def test_verdicts_json_shape():
    doc = json.loads(Q.VERDICTS.read_text(encoding="utf-8"))
    assert set(doc) == {"generated", "datasets"}
    ds = doc["datasets"]
    assert {"tickets", "complaints"} <= set(ds)
    sets = sorted(k for k in ds if k.startswith("agent-traces/"))
    assert sets == ["agent-traces/" + p.name for p in sorted((ROOT / "agents" / "data" / "traces").iterdir()) if p.is_dir()]
    assert ds["tickets"]["contract"] == "tickets.v1" and ds["tickets"]["path"] == "llms/data/tickets.jsonl"
    assert ds["agent-traces/desk-sdk"]["files"] == 26
    for entry in ds.values():
        assert entry["ok"] is True and all(x["ok"] for x in entry["verdicts"])
        assert not Path(entry["path"]).is_absolute()
    assert Q.VERDICTS.stat().st_size < 100_000


def test_check_ignores_the_clock_but_not_the_verdicts(tmp_path, capsys):
    doc = json.loads(Q.VERDICTS.read_text(encoding="utf-8"))
    later = copy.deepcopy(doc)
    later["generated"] = "0000000"
    for entry in later["datasets"].values():
        entry["age_days"] = 400.0 if entry["age_days"] is not None else None
        for x in entry["verdicts"]:
            if x["rule"] == "freshness":
                x["detail"] = "a different age"
    p = tmp_path / "verdicts.json"
    p.write_text(json.dumps(later))
    assert Q.check(p) == 0
    later["datasets"]["tickets"]["verdicts"][0]["ok"] = False
    p.write_text(json.dumps(later))
    assert Q.check(p) == 1
    assert "verdicts.json is stale" in capsys.readouterr().out
    assert Q.check(tmp_path / "missing.json") == 1
