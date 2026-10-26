"""Tests for ml/serve/server.py: the API in-process (TestClient), no network."""
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ML = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ML / "serve"))

import server  # noqa: E402
import train  # noqa: E402

LENGTH_ERROR = {"error": "text must be 1 to 20000 characters"}
BODY_ERROR = {"error": 'body must be a JSON object with a string field "text"'}
CASES = json.loads((ML / "data" / "fixtures" / "predictions.json").read_text("utf-8"))["cases"]


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:      # `with` runs startup (loads the model)
        yield c


@pytest.fixture
def root(tmp_path):
    """A temp copy of the registry and the models, safe to corrupt."""
    shutil.copy(ML / "registry.json", tmp_path / "registry.json")
    (tmp_path / "models").mkdir()
    shutil.copy(ML / "models" / "v1.json", tmp_path / "models" / "v1.json")
    return tmp_path


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "model_version": "v1"}


@pytest.mark.parametrize("case", CASES, ids=[c["name"][:40] for c in CASES])
def test_predict_matches_fixture(client, case):
    r = client.post("/predict", json={"text": case["input"]})
    exp = case["expected"]
    if not 1 <= len(case["input"]) <= 20000:    # the API rejects these; scoring has no limit
        assert r.status_code == 400 and r.json() == LENGTH_ERROR
        return
    assert r.status_code == 200
    got = r.json()
    assert got["label"] == exp["label"]
    assert got["low_confidence"] == exp["low_confidence"]
    assert got["model_version"] == "v1"
    assert set(got["scores"]) == set(exp["scores"])
    for k, v in exp["scores"].items():
        assert abs(got["scores"][k] - v) < 1e-12


def test_whitespace_is_valid_and_low_confidence(client):
    r = client.post("/predict", json={"text": "   \n\t "})
    assert r.status_code == 200 and r.json()["low_confidence"] is True


def test_empty_text_is_400(client):
    r = client.post("/predict", json={"text": ""})
    assert (r.status_code, r.json()) == (400, LENGTH_ERROR)


def test_length_limit_counts_code_points(client):
    assert client.post("/predict", json={"text": "a" * 20000}).status_code == 200
    r = client.post("/predict", json={"text": "a" * 20001})
    assert (r.status_code, r.json()) == (400, LENGTH_ERROR)
    # 20,000 two-byte characters are 40,000 bytes but 20,000 code points: allowed.
    assert client.post("/predict", json={"text": "é" * 20000}).status_code == 200


@pytest.mark.parametrize("body", [
    b"", b"not json", b"[]", b'"text"', b"null", b"{}", b'{"text": 5}', b'{"text": null}',
    b'{"text": ["a"]}', b'{"txt": "hi"}', b"\xff\xfe",
])
def test_malformed_bodies_are_400(client, body):
    r = client.post("/predict", content=body, headers={"content-type": "application/json"})
    assert (r.status_code, r.json()) == (400, BODY_ERROR)


def test_server_scoring_equals_train_score():
    model = json.loads((ML / "models" / "v1.json").read_text("utf-8"))
    index = {t: i for i, t in enumerate(model["vocabulary"])}
    for case in CASES:
        assert train.score(model, case["input"], index) == train.score(model, case["input"])


def test_load_production_ok(root):
    m = server.load_production(root)
    assert m["version"] == "v1" and "index" in m


def test_startup_missing_registry(root):
    (root / "registry.json").unlink()
    with pytest.raises(server.StartupError, match="registry"):
        server.load_production(root)


def test_startup_no_production_version(root):
    reg = json.loads((root / "registry.json").read_text())
    reg["versions"]["v1"]["stage"] = "archived"
    (root / "registry.json").write_text(json.dumps(reg))
    with pytest.raises(server.StartupError, match="production"):
        server.load_production(root)


def test_startup_missing_model_file(root):
    (root / "models" / "v1.json").unlink()
    with pytest.raises(server.StartupError, match="model file"):
        server.load_production(root)


def test_startup_hash_mismatch(root):
    p = root / "models" / "v1.json"
    p.write_text(p.read_text("utf-8") + " ", encoding="utf-8")
    with pytest.raises(server.StartupError, match="SHA-256"):
        server.load_production(root)


def test_app_refuses_to_start_on_bad_root(root, monkeypatch):
    (root / "models" / "v1.json").unlink()
    monkeypatch.setenv("ML_ROOT", str(root))
    with pytest.raises(server.StartupError):
        with TestClient(server.app):
            pass
