import hashlib
import json
import math
import shutil
from pathlib import Path

import pytest

import mlops

ML = Path(__file__).resolve().parent.parent.parent
T0 = "2023-01-01T00:00:00Z"


def fake_model(dir_: Path, version: str, kind: str = "logreg") -> Path:
    """A tiny stand-in for an exported model: only the fields the registry reads."""
    path = dir_ / f"{version}.json"
    path.write_text(json.dumps({"version": version, "model": kind,
                                "trained_on": {"from": "2019-01-01", "to": "2021-12-31"},
                                "metrics": {"macro_f1": 0.7}}) + "\n", encoding="utf-8")
    return path


def summary(macro: float, per_class: dict[str, float], n: int = 400) -> dict:
    return {"n": n, "macro_f1": macro, "per_class_f1": per_class}


# ── 1. Run tracker ──────────────────────────────────────────────────────────
def test_run_log_round_trip(tmp_path):
    model = fake_model(tmp_path, "m1")
    params = {"model": "logreg", "C": 0.3}
    metrics = {"test": {"accuracy": 0.8, "macro_f1": 0.75}}
    windows = {"train": {"from": "2019-01-01", "to": "2021-12-31"},
               "eval": {"name": "test", "from": "2022-01-01", "to": "2022-12-31"}}
    run_id = mlops.log_run(params, metrics, model, "sha256:abc", windows=windows,
                           runs_dir=tmp_path / "runs", time=T0)
    runs = mlops.list_runs(tmp_path / "runs")
    assert [r["run_id"] for r in runs] == [run_id]
    run = runs[0]
    assert run["params"] == params and run["metrics"] == metrics
    assert run["data_version"] == "sha256:abc"
    assert run["model"]["sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()
    assert run["windows"] == windows and run["time"] == T0
    assert "git_commit" in run and "git_dirty" in run          # None outside a git checkout


def test_log_run_without_kept_model_records_only_the_hash(tmp_path):
    model = fake_model(tmp_path, "m1")
    run_id = mlops.log_run({"model": "nb", "alpha": 0.1}, {}, model, "sha256:abc",
                           runs_dir=tmp_path / "runs", time=T0, keep_path=False)
    run = mlops.load_run(run_id, tmp_path / "runs")
    assert run["model"]["path"] is None
    assert run["model"]["sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()


def test_log_run_refuses_to_overwrite_a_changed_run(tmp_path):
    model, runs = fake_model(tmp_path, "m1"), tmp_path / "runs"
    params = {"model": "logreg", "C": 1.0}
    run_id = mlops.log_run(params, {"f1": 0.7}, model, "d", runs_dir=runs, time=T0)
    assert mlops.log_run(params, {"f1": 0.7}, model, "d", runs_dir=runs, time=T0) == run_id
    with pytest.raises(FileExistsError, match="replace"):
        mlops.log_run(params, {"f1": 0.8}, model, "d", runs_dir=runs, time=T0)
    assert mlops.load_run(run_id, runs)["metrics"] == {"f1": 0.7}
    mlops.log_run(params, {"f1": 0.8}, model, "d", runs_dir=runs, time=T0, replace=True)
    assert mlops.load_run(run_id, runs)["metrics"] == {"f1": 0.8}


def test_compare_runs_shows_param_and_metric_differences(tmp_path):
    model = fake_model(tmp_path, "m1")
    runs = tmp_path / "runs"
    a = mlops.log_run({"model": "logreg", "C": 0.3, "seed": 7}, {"test": {"macro_f1": 0.70}},
                      model, "d", runs_dir=runs, time=T0)
    b = mlops.log_run({"model": "logreg", "C": 3.0, "seed": 7}, {"test": {"macro_f1": 0.75}},
                      model, "d", runs_dir=runs, time=T0)
    diff = mlops.compare_runs(a, b, runs_dir=runs)
    assert diff["params"] == {"C": [0.3, 3.0]}                  # seed is equal, so not listed
    assert diff["metrics"]["test.macro_f1"][:2] == [0.70, 0.75]
    assert math.isclose(diff["metrics"]["test.macro_f1"][2], 0.05)


# ── 2. Registry ─────────────────────────────────────────────────────────────
@pytest.fixture
def reg(tmp_path):
    return tmp_path / "registry.json"


def test_stage_transitions_and_history(tmp_path, reg):
    v1 = mlops.register(fake_model(tmp_path, "v1"), registry=reg, time=T0)
    assert v1 == "v1"
    assert mlops.load_registry(reg)["versions"]["v1"]["stage"] == "staging"
    mlops.promote("v1", "production", registry=reg, time=T0)
    mlops.register(fake_model(tmp_path, "v2"), registry=reg, time=T0)
    mlops.promote("v2", "production", registry=reg, time=T0)
    data = mlops.load_registry(reg)
    assert {v: e["stage"] for v, e in data["versions"].items()} == \
        {"v1": "archived", "v2": "production"}
    assert [(h["action"], h["version"], h["from_stage"], h["to_stage"]) for h in data["history"]] == [
        ("register", "v1", None, "staging"),
        ("promote", "v1", "staging", "production"),
        ("register", "v2", None, "staging"),
        ("archive", "v1", "production", "archived"),
        ("promote", "v2", "staging", "production"),
    ]
    entry = data["versions"]["v2"]
    assert entry["path"] == "v2.json" and entry["model"] == "logreg"
    assert entry["sha256"] == hashlib.sha256((tmp_path / "v2.json").read_bytes()).hexdigest()


def test_promote_unknown_version_fails(tmp_path, reg):
    mlops.register(fake_model(tmp_path, "v1"), registry=reg, time=T0)
    with pytest.raises(KeyError, match="v9"):
        mlops.promote("v9", "production", registry=reg, time=T0)
    with pytest.raises(ValueError, match="stage"):
        mlops.promote("v1", "shipped", registry=reg, time=T0)


def test_register_twice_fails(tmp_path, reg):
    path = fake_model(tmp_path, "v1")
    mlops.register(path, registry=reg, time=T0)
    with pytest.raises(ValueError, match="already registered"):
        mlops.register(path, registry=reg, time=T0)


def test_rollback_without_previous_fails(tmp_path, reg):
    with pytest.raises(ValueError, match="no production"):
        mlops.rollback(registry=reg, time=T0)
    mlops.register(fake_model(tmp_path, "v1"), registry=reg, time=T0)
    mlops.promote("v1", "production", registry=reg, time=T0)
    before = reg.read_bytes()
    with pytest.raises(ValueError, match="no previous"):
        mlops.rollback(registry=reg, time=T0)
    assert reg.read_bytes() == before                            # a failed rollback changes nothing


def test_rollback_restores_previous_production(tmp_path, reg):
    for v in ("v1", "v2"):
        mlops.register(fake_model(tmp_path, v), registry=reg, time=T0)
        mlops.promote(v, "production", registry=reg, time=T0)
    n_before = len(mlops.load_registry(reg)["history"])
    assert mlops.rollback(registry=reg, time="2023-02-01T00:00:00Z") == "v1"
    data = mlops.load_registry(reg)
    assert mlops.production_version(data) == "v1"
    assert data["versions"]["v2"]["stage"] == "archived"
    new = data["history"][n_before:]
    assert [(h["action"], h["version"], h["from_stage"], h["to_stage"]) for h in new] == [
        ("rollback", "v2", "production", "archived"),
        ("rollback", "v1", "archived", "production"),
    ]
    assert all(h["time"] == "2023-02-01T00:00:00Z" for h in new)


def test_atomic_write_survives_crash(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    mlops.write_json_atomic(path, {"versions": {}, "history": ["old"]})
    before = path.read_bytes()

    def crash(src, dst):                                         # the power cut, just before the swap
        raise OSError("simulated crash between write and replace")

    monkeypatch.setattr(mlops.os, "replace", crash)
    with pytest.raises(OSError, match="simulated"):
        mlops.write_json_atomic(path, {"versions": {}, "history": ["new"]})
    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding="utf-8"))["history"] == ["old"]
    assert [p.name for p in tmp_path.iterdir()] == ["registry.json"]   # no temp file left behind


# ── 3. Promotion gate ───────────────────────────────────────────────────────
PROD = summary(0.70, {"a": 0.70, "b": 0.70})                     # band 1/sqrt(400) = 0.05


def test_gate_passes_on_a_clear_win():
    got = mlops.gate(summary(0.80, {"a": 0.80, "b": 0.80}), PROD)
    assert got["passed"] is True
    assert math.isclose(got["gain"], 0.10) and got["band"] == 0.05


def test_gate_fails_on_a_drop_beyond_the_band():
    got = mlops.gate(summary(0.60, {"a": 0.60, "b": 0.60}), PROD)
    assert got["passed"] is False
    assert any("macro-F1" in r for r in got["reasons"])


def test_gate_fails_on_a_gain_inside_the_band():
    assert mlops.gate(summary(0.74, {"a": 0.74, "b": 0.74}), PROD)["passed"] is False


def test_gate_fails_on_a_floor_breach():
    got = mlops.gate(summary(0.90, {"a": 1.0, "b": 0.45}), PROD)
    assert got["passed"] is False
    assert any("b" in r and "floor" in r for r in got["reasons"])
    assert mlops.gate(summary(0.90, {"a": 1.0, "b": 0.45}), PROD, floor=0.4)["passed"] is True


def test_gate_needs_the_same_cases():
    with pytest.raises(ValueError, match="same"):
        mlops.gate(summary(0.9, {"a": 0.9}, n=300), PROD)


# ── 4. Committed runs and registry ──────────────────────────────────────────
def test_committed_registry_history_is_real():
    data = mlops.load_registry(ML / "registry.json")
    hist = [(h["action"], h["version"]) for h in data["history"]]
    assert hist[:4] == [("register", "v1"), ("promote", "v1"), ("register", "v2"), ("gate", "v2")]
    gate_note = data["history"][3]["note"]
    assert gate_note.startswith(("passed", "failed"))
    if gate_note.startswith("passed"):
        assert mlops.production_version(data) == "v2"
    else:
        assert mlops.production_version(data) == "v1" and len(hist) == 4
    for v, entry in data["versions"].items():
        path = ML / entry["path"]
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest(), v


def test_committed_runs_cover_the_experiment_set():
    runs = {r["run_id"]: r for r in mlops.list_runs(ML / "runs")}
    assert {"nb-alpha0.1-2019-2021", "logreg-C0.3-2019-2021", "logreg-C1-2019-2021",
            "logreg-C3-2019-2021", "logreg-C1-2019-2022"} <= set(runs)
    for r in runs.values():                  # built from committed code: commit, run, commit
        assert r["git_commit"] and r["git_dirty"] is False, r["run_id"]
    v2 = runs["logreg-C1-2019-2022"]
    assert v2["model"]["path"] == "models/v2.json"
    assert v2["metrics"]["gate"]["passed"] in (True, False)
    # the run that matches v1's settings reproduces v1's recorded test metrics
    v1 = json.loads((ML / "models" / "v1.json").read_text(encoding="utf-8"))
    assert runs["logreg-C1-2019-2021"]["metrics"]["test"]["macro_f1"] == v1["metrics"]["macro_f1"]


def test_runs_name_the_registered_model_files():
    """Provenance: a registered version's run points at the same bytes the registry hashes."""
    reg = mlops.load_registry(ML / "registry.json")
    for version, entry in reg["versions"].items():
        run = mlops.load_run(entry["run"], ML / "runs")
        assert run["model"]["path"] == entry["path"], version
        assert run["model"]["sha256"] == entry["sha256"], version


def test_canonical_commands_reproduce_committed_files(tmp_path, monkeypatch):
    """Re-run the canonical commands in an empty copy and compare bytes. The git fields
    record the checkout at the time of the original run, so they are pinned to it here."""
    committed = mlops.list_runs(ML / "runs")[0]
    monkeypatch.setattr(mlops, "git_state",
                        lambda: (committed["git_commit"], committed["git_dirty"]))
    (tmp_path / "models").mkdir()
    shutil.copy(ML / "models" / "v1.json", tmp_path / "models" / "v1.json")
    for argv in mlops.CANONICAL:
        mlops.main(["--root", str(tmp_path), *argv])
    for rel in ["registry.json", "models/v2.json",
                *[f"runs/{p.name}" for p in sorted((ML / "runs").glob("*.json"))]]:
        assert (tmp_path / rel).read_bytes() == (ML / rel).read_bytes(), rel
    assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == \
        sorted(p.name for p in (ML / "runs").iterdir())
