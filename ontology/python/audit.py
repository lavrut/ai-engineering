#!/usr/bin/env python3
"""
audit — Lumen's ontology compared with every other artefact that implies the same facts.

The labelling guide, the two rule-bearing triage prompts, the ML category map and the
data contracts each restate part of what the ontology says: which categories and
priorities exist, what a category means, which rules hold. The audit reads each one in
place and writes one row per artefact per fact, `agree`, `disagree` or `missing`, then
a gate that is red when any row disagrees. A `missing` row (the artefact can't be read,
or doesn't state the fact) is counted but never turns the gate red: an artefact that
says nothing has not contradicted anything. The audit reports; it never fixes.

Run (from the repo root):
    .venv-ontology/bin/python ontology/python/audit.py            # rewrite audit.json, print the gate
    .venv-ontology/bin/python ontology/python/audit.py --check    # fail if it drifted
Free and offline: the other tracks' files are read, never imported or changed.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ontology as O  # noqa: E402

ROOT = O.ROOT
AUDIT = O.DERIVED / "audit.json"
GUIDE = "llms/data/labeling_guide.md"
PROMPTS = "llms/data/prompts.json"
PROMPT_KEYS = ("v2_rules", "v3_examples")  # v1_basic states no rules, so it implies no facts here
CATEGORY_MAP = "ml/data/category_map.json"
CONTRACTS = "data/contracts"
# Contract field -> the ontology class whose individuals its enum must equal.
ENUM_FIELDS = {"category": "Category", "priority": "Priority", "urgency": "Priority"}


# ── 1. Rows and helpers ─────────────────────────────────────────────────────
def row(artefact: str, fact: str, status: str, detail: str) -> dict:
    return {"artefact": artefact, "fact": fact, "status": status, "detail": detail}


def _ids(onto: O.Ontology, cls: str) -> list[str]:
    """The individuals of one class, in their `order`."""
    members = [(v.get("order", 0), i) for i, v in onto.individuals.items() if v.get("class") == cls]
    return [i for _, i in sorted(members)]


def _token(word: str, flags: int = 0) -> re.Pattern:
    """`word` standing alone: not inside a longer id such as N-F (for N) or how_tos."""
    return re.compile(rf"(?<![\w-]){re.escape(word)}(?![\w-])", flags)


def normalise(text: str) -> str:
    """Ruling O5: markdown emphasis and code marks, surrounding and repeated whitespace,
    and case never count as a difference; the words do."""
    text = text.replace("**", "").replace("`", "")
    return " ".join(text.split()).lower()


def _diff(ours: str, theirs: str, where: str) -> str:
    """A unified diff of the normalised texts, one sentence or clause (split after `.`,
    `;`, `:`) per line, so the changed clause stands out instead of one long line
    against another."""
    split = lambda t: re.split(r"(?<=[.;:])\s+", t)  # noqa: E731
    lines = difflib.unified_diff(split(ours), split(theirs), "ontology", where, n=0, lineterm="")
    return "\n".join(line for line in lines if not line.startswith("@@"))


def _read(root: Path, rel: str) -> tuple[str | None, str]:
    """(text, why it is None): a file that can't be read becomes a reason, never a raise."""
    try:
        return (root / rel).read_text(encoding="utf-8"), ""
    except FileNotFoundError:
        return None, "file not found"
    except (OSError, UnicodeDecodeError) as e:
        return None, f"cannot read: {e}"


# ── 2. The labelling guide ──────────────────────────────────────────────────
# A category row of the guide's table: | C2 | `billing` | Money, charges, ... |
TABLE_ROW = re.compile(r"^\|\s*(C\d+)\s*\|\s*`?([a-z_]+)`?\s*\|(.*)\|\s*$", re.M)


def audit_guide(onto: O.Ontology, root: Path) -> list[dict]:
    """Every constraint's rule id appears in the guide; every category's definition is the
    guide's table text for that category, worded alike after normalising."""
    text, why = _read(root, GUIDE)
    if text is None:
        return [row(GUIDE, "file", "missing", why)]
    out = []
    for rid in sorted({c["source"] for c in onto.constraints.values() if isinstance(c.get("source"), str)}):
        named = [cid for cid, c in onto.constraints.items() if c.get("source") == rid]
        if _token(rid).search(text):
            out.append(row(GUIDE, f"rule:{rid}", "agree", f"{rid} is in the guide ({', '.join(named)})"))
        else:
            out.append(row(GUIDE, f"rule:{rid}", "missing", f"{rid} is not in the guide; the ontology cites it for {', '.join(named)}"))
    table = {m.group(2): (m.group(1), m.group(3).strip()) for m in TABLE_ROW.finditer(text)}
    for cid in _ids(onto, "Category"):
        ind = onto.individuals[cid]
        fact = f"definition:{cid}"
        if cid not in table:
            out.append(row(GUIDE, fact, "missing", f"no row for {cid} in the guide's category table"))
            continue
        rid, theirs = table[cid]
        ours_n, theirs_n = normalise(str(ind.get("definition", ""))), normalise(theirs)
        notes = []
        if ind.get("source") != rid:
            notes.append(f"the guide gives {cid} rule {rid}; the ontology says {ind.get('source')}")
        if ours_n != theirs_n:
            notes.append(_diff(ours_n, theirs_n, f"guide {rid}"))
        if notes:
            out.append(row(GUIDE, fact, "disagree", "\n".join(notes)))
        else:
            out.append(row(GUIDE, fact, "agree", f"{rid} worded alike"))
    return out


# ── 3. The prompts ──────────────────────────────────────────────────────────
def audit_prompts(onto: O.Ontology, root: Path) -> list[dict]:
    """Each rule-bearing prompt names every category and priority, and states every
    checkable rule, judged (ruling O3) by its guide id or one of its `phrases`."""
    text, why = _read(root, PROMPTS)
    if text is None:
        return [row(PROMPTS, "file", "missing", why)]
    try:
        triage = json.loads(text)["triage"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return [row(PROMPTS, "file", "missing", f"not JSON with a triage section: {e}")]
    out = []
    for key in PROMPT_KEYS:
        art = f"{PROMPTS}#triage.{key}"
        prompt = triage.get(key) if isinstance(triage, dict) else None
        if not isinstance(prompt, str):
            out.append(row(art, "prompt", "missing", f"no triage.{key} prompt text"))
            continue
        for cls, label in (("Category", "category"), ("Priority", "priority")):
            for iid in _ids(onto, cls):
                if _token(iid).search(prompt):
                    out.append(row(art, f"{label}:{iid}", "agree", f"names {iid}"))
                else:
                    out.append(row(art, f"{label}:{iid}", "missing", f"never names the {label} {iid}"))
        for cid, c in onto.constraints.items():
            if c.get("kind") != "checkable":
                continue
            rid = c.get("source")
            hit = rid if isinstance(rid, str) and _token(rid, re.I).search(prompt) else None
            for ph in c.get("phrases") or []:
                if hit is None and isinstance(ph, str) and ph.lower() in prompt.lower():
                    hit = f'"{ph}"'
            if hit:
                out.append(row(art, f"rule:{cid}", "agree", f"stated ({hit})"))
            else:
                out.append(row(art, f"rule:{cid}", "missing", f"states neither {rid} nor any of its phrases"))
    return out


# ── 4. The ML category map ──────────────────────────────────────────────────
def audit_category_map(onto: O.Ontology, root: Path) -> list[dict]:
    """The map's `categories` and the ontology's Product individuals are one set: a
    product either side lacks is `disagree`, since both are closed lists."""
    text, why = _read(root, CATEGORY_MAP)
    if text is None:
        return [row(CATEGORY_MAP, "file", "missing", why)]
    try:
        cats = json.loads(text)["categories"]
        assert isinstance(cats, list)
    except (json.JSONDecodeError, KeyError, TypeError, AssertionError):
        return [row(CATEGORY_MAP, "file", "missing", "not JSON with a categories list")]
    out = []
    labels = {}
    for pid in _ids(onto, "Product"):
        label = onto.individuals[pid].get("label", pid)
        labels[label] = pid
        if label in cats:
            out.append(row(CATEGORY_MAP, f"product:{pid}", "agree", f'"{label}" is in categories'))
        else:
            out.append(row(CATEGORY_MAP, f"product:{pid}", "disagree", f'"{label}" is not in categories'))
    for c in cats:
        if c not in labels:
            out.append(row(CATEGORY_MAP, f"category:{c}", "disagree", f'"{c}" is in categories but is no Product in the ontology'))
    return out


# ── 5. The data contracts ───────────────────────────────────────────────────
def _enum_row(art: str, name: str, enum, want: list[str], what: str) -> dict:
    fact = f"enum:{name}"
    if not isinstance(enum, list):
        return row(art, fact, "missing", f"{name} has no enum")
    extra = [str(v) for v in enum if v not in want]
    lacking = [v for v in want if v not in enum]
    if not extra and not lacking:
        return row(art, fact, "agree", f"the {len(want)} {what}")
    parts = []
    if extra:
        parts.append(f"extra value{'s' if len(extra) > 1 else ''} the ontology lacks: {', '.join(extra)}")
    if lacking:
        parts.append(f"lacks the ontology's {', '.join(lacking)}")
    return row(art, fact, "disagree", "; ".join(parts))


def audit_contracts(onto: O.Ontology, root: Path) -> list[dict]:
    """Each contract's enum for category, priority or urgency equals the ontology's
    individuals; a `product` enum equals the Product labels. Contracts with none of
    these fields imply none of the ontology's facts and add no rows."""
    files = sorted((root / CONTRACTS).glob("*.yaml"))
    if not files:
        return [row(CONTRACTS, "file", "missing", "no contracts found")]
    want = {f: _ids(onto, cls) for f, cls in ENUM_FIELDS.items()}
    want["product"] = [onto.individuals[p].get("label", p) for p in _ids(onto, "Product")]
    what = {"category": "categories", "priority": "priorities", "urgency": "priorities", "product": "products"}
    out = []
    for f in files:
        art = f"{CONTRACTS}/{f.name}"
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
            fields = doc["schema"]["fields"]
            assert isinstance(fields, list)
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            out.append(row(art, "file", "missing", f"not valid YAML: {' '.join(str(e).split())}"))
            continue
        except (KeyError, TypeError, AssertionError):
            out.append(row(art, "file", "missing", "no schema.fields list"))
            continue
        for fld in fields:
            name = fld.get("name") if isinstance(fld, dict) else None
            if name in want:
                out.append(_enum_row(art, name, fld.get("enum"), want[name], what[name]))
    return out


# ── 6. The audit and the gate ───────────────────────────────────────────────
SECTIONS = (audit_guide, audit_prompts, audit_category_map, audit_contracts)


def gate(rows: list[dict]) -> dict:
    """Red when any row disagrees, naming each artefact and fact; `missing` is counted
    but allowed."""
    bad = [f"{r['artefact']} {r['fact']}" for r in rows if r["status"] == "disagree"]
    missing = sum(r["status"] == "missing" for r in rows)
    if bad:
        reason = f"red: {len(bad)} disagree ({'; '.join(bad)}); {missing} missing"
    else:
        reason = f"green: no artefact disagrees with the ontology; {missing} missing"
    return {"ok": not bad, "disagree": len(bad), "missing": missing, "reason": reason}


def audit(onto: O.Ontology, root: Path = ROOT) -> dict:
    """Every section's rows, sorted by (artefact, fact), and the gate over them.
    `root` is the repo, or a fixture tree with the same layout."""
    rows = [r for section in SECTIONS for r in section(onto, Path(root))]
    rows.sort(key=lambda r: (r["artefact"], r["fact"]))
    return {"rows": rows, "gate": gate(rows)}


# ── 7. Write, check, command line ───────────────────────────────────────────
def write(out: Path = AUDIT, root: Path = ROOT) -> dict:
    data = audit(O.load(O.LUMEN), root)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(O.dumps(data), encoding="utf-8")
    return data


def _report(data: dict) -> None:
    g = data["gate"]
    print(f"gate: {'ok' if g['ok'] else 'RED'} ({g['disagree']} disagree, {g['missing']} missing)")
    for r in data["rows"]:
        if r["status"] == "disagree":
            print(f"  disagree {r['artefact']} {r['fact']}:")
            for line in r["detail"].splitlines():
                print(f"    {line}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit the course's artefacts against Lumen's ontology.")
    ap.add_argument("--check", action="store_true", help="fail if audit.json drifted")
    ap.add_argument("--out", type=Path, default=AUDIT)
    args = ap.parse_args(argv)
    if args.check:
        want = O.dumps(audit(O.load(O.LUMEN)))
        have = args.out.read_text(encoding="utf-8") if args.out.exists() else None
        if have != want:
            print(f"{args.out.name}: drifted or missing; run audit.py to rewrite it")
            return 1
        print(f"{args.out.name}: up to date")
        return 0
    data = write(args.out)
    print(f"wrote {args.out.name}: {len(data['rows'])} rows")
    _report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
