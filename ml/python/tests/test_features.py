import json
from pathlib import Path

import numpy as np
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer

import train

FIXTURES = Path(__file__).resolve().parent.parent.parent / "data" / "fixtures"


def row(date):
    return {"id": date, "date": date, "product": "p", "product_raw": "p", "narrative": "n"}


def test_split_by_time_boundaries():
    rows = [row("2019-01-01"), row("2021-12-31"), row("2022-01-01"), row("2022-12-31"),
            row("2023-01-01"), row("2024-12-31")]
    out = train.split_by_time(rows)
    assert [r["date"] for r in out["train"]] == ["2019-01-01", "2021-12-31"]
    assert [r["date"] for r in out["test"]] == ["2022-01-01", "2022-12-31"]
    assert [r["date"] for r in out["stream"]] == ["2023-01-01", "2024-12-31"]


def test_split_rejects_dates_outside_all_splits():
    with pytest.raises(ValueError):
        train.split_by_time([row("2018-12-31")])


def test_real_sample_splits_cover_every_row():
    rows = train.load_complaints(train.PATHS["complaints"])
    out = train.split_by_time(rows)
    assert sum(len(v) for v in out.values()) == len(rows) == 3600


CASES = json.loads((FIXTURES / "tokenize.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_tokenize_fixture(case):
    assert train.tokenize(case["input"], train.FEATURES) == case["tokens"]


def test_fixture_has_enough_cases():
    assert len(CASES) >= 15


def test_tfidf_matches_sklearn():
    rows = train.load_complaints(train.PATHS["complaints"])
    docs = [r["narrative"] for r in train.split_by_time(rows)["train"]]
    # A small cap forces max_features to cut through many tied counts.
    for cfg in (train.FEATURES, {**train.FEATURES, "min_df": 3, "max_features": 500}):
        sk = TfidfVectorizer(
            analyzer=lambda t: train.tokenize(t, cfg), smooth_idf=True, sublinear_tf=False,
            norm="l2", min_df=cfg["min_df"], max_features=cfg["max_features"])
        expected = sk.fit_transform(docs)
        vocabulary, idf = train.fit_tfidf(docs, cfg)
        assert vocabulary == list(sk.get_feature_names_out())
        assert np.allclose(idf, sk.idf_, rtol=0, atol=1e-12)
        got = train.transform(docs, vocabulary, idf)
        assert got.shape == expected.shape
        assert abs(got - expected).max() < 1e-12


def test_transform_unknown_tokens_give_a_zero_row():
    got = train.transform(["zzz qqq", "apple apple"], ["apple"], np.array([2.0]))
    assert got.toarray().tolist() == [[0.0], [1.0]]
