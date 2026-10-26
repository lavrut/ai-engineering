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
    # A month of one unseen word, heavily shifted in both signals. With 29 complaints it is shown
    # but never alerted, under either flag; with 30 it alerts under both.
    def month(k):
        rows = [{**r, "narrative": "zebra " * 50} for r in stream[:k]]
        return monitor.monthly_drift(rows, model, reference_rows=splits["test"],
                                     train_rows=splits["train"])[0]
    small, full = month(29), month(30)
    assert small["n"] == 29
    assert small["psi_words"] >= monitor.ALERT_AT and small["psi_predictions"] >= monitor.ALERT_AT
    assert small["alert_convention"] is False and small["alert_calibrated"] is False
    assert full["alert_convention"] is True and full["alert_calibrated"] is True


def test_null_band_is_deterministic_and_ordered(setup):
    splits, model = setup
    index = {t: i for i, t in enumerate(model["vocabulary"])}
    ref = monitor.featurize(splits["test"], monitor.top_words(splits["train"], model), model, index)
    dates = [r["date"] for r in splits["test"]]
    a = monitor.null_band(ref, dates, 50, "month", draws=500)
    assert a == monitor.null_band(ref, dates, 50, "month", draws=500)
    q = monitor.null_band(ref, dates, 150, "quarter", draws=500)
    assert 0 < a["predictions"]["p50"] < a["predictions"]["p95"]
    assert q["predictions"]["p95"] < a["predictions"]["p95"]
    assert len(a["words"]["within_2022"]) == 12 and len(q["words"]["within_2022"]) == 4
    assert a["words"]["threshold"] == max(a["words"]["p95"], a["words"]["within_2022_max"])


def test_reference_rows_is_keyword_only(setup):
    splits, model = setup
    with pytest.raises(TypeError):
        monitor.monthly_drift(splits["stream"], model, splits["test"])


def test_stop_words_are_not_word_buckets(setup):
    splits, model = setup
    assert not set(monitor.top_words(splits["train"], model)) & monitor.STOP_WORDS


def test_drift_report_months_and_fields():
    report = json.loads(DRIFT.read_text())
    assert report["generated_by"] == "python monitor.py --write-reports"
    s = report["settings"]
    assert (s["alert_at"], s["epsilon"], s["min_n"], s["top_k"], s["model_version"]) == \
        (0.2, 1e-4, 30, monitor.TOP_K, "v1")
    assert report["null"]["month"]["n"] == 50 and report["null"]["quarter"]["n"] == 150
    assert report["null"]["month"]["draws"] >= 5000 and report["null"]["month"]["seed"] == 7
    chance = report["null"]["expected_chance_alerts"]
    assert chance["month"]["per_signal"] == pytest.approx(1.2)
    # Either of two signals: at most the sum (union bound), whatever their correlation.
    assert chance["month"]["either_signal_max"] == pytest.approx(2.4)
    assert chance["quarter"]["either_signal_max"] == pytest.approx(0.8)
    fields = {"n", "psi_predictions", "psi_words", "alert_convention", "alert_calibrated",
              "top_shifted_words"}
    months, quarters = report["months"], report["quarters"]
    assert [m["month"] for m in months] == [f"{y}-{m:02d}" for y in (2023, 2024) for m in range(1, 13)]
    assert [q["quarter"] for q in quarters] == [f"{y}-Q{q}" for y in (2023, 2024) for q in range(1, 5)]
    for unit, rows, n in (("month", months, 50), ("quarter", quarters, 150)):
        null = report["null"][unit]
        for m in rows:
            assert set(m) == fields | {unit} and m["n"] == n
            assert m["alert_convention"] == (m["psi_predictions"] >= 0.2 or m["psi_words"] >= 0.2)
            assert m["alert_calibrated"] == (m["psi_predictions"] > null["predictions"]["threshold"]
                                             or m["psi_words"] > null["words"]["threshold"])
            for w in m["top_shifted_words"]:
                assert set(w) == {"word", "contribution", "direction"} and w["direction"] in ("up", "down")
    old = report["reference_training_in_sample"]["months"]
    assert len(old) == 24 and "alert_convention" in old[0]
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
