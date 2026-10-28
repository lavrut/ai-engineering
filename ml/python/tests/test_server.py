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


# ── contract details (fix round 1) ──────────────────────────────────────────
def post(client, body, **headers):
    return client.post("/predict", content=body, headers=headers)


@pytest.mark.parametrize("body", [
    b"\xef\xbb\xbf" + b'{"text": "hi"}',                  # UTF-8 BOM
    '{"text": "hi"}'.encode("utf-16"),
    '{"text": "hi"}'.encode("utf-32"),
])
def test_bom_and_utf16_are_malformed(client, body):
    r = post(client, body)
    assert (r.status_code, r.json()) == (400, BODY_ERROR)


def test_body_size_cap(client):
    base = b'{"text":"hi"}'
    ok = post(client, base + b" " * (server.MAX_BODY - len(base)))
    assert server.MAX_BODY == 131072 and ok.status_code == 200
    big = post(client, base + b" " * (server.MAX_BODY - len(base) + 1))
    assert (big.status_code, big.json()) == (400, LENGTH_ERROR)


def test_content_type_and_extras_and_duplicates(client):
    assert post(client, b'{"text":"hi"}').status_code == 200
    assert post(client, b'{"text":"hi"}', **{"content-type": "text/plain"}).status_code == 200
    assert post(client, b'{"text":"hi","x":1}').status_code == 200
    dup = post(client, b'{"text":"first","text":""}')           # last wins: empty
    assert (dup.status_code, dup.json()) == (400, LENGTH_ERROR)
    dup = post(client, b'{"text":"","text":"mortgage"}')
    assert dup.status_code == 200


# An integer longer than Python's 4,300-digit conversion limit in a field we ignore: Go never
# converts an ignored field, so Python must not either (a long integer in "text" stays a 400).
LONG_INT = b"1" * 5000


@pytest.mark.parametrize("body, status", [
    (b'{"text":"hi","x":' + LONG_INT + b"}", 200),
    (b'{"text":"hi","x":-' + LONG_INT + b"}", 200),
    (b'{"text":"hi","x":[0,{"y":' + LONG_INT + b"}]}", 200),
    (b'{"text":' + LONG_INT + b"}", 400),
    (b'{"text":5}', 400),
])
def test_long_integers_are_never_converted(client, body, status):
    r = post(client, body)
    assert r.status_code == status
    if status == 400:
        assert r.json() == BODY_ERROR


def test_four_byte_emoji_boundary(client):
    def body(n):
        return json.dumps({"text": "\U0001F600" * n}, ensure_ascii=False).encode("utf-8")
    assert post(client, body(20000)).status_code == 200
    r = post(client, body(20001))
    assert (r.status_code, r.json()) == (400, LENGTH_ERROR)


def test_score_rejects_a_foreign_index():
    model = json.loads((ML / "models" / "v1.json").read_text("utf-8"))
    with pytest.raises(ValueError, match="index"):
        train.score(model, "mortgage", {"mortgage": 0})


def test_startup_version_mismatch(root):
    p = root / "models" / "v1.json"
    m = json.loads(p.read_text("utf-8"))
    m["version"] = "v9"
    p.write_text(json.dumps(m), encoding="utf-8")
    reg = json.loads((root / "registry.json").read_text())
    reg["versions"]["v1"]["sha256"] = server.mlops.sha256_file(p)
    (root / "registry.json").write_text(json.dumps(reg))
    with pytest.raises(server.StartupError, match="v9"):
        server.load_production(root)


# ── strict JSON ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("body", [
    b'{"text":"hi","x":NaN}', b'{"text":"hi","x":Infinity}', b'{"text":"hi","x":-Infinity}',
    b'{"text":NaN}',
    b'{"text":"hi","x":' + b"[" * 20000 + b"]" * 20000 + b"}",      # 20,000-deep nesting
])
def test_non_json_constants_and_deep_nesting_are_malformed(client, body):
    r = post(client, body)
    assert (r.status_code, r.json()) == (400, BODY_ERROR)


def test_lone_surrogate_is_one_code_point(client):
    r = post(client, b'{"text":"\\ud800"}')
    assert r.status_code == 200 and r.json()["low_confidence"] is True
