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
    info       id, version, status (active | deprecated), owner (a track), description, sunset
    schema     fields: name, type, required, meaning, and enum or pattern where it applies
               (a pattern is a full match, like ^...$ in Go's RE2; lookarounds, backreferences,
               possessive quantifiers and \\A \\Z are rejected so both languages agree)
    quality    row_count {min, max}, unique [fields], enum_coverage [fields]
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
    if "generated_at" in info:
        _text(info, "generated_at", "info")


def _unsupported(pat: str) -> str | None:
    """Name the first construct Python's re has and Go's RE2 lacks, or None.

    A contract pattern is a full match (re.fullmatch here; ^...$ in RE2), so
    the two engines agree on the subset this lets through.
    """
    in_class, i = False, 0
    while i < len(pat):
        c = pat[i]
        nxt = pat[i + 1] if i + 1 < len(pat) else ""
        if c == "\\":
            if nxt in "AZ":
                return f"\\{nxt} anchor"
            if nxt in "123456789":
                return f"backreference \\{nxt}"
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
            if nxt == "^":
                i += 1
            if pat[i + 1:i + 2] == "]":
                i += 1
        elif c == "(" and nxt == "?":
            for tok, name in (("=", "lookahead"), ("!", "lookahead"), ("<=", "lookbehind"),
                              ("<!", "lookbehind"), (">", "atomic group"), ("P=", "backreference")):
                if pat.startswith(tok, i + 2):
                    return f"{name} (?{tok}"
        elif c in "*+?}" and nxt == "+":
            return f"possessive quantifier {c}+"
        i += 1
    return None


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
            bad = _unsupported(fld["pattern"])
            if bad:
                raise ContractError(f"{where}.pattern ({name}) uses syntax Go RE2 lacks: {bad}")
            try:
                re.compile(fld["pattern"])
            except re.error as e:
                raise ContractError(f"{where}.pattern is not a valid pattern: {e}") from None


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
    try:
        doc = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError:
        raise ContractError(f"{path.name} is not valid YAML") from None
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


def parse_rows(text: str) -> Rows:
    """Parse dataset text: a JSON array, one JSON object (one row), or JSONL.

    Row numbers count parsed rows from 1; a bad line is a problem, not a row.
    """
    stripped = text.strip()
    if not stripped:
        return Rows()
    try:
        whole = json.loads(stripped)
    except ValueError:
        whole = None
        is_whole = False
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
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
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
        rx = re.compile(fld["pattern"])
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
    for name in quality.get("unique", []):
        first: dict[str, int] = {}
        fails = []
        for i, r in enumerate(rows, 1):
            if not _present(r, name):
                continue
            key = _show(r[name])
            if key in first:
                fails.append(f"row {i}: duplicate of row {first[key]}")
            else:
                first[key] = i
        out.append(_verdict("unique", name, *_first(fails, f"{_plural(len(rows))}, no duplicates")))
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
