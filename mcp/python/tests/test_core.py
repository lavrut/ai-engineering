"""Tests for lumen_core: tickets, classifier parity with ml/serve, and the label store."""
from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from lumen_core import classify, labels, tickets

ROOT = Path(__file__).resolve().parents[3]


# ── 1. Tickets ──────────────────────────────────────────────────────────────
def test_load_tickets_count():
    ts = tickets.load_tickets()
    assert len(ts) == 61
    assert ts[-1]["id"] == "t061"


def test_search_ranking():
    hits = tickets.search("refund duplicate")
    assert hits[0]["id"] == "t001"
    assert set(hits[0]) == {"id", "category", "priority", "untrusted_body"}
    assert len(tickets.search("the", limit=3)) == 3


def test_untrusted_body_verbatim():
    row = json.loads((ROOT / "mcp/data/injection.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert tickets.get("t061")["untrusted_body"] == row["text"]
    assert "ignore prior instructions" in row["text"]


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        tickets.get("t999")


# ── 2. Classifier ───────────────────────────────────────────────────────────
def test_classify_matches_ml_serve():
    cases = json.loads((ROOT / "ml/data/fixtures/predictions.json").read_text(encoding="utf-8"))["cases"]
    assert cases
    for c in cases:
        got = classify.classify(c["input"])
        assert got["label"] == c["expected"]["label"], c["name"]
        assert got["low_confidence"] == c["expected"]["low_confidence"], c["name"]
        assert got["scores"] == c["expected"]["scores"], c["name"]
        assert got["model_version"]


# ── 3. Labels ───────────────────────────────────────────────────────────────
def _kw(**over):
    kw = dict(source="agent", client_id="c1", model=None, confirmed=False)
    kw.update(over)
    return kw


def test_write_label_rejects_bad_label(tmp_path):
    p = tmp_path / "l.jsonl"
    with pytest.raises(ValueError):
        labels.write_label("t001", "nonsense", path=p, **_kw())
    assert not p.exists()


def test_write_label_row_shape(tmp_path):
    p = tmp_path / "l.jsonl"
    row = labels.write_label("t001", "billing", path=p, **_kw(source="human", confirmed=True))
    assert set(row) == {"ticket_id", "label", "source", "client_id", "model", "confirmed", "time"}
    assert json.loads(p.read_text().splitlines()[0]) == row


def test_eligible_for_eval():
    assert labels.eligible_for_eval({"source": "human", "confirmed": True})
    assert not labels.eligible_for_eval({"source": "human", "confirmed": False})
    assert not labels.eligible_for_eval({"source": "agent", "confirmed": True})
    assert not labels.eligible_for_eval({"source": "agent", "confirmed": False})


def _worker(path: str, tag: str) -> None:
    for i in range(50):
        labels.write_label(f"t{i:03d}", "bug", path=Path(path),
                           source="agent", client_id=tag, model="m" * 2000, confirmed=False)


def test_concurrent_label_appends(tmp_path):
    p = tmp_path / "l.jsonl"
    procs = [multiprocessing.Process(target=_worker, args=(str(p), t)) for t in ("a", "b")]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join()
        assert pr.exitcode == 0
    lines = p.read_text().splitlines()
    assert len(lines) == 100
    rows = [json.loads(l) for l in lines]
    assert sorted(r["client_id"] for r in rows).count("a") == 50


# ── 4. Ruling additions (R14, R15) ──────────────────────────────────────────
def test_search_ascii_only_semantics():
    # \x1f is not ASCII whitespace for the core, so "a\x1fb" is ONE word, not two.
    assert tickets.search("a\x1fb") == []
    # Non-ASCII letters are not case-folded: "İ"/"Σ" only match themselves exactly.
    assert tickets.search("İ") == []
    assert tickets.search("Σ") == []
    assert [h["id"] for h in tickets.search("REFUND DUPLICATE")] == [h["id"] for h in tickets.search("refund duplicate")]
    assert tickets.search("refund\x0bduplicate")[0]["id"] == "t001"   # \v splits


def test_categories_match_triage_lab():
    import sys
    sys.path.insert(0, str(ROOT / "llms" / "python"))
    import triage_lab
    import lumen_core
    assert lumen_core.CATEGORIES == triage_lab.CATEGORIES


def test_search_injection_ticket_shape():
    hits = tickets.search("IMPORTANT SYSTEM NOTE")
    t061 = [h for h in hits if h["id"] == "t061"]
    assert len(t061) == 1
    assert set(t061[0]) == {"id", "category", "priority", "untrusted_body"}
    row = json.loads((ROOT / "mcp/data/injection.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert t061[0]["untrusted_body"] == row["text"]


def test_classify_load_failure_leaves_cache_empty(monkeypatch):
    """If load_production raises, nothing is cached, so the next call retries cleanly."""
    classify.classify("warm up")           # make sure ml_serve_server is importable
    saved = dict(classify._cache)
    classify._cache.clear()
    mod = saved["server"]
    monkeypatch.setattr(mod, "load_production", lambda root: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(classify.importlib.util, "module_from_spec", lambda spec: mod)
    monkeypatch.setattr(classify.importlib.util, "spec_from_file_location",
                        lambda *a, **k: type("S", (), {"loader": type("L", (), {"exec_module": lambda self, m: None})()})())
    with pytest.raises(RuntimeError):
        classify._load()
    assert classify._cache == {}
    classify._cache.update(saved)
