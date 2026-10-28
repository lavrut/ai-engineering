"""The twin test: the Go validator and the Python validator print the same lines.

Each fixture case is written to a temp dir as a YAML contract, exported to
<stem>.contract.json with contract.py --export (the file Go reads), and its
dataset as a file; both command lines run and their stdout and exit codes are
compared byte for byte. Edge cases the fixtures do not pin (1.0 against 1,
NaN, key order, Unicode line breaks, bad contracts) run the same way.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CONTRACT_PY = HERE.parent / "contract.py"
GO_DIR = HERE.parents[1] / "go"
CASES = [json.loads(line) for line in (HERE.parents[1] / "fixtures" / "cases.jsonl").read_text().splitlines()]

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")


# ── 1. Running both ─────────────────────────────────────────────────────────
def run_python(*args: str) -> tuple[str, int]:
    p = subprocess.run([sys.executable, str(CONTRACT_PY), *args], capture_output=True, text=True, cwd=ROOT)
    return p.stdout, p.returncode


def run_go(*args: str) -> tuple[str, int]:
    p = subprocess.run(["go", "run", "./validate", *args], capture_output=True, text=True, cwd=GO_DIR)
    return p.stdout, p.returncode


def write_dataset(tmp: Path, case: dict) -> Path:
    data = tmp / "data.jsonl"
    if case.get("dataset") is not None:
        data.write_bytes(case["dataset"].encode("utf-8"))
    else:
        data.write_bytes("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in case["rows"]).encode("utf-8"))
    return data


def age_args(case: dict) -> list[str]:
    return [] if case.get("age_days") is None else ["--age-days", str(case["age_days"])]


def both(tmp: Path, case: dict) -> tuple[tuple[str, int], tuple[str, int]]:
    """Python on the YAML, Go on its export; a contract that will not export is given to both as JSON."""
    src = tmp / "c.yaml"
    if isinstance(case["contract"], str):
        src.write_text(case["contract"], encoding="utf-8")
        shutil.copy(src, tmp / "c.contract.json")
    else:
        src.write_text(yaml.safe_dump(case["contract"], allow_unicode=True, sort_keys=False), encoding="utf-8")
        out, code = run_python("--export", str(src))
        assert code == 0, out
    data = write_dataset(tmp, case)
    py = run_python(str(src), str(data), *age_args(case))
    go = run_go(str(tmp / "c.contract.json"), str(data), *age_args(case))
    return py, go


# ── 2. The fixtures ─────────────────────────────────────────────────────────
@needs_go
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_go_and_python_print_identical_lines(tmp_path, case):
    (py_out, py_code), (go_out, go_code) = both(tmp_path, case)
    assert go_out == py_out
    assert go_code == py_code
    bad = sum(1 for x in case["expect"] if not x["ok"])
    assert py_out.endswith(f"verdicts: {len(case['expect']) - bad} ok, {bad} failed\n")
    assert py_code == (1 if bad else 0)


# ── 3. Edges the fixtures leave open ────────────────────────────────────────
def contract(fields, quality=None, max_age_days=30):
    return {
        "info": {"id": "edge.v1", "version": 1, "status": "active", "owner": "data", "description": "edges"},
        "schema": {"fields": fields},
        "quality": quality or {},
        "servicing": {"freshness": {"max_age_days": max_age_days}},
        "consumers": [{"name": "c", "version": "1", "reads": "x.jsonl"}],
    }


def fld(name, type_="string", required=False, **kw):
    return {"name": name, "type": type_, "required": required, "meaning": name, **kw}


NUMBERS = contract([fld("n", "integer", True), fld("x", "number", enum=[1, 2.5, 1e16]), fld("o", "object"), fld("a", "array")],
                   {"unique": ["o"], "row_count": {"min": 0, "max": 9}}, max_age_days=7.25)
BAD = '{"info": {"id": "x", "version": 1, "status": "active", "owner": "o", "description": "d"}, ' \
      '"schema": {"fields": [{"name": "id", "type": "string", "required": true, "meaning": "m", "pattern": %s}]}, ' \
      '"quality": %s, "servicing": {"freshness": {"max_age_days": 3}}, "consumers": []}'
EDGES = [
    {"name": "numbers-and-key-order", "contract": NUMBERS, "age_days": 1e6, "dataset":
        '{"n": 1.0, "x": 1e16, "o": {"b": 1, "a": 2}, "a": [1, 2.50, -0]}\n'
        '{"n": -0, "x": 2.5000, "o": {"a": 2, "b": 1}, "a": []}\n'
        '{"n": 1e400, "x": NaN, "o": {"b": 1, "a": 2}, "a": {}}\n'
        '{"n": 123456789012345678901234567890, "x": -Infinity, "o": {"a": 1, "a": 3}}\n'},
    {"name": "strings-and-line-breaks", "age_days": 0.00001,
     "contract": contract([fld("s", pattern="[a-zé]+", enum=["abc", "é", "a\"b"])], {"enum_coverage": ["s"]}),
     "dataset": '{"s": "abc"}\r\n{"s": "a\\u2028b"}\n{"s": "x y"}\n\x1c\n{"s": "\\ud83d\\ude00\\t"}\n {"s": "é"} \n'},
    {"name": "whole-forms", "contract": contract([fld("s")]), "age_days": None, "dataset": '\x1c [{"s": 1}, null, [2]] 　'},
    {"name": "scalar-document", "contract": contract([fld("s")]), "age_days": 3, "dataset": '"just text"'},
    {"name": "broken-array", "contract": contract([fld("s")]), "age_days": None, "dataset": '[{"s": 1},'},
    {"name": "lookahead-pattern", "contract": BAD % ('"(?!x)y"', "{}"), "age_days": None, "rows": [{"id": "y"}]},
    {"name": "non-string-pattern", "contract": BAD % ("3", "{}"), "age_days": None, "rows": []},
    {"name": "unknown-quality-name", "contract": BAD % ('"y"', '{"unique": ["it\'s"]}'), "age_days": None, "rows": []},
    {"name": "row-count-min-above-max", "contract": BAD % ('"y"', '{"row_count": {"max": 1, "min": 2}}'),
     "age_days": None, "rows": []},
]


@needs_go
@pytest.mark.parametrize("case", EDGES, ids=[c["name"] for c in EDGES])
def test_go_and_python_agree_on_edges(tmp_path, case):
    (py_out, py_code), (go_out, go_code) = both(tmp_path, case)
    assert go_out == py_out
    assert go_code == py_code


@needs_go
def test_missing_dataset_and_missing_contract(tmp_path):
    case = {"contract": contract([fld("s")]), "rows": [], "age_days": None}
    both(tmp_path, case)  # writes c.yaml and its export
    py = run_python(str(tmp_path / "c.yaml"), str(tmp_path / "nope.jsonl"))
    go = run_go(str(tmp_path / "c.contract.json"), str(tmp_path / "nope.jsonl"))
    assert go == py and "FAIL parse -: dataset file not found" in go[0]
    go = run_go(str(tmp_path / "gone.contract.json"), str(tmp_path / "nope.jsonl"))
    assert go == ("FAIL contract -: contract file not found: gone.contract.json\nverdicts: 0 ok, 1 failed\n", 1)
