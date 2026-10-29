#!/usr/bin/env python3
"""
generate — the artefacts an AI system needs, written from the ontology alone.

The ontology (ontology/lumen.yaml) says what each category and priority means. Three
things are made from it: the definitions as prompt prose, the JSON schema of a triage
result, and the input schema of the MCP server's `label_ticket` tool. Each is then
compared with the artefact the course wrote by hand (the triage prompt's rule text,
the lab's output schema, the server's tool). The comparison is a fact for the lesson:
a unified diff, never a test failure.

Run (from the repo root):
    .venv-ontology/bin/python ontology/python/generate.py            # rewrite generated.json
    .venv-ontology/bin/python ontology/python/generate.py --check    # fail if it drifted
Free and offline: the other tracks' files are read, never imported or changed.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ontology as O  # noqa: E402

ROOT = O.ROOT
GENERATED = O.DERIVED / "generated.json"
PROMPTS = ROOT / "llms" / "data" / "prompts.json"
TRIAGE_LAB = ROOT / "llms" / "python" / "triage_lab.py"
OPS_SERVER = ROOT / "mcp" / "python" / "ops_server.py"
LUMEN_CORE = ROOT / "mcp" / "core" / "python" / "lumen_core" / "__init__.py"


# ── 1. Definitions ──────────────────────────────────────────────────────────
def _names(onto: O.Ontology, cls: str) -> list[str]:
    """The ids of a class's individuals in the guide's decision order (`order`)."""
    members = [(i["order"], iid) for iid, i in onto.individuals.items() if i.get("class") == cls]
    return [iid for _, iid in sorted(members)]


def definitions_text(onto: O.Ontology) -> str:
    """Categories in order, then priorities in order, one line each: `- <id>: <definition>`."""
    lines = []
    for cls in ("Category", "Priority"):
        for iid in _names(onto, cls):
            lines.append(f"- {iid}: " + " ".join(str(onto.individuals[iid].get("definition", "")).split()))
    return "\n".join(lines) + "\n"


# ── 2. Schema ───────────────────────────────────────────────────────────────
def json_schema(onto: O.Ontology) -> dict:
    """Structured output for a triage result: category and priority enumerations from the
    individuals, a free reply, nothing else allowed."""
    return {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": _names(onto, "Category")},
            "priority": {"type": "string", "enum": _names(onto, "Priority")},
            "reply": {"type": "string"},
        },
        "required": ["category", "priority", "reply"],
        "additionalProperties": False,
    }


# ── 3. Tool ─────────────────────────────────────────────────────────────────
def tool_definition(onto: O.Ontology) -> dict:
    """The `label_ticket` tool: the id's pattern comes from the Ticket id property, the
    label's enumeration from the categories."""
    id_prop = {"type": "string"}
    if "pattern" in onto.properties.get("id", {}):
        id_prop["pattern"] = onto.properties["id"]["pattern"]
    return {
        "name": "label_ticket",
        "input_schema": {
            "type": "object",
            "properties": {"id": id_prop, "label": {"type": "string", "enum": _names(onto, "Category")}},
            "required": ["id", "label"],
        },
    }


# ── 4. Compare ──────────────────────────────────────────────────────────────
def _module_values(path: Path) -> tuple[ast.Module, dict]:
    """A module's top-level assignments that evaluate from literals and earlier ones."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    env: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                env[node.targets[0].id] = eval(compile(ast.Expression(node.value), str(path), "eval"), {}, dict(env))
            except Exception:  # noqa: BLE001 - not a literal we can read; skipped
                pass
    return tree, env


def _rule_lines(prompt: str) -> str:
    """The lines that define categories and priorities: from 'Categories' to 'Reply:'."""
    start, end = prompt.index("Categories"), prompt.index("Reply:")
    return prompt[start:end].strip() + "\n"


def _udiff(a: str, b: str, name_a: str, name_b: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), name_a, name_b))


def _dump(obj: dict) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _existing_tool() -> dict:
    """label_ticket as the server declares it: parameters typed `str`, the label checked
    against lumen_core.CATEGORIES with a fixed error text. No pattern, no enum."""
    _, core = _module_values(LUMEN_CORE)
    tree, _ = _module_values(OPS_SERVER)
    env = {"CATEGORIES": core["CATEGORIES"]}
    err = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ERR_LABEL")
    fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "label_ticket")
    params = {a.arg: ast.unparse(a.annotation) for a in fn.args.args if a.arg in ("id", "label")}
    return {
        "name": fn.name,
        "input_schema": {
            "type": "object",
            "properties": {k: {"type": "string"} for k, t in params.items() if t == "str"},
            "required": list(params),
        },
        "error_label": eval(compile(ast.Expression(err.value), "ERR_LABEL", "eval"), {}, env),
        "label_values": core["CATEGORIES"],
    }


def compare(onto: O.Ontology) -> dict:
    """Per artefact: the course's hand-written one, the generated one and a unified diff
    (schemas are diffed as sorted-key JSON)."""
    prompts = json.loads(PROMPTS.read_text(encoding="utf-8"))["triage"]
    v2, v3 = _rule_lines(prompts["v2_rules"]), _rule_lines(prompts["v3_examples"])
    gen_text = definitions_text(onto)
    _, lab = _module_values(TRIAGE_LAB)
    lab_schema, gen_schema = lab["TRIAGE_SCHEMA"], json_schema(onto)
    old_tool, new_tool = _existing_tool(), tool_definition(onto)
    return {
        "definitions": {
            "existing": v2, "generated": gen_text, "diff": _udiff(v2, gen_text, "prompts.json triage.v2_rules", "generated definitions"),
            "v3_rule_lines_equal_v2": v2 == v3,
        },
        "schema": {
            "existing": lab_schema, "generated": gen_schema,
            "diff": _udiff(_dump(lab_schema), _dump(gen_schema), "triage_lab.py TRIAGE_SCHEMA", "generated json_schema"),
        },
        "tool": {
            "existing": old_tool, "generated": new_tool,
            "diff": _udiff(_dump(old_tool), _dump(new_tool), "ops_server.py label_ticket", "generated tool_definition"),
        },
    }


# ── 5. Entry point ──────────────────────────────────────────────────────────
def build(onto: O.Ontology) -> dict:
    return {"definitions_text": definitions_text(onto), "json_schema": json_schema(onto),
            "tool_definition": tool_definition(onto), "compare": compare(onto)}


def write(out: Path = GENERATED, onto: O.Ontology | None = None) -> dict:
    """Write generated.json (deterministic: sorted keys, trailing newline) and return it."""
    data = build(onto or O.load(O.LUMEN))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(O.dumps(data), encoding="utf-8")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the AI artefacts from Lumen's ontology.")
    ap.add_argument("--check", action="store_true", help="fail if generated.json drifted")
    ap.add_argument("--out", type=Path, default=GENERATED)
    args = ap.parse_args(argv)
    if args.check:
        want = O.dumps(build(O.load(O.LUMEN)))
        have = args.out.read_text(encoding="utf-8") if args.out.exists() else None
        if have != want:
            print(f"{args.out.name}: drifted or missing; run generate.py to rewrite it")
            return 1
        print(f"{args.out.name}: up to date")
        return 0
    write(args.out)
    print(f"wrote {args.out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
