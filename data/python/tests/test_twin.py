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
CASES = [json.loads(line) for line in (HERE.parents[1] / "fixtures" / "cases.jsonl").read_text(encoding="utf-8").split("\n")[:-1]]

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")


# ── 1. Running both ─────────────────────────────────────────────────────────
def run_python(*args: str) -> tuple[bytes, int]:
    """Raw stdout bytes: no decoding or newline translation hides a difference."""
    p = subprocess.run([sys.executable, str(CONTRACT_PY), *args], capture_output=True, cwd=ROOT)
    return p.stdout, p.returncode


GO_BIN: list[Path] = []


@pytest.fixture(scope="session", autouse=True)
def go_binary(tmp_path_factory):
    """Build once (from the Go build cache): `go run` would report every non-zero exit as 1."""
    if shutil.which("go") is None:
        return
    out = tmp_path_factory.mktemp("go") / "validate"
    subprocess.run(["go", "build", "-o", str(out), "./validate"], cwd=GO_DIR, check=True)
    GO_BIN[:] = [out]


def run_go(*args: str) -> tuple[bytes, int]:
    p = subprocess.run([str(GO_BIN[0]), *args], capture_output=True, cwd=GO_DIR)
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


def both(tmp: Path, case: dict) -> tuple[tuple[bytes, int], tuple[bytes, int]]:
    """Python on the YAML, Go on its export.

    A contract that will not export (it fails to load) is given to Go as the same
    document in JSON, so both sides judge the same contract; a string contract is
    written as is to both files.
    """
    src = tmp / "c.yaml"
    if isinstance(case["contract"], str):
        src.write_text(case["contract"], encoding="utf-8")
        shutil.copy(src, tmp / "c.contract.json")
    else:
        src.write_text(yaml.safe_dump(case["contract"], allow_unicode=True, sort_keys=False), encoding="utf-8")
        out, code = run_python("--export", str(src))
        if code != 0:
            assert out.startswith(b"FAIL contract -: "), out
            (tmp / "c.contract.json").write_text(json.dumps(case["contract"], ensure_ascii=False), encoding="utf-8")
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
    assert py_out.endswith(f"verdicts: {len(case['expect']) - bad} ok, {bad} failed\n".encode())
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
def pat_case(name, pattern, values):
    return {"name": name, "contract": contract([fld("s", pattern=pattern)]), "age_days": None,
            "rows": [{"s": x} for x in values]}


D7_PROBES = [
    pat_case("d7-digit", r"\d+", [chr(0x663), "42"]),
    pat_case("d7-word", r"\w+", ["é", "a_1"]),
    pat_case("d7-space", r"\s", [chr(0xA0), chr(0x0B), " "]),
    pat_case("d7-negated-in-class", r"[\S\D]+", [chr(0x0B), chr(0x663), " 1"]),
    pat_case("d7-open-repeat", "a{,2}", ["aa"]),
    pat_case("d7-unicode-escape", "\\" + "u0041", ["A"]),
    pat_case("d7-named-char", r"\N{LATIN SMALL LETTER A}", ["a"]),
    pat_case("d7-comment", "(?#c)a", ["a"]),
    pat_case("d7-ascii-flag", "(?a)a", ["a"]),
    pat_case("d7-backspace-class", r"[\b]", ["a"]),
    pat_case("d7-posix-class", "[[:alpha:]]+", ["ab"]),
    pat_case("d7-inner-dollar", "a$\n", ["a\n"]),
    pat_case("d7-lazy", "a+?", ["aa"]),
    pat_case("d7-full-match-anchors", "^[a-c-]+$", ["ab-c", "abd"]),
    {"name": "int-over-4300-digits", "contract": contract([fld("n", "integer")]), "age_days": None,
     "dataset": '{"n": ' + "7" * 5000 + '}\n{"n": 1}\n'},
]
EDGES = D7_PROBES + [
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
    # ruling D16: CPython's recursion limit and Go's U+FFFD no longer decide what a row says
    {"name": "nesting-10000-deep", "contract": contract([fld("s")]), "age_days": None,
     "dataset": '{"s": "a"}\n{"s": ' + "[" * 10000 + "]" * 10000 + '}\n'},
    {"name": "nesting-10000-deep-whole", "contract": contract([fld("s")]), "age_days": None,
     "dataset": '{\n "s": ' + "{\"a\": " * 10000 + "1" + "}" * 10000 + "\n}"},
    {"name": "lone-surrogates", "contract": contract([fld("s")]), "age_days": None,
     "dataset": '{"s": "\\ud800\\u0041"}\n{"s": "\\udfff"}\n{"s": "\\ud800"\n["\\ud800"]\n'},
    {"name": "lone-surrogate-in-array", "contract": contract([fld("s")]), "age_days": None,
     "dataset": '[{"s": "ok"},\n {"s": "\\ud800"}]'},
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
    assert go == py and b"FAIL parse -: dataset file not found" in go[0]
    go = run_go(str(tmp_path / "gone.contract.json"), str(tmp_path / "nope.jsonl"))
    assert go == (b"FAIL contract -: contract file not found: gone.contract.json\nverdicts: 0 ok, 1 failed\n", 1)


@needs_go
def test_unparseable_contract_file_reads_the_same(tmp_path):
    (tmp_path / "c.yaml").write_text("info: [unclosed", encoding="utf-8")
    (tmp_path / "c.contract.json").write_text('{"info": [', encoding="utf-8")
    (tmp_path / "d.jsonl").write_text("", encoding="utf-8")
    py = run_python(str(tmp_path / "c.yaml"), str(tmp_path / "d.jsonl"))
    go = run_go(str(tmp_path / "c.contract.json"), str(tmp_path / "d.jsonl"))
    assert py[0].replace(b"c.yaml", b"c.contract.json") == go[0] and py[1] == go[1] == 1
    assert go[0].startswith(b"FAIL contract -: c.contract.json is not a valid contract file\n")


@needs_go
@pytest.mark.parametrize("age", ["-3", chr(0x663), "1_0"])
def test_bad_age_days_is_a_usage_error_in_both(tmp_path, age):
    case = {"contract": contract([fld("s")]), "rows": [], "age_days": None}
    both(tmp_path, case)
    args = ["--age-days", age]
    assert run_python(str(tmp_path / "c.yaml"), str(tmp_path / "data.jsonl"), *args)[1] == 2
    assert run_go(str(tmp_path / "c.contract.json"), str(tmp_path / "data.jsonl"), *args)[1] == 2
