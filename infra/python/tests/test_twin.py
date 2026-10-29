"""The twin test: the Go gateway stages and the Python ones give the same decisions and status.

The Go binary is built once per session into a temp dir with `go build -o` (from the Go build
cache; nothing is downloaded: infra/go/go.mod has no requires). Every test skips when `go` is
not installed.

1. stages.py --cases and <bin> --cases over cases.json print the same lines, byte for byte.
2. Each configs/*.yaml (and infra/gateway.yaml, the one that loads) is judged by both. Go never parses YAML, so a YAML that Python loads is
   exported with config.py --export --out into a temp dir and Go runs --check-config on that
   export. A YAML that Python refuses never produces an export: for those the test asserts
   Python's refusal (exit 1, no file written) and skips Go, since there is nothing to hand it.
   The same rules are then checked from the Go side by feeding Go the YAML's document as JSON,
   which Go must also refuse.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import FIXTURES, ROOT

PY = ROOT / "infra" / "python"
GO_DIR = ROOT / "infra" / "go"
CASES = FIXTURES / "cases.json"
CONFIGS = [ROOT / "infra" / "gateway.yaml", *sorted((FIXTURES / "configs").glob("*.yaml"))]  # one valid, eight not

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")
GO_BIN: list[Path] = []


@pytest.fixture(scope="session", autouse=True)
def go_binary(tmp_path_factory):
    """Build once: `go run` would report every non-zero exit as 1."""
    if shutil.which("go") is None:
        return
    out = tmp_path_factory.mktemp("go") / "gateway"
    subprocess.run(["go", "build", "-o", str(out), "./gateway"], cwd=GO_DIR, check=True)
    GO_BIN[:] = [out]


def run_python(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(PY / script), *args], capture_output=True, cwd=ROOT)


def run_go(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(GO_BIN[0]), *args], capture_output=True, cwd=GO_DIR)


# ── 1. The cases ────────────────────────────────────────────────────────────
@needs_go
def test_python_and_go_agree_on_every_case():
    py = run_python("stages.py", "--cases", str(CASES))
    go = run_go("--cases", str(CASES))
    assert py.returncode == 0, py.stderr
    assert go.returncode == 0, go.stderr
    py_lines, go_lines = py.stdout.decode().splitlines(), go.stdout.decode().splitlines()
    assert len(py_lines) == len(json.loads(CASES.read_text()))
    for p, g in zip(py_lines, go_lines):
        assert g == p
    assert go.stdout == py.stdout


# ── 2. The configs ──────────────────────────────────────────────────────────
@needs_go
@pytest.mark.parametrize("path", CONFIGS, ids=[p.stem for p in CONFIGS])
def test_both_reject_the_same_configs(tmp_path, path):
    export = tmp_path / "config.json"
    py = run_python("config.py", str(path), "--export", "--out", str(export))
    if py.returncode != 0:
        assert py.returncode == 1 and py.stderr.startswith(b"config error: "), py.stderr
        assert not export.exists()
        # Go cannot see the YAML; hand it the same document as JSON in the export's shape.
        raw = yaml.safe_load(path.read_text())
        as_export = tmp_path / "as_export.json"
        as_export.write_text(json.dumps(yaml_to_export_shape(raw)))
        go = run_go("--check-config", str(as_export))
        assert go.returncode == 2, (go.stdout, go.stderr)
        assert go.stderr.startswith(b"config error: ")
        return
    go = run_go("--check-config", str(export))
    assert go.returncode == 0, go.stderr
    assert path.name == "gateway.yaml"  # every fixture under configs/ is invalid on purpose


def yaml_to_export_shape(raw: dict) -> dict:
    """The YAML's sections in the JSON export's shape, as far as the document allows: the
    '*' route becomes deny_unknown, budgets.untagged and traces.dir move to the top level. NaN
    is written as NaN, which Python's json reads and Go's refuses (both refuse the config)."""
    d = {k: v for k, v in raw.items() if k not in ("routes", "budgets", "traces")}
    if isinstance(raw.get("routes"), dict):
        d["routes"] = {k: v for k, v in raw["routes"].items() if k != "*"}
        d["deny_unknown"] = raw["routes"].get("*") == "deny"
    if isinstance(raw.get("budgets"), dict):
        d["budgets"] = {k: v for k, v in raw["budgets"].items() if k != "untagged"}
        if "untagged" in raw["budgets"]:
            d["untagged"] = raw["budgets"]["untagged"]
    if isinstance(raw.get("traces"), dict):
        d["traces_dir"] = raw["traces"].get("dir")
    elif "traces" in raw:
        d["traces"] = raw["traces"]
    return d


@needs_go
def test_python_accepts_the_shape_go_is_handed():
    """yaml_to_export_shape is faithful: the real gateway.yaml in that shape loads in Python
    (config.from_dict) and in Go (--check-config), and the gateway's own export loads in Go."""
    sys.path.insert(0, str(PY))
    import config
    raw = yaml.safe_load((ROOT / "infra" / "gateway.yaml").read_text())
    assert config.to_json(config.from_dict(yaml_to_export_shape(raw))) == config.to_json(config.build(raw))
    assert run_go("--check-config", str(FIXTURES / "config.json")).returncode == 0


@needs_go
def test_go_names_the_key_as_python_does(tmp_path):
    """The same error wording for the key, on exports a YAML could not have produced."""
    sys.path.insert(0, str(PY))
    import config
    base = json.loads((FIXTURES / "config.json").read_text())
    bad = [
        {**base, "retries": {}},
        {**base, "cache": {"ttl_s": 0, "skip_tools": True}},
        {**base, "cache": {"ttl_s": 60.0, "skip_tools": True}},
        {**base, "cache": {"ttl_s": 60, "skip_tools": "yes"}},
        {**base, "retry": {"attempts": 3, "base_ms": 500, "max_ms": 200}},
        {**base, "models": {"m": {"rpm": True, "tpm": 1, "price": "lab"}}},
        {**base, "models": {"claude-haiku-5-5": {"rpm": 60, "tpm": 200000, "price": [1, -2.5]}}},
        {**base, "models": {"claude-haiku-5-5": {"rpm": 60, "tpm": 200000, "price": "free"}}},
        {**base, "routes": {"claude-haiku-5-5": {"to": "claude-x"}}},
        {**base, "budgets": {**base["budgets"], "desk": {"limit_usd": 1.0, "warn_usd": 1.5}}},
        {**base, "untagged": "nobody"},
        {**base, "upstream": "https://example.com:443/v1"},
        {k: v for k, v in base.items() if k != "untagged"},
        {k: v for k, v in base.items() if k != "limit"},
        {**base, "limit": {"max_wait_ms": 1000, "extra": 1}},
    ]
    for i, d in enumerate(bad):
        with pytest.raises(config.ConfigError) as e:
            config.from_dict(d)
        p = tmp_path / f"bad{i}.json"
        p.write_text(json.dumps(d))
        go = run_go("--check-config", str(p))
        assert go.returncode == 2
        assert go.stderr.decode() == f"config error: {e.value}\n"
