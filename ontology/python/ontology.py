#!/usr/bin/env python3
"""
ontology — load Lumen's ontology, check it is consistent, validate records against it.

The ontology (ontology/lumen.yaml) is the one place that says what a ticket is, what
each category and priority means and which labelling rules hold. Everything later in
the track reads it through this module: the generator turns it into prompt text and
schemas, the grounding step validates model outputs with it, the audit compares it
with the course's other artefacts, and the app reads the exported ontology.json.

Run (from the repo root):
    .venv-ontology/bin/python ontology/python/ontology.py check                       # consistency findings
    .venv-ontology/bin/python ontology/python/ontology.py validate Ticket llms/data/tickets.jsonl
    .venv-ontology/bin/python ontology/python/ontology.py export                      # rewrite ontology.json
    .venv-ontology/bin/python ontology/python/ontology.py export --check              # fail if it drifted
Free and offline: no model calls.

Every problem is a finding, {level, rule, subject, detail}, sorted by (level, rule,
subject). A malformed file or a dangling reference is a finding naming the id, never
an exception, so one bad entry never hides the rest. A rule that needs the ticket's
text is `manual`: listed on every record, never pretended to be checked.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ── 1. File ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
LUMEN = ROOT / "ontology" / "lumen.yaml"
DERIVED = ROOT / "ontology" / "data" / "derived"
ONTOLOGY_JSON = DERIVED / "ontology.json"

SECTIONS = ("classes", "properties", "relations", "individuals", "constraints")
LITERALS = ("string", "integer", "number", "boolean", "date")
CARDINALITIES = ("one", "many")
KINDS = ("checkable", "manual")
OPS = ("eq", "ne", "in", "not_in", "gt", "lt", "gte", "lte", "present", "absent")
# Ruling O12: gt/lt/gte/lte only where the values have an order. On an enumeration or a
# string they would compare text alphabetically ('high' < 'normal'); an individual's
# `order` is a decision order, not a rank (O4), so no ordering is derived from it.
ORDERED_OPS = ("gt", "lt", "gte", "lte")
ORDERED_TYPES = ("integer", "number", "date")
LEVELS = ("error", "warning", "manual")

# The fields whose value is the id of another entry, per section. load() reports any
# non-string value in them as `malformed`, so the checks below can look ids up safely;
# a new id-valued field goes here. Property ranges and constraint clauses, which hold
# ids inside structures, are checked by _malformed_refs as well.
REF_FIELDS = {
    "classes": ("subclass_of",),
    "properties": ("domain",),
    "relations": ("domain", "range"),
    "individuals": ("class",),
    "constraints": ("class",),
}


@dataclass
class Ontology:
    """The five sections as dicts keyed by id, in file order, plus where they came from.

    `findings` holds what went wrong while loading (a parse error, a section in the
    wrong shape, a duplicate id); check() reports them with everything else.
    """
    classes: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)
    relations: dict = field(default_factory=dict)
    individuals: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)
    source: Path = LUMEN
    findings: list = field(default_factory=list)


def finding(level: str, rule: str, subject: str, detail: str) -> dict:
    return {"level": level, "rule": rule, "subject": str(subject), "detail": detail}


def _is_id(v) -> bool:
    """An id is a non-empty string; anything else (a list, a mapping, a number) is not."""
    return isinstance(v, str) and bool(v)


def _bad(field_name: str, v) -> str:
    return f"{field_name} must be an id (a string), got {type(v).__name__} {v!r}"


def _malformed_refs(section: str, eid: str, entry: dict) -> list[dict]:
    """A `malformed` finding for each id-valued field that holds something else."""
    out = []
    for key in REF_FIELDS.get(section, ()):
        if key in entry and entry[key] is not None and not _is_id(entry[key]):
            out.append(finding("error", "malformed", eid, _bad(key, entry[key])))
    if section == "properties" and "range" in entry:
        rng = entry["range"]
        if isinstance(rng, dict):
            for kind, members in rng.items():
                for m in members if isinstance(members, list) else []:
                    if not _is_id(m):
                        out.append(finding("error", "malformed", eid, _bad(f"range {kind} member", m)))
        elif not _is_id(rng):
            out.append(finding("error", "malformed", eid, _bad("range", rng)))
    if section == "constraints":
        for part in ("when", "then"):
            clauses = entry.get(part)
            for i, cl in enumerate(clauses if isinstance(clauses, list) else []):
                if isinstance(cl, dict) and "property" in cl and not _is_id(cl["property"]):
                    out.append(finding("error", "malformed", eid, _bad(f"{part}[{i}].property", cl["property"])))
    return out


def _guarded(fn):
    """The generic guard: a check or validation that still raises on some shape nobody
    foresaw becomes one `internal` finding instead of a traceback that hides the rest."""
    import functools

    @functools.wraps(fn)
    def run(onto, *args, **kwargs):
        try:
            return fn(onto, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 - the point is that nothing escapes
            subject = getattr(getattr(onto, "source", None), "name", "ontology")
            return [finding("error", "internal", subject, f"{fn.__name__} raised {type(e).__name__}: {e}")]
    return run


def _sorted(findings: list) -> list:
    return sorted(findings, key=lambda f: (f["level"], f["rule"], f["subject"], f["detail"]))


def load(path: Path) -> Ontology:
    """Read the YAML into an Ontology. Never raises on bad content: each problem becomes
    a finding on the returned object, and the entries that did load are kept."""
    path = Path(path)
    onto = Ontology(source=path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        onto.findings.append(finding("error", "malformed", path.name, "file not found"))
        return onto
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        onto.findings.append(finding("error", "malformed", path.name, f"not valid YAML: {' '.join(str(e).split())}"))
        return onto
    if not isinstance(raw, dict):
        onto.findings.append(finding("error", "malformed", path.name, "the file is not a mapping of sections"))
        return onto
    for key in raw:
        if key not in SECTIONS:
            onto.findings.append(finding("warning", "unknown_section", str(key), f"not one of {', '.join(SECTIONS)}"))
    seen: dict[str, str] = {}  # id -> the section it was first defined in
    for name in SECTIONS:
        if name not in raw:
            onto.findings.append(finding("error", "malformed", name, "section missing"))
            continue
        entries = raw[name] or []
        if not isinstance(entries, list):
            onto.findings.append(finding("error", "malformed", name, f"must be a list of entries, got {type(entries).__name__}"))
            continue
        section = getattr(onto, name)
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
                onto.findings.append(finding("error", "malformed", f"{name}[{i}]", "an entry must be a mapping with a string id"))
                continue
            eid = entry["id"]
            if eid in seen:
                where = "again in " + name if seen[eid] == name else f"in {seen[eid]} and in {name}"
                onto.findings.append(finding("error", "duplicate_id", eid, f"defined {where}; the first one is kept"))
                continue
            seen[eid] = name
            section[eid] = entry
            onto.findings += _malformed_refs(name, eid, entry)
    return onto


# ── 2. Checks ───────────────────────────────────────────────────────────────
def ancestors(onto: Ontology, cls: str) -> list[str]:
    """cls and its superclasses, nearest first; stops at a cycle or an unknown class."""
    out: list[str] = []
    while _is_id(cls) and cls in onto.classes and cls not in out:
        out.append(cls)
        cls = onto.classes[cls].get("subclass_of")
    return out


def class_properties(onto: Ontology, cls: str) -> dict:
    """The properties a record of cls may carry: its own and its superclasses'."""
    chain = set(ancestors(onto, cls))
    return {pid: p for pid, p in onto.properties.items() if _is_id(p.get("domain")) and p["domain"] in chain}


def _cycles(onto: Ontology) -> list[list[str]]:
    """Each subclass_of cycle once, as a path starting and ending at its smallest id."""
    found: dict[frozenset, list[str]] = {}
    for start in onto.classes:
        path, cls = [], start
        while _is_id(cls) and cls in onto.classes and cls not in path:
            path.append(cls)
            cls = onto.classes[cls].get("subclass_of")
        if _is_id(cls) and cls in path:
            loop = path[path.index(cls):]
            key = frozenset(loop)
            if key not in found:
                i = loop.index(min(loop))
                ring = loop[i:] + loop[:i]
                found[key] = ring + [ring[0]]
    return sorted(found.values())


def _check_range(onto: Ontology, pid: str, rng) -> list[dict]:
    if rng is not None and not isinstance(rng, (str, dict)):
        return []  # load() reported it as malformed
    if isinstance(rng, str):
        if rng in LITERALS or rng in onto.classes:
            return []
        return [finding("error", "dangling_range", pid, f"range {rng!r} is neither a class nor a literal type ({', '.join(LITERALS)})")]
    if isinstance(rng, dict) and len(rng) == 1 and isinstance(next(iter(rng.values())), list):
        kind, members = next(iter(rng.items()))
        if kind == "values":
            return [] if members else [finding("error", "dangling_range", pid, "empty enumeration")]
        if kind == "individuals":
            if not all(_is_id(m) for m in members):
                return []  # load() reported the member as malformed
            unknown = [m for m in members if m not in onto.individuals]
            if unknown:
                return [finding("error", "dangling_range", pid, f"unknown individuals: {', '.join(map(str, unknown))}")]
            return [] if members else [finding("error", "dangling_range", pid, "empty enumeration")]
    return [finding("error", "dangling_range", pid, "range must be a class, a literal type, {individuals: [...]} or {values: [...]}")]


def _check_constraint(onto: Ontology, cid: str, c: dict) -> list[dict]:
    out = []
    kind = c.get("kind")
    if kind not in KINDS:
        out.append(finding("error", "constraint_malformed", cid, f"kind {kind!r} is not one of {', '.join(KINDS)}"))
    for key in ("source", "text"):
        if not c.get(key):
            out.append(finding("error", "constraint_malformed", cid, f"{key} missing"))
    cls = c.get("class")
    if cls is not None and not _is_id(cls):
        return out  # load() reported it as malformed
    if cls not in onto.classes:
        out.append(finding("error", "constraint_unknown_class", cid, f"class {cls!r} is not defined"))
        return out
    if kind == "manual":
        if "when" in c or "then" in c:
            out.append(finding("warning", "constraint_malformed", cid, "a manual constraint has no when/then; they are ignored"))
        return out
    if kind != "checkable":
        return out
    props = class_properties(onto, cls)
    if not isinstance(c.get("then"), list) or not c["then"]:
        out.append(finding("error", "constraint_malformed", cid, "a checkable constraint needs a non-empty then list"))
    if not isinstance(c.get("when", []), list):
        out.append(finding("error", "constraint_malformed", cid, "when must be a list"))
    if "phrases" in c and not (isinstance(c["phrases"], list) and all(isinstance(p, str) for p in c["phrases"])):
        out.append(finding("error", "constraint_malformed", cid, "phrases must be a list of strings"))
    for clause in _clauses(c):
        if not isinstance(clause, dict):
            out.append(finding("error", "constraint_malformed", cid, f"clause {clause!r} is not a mapping"))
            continue
        prop, op = clause.get("property"), clause.get("op")
        if "property" in clause and not _is_id(prop):
            continue  # load() reported it as malformed
        if prop not in props:
            out.append(finding("error", "constraint_unknown_property", cid, f"{cls} has no property {prop!r}"))
            continue
        if op not in OPS:
            out.append(finding("error", "constraint_malformed", cid, f"op {op!r} is not one of {', '.join(OPS)}"))
            continue
        if op in ("in", "not_in") and not isinstance(clause.get("value"), list):
            out.append(finding("error", "constraint_malformed", cid, f"{op} on {prop} needs a list value"))
            continue
        if not _ordered_ok(op, props[prop]):
            out.append(finding("error", "constraint_malformed", cid,
                               f"{op} on {prop} needs an {', '.join(ORDERED_TYPES[:-1])} or {ORDERED_TYPES[-1]} property; "
                               f"{prop}'s range {props[prop].get('range')!r} has no order"))
            continue
        # a value outside the property's enumeration is a typo the clause would never match
        allowed = _enumeration(props[prop].get("range"))
        if allowed is not None and op in ("eq", "ne", "in", "not_in"):
            values = clause.get("value") if op in ("in", "not_in") else [clause.get("value")]
            bad = [v for v in values if v not in allowed]
            if bad:
                out.append(finding("error", "constraint_unknown_value", cid, f"{prop} has no value {', '.join(map(str, bad))}"))
    return out


def _ordered_ok(op, prop: dict) -> bool:
    """Ruling O12: an ordered op needs an integer, number or date property."""
    return op not in ORDERED_OPS or prop.get("range") in ORDERED_TYPES


def _clauses(c: dict) -> list:
    when, then = c.get("when") or [], c.get("then") or []
    return (when if isinstance(when, list) else []) + (then if isinstance(then, list) else [])


def _enumeration(rng) -> list | None:
    if isinstance(rng, dict) and len(rng) == 1:
        members = next(iter(rng.values()))
        return members if isinstance(members, list) else None
    return None


@_guarded
def check(onto: Ontology) -> list[dict]:
    """Consistency findings: whatever loading found, then dangling references, subclass
    cycles, unknown ranges, constraints over unknown classes or properties, individuals
    of undefined classes and required properties with no domain. An id-valued field that
    holds a non-id was reported `malformed` by load(), so it is skipped here, not
    reported twice."""
    out = list(onto.findings)
    if any(f["rule"] == "malformed" and f["subject"] == onto.source.name for f in out):
        return _sorted(out)  # nothing loaded; the parse error is the whole story

    for cid, c in onto.classes.items():
        if not c.get("definition"):
            out.append(finding("warning", "missing_definition", cid, "class has no definition"))
        parent = c.get("subclass_of")
        if parent is not None and _is_id(parent) and parent not in onto.classes:
            out.append(finding("error", "dangling_reference", cid, f"subclass_of {parent!r} is not a class"))
    for ring in _cycles(onto):
        out.append(finding("error", "subclass_cycle", ring[0], "subclass_of loops: " + " -> ".join(ring)))

    for pid, p in onto.properties.items():
        domain = p.get("domain")
        if domain is None:
            if p.get("required"):
                out.append(finding("error", "required_without_domain", pid, "a required property needs a domain class"))
        elif _is_id(domain) and domain not in onto.classes:
            out.append(finding("error", "dangling_reference", pid, f"domain {domain!r} is not a class"))
        out += _check_range(onto, pid, p.get("range"))
        if p.get("cardinality", "one") not in CARDINALITIES:
            out.append(finding("error", "bad_cardinality", pid, f"cardinality must be one of {', '.join(CARDINALITIES)}"))
        if "pattern" in p:
            try:
                re.compile(p["pattern"])
            except (re.error, TypeError) as e:
                out.append(finding("error", "bad_pattern", pid, f"pattern does not compile: {e}"))

    for rid, r in onto.relations.items():
        for end in ("domain", "range"):
            v = r.get(end)
            if v is None or (_is_id(v) and v not in onto.classes):
                out.append(finding("error", "dangling_reference", rid, f"{end} {r.get(end)!r} is not a class"))

    orders: dict[tuple, str] = {}
    for iid, ind in onto.individuals.items():
        cls = ind.get("class")
        if cls is None or (_is_id(cls) and cls not in onto.classes):
            out.append(finding("error", "individual_class_undefined", iid, f"class {cls!r} is not defined"))
        for key in ("definition", "source"):
            if not ind.get(key):
                out.append(finding("warning", f"missing_{key}", iid, f"individual has no {key}"))
        order = ind.get("order")
        if order is not None and (cls is None or _is_id(cls)):
            if not isinstance(order, int) or isinstance(order, bool):
                out.append(finding("error", "bad_order", iid, f"order must be an integer, got {order!r}"))
            elif (cls, order) in orders:
                out.append(finding("error", "duplicate_order", iid, f"order {order} is also {orders[(cls, order)]}'s"))
            else:
                orders[(cls, order)] = iid

    for cid, c in onto.constraints.items():
        out += _check_constraint(onto, cid, c)
    return _sorted(out)


# ── 3. Constraint expressions ───────────────────────────────────────────────
class NotEvaluable(Exception):
    """A clause that cannot be judged on this record (a value of the wrong type)."""


def clause_holds(clause: dict, record: dict) -> bool:
    """One {property, op, value} clause against a record. present/absent look at the key;
    every other op needs the value present (the caller makes sure of that)."""
    prop, op, want = clause["property"], clause["op"], clause.get("value")
    if op == "present":
        return record.get(prop) is not None
    if op == "absent":
        return record.get(prop) is None
    have = record[prop]
    if op == "eq":
        return have == want
    if op == "ne":
        return have != want
    if op == "in":
        return have in want
    if op == "not_in":
        return have not in want
    try:
        return {"gt": have > want, "lt": have < want, "gte": have >= want, "lte": have <= want}[op]
    except TypeError as e:
        raise NotEvaluable(f"cannot compare {have!r} {op} {want!r}") from e


def _clause_well_formed(cl) -> bool:
    """The shape check() demands, repeated here so a malformed clause is never run:
    a mapping with an id property and a known op; in/not_in take a list (a string
    value would otherwise run as a substring match)."""
    return (isinstance(cl, dict) and _is_id(cl.get("property")) and cl.get("op") in OPS
            and (cl["op"] not in ("in", "not_in") or isinstance(cl.get("value"), list)))


def evaluate(c: dict, record: dict, subject: str, known: dict | None = None, invalid: set | None = None) -> list[dict]:
    """A checkable constraint on one record: an error when every `when` clause holds and
    any `then` clause fails. Not evaluable is a `manual` finding saying why, never a silent
    pass: the constraint is malformed, names a property outside `known` (the class's
    properties by id) or uses an ordered op on one with no order (O12), a property it
    reads is absent, or is in `invalid` (its value already failed its own range or type
    check, so judging the rule on it would count one mistake twice)."""
    clauses = _clauses(c)
    rule = f"{c.get('source')}: {c['text']}"
    if not isinstance(c.get("then"), list) or not c["then"] \
            or not all(_clause_well_formed(cl) for cl in clauses) \
            or (known is not None and any(cl["property"] not in known or not _ordered_ok(cl["op"], known[cl["property"]])
                                          for cl in clauses)):
        return [finding("manual", c["id"], subject, f"not evaluated: the constraint is malformed (see check). {rule}")]
    absent = sorted({cl["property"] for cl in clauses
                     if cl["op"] not in ("present", "absent") and record.get(cl["property"]) is None})
    if absent:
        return [finding("manual", c["id"], subject, f"not evaluated: {', '.join(absent)} absent. {rule}")]
    bad = sorted({cl["property"] for cl in clauses} & (invalid or set()))
    if bad:
        return [finding("manual", c["id"], subject, f"not evaluated: {', '.join(bad)} invalid (see above). {rule}")]
    try:
        if not all(clause_holds(cl, record) for cl in c.get("when") or []):
            return []
        failed = [cl for cl in c["then"] if not clause_holds(cl, record)]
    except (NotEvaluable, TypeError) as e:
        return [finding("manual", c["id"], subject, f"not evaluated: {e}. {c['source']}: {c['text']}")]
    if not failed:
        return []
    why = "; ".join(f"{cl['property']}={record.get(cl['property'])!r} fails {cl['op']} {cl.get('value')!r}" for cl in failed)
    return [finding("error", c["id"], subject, f"{c['source']}: {c['text']} ({why})")]


# ── 4. Validation ───────────────────────────────────────────────────────────
def _type_ok(literal: str, v) -> bool:
    if literal == "string":
        return isinstance(v, str)
    if literal == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if literal == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if literal == "boolean":
        return isinstance(v, bool)
    if literal == "date":
        if isinstance(v, _dt.date):
            return True
        try:
            _dt.date.fromisoformat(v)
            return True
        except (TypeError, ValueError):
            return False
    return False


def _check_value(onto: Ontology, pid: str, p: dict, v, subject: str) -> list[dict]:
    rng = p.get("range")
    if isinstance(rng, str) and rng in LITERALS:
        if not _type_ok(rng, v):
            return [finding("error", "type", subject, f"{pid} must be a {rng}, got {type(v).__name__} {v!r}")]
        if "pattern" in p and isinstance(v, str):
            try:
                matched = re.search(p["pattern"], v)
            except (re.error, TypeError):
                return [finding("warning", "pattern", subject, f"{pid}'s pattern does not compile (see check)")]
            if not matched:
                return [finding("error", "pattern", subject, f"{pid} {v!r} does not match {p['pattern']}")]
        return []
    if isinstance(rng, str) and rng in onto.classes:
        ind = onto.individuals.get(v) if isinstance(v, str) else None
        if ind is None or rng not in ancestors(onto, ind.get("class")):
            return [finding("error", "range", subject, f"{pid} {v!r} is not an individual of {rng}")]
        return []
    allowed = _enumeration(rng)
    if allowed is not None:
        if v not in allowed:
            return [finding("error", "range", subject, f"{pid} {v!r} is not one of {', '.join(map(str, allowed))}")]
        return []
    return [finding("error", "range", subject, f"{pid} has a range the ontology cannot check (see check())")]


@_guarded
def validate(onto: Ontology, record: dict, cls: str, partial: bool = False) -> list[dict]:
    """Findings for one record read as an instance of cls: required properties present,
    cardinality, types, patterns, ranges and enumerations, then the checkable constraints
    on cls (errors when violated) and the manual ones (listed as `manual`).

    partial=True is for model outputs and tool calls, which carry only some properties:
    a missing required property is not an error, only present values are checked, and
    a checkable constraint runs only when every property it references is present.
    """
    rid = record.get("id") if isinstance(record, dict) else None
    rid = rid if isinstance(rid, str) and rid else "<record>"
    if not _is_id(cls) or cls not in onto.classes:
        return [finding("error", "unknown_class", rid, f"{cls!r} is not a class of the ontology")]
    if not isinstance(record, dict):
        return [finding("error", "type", rid, "a record must be a mapping")]
    props = class_properties(onto, cls)
    out: list[dict] = []
    invalid: set[str] = set()  # properties whose value failed its own check
    for pid, p in props.items():
        subject = f"{rid}.{pid}"
        v = record.get(pid)
        before = len(out)
        if v is None:
            if p.get("required") and not partial:
                out.append(finding("error", "required", subject, f"{cls} requires {pid}"))
            continue
        if p.get("cardinality", "one") == "many":
            if not isinstance(v, list):
                out.append(finding("error", "cardinality", subject, f"{pid} takes many values (a list), got {type(v).__name__}"))
                continue
            for item in v:
                out += _check_value(onto, pid, p, item, subject)
        elif isinstance(v, list):
            out.append(finding("error", "cardinality", subject, f"{pid} takes one value, got a list"))
        else:
            out += _check_value(onto, pid, p, v, subject)
        if any(f["level"] == "error" for f in out[before:]):
            invalid.add(pid)
    for key in record:
        if key not in props:
            out.append(finding("warning", "unknown_property", f"{rid}.{key}", f"{cls} has no property {key!r}"))

    chain = set(ancestors(onto, cls))
    for cid, c in onto.constraints.items():
        if not _is_id(c.get("class")) or c["class"] not in chain:
            continue
        if c.get("kind") == "manual":
            out.append(finding("manual", cid, rid, f"{c.get('source')}: {' '.join(str(c.get('text', '')).split())}"))
        elif c.get("kind") == "checkable":
            out += evaluate({**c, "text": " ".join(str(c.get("text", "")).split())}, record, rid, props, invalid)
    return _sorted(out)


# ── 5. Export ───────────────────────────────────────────────────────────────
def to_json(onto: Ontology) -> dict:
    """The five sections as lists sorted by id, for the app. Order that matters (the
    guide's decision order) travels in each individual's `order`, not in list position."""
    def norm(entry: dict) -> dict:
        return {k: (" ".join(v.split()) if isinstance(v, str) and k not in ("pattern",) else v)
                for k, v in entry.items()}
    data = {name: [norm(e) for _, e in sorted(getattr(onto, name).items())] for name in SECTIONS}
    try:
        data["source"] = onto.source.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        data["source"] = onto.source.name
    return data


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export(onto: Ontology, out: Path) -> dict:
    """Write ontology.json (deterministic: sorted keys, trailing newline) and return it."""
    data = to_json(onto)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(data), encoding="utf-8")
    return data


# ── 6. Command line ─────────────────────────────────────────────────────────
def _print(findings: list, show_manual: bool = False) -> None:
    for f in findings:
        if f["level"] != "manual" or show_manual:
            print(f"{f['level']:7} {f['rule']} {f['subject']}: {f['detail']}")


def _display(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lumen's ontology: check, validate, export.")
    ap.add_argument("--file", type=Path, default=LUMEN, help="the ontology YAML (default ontology/lumen.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="consistency findings; exit 1 on any error")
    c.add_argument("--file", type=Path, default=argparse.SUPPRESS)
    v = sub.add_parser("validate", help="validate a JSONL file of records as instances of a class")
    v.add_argument("cls")
    v.add_argument("jsonl", type=Path)
    v.add_argument("--partial", action="store_true", help="missing properties are not errors")
    v.add_argument("--manual", action="store_true", help="also print the manual findings")
    v.add_argument("--file", type=Path, default=argparse.SUPPRESS)
    e = sub.add_parser("export", help="write ontology.json for the app")
    e.add_argument("--check", action="store_true", help="fail if ontology.json drifted")
    e.add_argument("--out", type=Path, default=ONTOLOGY_JSON)
    e.add_argument("--file", type=Path, default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    onto = load(args.file)

    if args.cmd == "check":
        findings = check(onto)
        _print(findings, show_manual=True)
        errors = sum(f["level"] == "error" for f in findings)
        print(f"{_display(onto.source)}: {errors} errors, {len(findings) - errors} other findings")
        return 1 if errors else 0

    if args.cmd == "validate":
        n, counts = 0, {lvl: 0 for lvl in LEVELS}
        try:
            lines = args.jsonl.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as err:
            print(f"cannot read {_display(args.jsonl)}: {err.strerror if isinstance(err, OSError) else err}")
            return 1
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            n += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as err:
                found = [finding("error", "malformed", f"line {i}", f"not JSON: {err}")]
            else:
                found = validate(onto, record, args.cls, partial=args.partial)
            _print(found, show_manual=args.manual)
            for f in found:
                counts[f["level"]] += 1
        print(f"{n} records, {counts['error']} errors, {counts['warning']} warnings, {counts['manual']} manual")
        return 1 if counts["error"] else 0

    # export
    data = to_json(onto)
    name = _display(args.out)
    if args.check:
        if not args.out.exists():
            print(f"{name} missing: run ontology.py export")
            return 1
        if args.out.read_text(encoding="utf-8") != dumps(data):
            print(f"{name} drifted from {_display(onto.source)}: run ontology.py export")
            return 1
        print(f"{args.out.name} matches")
        return 0
    export(onto, args.out)
    print(f"wrote {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
