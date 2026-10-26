"""Tests for ml/python/bench.py's pure parts (starting servers is left to the real run)."""
import numpy as np
import pytest

import bench


def test_summarize_matches_numpy_percentiles():
    ms = [5.0, 1.0, 3.0, 2.0, 4.0, 10.0, 7.5]
    s = bench.summarize(ms)
    assert s["n"] == 7 and s["min_ms"] == 1.0 and s["max_ms"] == 10.0
    assert s["p50_ms"] == pytest.approx(np.percentile(ms, 50))
    assert s["p95_ms"] == pytest.approx(np.percentile(ms, 95))
    assert s["mean_ms"] == pytest.approx(np.mean(ms))


def test_same_answer_tolerance():
    a = {"label": "A", "low_confidence": False, "model_version": "v1", "scores": {"A": 0.7, "B": 0.3}}
    b = {**a, "scores": {"A": 0.7 + 5e-10, "B": 0.3}}
    assert bench.same_answer(a, b)
    assert not bench.same_answer(a, {**a, "scores": {"A": 0.7 + 2e-9, "B": 0.3}})
    assert not bench.same_answer(a, {**a, "label": "B"})


def test_request_set_is_the_valid_fixture_texts():
    texts = bench.request_texts()
    assert len(texts) == 28 and all(1 <= len(t) <= bench.MAX_CHARS for t in texts)
