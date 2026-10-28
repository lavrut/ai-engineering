#!/usr/bin/env python3
"""
gen_cases — writes data/fixtures/cases.jsonl, the cases both validators must reproduce.

Each line: {"name", "contract", "rows" | "dataset", "age_days", "expect"}.
`expect` is written out by hand below, not computed by contract.py, so the file
is a second opinion on the validator and the Go twin has something to match.
A contract that fails to load expects one `contract` verdict, as the CLI prints.
`dataset` (raw text) replaces `rows` for the cases that test reading the file.

Run (from the repo root):
    .venv-data/bin/python data/fixtures/gen_cases.py            # rewrite cases.jsonl
    .venv-data/bin/python data/fixtures/gen_cases.py --check    # fail if it drifted
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "cases.jsonl"


# ── 1. Building blocks ──────────────────────────────────────────────────────
def field(name, type_="string", required=False, **extra):
    return {"name": name, "type": type_, "required": required, "meaning": f"the {name}", **extra}


def contract(fields, quality=None, max_age_days=30):
    return {
        "info": {"id": "pets.v1", "version": "1", "status": "active", "owner": "data", "description": "pets"},
        "schema": {"fields": fields},
        "quality": quality or {},
        "servicing": {"freshness": {"max_age_days": max_age_days}},
        "consumers": [{"name": "shop", "version": "1", "reads": "pets.jsonl"}],
    }


def v(rule, fld, ok, detail):
    return {"rule": rule, "field": fld, "ok": ok, "detail": detail}


ARABIC_THREE, NBSP, VTAB = chr(0x663), chr(0xA0), chr(0x0B)  # Unicode digit, no-break space, \v
FRESH = v("freshness", None, True, "unknown: no git date")
ID = [field("id", "integer", True)]


def case(name, con, expect, rows=None, dataset=None, age_days=None):
    body = {"name": name, "contract": con, "age_days": age_days, "expect": expect}
    body["rows"] = rows if dataset is None else None
    if dataset is not None:
        body["dataset"] = dataset
    return body


# ── 2. The cases ────────────────────────────────────────────────────────────
def cases() -> list[dict]:
    return [
        case("passing-dataset",
             contract([field("id", "integer", True), field("kind", enum=["cat", "dog"]), field("code", pattern="^[A-Z]{2}[0-9]+$")],
                      {"row_count": {"min": 1, "max": 5}, "unique": ["id"], "enum_coverage": ["kind"]}),
             [v("enum", "kind", True, "all values in enum"),
              v("enum_coverage", "kind", True, "all 2 values appear"),
              FRESH,
              v("pattern", "code", True, "all values match"),
              v("required", "id", True, "present in 2 rows"),
              v("row_count", None, True, "2 rows (min 1, max 5)"),
              v("type", "code", True, "all values are string"),
              v("type", "id", True, "all values are integer"),
              v("type", "kind", True, "all values are string"),
              v("unique", "id", True, "2 rows, no duplicates")],
             rows=[{"id": 1, "kind": "cat", "code": "AB1"}, {"id": 2, "kind": "dog", "code": "CD22"}]),
        case("required-missing", contract(ID),
             [FRESH, v("required", "id", False, "row 2: missing (+1 more)"), v("type", "id", True, "all values are integer")],
             rows=[{"id": 1}, {}, {"id": None}]),
        case("type-mismatch", contract([field("n", "integer"), field("m", "number"), field("b", "boolean")]),
             [v("freshness", None, True, "unknown: no git date"),
              v("type", "b", False, "row 1: expected boolean, got integer"),
              v("type", "m", True, "all values are number"),
              v("type", "n", False, "row 2: expected integer, got string (+1 more)")],
             rows=[{"n": 1, "m": 2, "b": 1}, {"n": "x", "m": 2.5}, {"n": 2.5}]),
        case("enum-violation", contract([field("kind", enum=["cat", "dog"])]),
             [v("enum", "kind", False, 'row 2: "bird" not in enum ["cat", "dog"]'),
              FRESH, v("type", "kind", True, "all values are string")],
             rows=[{"kind": "cat"}, {"kind": "bird"}]),
        case("pattern-violation", contract([field("code", pattern="^[A-Z]{2}[0-9]+$")]),
             [FRESH, v("pattern", "code", False, 'row 1: "ab1" does not match ^[A-Z]{2}[0-9]+$'),
              v("type", "code", True, "all values are string")],
             rows=[{"code": "ab1"}, {"code": "AB1"}]),
        case("extra-field-is-ok", contract(ID),
             [v("extra_field", "color", True, "not in schema (2 rows)"),
              v("extra_field", "size", True, "not in schema (1 row)"),
              FRESH, v("required", "id", True, "present in 2 rows"), v("type", "id", True, "all values are integer")],
             rows=[{"id": 1, "color": "red", "size": 3}, {"id": 2, "color": "blue"}]),
        case("empty-file",
             contract([field("id", "integer", True), field("kind", enum=["cat", "dog"]), field("code", pattern="^[A-Z]+$")],
                      {"row_count": {"min": 1}, "unique": ["id"], "enum_coverage": ["kind"]}),
             [v("enum", "kind", True, "no rows"), v("enum_coverage", "kind", True, "no rows"), FRESH,
              v("pattern", "code", True, "no rows"), v("required", "id", True, "no rows"),
              v("row_count", None, False, "0 rows, below min 1"),
              v("type", "code", True, "no rows"), v("type", "id", True, "no rows"), v("type", "kind", True, "no rows"),
              v("unique", "id", True, "no rows")],
             dataset=""),
        case("anchored-pattern-is-a-full-match", contract([field("code", pattern="^T-[0-9]{4}$"), field("word", pattern="[a-z]+")]),
             [FRESH, v("pattern", "code", False, 'row 2: "T-0001\\n" does not match ^T-[0-9]{4}$ (+1 more)'),
              v("pattern", "word", False, 'row 1: "abc1" does not match [a-z]+'),
              v("type", "code", True, "all values are string"), v("type", "word", True, "all values are string")],
             rows=[{"code": "T-0001", "word": "abc1"}, {"code": "T-0001\n", "word": "abc"}, {"code": "x"}]),
        # ruling D7: \d and \s are ASCII in both languages; a{,2} is refused by both
        case("digit-class-is-ascii", contract([field("code", pattern="\\d+")]),
             [FRESH, v("pattern", "code", False, f'row 1: "{ARABIC_THREE}" does not match \\d+'),
              v("type", "code", True, "all values are string")],
             rows=[{"code": ARABIC_THREE}, {"code": "42"}]),
        case("space-class-is-ascii", contract([field("s", pattern="\\s")]),
             [FRESH, v("pattern", "s", False, f'row 1: "{NBSP}" does not match \\s (+1 more)'),
              v("type", "s", True, "all values are string")],
             rows=[{"s": NBSP}, {"s": VTAB}, {"s": " "}, {"s": "\t"}]),
        case("open-repeat-is-an-invalid-pattern", contract([field("code", pattern="a{,2}")]),
             [v("contract", None, False, "schema.fields[0].pattern: invalid pattern: {,2}")],
             rows=[{"code": "aa"}]),
        case("row-count-above-max", contract(ID, {"row_count": {"max": 1}}),
             [FRESH, v("required", "id", True, "present in 2 rows"),
              v("row_count", None, False, "2 rows, above max 1"), v("type", "id", True, "all values are integer")],
             rows=[{"id": 1}, {"id": 2}]),
        case("duplicate-key", contract(ID, {"unique": ["id"]}),
             [FRESH, v("required", "id", True, "present in 3 rows"), v("type", "id", True, "all values are integer"),
              v("unique", "id", False, "row 3: duplicate of row 1")],
             rows=[{"id": 1}, {"id": 2}, {"id": 1}]),
        case("enum-not-covered", contract([field("kind", enum=["cat", "dog", "fish"])], {"enum_coverage": ["kind"]}),
             [v("enum", "kind", True, "all values in enum"),
              v("enum_coverage", "kind", False, 'missing values: "dog", "fish"'),
              FRESH, v("type", "kind", True, "all values are string")],
             rows=[{"kind": "cat"}]),
        case("stale-file", contract(ID, max_age_days=10),
             [v("freshness", None, False, "11.5 days old, over max 10"),
              v("required", "id", True, "present in 1 row"), v("type", "id", True, "all values are integer")],
             rows=[{"id": 1}], age_days=11.5),
        case("fresh-file", contract(ID, max_age_days=10),
             [v("freshness", None, True, "10 days old (max 10)"),
              v("required", "id", True, "present in 1 row"), v("type", "id", True, "all values are integer")],
             rows=[{"id": 1}], age_days=10),
        case("bad-jsonl-line", contract(ID),
             [FRESH, v("parse", None, False, "line 2: invalid JSON (+1 more)"),
              v("required", "id", True, "present in 2 rows"), v("type", "id", True, "all values are integer")],
             dataset='{"id": 1}\n{oops\n\n[3]\n{"id": 2}\n'),
        case("form-json-array", contract(ID),
             [FRESH, v("required", "id", True, "present in 2 rows"), v("type", "id", True, "all values are integer")],
             dataset='[{"id": 1},\n {"id": 2}]'),
        case("form-single-object", contract(ID),
             [FRESH, v("required", "id", True, "present in 1 row"), v("type", "id", True, "all values are integer")],
             dataset='{\n  "id": 7\n}\n'),
        case("form-jsonl", contract(ID),
             [FRESH, v("required", "id", True, "present in 2 rows"), v("type", "id", True, "all values are integer")],
             dataset='{"id": 1}\n{"id": 2}\n'),
        case("array-with-non-object", contract(ID),
             [FRESH, v("parse", None, False, "item 2: expected object, got string"),
              v("required", "id", True, "present in 1 row"), v("type", "id", True, "all values are integer")],
             dataset='[{"id": 1}, "x"]'),
    ]


# ── 3. Writing and checking ─────────────────────────────────────────────────
def render() -> str:
    return "".join(json.dumps(c, sort_keys=True, ensure_ascii=False) + "\n" for c in cases())


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = render()
    if "--check" in argv:
        if OUT.exists() and OUT.read_text(encoding="utf-8") == text:
            print(f"cases.jsonl current ({len(cases())} cases)")
            return 0
        print("cases.jsonl is stale: run gen_cases.py")
        return 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote cases.jsonl ({len(cases())} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
