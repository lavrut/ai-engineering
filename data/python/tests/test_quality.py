"""Tests for quality.py: git freshness as of a pinned date, the trace-set roll-up and verdicts.json.

Ruling D10: freshness is measured as of a date. These tests pass a fixed as_of and
never depend on today's date, so they stay green for as long as the data does.
"""
import json
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import quality as Q  # noqa: E402

ROOT = HERE.parents[2]
CONTRACTS = ROOT / "data" / "contracts"
TICKETS = ROOT / "llms" / "data" / "tickets.jsonl"
AS_OF = date(2026, 10, 28)


def v(rule, field, ok, detail):
    return {"rule": rule, "field": field, "ok": ok, "detail": detail}


def freshness(verdicts):
    return next(x for x in verdicts if x["rule"] == "freshness")


@pytest.fixture
def frozen_clock(monkeypatch):
    """Today's date moved far ahead: anything reading the real clock would go stale."""
    monkeypatch.setattr(Q, "today", lambda: date(2031, 6, 1))


# ── 1. Freshness from git, as of a date ─────────────────────────────────────
def test_git_age_days_none_without_git(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"id": 1}\n')
    assert Q.git_age_days(p, AS_OF) is None
    assert Q.git_age_days(tmp_path / "missing.jsonl", AS_OF) is None


def test_git_age_days_is_measured_from_as_of():
    stamp = Q.commit_time(TICKETS)
    assert stamp is not None
    day = datetime.fromtimestamp(stamp, timezone.utc).date()
    age = Q.git_age_days(TICKETS, day + timedelta(days=10))
    assert 9 <= age <= 10
    assert Q.git_age_days(TICKETS, day - timedelta(days=5)) == 0  # as_of before the commit: never negative


def test_a_dataset_400_days_old_fails_freshness():
    day = datetime.fromtimestamp(Q.commit_time(TICKETS), timezone.utc).date()
    out = Q.run(CONTRACTS / "tickets.v1.yaml", TICKETS, as_of=day + timedelta(days=401))
    f = freshness(out)
    assert f["ok"] is False and f["detail"].endswith("days old, over max 365")
    assert 400 <= float(f["detail"].split()[0]) <= 401


def test_freshness_unknown_in_zip_download(tmp_path):
    """A zip download has no .git: freshness is unknown, which passes with a note."""
    copy_ = tmp_path / "tickets.jsonl"
    shutil.copy(TICKETS, copy_)
    out = Q.run(CONTRACTS / "tickets.v1.yaml", copy_, as_of=AS_OF)
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
def test_verdicts_json_check(capsys, frozen_clock):
    """--check rebuilds with the stored as_of, so a later day changes nothing."""
    assert Q.main(["--check"]) == 0
    assert capsys.readouterr().out.strip() == "verdicts.json matches"


def test_verdicts_json_shape():
    doc = json.loads(Q.VERDICTS.read_text(encoding="utf-8"))
    assert set(doc) == {"as_of", "datasets"}
    assert date.fromisoformat(doc["as_of"])
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


def test_two_builds_are_byte_identical(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    Q.write_verdicts(a, as_of=AS_OF)
    Q.write_verdicts(b, as_of=AS_OF)
    assert a.read_bytes() == b.read_bytes()
    assert json.loads(a.read_text())["as_of"] == "2026-10-28"


def test_check_follows_the_stored_as_of_not_the_clock(tmp_path, capsys, frozen_clock):
    p = tmp_path / "verdicts.json"
    Q.write_verdicts(p, as_of=AS_OF)
    assert Q.check(p) == 0  # the clock says 2031; the file says 2026-10-28
    doc = json.loads(p.read_text())
    doc["datasets"]["tickets"]["verdicts"][0]["ok"] = False
    p.write_text(json.dumps(doc))
    assert Q.check(p) == 1
    assert "verdicts.json is stale" in capsys.readouterr().out
    assert Q.check(tmp_path / "missing.json") == 1


def test_default_run_keeps_the_stored_as_of(tmp_path, monkeypatch, frozen_clock):
    p = tmp_path / "verdicts.json"
    Q.write_verdicts(p, as_of=AS_OF)
    monkeypatch.setattr(Q, "VERDICTS", p)
    assert Q.main([]) == 0
    assert json.loads(p.read_text())["as_of"] == "2026-10-28"


def test_live_past_max_age_exits_1_and_writes_nothing(tmp_path, monkeypatch, capsys):
    p = tmp_path / "verdicts.json"
    monkeypatch.setattr(Q, "VERDICTS", p)
    assert Q.main(["--live", "--as-of", "2030-01-01"]) == 1
    out = capsys.readouterr().out
    assert "FAIL tickets (tickets.v1)" in out and "as of 2030-01-01" in out
    assert not p.exists()
