import json
import math
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import train

ML = Path(__file__).resolve().parent.parent.parent
FIXTURES = ML / "data" / "fixtures"
REPORTS = ML / "reports"
V1 = ML / "models" / "v1.json"


@pytest.fixture(scope="module")
def rows():
    return train.load_complaints(train.PATHS["complaints"])


@pytest.fixture(scope="module")
def fitted(rows):
    return train.train_models(train.split_by_time(rows)["train"])


# ── metrics ─────────────────────────────────────────────────────────────────
METRIC_CASES = json.loads((FIXTURES / "metrics_cases.json").read_text(encoding="utf-8"))["cases"]


def close(got, fraction):
    return math.isclose(got, float(Fraction(fraction)), rel_tol=0, abs_tol=1e-12)


@pytest.mark.parametrize("case", METRIC_CASES, ids=[c["name"] for c in METRIC_CASES])
def test_metrics_match_hand_fixture(case):
    got = train.per_class_metrics(case["y_true"], case["y_pred"], case["classes"])
    assert got["classes"] == case["classes"]
    assert got["confusion"] == case["confusion"]
    assert got["n"] == case["n"]
    assert close(got["accuracy"], case["accuracy"])
    assert math.isclose(got["band"], 1 / math.sqrt(case["n"]))
    for label, want in case["per_class"].items():
        assert got["per_class"][label]["support"] == want["support"]
        for k in ("precision", "recall", "f1"):
            assert close(got["per_class"][label][k], want[k]), (label, k)
    for avg in ("macro", "weighted"):
        for k in ("precision", "recall", "f1"):
            assert close(got[avg][k], case[avg][k]), (avg, k)


def test_noise_band():
    assert train.noise_band(400) == 0.05
    with pytest.raises(ValueError):
        train.noise_band(0)


# ── splits ──────────────────────────────────────────────────────────────────
def test_calendar_invalid_date_is_rejected():
    r = {"id": "1", "date": "2021-02-30", "product": "p", "product_raw": "p", "narrative": "n"}
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        train.split_by_time([r])


def test_no_leakage(rows):
    """Changing every test and stream complaint must not change anything that was fitted."""
    poisoned = [r if r["date"] <= train.SPLITS["train"][1]
                else {**r, "narrative": "leakword " * 50 + r["narrative"][::-1]} for r in rows]
    a = train.fit_and_evaluate(rows)["fitted"]
    b = train.fit_and_evaluate(poisoned)["fitted"]
    assert "leakword" not in b["vocabulary"]
    assert a["vocabulary"] == b["vocabulary"]
    assert np.array_equal(a["idf"], b["idf"])
    for name in ("nb", "logreg"):
        ma, mb = a["models"][name], b["models"][name]
        coef = "coef_" if name == "logreg" else "feature_log_prob_"
        assert np.array_equal(getattr(ma, coef), getattr(mb, coef))


# ── scoring and export ──────────────────────────────────────────────────────
def export(fitted, name="logreg"):
    meta = {"version": "test", "created": "2026-10-26", "data_version": "sha256:x",
            "majority_class": fitted["majority_class"], "metrics": {}}
    return train.export_model(fitted["models"][name], fitted["vocabulary"], fitted["idf"], meta)


@pytest.mark.parametrize("name", ["logreg", "nb"])
def test_score_matches_sklearn_predict_proba(rows, fitted, name):
    texts = [r["narrative"] for r in train.split_by_time(rows)["test"][:50]]
    model_json = export(fitted, name)
    expected = fitted["models"][name].predict_proba(
        train.transform(texts, fitted["vocabulary"], fitted["idf"]))
    for text, want in zip(texts, expected):
        got = train.score(model_json, text)
        probs = [got["scores"][c] for c in model_json["classes"]]
        assert np.allclose(probs, want, rtol=0, atol=1e-9)
        assert got["label"] == model_json["classes"][int(np.argmax(want))]


def test_export_round_trip(fitted):
    model_json = export(fitted)
    back = json.loads(json.dumps(model_json))
    assert back == model_json                        # floats survive JSON exactly
    for key in ("version", "created", "data_version", "trained_on", "tokenizer", "vocabulary",
                "idf", "classes", "coef", "intercept", "majority_class", "metrics"):
        assert key in back, key
    assert back["tokenizer"] == train.FEATURES
    assert back["trained_on"] == {"from": "2019-01-01", "to": "2021-12-31"}
    assert len(back["idf"]) == len(back["vocabulary"])
    assert len(back["coef"]) == len(back["intercept"]) == len(back["classes"])
    assert all(len(row) == len(back["vocabulary"]) for row in back["coef"])
    text = "They reported a late payment on my credit report"
    assert train.score(back, text) == train.score(model_json, text)


def tiny_model(coef):
    return {"tokenizer": train.FEATURES, "vocabulary": ["bad", "good"], "idf": [1.0, 1.0],
            "classes": ["a", "b", "c"], "coef": coef, "intercept": [0.0, 0.1, 0.0],
            "majority_class": "c"}


def test_low_confidence_rules():
    weak = tiny_model([[0.0, 0.5], [0.0, 0.0], [0.0, 0.0]])
    strong = tiny_model([[0.0, 9.0], [0.0, 0.0], [0.0, 0.0]])
    for text in ("", "   ", "zzz qqq", "XXXX XX/XX/XXXX"):
        got = train.score(strong, text)
        assert got["label"] == "c" and got["low_confidence"] is True, text
        assert math.isclose(sum(got["scores"].values()), 1.0)
    got = train.score(weak, "good")                 # top probability ~0.44
    assert got["label"] == "a" and got["low_confidence"] is True
    assert max(got["scores"].values()) < 0.5
    got = train.score(strong, "Good GOOD")
    assert got["label"] == "a" and got["low_confidence"] is False


def test_score_ties_go_to_the_first_class():
    got = train.score(tiny_model([[0.0, 0.1], [0.0, 0.0], [0.0, 0.0]]), "good")   # a ties b
    assert got["label"] == "a"


# ── committed outputs ───────────────────────────────────────────────────────
def test_v1_reproduces_from_the_sample(fitted):
    v1 = json.loads(V1.read_text(encoding="utf-8"))
    assert v1["version"] == "v1" and v1["model"] == "logreg"
    assert v1["vocabulary"] == fitted["vocabulary"]
    assert v1["idf"] == fitted["idf"].tolist()
    assert np.allclose(v1["coef"], fitted["models"]["logreg"].coef_, rtol=0, atol=1e-9)
    assert v1["majority_class"] == fitted["majority_class"]
    assert v1["data_version"] == train.data_version(train.PATHS["complaints"])


def test_predictions_fixture_matches_v1():
    v1 = json.loads(V1.read_text(encoding="utf-8"))
    fixture = json.loads((FIXTURES / "predictions.json").read_text(encoding="utf-8"))
    assert fixture["model"] == "ml/models/v1.json"
    cases = fixture["cases"]
    assert len(cases) == 30
    assert {"", "   \n\t "} <= {c["input"] for c in cases}
    assert max(len(c["input"]) for c in cases) == 25000
    for c in cases:
        got = train.score(v1, c["input"])
        assert got["label"] == c["expected"]["label"], c["name"]
        assert got["low_confidence"] == c["expected"]["low_confidence"], c["name"]
        for label, p in c["expected"]["scores"].items():
            assert abs(got["scores"][label] - p) <= 1e-12, c["name"]


@pytest.mark.parametrize("name", ["dataset", "metrics", "training_log"])
def test_reports_have_generated_by(name):
    report = json.loads((REPORTS / f"{name}.json").read_text(encoding="utf-8"))
    assert report["generated_by"] == "python train.py --model logreg --version v1"


def test_dataset_snippets_are_short():
    report = json.loads((REPORTS / "dataset.json").read_text(encoding="utf-8"))
    for category, snippets in report["snippets"].items():
        assert 1 <= len(snippets) <= 3, category
        assert all(len(s["text"]) <= 200 for s in snippets)


def test_metrics_report_shape():
    report = json.loads((REPORTS / "metrics.json").read_text(encoding="utf-8"))
    assert set(report["models"]) == {"majority", "nb", "logreg"}
    for m in report["models"].values():
        for split in ("train", "test"):
            assert len(m[split]["confusion"]) == len(report["classes"])
    assert report["models"]["logreg"]["test"]["macro"]["f1"] > report["models"]["majority"]["test"]["macro"]["f1"]


def test_training_log_objective_goes_down():
    log = json.loads((REPORTS / "training_log.json").read_text(encoding="utf-8"))
    points = log["checkpoints"]
    assert points[0]["iteration"] == 0
    assert math.isclose(points[0]["loss"], math.log(len(log["classes"])))
    iters = [p["iteration"] for p in points]
    assert iters == sorted(set(iters))
    objective = [p["loss"] + p["penalty"] for p in points]
    assert all(b <= a + 1e-12 for a, b in zip(objective, objective[1:]))
    assert any("top_words" in p for p in points)
