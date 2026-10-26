import json
from pathlib import Path

import pytest

import monitor
import train

ML = Path(__file__).resolve().parent.parent.parent
CASES = json.loads((ML / "data" / "fixtures" / "psi_cases.json").read_text())
DRIFT = ML / "reports" / "drift.json"


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_psi_matches_hand_computed_cases(case):
    assert monitor.psi(case["expected"], case["actual"]) == pytest.approx(case["psi"], abs=1e-12)


def test_identical_distributions_have_zero_psi():
    assert monitor.psi([5, 9, 1], [10, 18, 2]) == pytest.approx(0.0, abs=1e-15)


def test_empty_bucket_is_finite():
    assert 0 < monitor.psi([10, 0], [0, 10]) < 100


@pytest.fixture(scope="module")
def setup():
    rows = train.load_complaints(train.PATHS["complaints"])
    splits = train.split_by_time(rows)
    model = json.loads((ML / "models" / "v1.json").read_text())
    return splits, model


def test_small_months_never_alert(setup):
    splits, model = setup
    stream = [r for r in splits["stream"] if r["date"].startswith("2023-01")]
    # Same texts as a heavily shifted month, but only 29 of them: shown, never alerted.
    shifted = [{**r, "narrative": "zebra " * 50} for r in stream[:29]]
    month = monitor.monthly_drift(shifted, model, splits["train"])[0]
    assert month["n"] == 29
    assert month["psi_words"] >= monitor.ALERT_AT
    assert month["alert"] is False
    full = monitor.monthly_drift([{**r, "narrative": "zebra " * 50} for r in stream[:30]],
                                 model, splits["train"])[0]
    assert full["n"] == 30 and full["alert"] is True


def test_drift_report_months_and_fields():
    report = json.loads(DRIFT.read_text())
    assert report["generated_by"] == "python monitor.py --write-reports"
    assert report["settings"] == {"alert_at": 0.2, "epsilon": 1e-4, "min_n": 30,
                                  "top_k": monitor.TOP_K, "model_version": "v1"}
    months = report["months"]
    assert [m["month"] for m in months] == [f"{y}-{m:02d}" for y in (2023, 2024) for m in range(1, 13)]
    for m in months:
        assert set(m) == {"month", "n", "psi_predictions", "psi_words", "alert", "top_shifted_words"}
        assert m["n"] == 50
        assert m["alert"] == ((m["psi_predictions"] >= 0.2 or m["psi_words"] >= 0.2) and m["n"] >= 30)
        for w in m["top_shifted_words"]:
            assert set(w) == {"word", "contribution", "direction"} and w["direction"] in ("up", "down")
    assert set(report["real_class_shares"]) == set(report["sample_class_shares"]) == \
        {str(y) for y in range(2019, 2025)}
    for shares in report["real_class_shares"].values():
        assert sum(shares.values()) == pytest.approx(1.0)


def test_real_shares_agree_with_fetch_sample_table():
    import fetch_sample
    real = monitor.real_counts()
    line = next(l for l in fetch_sample.shares(real, real) if l.startswith("| Mortgage"))
    assert line.split("|")[2].strip().split(" / ")[0] == "8.8"
    assert monitor.class_shares(real)["2024"]["Mortgage"] == pytest.approx(0.015, abs=0.0005)


def test_canonical_build_is_byte_identical(tmp_path, monkeypatch, pinned_stack):
    monkeypatch.setitem(monitor.PATHS, "reports", tmp_path)
    monitor.main(["--write-reports"])
    assert (tmp_path / "drift.json").read_bytes() == DRIFT.read_bytes()


def test_run_without_flag_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setitem(monitor.PATHS, "reports", tmp_path)
    monitor.main([])
    assert list(tmp_path.iterdir()) == []
