#!/usr/bin/env python3
"""
contract — a data contract format and its validator, in one file.

A data contract is a YAML file that says what a dataset promises its
consumers: its shape, its quality, how fresh it stays and who reads it. The
validator turns those promises into verdicts, so a broken promise is a
failed line, not a surprise downstream. The Go twin (data/go) reads the JSON
export of the same contract and must print the same lines.

Run (from the repo root):
    python3 -m venv .venv-data && .venv-data/bin/pip install -r data/requirements.txt
    .venv-data/bin/python data/python/contract.py <contract.yaml> <dataset> [--age-days N]
    .venv-data/bin/python data/python/contract.py --export <contract.yaml>
    .venv-data/bin/python data/python/contract.py --export --check
Free and offline: no model calls.

A contract has exactly five sections:
    info       id, version, status (active | deprecated), owner (a track), description, sunset,
               dataset (the path or glob, from the repo root, of the file the contract governs)
    schema     fields: name, type, required, meaning, and enum or pattern where it applies
               (a pattern is a full match in the allow-list grammar of ruling D7, written in
               data/fixtures/README.md; \\d \\w \\s are ASCII; anything else is refused)
    quality    row_count {min, max}, unique [fields] (one composite key, its verdict field
               the names joined by ","), enum_coverage [fields]
    servicing  freshness {max_age_days}
    consumers  name, version, reads
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# ── 1. Contract files ───────────────────────────────────────────────────────
SECTIONS = ("info", "schema", "quality", "servicing", "consumers")
FIELD_TYPES = ("string", "integer", "number", "boolean", "object", "array")
STATUSES = ("active", "deprecated")
QUALITY_RULES = ("row_count", "unique", "enum_coverage")
CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"
EXPORT_SUFFIX = ".contract.json"


class ContractError(Exception):
    """A malformed contract; the message names the section or field."""


class _Loader(yaml.SafeLoader):
    """SafeLoader that leaves 2026-12-01 a string: sunset dates are text, not objects."""


_Loader.yaml_implicit_resolvers = {
    key: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _version(x: Any, where: str) -> str:
    """A version is a major like 1 or a string like '1'; stored as a string."""
    if _is_int(x):
        return str(x)
    if isinstance(x, str) and x:
        return x
    raise ContractError(f"{where} must be a non-empty string or integer")


def _text(m: dict, key: str, where: str) -> str:
    if not isinstance(m.get(key), str) or not m[key]:
        raise ContractError(f"{where}.{key} must be a non-empty string")
    return m[key]


def _mapping(x: Any, where: str) -> dict:
    if not isinstance(x, dict):
        raise ContractError(f"{where} must be a mapping")
    return x


def _date(x: Any, where: str) -> None:
    if not isinstance(x, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", x):
        raise ContractError(f"{where} must be a date like 2026-12-01")


def _check_info(info: Any) -> None:
    info = _mapping(info, "info")
    _text(info, "id", "info")
    info["version"] = _version(info.get("version"), "info.version")
    if info.get("status") not in STATUSES:
        raise ContractError(f"info.status must be one of {', '.join(STATUSES)}")
    _text(info, "owner", "info")
    _text(info, "description", "info")
    if "sunset" in info:
        _date(info["sunset"], "info.sunset")
    elif info["status"] == "deprecated":
        raise ContractError("info.sunset is required when info.status is deprecated")
    for key in ("generated_at", "dataset"):
        if key in info:
            _text(info, key, "info")


# A pattern is checked against an allow-list grammar by the same small tokenizer in
# Python and Go (ruling D7), so a pattern either means the same thing in both engines
# or is refused by both. \d \w \s are rewritten to explicit ASCII classes, ^ and $ are
# dropped (the match is already full), and the rest is passed through unchanged.
# Ruling D9: counted repeats stop at 1000, and a counted repeat may not wrap a group
# that holds one, since RE2 refuses the product ((a{1000}){1000}) that Python accepts.
PATTERN_META = ".*+?()[]{}|^$\\-"
CLASS_ESCAPES = "dDwWsS"
_MAX = "\\U0010ffff"  # Go writes \x{10FFFF}
_CLASS_OUT = {"d": "[0-9]", "D": "[^0-9]", "w": "[0-9A-Za-z_]", "W": "[^0-9A-Za-z_]",
              "s": "[ \\t\\n\\r\\f]", "S": "[^ \\t\\n\\r\\f]"}
_CLASS_IN = {"d": "0-9", "w": "0-9A-Za-z_", "s": " \\t\\n\\r\\f",
             "D": "\\x00-\\x2f\\x3a-" + _MAX,
             "W": "\\x00-\\x2f\\x3a-\\x40\\x5b-\\x5e\\x60\\x7b-" + _MAX,
             "S": "\\x00-\\x08\\x0b\\x0e-\\x1f\\x21-" + _MAX}
_MAX_REPEAT = 1000


def _bad(tok: str):
    raise ContractError(f"invalid pattern: {tok}")


def _class_lit(c: str) -> str:
    return "\\" + c if c in "\\]^-[" else c


def _bracket(p: str, i: int) -> tuple[str, str, int]:
    """One bracket class from p[i] == '[': (source, engine form, index after it)."""
    j, neg = i + 1, ""
    if p[j:j + 1] == "^":
        j, neg = j + 1, "^"
    items = []  # (kind, char): kind is lit, esc (a class escape) or dash (an unescaped -)
    first = j
    while True:
        if j >= len(p):
            _bad("[")
        c = p[j]
        if c == "]":
            if j == first:
                _bad(p[i:j + 1])
            break
        if c == "[":
            _bad("[")
        if c == "\\":
            e = p[j + 1:j + 2]
            if e and e in CLASS_ESCAPES:
                items.append(("esc", e))
            elif e and e in PATTERN_META:
                items.append(("lit", e))
            else:
                _bad("\\" + e)
            j += 2
            continue
        items.append(("dash" if c == "-" else "lit", c))
        j += 1
    out, k = [], 0
    while k < len(items):
        kind, c = items[k]
        if kind != "esc" and k + 2 < len(items) and items[k + 1][0] == "dash":
            hi_kind, hi = items[k + 2]
            if hi_kind == "esc" or ord(c) > ord(hi):
                _bad(f"{c}-{hi}")
            out.append(f"{_class_lit(c)}-{_class_lit(hi)}")
            k += 3
            continue
        if kind == "dash" and 0 < k < len(items) - 1:
            _bad("-")
        out.append(_CLASS_IN[c] if kind == "esc" else _class_lit(c))
        k += 1
    return p[i:j + 1], "[" + neg + "".join(out) + "]", j + 1


def _tokenize(p: str) -> list[tuple[str, str]]:
    """(source token, engine form) pairs; ContractError naming the first token outside D7."""
    toks: list[tuple[str, str]] = []
    i, depth, prev = 0, 0, ""
    counted = [False]  # per open group (and the top level): does it hold a counted repeat?
    closed = False  # the group that just closed held one
    while i < len(p):
        c = p[i]
        kind, just_closed = "atom", False
        if c == "\\":
            e = p[i + 1:i + 2]
            if not e or (e not in PATTERN_META and e not in CLASS_ESCAPES):
                _bad("\\" + e)
            tok = (p[i:i + 2], _CLASS_OUT.get(e, p[i:i + 2]))
        elif c == "[":
            src, eng, nxt = _bracket(p, i)
            tok = (src, eng)
        elif c == "(":
            if p.startswith("(?:", i):
                tok = ("(?:", "(?:")
            elif p.startswith("(?", i):
                _bad(p[i:i + 3])
            else:
                tok = ("(", "(")
            depth, kind = depth + 1, "open"
            counted.append(False)
        elif c == ")":
            if depth == 0:
                _bad(")")
            depth, tok = depth - 1, (")", ")")
            just_closed = counted.pop()
            counted[-1] = counted[-1] or just_closed
        elif c == "|":
            tok, kind = ("|", "|"), "alt"
        elif c in "*+?{":
            src = c
            if c == "{":
                m = re.match(r"\{([0-9]+)(,([0-9]*))?\}", p[i:])
                if not m:
                    close = p.find("}", i)
                    _bad(p[i:close + 1] if close >= 0 else "{")
                src = m.group(0)
                lo, hi = int(m.group(1)), m.group(3)
                if lo > _MAX_REPEAT or (hi and (int(hi) < lo or int(hi) > _MAX_REPEAT)):
                    _bad(src)
            if prev != "atom":
                _bad(src)
            if c == "{":
                if closed:
                    _bad(src)
                counted[-1] = True
            if p[i + len(src):i + len(src) + 1] in ("?", "+") and i + len(src) < len(p):
                _bad(src + p[i + len(src)])
            tok, kind = (src, src), "quant"
        elif c == "^":
            if toks:
                _bad("^")
            tok, kind = ("^", ""), "anchor"
        elif c == "$":
            if i != len(p) - 1:
                _bad("$")
            tok, kind = ("$", ""), "anchor"
        elif c in "]}":
            _bad(c)
        else:
            tok = (c, c)
        i = nxt if c == "[" else i + len(tok[0])
        toks.append(tok)
        prev, closed = kind, just_closed
    if depth:
        _bad("(")
    return toks


def pattern_tokens(p: str) -> list[str]:
    """The pattern's tokens under ruling D7, or ContractError("invalid pattern: <token>")."""
    return [src for src, _ in _tokenize(p)]


def compile_pattern(p: str) -> re.Pattern:
    """Compile a D7 pattern for fullmatch: ASCII classes, anchors dropped."""
    return re.compile("".join(eng for _, eng in _tokenize(p)), re.ASCII)


def _check_schema(schema: Any) -> None:
    schema = _mapping(schema, "schema")
    fields = schema.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ContractError("schema.fields must be a non-empty list")
    seen: set[str] = set()
    for i, fld in enumerate(fields):
        where = f"schema.fields[{i}]"
        fld = _mapping(fld, where)
        name = _text(fld, "name", where)
        if name in seen:
            raise ContractError(f"{where}.name {name!r} is declared twice")
        seen.add(name)
        if fld.get("type") not in FIELD_TYPES:
            raise ContractError(f"{where}.type must be one of {', '.join(FIELD_TYPES)}")
        if not isinstance(fld.get("required"), bool):
            raise ContractError(f"{where}.required must be true or false")
        _text(fld, "meaning", where)
        if "enum" in fld:
            if not isinstance(fld["enum"], list) or not fld["enum"]:
                raise ContractError(f"{where}.enum must be a non-empty list")
            for e in fld["enum"]:
                if not _type_ok(e, fld["type"]):
                    raise ContractError(f"{where}.enum value {_show(e)} is not of type {fld['type']}")
        if "pattern" in fld:
            if fld["type"] != "string":
                raise ContractError(f"{where}.pattern needs type string")
            if not isinstance(fld["pattern"], str):
                raise ContractError(f"{where}.pattern must be a string")
            try:
                compile_pattern(fld["pattern"])
            except ContractError as e:
                raise ContractError(f"{where}.pattern: {e}") from None
            except re.error:
                raise ContractError(f"{where}.pattern: invalid pattern: {fld['pattern']}") from None


def _check_quality(quality: Any, fields: dict[str, dict]) -> None:
    quality = _mapping(quality, "quality")
    for key in quality:
        if key not in QUALITY_RULES:
            raise ContractError(f"quality.{key} is not a known rule ({', '.join(QUALITY_RULES)})")
    if "row_count" in quality:
        rc = _mapping(quality["row_count"], "quality.row_count")
        for key in rc:
            if key not in ("min", "max"):
                raise ContractError(f"quality.row_count.{key} is not min or max")
            if not _is_int(rc[key]) or rc[key] < 0:
                raise ContractError(f"quality.row_count.{key} must be a non-negative integer")
        if not rc:
            raise ContractError("quality.row_count needs min or max")
        if "min" in rc and "max" in rc and rc["min"] > rc["max"]:
            raise ContractError("quality.row_count.min is above max")
    for key in ("unique", "enum_coverage"):
        if key not in quality:
            continue
        names = quality[key]
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ContractError(f"quality.{key} must be a list of field names")
        for n in names:
            if n not in fields:
                raise ContractError(f"quality.{key} names {n!r}, which is not a schema field")
            if key == "enum_coverage" and "enum" not in fields[n]:
                raise ContractError(f"quality.enum_coverage names {n!r}, which has no enum")


def _check_servicing(servicing: Any) -> None:
    servicing = _mapping(servicing, "servicing")
    fr = _mapping(servicing.get("freshness"), "servicing.freshness")
    age = fr.get("max_age_days")
    if isinstance(age, bool) or not isinstance(age, (int, float)) or age < 0:
        raise ContractError("servicing.freshness.max_age_days must be a non-negative number")


def _check_consumers(consumers: Any) -> None:
    if not isinstance(consumers, list):
        raise ContractError("consumers must be a list")
    for i, c in enumerate(consumers):
        where = f"consumers[{i}]"
        c = _mapping(c, where)
        _text(c, "name", where)
        c["version"] = _version(c.get("version"), f"{where}.version")
        _text(c, "reads", where)


def load_contract(path: Path) -> dict:
    """Read and validate a contract file; raise ContractError naming what is wrong."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise ContractError(f"contract file not found: {path.name}") from None
    except UnicodeDecodeError:
        raise ContractError(f"{path.name} is not a valid contract file") from None
    try:
        doc = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError:
        raise ContractError(f"{path.name} is not a valid contract file") from None
    return check_contract(doc)


def check_contract(doc: Any) -> dict:
    """Validate a parsed contract (from YAML, or a fixture's JSON); raise ContractError."""
    doc = _mapping(doc, "contract")
    for section in SECTIONS:
        if section not in doc:
            raise ContractError(f"missing section: {section}")
    for key in doc:
        if key not in SECTIONS:
            raise ContractError(f"unknown section: {key}")
    _check_info(doc["info"])
    _check_schema(doc["schema"])
    _check_quality(doc["quality"], {f["name"]: f for f in doc["schema"]["fields"]})
    _check_servicing(doc["servicing"])
    _check_consumers(doc["consumers"])
    return doc


def export_text(contract: dict) -> str:
    """The machine form Go reads: keys sorted, indent 1, trailing newline."""
    return json.dumps(contract, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def export_path(path: Path) -> Path:
    """tickets.v1.yaml -> tickets.v1.contract.json, beside the source."""
    stem = path.stem if path.suffix == ".yaml" else path.name
    return path.with_name(stem + EXPORT_SUFFIX)


def export_contract(path: Path) -> Path:
    out = export_path(Path(path))
    out.write_text(export_text(load_contract(path)), encoding="utf-8")
    return out


def export_check(directory: Path = CONTRACTS_DIR) -> int:
    """1 when any export is missing or differs from its YAML, naming each."""
    stale = 0
    files = sorted(Path(directory).glob("*.yaml"))
    for p in files:
        out = export_path(p)
        try:
            ok = out.read_text(encoding="utf-8") == export_text(load_contract(p))
        except (OSError, ContractError):
            ok = False
        if not ok:
            stale += 1
            print(f"stale: {out.name} (run contract.py --export {p.name})")
    print(f"exports: {len(files) - stale} current, {stale} stale")
    return 1 if stale else 0


# ── 2. Rows ─────────────────────────────────────────────────────────────────
class Rows(list):
    """A list of row dicts that also remembers what could not be read.

    read_rows must not raise on a bad line, so the problems ride along and
    validate() turns them into one failed `parse` verdict.
    """

    def __init__(self, rows=(), problems=()):
        super().__init__(rows)
        self.problems = list(problems)


def _json_type(x: Any) -> str:
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "boolean"
    if isinstance(x, int):
        return "integer"
    if isinstance(x, float):
        return "number"
    if isinstance(x, str):
        return "string"
    if isinstance(x, list):
        return "array"
    return "object"


MAX_DEPTH = 64  # ruling D16: one cap in both languages, not CPython's C stack
_HEX = frozenset("0123456789abcdefABCDEF")


def _hex4(s: str, i: int) -> int | None:
    h = s[i:i + 4]
    return int(h, 16) if len(h) == 4 and all(c in _HEX for c in h) else None


def _scan(text: str, array: bool = False) -> tuple[int, str] | None:
    """What json.loads must never see, found before decoding (ruling D16).

    A lexical pass: brackets outside strings count depth (a row's own braces are
    level 1; an array's items are rows, so its bracket does not count), and a
    `\\u` escape for a surrogate must be a high one followed by a low one.
    Returns (row, problem), the row being the array item, or 1 for one line.
    """
    depth, row, i, n = 0, 1, 0, len(text)
    limit = MAX_DEPTH + (1 if array else 0)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] != "\\":
                    i += 1
                    continue
                hi = _hex4(text, i + 2) if text[i + 1:i + 2] == "u" else None
                if hi is not None and 0xD800 <= hi <= 0xDFFF:
                    lo = _hex4(text, i + 8) if hi < 0xDC00 and text[i + 6:i + 8] == "\\u" else None
                    if lo is None or not 0xDC00 <= lo <= 0xDFFF:
                        return row, "invalid unicode escape"
                    i += 12
                    continue
                i += 2
        elif c in "[{":
            depth += 1
            if depth > limit:
                return row, f"nesting deeper than {MAX_DEPTH}"
        elif c in "]}":
            depth -= 1
        elif c == "," and array and depth == 1:
            row += 1
        i += 1
    return None


def parse_rows(text: str) -> Rows:
    """Parse dataset text: a JSON array, one JSON object (one row), or JSONL.

    Row numbers count parsed rows from 1; a bad line is a problem, not a row.
    A problem found before decoding (ruling D16) names its row: the item in an
    array, the line in JSONL (a single object is line 1).
    """
    stripped = text.strip()
    if not stripped:
        return Rows()
    if stripped.startswith("["):
        bad = _scan(stripped, array=True)
        if bad:
            return Rows(problems=[f"row {bad[0]}: {bad[1]}"])
    is_whole = False
    if stripped.startswith("[") or _scan(stripped) is None:
        try:
            whole = json.loads(stripped)
        except (ValueError, RecursionError):
            pass
        else:
            is_whole = True
    if is_whole and isinstance(whole, dict):
        return Rows([whole])
    if is_whole and isinstance(whole, list):
        rows, problems = [], []
        for i, item in enumerate(whole, start=1):
            if isinstance(item, dict):
                rows.append(item)
            else:
                problems.append(f"item {i}: expected object, got {_json_type(item)}")
        return Rows(rows, problems)
    if is_whole:
        return Rows(problems=[f"expected object or array, got {_json_type(whole)}"])
    if stripped.startswith("["):
        return Rows(problems=["invalid JSON array"])
    rows, problems = [], []
    # Only \n ends a row (after universal newlines): str.splitlines would also break inside a
    # string on U+2028, U+2029 or U+0085, which JSON allows raw.
    for n, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        if not line.strip():
            continue
        bad = _scan(line)
        if bad:
            problems.append(f"row {n}: {bad[1]}")
            continue
        try:
            obj = json.loads(line)
        except (ValueError, RecursionError):
            problems.append(f"line {n}: invalid JSON")
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            problems.append(f"line {n}: expected object, got {_json_type(obj)}")
    return Rows(rows, problems)


def read_rows(path: Path) -> list[dict]:
    """Read a dataset file as rows; a bad line or a missing file is a problem, not an exception."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return Rows(problems=["dataset file not found"])
    except UnicodeDecodeError:
        return Rows(problems=["dataset is not valid UTF-8"])
    return parse_rows(text)


# ── 3. Rules ────────────────────────────────────────────────────────────────
def _plural(n: int, word: str = "row") -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _show(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)


def _num(x: float) -> str:
    return f"{x:g}"


def _first(fails: list[str], ok_detail: str) -> tuple[bool, str]:
    """ok with the pass text, or the first failure and how many more there were."""
    if not fails:
        return True, ok_detail
    more = f" (+{len(fails) - 1} more)" if len(fails) > 1 else ""
    return False, fails[0] + more


def _present(row: dict, name: str) -> bool:
    return row.get(name) is not None


def _type_ok(value: Any, kind: str) -> bool:
    got = _json_type(value)
    return got == kind or (kind == "number" and got == "integer")


def _verdict(rule: str, field: str | None, ok: bool, detail: str) -> dict:
    return {"rule": rule, "field": field, "ok": ok, "detail": detail}


def _field_rules(fld: dict, rows: list[dict]) -> list[dict]:
    name, kind = fld["name"], fld["type"]
    out = []
    if fld["required"]:
        fails = [f"row {i}: missing" for i, r in enumerate(rows, 1) if not _present(r, name)]
        out.append(_verdict("required", name, *_first(fails, f"present in {_plural(len(rows))}")))
    fails = [
        f"row {i}: expected {kind}, got {_json_type(r[name])}"
        for i, r in enumerate(rows, 1)
        if _present(r, name) and not _type_ok(r[name], kind)
    ]
    out.append(_verdict("type", name, *_first(fails, f"all values are {kind}")))
    if "enum" in fld:
        allowed = {_show(e) for e in fld["enum"]}
        fails = [
            f"row {i}: {_show(r[name])} not in enum {_show(fld['enum'])}"
            for i, r in enumerate(rows, 1)
            if _present(r, name) and _show(r[name]) not in allowed
        ]
        out.append(_verdict("enum", name, *_first(fails, "all values in enum")))
    if "pattern" in fld:
        rx = compile_pattern(fld["pattern"])
        fails = [
            f"row {i}: {_show(r[name])} does not match {fld['pattern']}"
            for i, r in enumerate(rows, 1)
            if isinstance(r.get(name), str) and not rx.fullmatch(r[name])
        ]
        out.append(_verdict("pattern", name, *_first(fails, "all values match")))
    return out


def _extra_fields(declared: set[str], rows: list[dict]) -> list[dict]:
    """Fields the contract does not mention: reported, never failed (additive change)."""
    counts: dict[str, int] = {}
    for r in rows:
        for k in r:
            if k not in declared:
                counts[k] = counts.get(k, 0) + 1
    return [_verdict("extra_field", k, True, f"not in schema ({_plural(n)})") for k, n in counts.items()]


def _quality_rules(quality: dict, fields: dict[str, dict], rows: list[dict]) -> list[dict]:
    out = []
    rc = quality.get("row_count")
    if rc:
        n, lo, hi = len(rows), rc.get("min"), rc.get("max")
        bounds = ", ".join(([f"min {lo}"] if lo is not None else []) + ([f"max {hi}"] if hi is not None else []))
        if lo is not None and n < lo:
            out.append(_verdict("row_count", None, False, f"{_plural(n)}, below min {lo}"))
        elif hi is not None and n > hi:
            out.append(_verdict("row_count", None, False, f"{_plural(n)}, above max {hi}"))
        else:
            out.append(_verdict("row_count", None, True, f"{_plural(n)} ({bounds})"))
    key_fields = quality.get("unique", [])
    if key_fields:
        # ruling D8: the listed fields are ONE composite key; a row lacking any of them fails
        first: dict[str, int] = {}
        fails = []
        for i, r in enumerate(rows, 1):
            missing = [n for n in key_fields if not _present(r, n)]
            if missing:
                fails.append(f"row {i}: missing key field {missing[0]}")
                continue
            key = _show([r[n] for n in key_fields])
            if key in first:
                fails.append(f"row {i}: duplicate of row {first[key]}")
            else:
                first[key] = i
        out.append(_verdict("unique", ",".join(key_fields), *_first(fails, f"{_plural(len(rows))}, no duplicates")))
    for name in quality.get("enum_coverage", []):
        seen = {_show(r[name]) for r in rows if _present(r, name)}
        missing = [_show(e) for e in fields[name]["enum"] if _show(e) not in seen]
        if missing:
            out.append(_verdict("enum_coverage", name, False, "missing values: " + ", ".join(missing)))
        else:
            out.append(_verdict("enum_coverage", name, True, f"all {len(fields[name]['enum'])} values appear"))
    return out


def _freshness(servicing: dict, age: float | None) -> dict:
    limit = servicing["freshness"]["max_age_days"]
    if age is None:
        return _verdict("freshness", None, True, "unknown: no git date")
    if age > limit:
        return _verdict("freshness", None, False, f"{_num(age)} days old, over max {_num(limit)}")
    return _verdict("freshness", None, True, f"{_num(age)} days old (max {_num(limit)})")


# ── 4. Verdicts ─────────────────────────────────────────────────────────────
def validate(contract: dict, rows: list[dict], *, file_age_days: float | None = None) -> list[dict]:
    """Check rows against a loaded contract; one verdict per rule and field, sorted.

    Freshness is judged from file_age_days, which the caller computes (from git
    in the catalogue); None means unknown, which passes with a note.
    """
    fields = {f["name"]: f for f in contract["schema"]["fields"]}
    out = []
    problems = getattr(rows, "problems", [])
    if problems:
        out.append(_verdict("parse", None, *_first(problems, "")))
    for fld in contract["schema"]["fields"]:
        out.extend(_field_rules(fld, rows))
    out.extend(_extra_fields(set(fields), rows))
    out.extend(_quality_rules(contract["quality"], fields, rows))
    out.append(_freshness(contract["servicing"], file_age_days))
    if not rows:
        # nothing to check is not a failure; the row_count rule alone says whether empty is allowed
        for x in out:
            if x["rule"] not in ("row_count", "freshness", "parse"):
                x["ok"], x["detail"] = True, "no rows"
    return sorted(out, key=lambda x: (x["rule"], x["field"] or ""))


def format_lines(verdicts: list[dict]) -> list[str]:
    """`OK  rule field: detail` per verdict (field null prints as -), then the summary."""
    lines = [
        f"{'OK ' if x['ok'] else 'FAIL'} {x['rule']} {x['field'] if x['field'] is not None else '-'}: {x['detail']}"
        for x in verdicts
    ]
    bad = sum(1 for x in verdicts if not x["ok"])
    lines.append(f"verdicts: {len(verdicts) - bad} ok, {bad} failed")
    return lines


# ── 5. Command line ─────────────────────────────────────────────────────────
def _non_negative(ap: argparse.ArgumentParser, text: str) -> float:
    try:
        if not text.isascii() or "_" in text:
            raise ValueError  # float() also takes Unicode digits and 1_0; Go does not
        x = float(text)
    except ValueError:
        ap.error(f"--age-days needs a number, got {text!r}")
    if x < 0:
        ap.error("--age-days must not be negative")
    return x


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate a dataset against its data contract.")
    ap.add_argument("paths", nargs="*", help="<contract.yaml> <dataset>, or <contract.yaml> with --export")
    ap.add_argument("--age-days", type=lambda s: _non_negative(ap, s), default=None, help="age of the dataset file in days (from git)")
    ap.add_argument("--export", action="store_true", help="write <contract>.contract.json for the Go twin")
    ap.add_argument("--check", action="store_true", help="with --export: fail if data/contracts exports are stale")
    args = ap.parse_args(argv)
    if args.export:
        if args.check and not args.paths:
            return export_check()
        if len(args.paths) != 1 or args.check:
            ap.error("--export takes one contract path, or --check alone")
        try:
            print(f"wrote {export_contract(Path(args.paths[0])).name}")
        except ContractError as e:
            print(f"FAIL contract -: {e}")
            return 1
        return 0
    if len(args.paths) != 2:
        ap.error("expected <contract.yaml> <dataset>")
    try:
        contract = load_contract(Path(args.paths[0]))
    except ContractError as e:
        verdicts = [_verdict("contract", None, False, str(e))]
    else:
        verdicts = validate(contract, read_rows(Path(args.paths[1])), file_age_days=args.age_days)
    print("\n".join(format_lines(verdicts)))
    return 1 if any(not x["ok"] for x in verdicts) else 0


if __name__ == "__main__":
    sys.exit(main())
