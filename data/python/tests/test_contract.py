"""Tests for contract.py: the contract format and the Python validator.

The verdict dicts are asserted whole, because the Go twin must reproduce
them exactly. Row numbers in details are 1-based.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "fixtures"))

import contract as C  # noqa: E402
import gen_cases  # noqa: E402


def make(fields, quality=None, freshness=None):
    return {
        "info": {"id": "t.v1", "version": "1", "status": "active", "owner": "data", "description": "t"},
        "schema": {"fields": fields},
        "quality": quality or {},
        "servicing": {"freshness": {"max_age_days": freshness if freshness is not None else 30}},
        "consumers": [{"name": "c", "version": "1", "reads": "x.jsonl"}],
    }


def f(name, type_="string", required=False, **kw):
    return {"name": name, "type": type_, "required": required, "meaning": name, **kw}


def v(rule, field, ok, detail):
    return {"rule": rule, "field": field, "ok": ok, "detail": detail}


def test_required_field_missing_fails():
    out = C.validate(make([f("id", "integer", True)]), [{"id": 1}, {}, {"id": None}])
    assert v("required", "id", False, "row 2: missing (+1 more)") in out
    out = C.validate(make([f("id", "integer", True)]), [{"id": 1}])
    assert v("required", "id", True, "present in 1 row") in out


def test_type_mismatch_fails_with_row_index():
    out = C.validate(make([f("n", "integer")]), [{"n": 1}, {"n": 2}, {"n": "x"}])
    assert v("type", "n", False, "row 3: expected integer, got string") in out
    # a bool is not an integer, an integer is a number
    out = C.validate(make([f("n", "integer"), f("m", "number")]), [{"n": True, "m": 2}])
    assert v("type", "n", False, "row 1: expected integer, got boolean") in out
    assert v("type", "m", True, "all values are number") in out


def test_enum_and_pattern():
    fields = [f("s", enum=["a", "b"]), f("c", pattern="^[A-Z]{2}$")]
    out = C.validate(make(fields), [{"s": "a", "c": "AB"}, {"s": "z", "c": "ab"}])
    assert v("enum", "s", False, 'row 2: "z" not in enum ["a", "b"]') in out
    assert v("pattern", "c", False, 'row 2: "ab" does not match ^[A-Z]{2}$') in out
    out = C.validate(make(fields), [{"s": "a", "c": "AB"}])
    assert v("enum", "s", True, "all values in enum") in out
    assert v("pattern", "c", True, "all values match") in out


def test_extra_field_is_ok_warning():
    out = C.validate(make([f("a")]), [{"a": "x", "zz": 1}, {"zz": 2}])
    assert v("extra_field", "zz", True, "not in schema (2 rows)") in out
    assert all(x["ok"] for x in out if x["rule"] == "extra_field")


def test_row_count_bounds_and_empty_file(tmp_path):
    c = make([f("a")], {"row_count": {"min": 1, "max": 2}})
    assert v("row_count", None, False, "3 rows, above max 2") in C.validate(c, [{}, {}, {}])
    assert v("row_count", None, True, "2 rows (min 1, max 2)") in C.validate(c, [{}, {}])
    empty = tmp_path / "e.jsonl"
    empty.write_text("")
    assert C.read_rows(empty) == []
    assert v("row_count", None, False, "0 rows, below min 1") in C.validate(c, C.read_rows(empty))


def test_empty_dataset_only_row_count_fails():
    c = make([f("id", "integer", True, enum=[1, 2]), f("c", pattern="^a$")],
             {"row_count": {"min": 1}, "unique": ["id"], "enum_coverage": ["id"]})
    out = C.validate(c, [])
    assert [x["rule"] for x in out if not x["ok"]] == ["row_count"]
    for x in out:
        if x["rule"] not in ("row_count", "freshness"):
            assert x["detail"] == "no rows", x
    assert v("enum_coverage", "id", True, "no rows") in out
    assert v("unique", "id", True, "no rows") in out


def test_unique_key():
    c = make([f("id", "integer")], {"unique": ["id"]})
    assert v("unique", "id", False, "row 3: duplicate of row 1") in C.validate(c, [{"id": 1}, {"id": 2}, {"id": 1}])
    assert v("unique", "id", True, "2 rows, no duplicates") in C.validate(c, [{"id": 1}, {"id": 2}])


def test_unique_is_one_composite_key():
    """Ruling D8: the listed fields form one key; each field may repeat on its own."""
    c = make([f("a"), f("b", "integer")], {"unique": ["a", "b"]})
    out = C.validate(c, [{"a": "x", "b": 1}, {"a": "x", "b": 2}, {"a": "y", "b": 1}])
    assert [x for x in out if x["rule"] == "unique"] == [v("unique", "a,b", True, "3 rows, no duplicates")]
    out = C.validate(c, [{"a": "x", "b": 1}, {"a": "x"}, {"b": 1}, {"a": "x", "b": 1}])
    assert v("unique", "a,b", False, "row 2: missing key field b (+2 more)") in out
    out = C.validate(make([f("id", "integer")], {"unique": ["id"]}), [{"id": 1}, {}])
    assert v("unique", "id", False, "row 2: missing key field id") in out


def test_enum_coverage():
    c = make([f("s", enum=["a", "b", "c"])], {"enum_coverage": ["s"]})
    assert v("enum_coverage", "s", False, 'missing values: "b", "c"') in C.validate(c, [{"s": "a"}])
    rows = [{"s": "a"}, {"s": "b"}, {"s": "c"}]
    assert v("enum_coverage", "s", True, "all 3 values appear") in C.validate(c, rows)


def test_freshness_unknown_when_no_age():
    out = C.validate(make([f("a")]), [{}])
    assert v("freshness", None, True, "unknown: no git date") in out


def test_freshness_fails_past_max_age():
    c = make([f("a")], freshness=10)
    assert v("freshness", None, False, "11 days old, over max 10") in C.validate(c, [{}], file_age_days=11)
    assert v("freshness", None, True, "10 days old (max 10)") in C.validate(c, [{}], file_age_days=10)
    assert v("freshness", None, True, "2.5 days old (max 10)") in C.validate(c, [{}], file_age_days=2.5)


def test_verdicts_sorted_and_exact_shape():
    fields = [f("b", "integer", True), f("a", "string", True, enum=["x"])]
    c = make(fields, {"row_count": {"min": 0}})
    out = C.validate(c, [{"a": "x", "b": 1, "q": 0}])
    assert [(x["rule"], x["field"]) for x in out] == [
        ("enum", "a"), ("extra_field", "q"), ("freshness", None), ("required", "a"),
        ("required", "b"), ("row_count", None), ("type", "a"), ("type", "b"),
    ]
    for x in out:
        assert list(x) == ["rule", "field", "ok", "detail"]
        assert isinstance(x["ok"], bool) and isinstance(x["detail"], str)
    assert out == C.validate(c, [{"a": "x", "b": 1, "q": 0}])


GOOD_YAML = """
info: {id: t.v1, version: 1, status: active, owner: data, description: d}
schema:
  fields:
    - {name: id, type: integer, required: true, meaning: the id}
quality: {row_count: {min: 1}}
servicing: {freshness: {max_age_days: 30}}
consumers:
  - {name: c, version: 1, reads: x.jsonl}
"""


def write_contract(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return p


def test_load_contract_ok_and_keeps_dates_as_strings(tmp_path):
    p = write_contract(tmp_path, GOOD_YAML.replace("status: active", "status: deprecated, sunset: 2026-12-01"))
    c = C.load_contract(p)
    assert set(c) == {"info", "schema", "quality", "servicing", "consumers"}
    assert c["info"]["sunset"] == "2026-12-01"
    assert c["info"]["version"] == "1"


@pytest.mark.parametrize("old,new,needle", [
    ("quality: {row_count: {min: 1}}\n", "", "quality"),
    ("type: integer", "type: int", "schema.fields[0].type"),
    ("owner: data, ", "", "info.owner"),
    ("max_age_days: 30", "max_age_days: soon", "servicing.freshness.max_age_days"),
    ("row_count: {min: 1}", "median: 3", "quality.median"),
    ("{name: c, version: 1, reads: x.jsonl}", "{name: c}", "consumers[0]"),
    ("status: active", "status: retired", "info.status"),
    ("status: active", "status: deprecated", "info.sunset"),
    ("{name: id, type: integer, required: true, meaning: the id}",
     "{name: id, type: string, required: true, meaning: x, pattern: 2026}", "schema.fields[0].pattern must be a string"),
    ("{name: id, type: integer, required: true, meaning: the id}",
     "{name: id, type: string, required: true, meaning: x, enum: open}", "schema.fields[0].enum must be a non-empty list"),
    ("{name: id, type: integer, required: true, meaning: the id}",
     "{name: id, type: integer, required: true, meaning: x, enum: ['1']}", "schema.fields[0].enum value"),
    ("row_count: {min: 1}", "row_count: {min: 5, max: 2}", "quality.row_count.min"),
])

def test_bad_contract_names_section(tmp_path, old, new, needle):
    p = write_contract(tmp_path, GOOD_YAML.replace(old, new))
    with pytest.raises(C.ContractError) as e:
        C.load_contract(p)
    assert needle in str(e.value)


def test_bad_contract_missing_file_and_bad_yaml(tmp_path):
    with pytest.raises(C.ContractError, match="contract file not found"):
        C.load_contract(tmp_path / "nope.yaml")
    with pytest.raises(C.ContractError, match="^c.yaml is not a valid contract file$"):
        C.load_contract(write_contract(tmp_path, "info: [unclosed"))


def test_bad_jsonl_line_is_parse_verdict(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text('{"id": 1}\n{oops\n{"id": 2}\n')
    rows = C.read_rows(p)
    assert rows == [{"id": 1}, {"id": 2}]
    out = C.validate(make([f("id", "integer", True)]), rows)
    assert v("parse", None, False, "line 2: invalid JSON") in out
    assert [x["rule"] for x in out].index("parse") < [x["rule"] for x in out].index("required")


def test_read_rows_forms_and_missing_file(tmp_path):
    (tmp_path / "a.json").write_text('[{"a": 1}, {"a": 2}]')
    (tmp_path / "o.json").write_text('{\n "a": 1\n}\n')
    assert C.read_rows(tmp_path / "a.json") == [{"a": 1}, {"a": 2}]
    assert C.read_rows(tmp_path / "o.json") == [{"a": 1}]
    out = C.validate(make([f("a")]), C.read_rows(tmp_path / "missing.jsonl"))
    assert v("parse", None, False, "dataset file not found") in out
    (tmp_path / "x.json").write_text("[1, 2]")
    assert v("parse", None, False, "item 1: expected object, got integer (+1 more)") in C.validate(
        make([f("a")]), C.read_rows(tmp_path / "x.json"))


def test_cli_prints_lines_and_exit_code(tmp_path, capsys):
    cp = write_contract(tmp_path, GOOD_YAML)
    ok = tmp_path / "ok.jsonl"
    ok.write_text('{"id": 1}\n')
    assert C.main([str(cp), str(ok), "--age-days", "3"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "OK  freshness -: 3 days old (max 30)",
        "OK  required id: present in 1 row",
        "OK  row_count -: 1 row (min 1)",
        "OK  type id: all values are integer",
        "verdicts: 4 ok, 0 failed",
    ]
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": "x"}\n')
    assert C.main([str(cp), str(bad)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert "FAIL type id: row 1: expected integer, got string" in out
    assert out[-1] == "verdicts: 3 ok, 1 failed"
    # a malformed contract is a failed verdict, never a traceback
    assert C.main([str(write_contract(tmp_path, "info: 3")), str(ok)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("FAIL contract -: ")
    assert out[-1] == "verdicts: 0 ok, 1 failed"


def test_export_writes_sorted_json_and_check_detects_stale(tmp_path, capsys):
    cp = write_contract(tmp_path, GOOD_YAML)
    assert C.main(["--export", str(cp)]) == 0
    out = tmp_path / "c.contract.json"
    text = out.read_text()
    assert text.endswith("}\n") and text.startswith('{\n "consumers"')
    assert json.loads(text) == C.load_contract(cp)
    d = tmp_path / "contracts"
    d.mkdir()
    (d / "a.yaml").write_text(GOOD_YAML)
    assert C.export_check(d) == 1  # no export yet: stale
    assert "a.contract.json" in capsys.readouterr().out
    C.export_contract(d / "a.yaml")
    assert C.export_check(d) == 0
    (d / "a.yaml").write_text(GOOD_YAML.replace("min: 1", "min: 2"))
    assert C.export_check(d) == 1
    assert "stale" in capsys.readouterr().out


def test_cases_file_matches_generator():
    assert gen_cases.main(["--check"]) == 0


def test_every_fixture_case_reproduces():
    lines = (HERE.parents[1] / "fixtures" / "cases.jsonl").read_text().splitlines()
    assert len(lines) >= 12
    names = set()
    for line in lines:
        case = json.loads(line)
        names.add(case["name"])
        rows = C.parse_rows(case["dataset"]) if "dataset" in case else case["rows"]
        try:  # a contract that fails to load is one contract verdict, as on the command line
            con = C.check_contract(copy.deepcopy(case["contract"]))
        except C.ContractError as e:
            got = [v("contract", None, False, str(e))]
        else:
            got = C.validate(con, rows, file_age_days=case["age_days"])
        assert got == case["expect"], case["name"]
    assert len(names) == len(lines)


# Ruling D7: every allowed form, with its tokens and engine form; Go's validate_test.go has the same table.
ALLOWED = [
    ("abc", ["a", "b", "c"], "abc"),
    ("é.", ["é", "."], "é."),
    (r"\.\*\+\?\(\)\[\]\{\}\|\^\$\\\-", [r"\.", r"\*", r"\+", r"\?", r"\(", r"\)", r"\[", r"\]", r"\{", r"\}",
                                      r"\|", r"\^", r"\$", r"\\", r"\-"], r"\.\*\+\?\(\)\[\]\{\}\|\^\$\\\-"),
    (r"\d\D\w\W\s\S", [r"\d", r"\D", r"\w", r"\W", r"\s", r"\S"],
     r"[0-9][^0-9][0-9A-Za-z_][^0-9A-Za-z_][ \t\n\r\f][^ \t\n\r\f]"),
    ("[abc]", ["[abc]"], "[abc]"),
    ("[^a-z]", ["[^a-z]"], "[^a-z]"),
    (r"[\d\w\s_]", [r"[\d\w\s_]"], r"[0-90-9A-Za-z_ \t\n\r\f_]"),
    (r"[\D]", [r"[\D]"], r"[\x00-\x2f\x3a-\U0010ffff]"),
    (r"[\W\S]", [r"[\W\S]"], r"[\x00-\x2f\x3a-\x40\x5b-\x5e\x60\x7b-\U0010ffff\x00-\x08\x0b\x0e-\x1f\x21-\U0010ffff]"),
    (r"[-a\]^]", [r"[-a\]^]"], r"[\-a\]\^]"),
    ("(a)(?:b)", ["(", "a", ")", "(?:", "b", ")"], "(a)(?:b)"),
    ("a|b|", ["a", "|", "b", "|"], "a|b|"),
    ("a*b+c?", ["a", "*", "b", "+", "c", "?"], "a*b+c?"),
    ("a{2}b{2,}c{2,5}", ["a", "{2}", "b", "{2,}", "c", "{2,5}"], "a{2}b{2,}c{2,5}"),
    ("a{1000}b{0,1000}", ["a", "{1000}", "b", "{0,1000}"], "a{1000}b{0,1000}"),
    ("(a{2})+(b){3}", ["(", "a", "{2}", ")", "+", "(", "b", ")", "{3}"], "(a{2})+(b){3}"),
    ("^ab$", ["^", "a", "b", "$"], "ab"),
    ("", [], ""),
]
REJECTED = [
    ("(?i)abc", "(?i"), ("(?x)a", "(?x"), ("(?a)a", "(?a"), ("(?=x)", "(?="), ("(?!x)", "(?!"),
    ("(?<=A)B", "(?<"), ("(?P<n>a)", "(?P"), ("(?#c)", "(?#"), ("(?>a)", "(?>"),
    (r"(a)\1", r"\1"), ("\\" + "u0041", r"\u"), (r"\N{LATIN SMALL LETTER A}", r"\N"), (r"\p{L}+", r"\p"),
    (r"\Aab", r"\A"), (r"ab\Z", r"\Z"), (r"a\b", r"\b"), (r"a\B", r"\B"), (r"\n", r"\n"), ("a\\", "\\"),
    ("[[:alpha:]]", "["), (r"[\b]", r"\b"), ("[]", "[]"), ("[^]", "[^]"), ("[abc", "["), ("[z-a]", "z-a"),
    (r"[\d-z]", "-"), ("[a-c-e]", "-"),
    ("a{,2}", "{,2}"), ("a{x}", "{x}"), ("a{2", "{"), ("a{3,2}", "{3,2}"), ("a{1001}", "{1001}"),
    ("a{1001,}", "{1001,}"), ("a{2,1001}", "{2,1001}"), ("a{99999999999999999999}", "{99999999999999999999}"),
    # ruling D9: counted repeats stop at 1000 and never wrap a group that holds one
    ("(a{2}){3}", "{3}"), ("((a{2})b){2,}", "{2,}"), ("(?:x|(a{1000})){1000}", "{1000}"), ("((a){2}){2,5}", "{2,5}"),
    ("a*?", "*?"), ("a+?", "+?"), ("a??", "??"), ("a{2}?", "{2}?"), ("a++", "++"), ("a*+", "*+"), ("a?+", "?+"),
    ("*a", "*"), ("a**", "*"), ("(+)", "+"), ("a|*", "*"), ("^*", "*"),
    ("a^", "^"), ("a$b", "$"), ("(a$)", "$"), ("(a", "("), ("a)", ")"), ("a]", "]"), ("a}", "}"),
]


@pytest.mark.parametrize("pattern,tokens,engine", ALLOWED)
def test_allowed_pattern_tokens(pattern, tokens, engine):
    assert C.pattern_tokens(pattern) == tokens
    assert C.compile_pattern(pattern).pattern == engine


@pytest.mark.parametrize("pattern,token", REJECTED)
def test_rejected_pattern_names_the_token(pattern, token):
    with pytest.raises(C.ContractError) as e:
        C.pattern_tokens(pattern)
    assert str(e.value) == f"invalid pattern: {token}"


def test_rejected_pattern_in_a_contract(tmp_path):
    text = GOOD_YAML.replace("{name: id, type: integer, required: true, meaning: the id}",
                             "{name: id, type: string, required: true, meaning: x, pattern: 'a{,2}'}")
    with pytest.raises(C.ContractError) as e:
        C.load_contract(write_contract(tmp_path, text))
    assert str(e.value) == "schema.fields[0].pattern: invalid pattern: {,2}"


def test_classes_are_ascii():
    assert not C.compile_pattern(r"\d").fullmatch("٣")
    assert not C.compile_pattern(r"\w").fullmatch("é")
    assert not C.compile_pattern(r"\s").fullmatch(" ")
    assert not C.compile_pattern(r"\s").fullmatch("\v")
    assert C.compile_pattern(r"\S").fullmatch("\v")
    assert C.compile_pattern(r"[\S]").fullmatch("\v") and C.compile_pattern(r"[\D]").fullmatch("٣")


def test_pattern_is_a_full_match():
    c = make([f("c", pattern="^[A-Z]{2}$"), f("d", pattern="[a-z]+")])
    out = C.validate(c, [{"c": "AB\n", "d": "abc1"}])
    assert v("pattern", "c", False, 'row 1: "AB\\n" does not match ^[A-Z]{2}$') in out
    assert v("pattern", "d", False, 'row 1: "abc1" does not match [a-z]+') in out


def test_negative_age_days_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit):
        C.main(["a.yaml", "b.jsonl", "--age-days", "-3"])


@pytest.mark.parametrize("age", ["-3", "\u0663", "1_0"])
def test_bad_age_days_is_a_usage_error(age):
    with pytest.raises(SystemExit):
        C.main(["a.yaml", "b.jsonl", "--age-days", age])
