#!/usr/bin/env python3
"""
catalog — the catalogue and lineage of the datasets the course's tracks share.

A data fabric starts with knowing what exists: where each dataset lives, how
big it is, what its rows look like, which track writes it and which tracks read
it. This module answers that for a fixed list of datasets (never a filesystem
crawl, so a stray file never sneaks in) and writes two files the app reads:
catalog.json (one entry per dataset) and lineage.json (producers, datasets and
consumers as a graph, with the contract and the declared version on each edge).

Run (from the repo root):
    .venv-data/bin/python data/python/catalog.py           # rewrite catalog.json and lineage.json
    .venv-data/bin/python data/python/catalog.py --check   # fail if either drifted
Free and offline: no model calls.

Datasets are read in place and never modified. A listed dataset with no files is
recorded as missing, never skipped. Files git ignores are left out, because a
fresh clone would not have them. last_commit is the full sha (ruling D18; the
app shows its first 7 characters). A zip download has no git history, and a
shallow clone has only its tip, so in both last_commit is null and --check
compares everything else, saying so (ruling D17).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import contract as C
import quality as Q

# ── 1. The datasets ─────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "data" / "contracts"
DERIVED = ROOT / "data" / "data" / "derived"
CATALOG = DERIVED / "catalog.json"
LINEAGE = DERIVED / "lineage.json"
SAMPLE_ROWS = 50  # the schema sketch reads this many rows of the first file

# Fixed list, never a crawl. `path` is a glob from the repo root (`**` reaches subfolders).
# A dataset under a contract takes its consumers from the contract's `consumers` section;
# the others declare theirs here. `derived_from` names the dataset a derived file is written from.
DATASETS = [
    {"id": "tickets", "path": "llms/data/tickets.jsonl", "producer": "llms", "contract": "tickets.v1",
     "description": "The 60 Lumen support tickets with gold category, priority and split."},
    {"id": "tickets_v2", "path": "data/data/tickets.v2.jsonl", "producer": "data", "contract": "tickets.v2",
     "derived_from": "tickets",
     "description": "The same tickets in the v2 shape (priority renamed urgency), written by migrate_tickets.py."},
    {"id": "complaints", "path": "ml/data/complaints.jsonl", "producer": "ml", "contract": "complaints.v1",
     "description": "The 3600 public consumer complaints the ML classifier trains on."},
    {"id": "category_map", "path": "ml/data/category_map.json", "producer": "ml",
     "consumers": ["ml-fetch-sample", "app-lessons"],
     "description": "How raw complaint products map onto the classifier's categories."},
    {"id": "reply_labels", "path": "llms/data/reply_labels.jsonl", "producer": "llms",
     "consumers": ["llms-triage-lab", "app-lessons"],
     "description": "Human labels on draft replies, the gold set for LLMs lesson 6's judge."},
    {"id": "model_responses", "path": "llms/data/responses/**/*.jsonl", "producer": "llms",
     "consumers": ["llms-replay", "app-lessons"],
     "description": "Saved model responses per prompt and model, replayed instead of paid calls."},
    {"id": "ml_reports", "path": "ml/reports/*.json", "producer": "ml",
     "consumers": ["mcp-read-server", "app-lessons"],
     "description": "The ML track's reports: dataset, metrics, drift, latency and the LLM comparison."},
    {"id": "ml_registry", "path": "ml/registry.json", "producer": "ml",
     "consumers": ["ml-serve", "mcp-read-server"],
     "description": "The model registry: versions and which one serves."},
    {"id": "mcp_recordings", "path": "mcp/data/recordings/**/*.json*", "producer": "mcp",
     "consumers": ["mcp-replay", "agents-run"],
     "description": "Recorded model calls the MCP agent replays offline."},
    {"id": "mcp_derived", "path": "mcp/data/derived/*.json", "producer": "mcp",
     "consumers": ["app-lessons"],
     "description": "Numbers and snapshots the MCP lessons and aids are built from."},
    {"id": "agents_traces", "path": "agents/data/traces/*/*.json", "producer": "agents", "contract": "agent-traces.v1",
     "description": "One recorded agent run per file, grouped by trace set."},
    {"id": "agents_derived", "path": "agents/data/derived/*.json", "producer": "agents",
     "consumers": ["app-lessons"],
     "description": "Scores, costs and patterns the agents lessons and aids are built from."},
]

# Every consumer the lineage names, and the track it belongs to.
CONSUMER_TRACKS = {
    "mcp-read-server": "mcp", "mcp-replay": "mcp", "agents-eval": "agents", "agents-eval-v2": "agents",
    "agents-run": "agents",
    "ml-fetch-sample": "ml", "ml-serve": "ml", "llms-triage-lab": "llms", "llms-replay": "llms",
    "app-lessons": "app",
}


def by_id(ds_id: str) -> dict:
    return next(d for d in DATASETS if d["id"] == ds_id)


def consumer_track(name: str) -> str:
    """The track a consumer belongs to; an unlisted name `<track>-<what>` names its own."""
    return CONSUMER_TRACKS.get(name, name.split("-")[0])


# ── 2. Files and git ────────────────────────────────────────────────────────
def _git(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess | None:
    """One git call in root; None without git (a zip download, or git not installed)."""
    try:
        return subprocess.run(["git", *args], cwd=root, input=stdin, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def _in_git(root: Path) -> bool:
    p = _git(root, "rev-parse", "--is-inside-work-tree")
    return p is not None and p.returncode == 0 and p.stdout.strip() == "true"


def files(root: Path, pattern: str) -> list[str]:
    """The dataset's files, relative to root, sorted; git-ignored files left out."""
    found = sorted(p.relative_to(root).as_posix() for p in Path(root).glob(pattern) if p.is_file())
    if found and _in_git(root):
        p = _git(root, "check-ignore", "--stdin", stdin="\n".join(found) + "\n")
        ignored = set(p.stdout.split("\n")) if p is not None and p.returncode == 0 else set()
        found = [f for f in found if f not in ignored]
    return found


def last_commit(root: Path, paths: list[str]) -> str | None:
    """Full sha of the last commit that changed any of the files; None without full history.

    The full sha, because git's own abbreviation grows a digit as the repo grows (ruling D18).
    """
    if not paths or not Q.has_history(Path(root)):
        return None
    p = _git(root, "log", "-1", "--format=%H", "--", *paths)
    sha = p.stdout.strip() if p is not None and p.returncode == 0 else ""
    return sha or None


# ── 3. The catalogue ────────────────────────────────────────────────────────
def sketch(rows: list[dict]) -> dict[str, str]:
    """Field name -> type, from the first SAMPLE_ROWS rows, in the six contract types.

    Nested values are just `object` or `array`. integer and number together are number;
    other mixes take the most common type. A field seen only as null sketches as string,
    since a sample of nulls says nothing about the type.
    """
    seen: dict[str, Counter] = {}
    for row in rows[:SAMPLE_ROWS]:
        for name, value in row.items():
            seen.setdefault(name, Counter())
            kind = C._json_type(value)
            if kind != "null":
                seen[name][kind] += 1
    out = {}
    for name, kinds in seen.items():
        if set(kinds) == {"integer", "number"}:
            out[name] = "number"
        elif kinds:
            out[name] = max(C.FIELD_TYPES, key=lambda k: (kinds[k], -C.FIELD_TYPES.index(k)))
        else:
            out[name] = "string"
    return out


def _contract_file(contracts: Path, contract_id: str) -> Path:
    return contracts / f"{contract_id}.yaml"


def base_id(contract_id: str) -> str:
    """tickets.v2 -> tickets: the name every major version of a contract shares."""
    return contract_id.rsplit(".v", 1)[0]


def _contracts(contracts: Path) -> list[dict]:
    return [C.load_contract(p) for p in sorted(Path(contracts).glob("*.yaml"))]


def catalog(root: Path, datasets: list[dict] | None = None) -> dict:
    """{id: {path, files, bytes, rows, schema, producer, consumers, contract, last_commit, missing, description}}.

    `consumers` lists tracks, from the contract when there is one, else the declared list.
    Each major version governs its own file (ruling D13), so a reader is listed under the
    file it reads: after the capstone, tickets (v1) for the MCP server, tickets_v2 for the agents.
    """
    root = Path(root)
    contracts = root / "data" / "contracts"
    out = {}
    for ds in datasets if datasets is not None else DATASETS:
        found = files(root, ds["path"])
        if ds.get("contract"):
            names = [c["name"] for c in C.load_contract(_contract_file(contracts, ds["contract"]))["consumers"]]
        else:
            names = ds.get("consumers", [])
        rows = 0
        schema: dict[str, str] = {}
        for i, rel in enumerate(found):
            got = C.read_rows(root / rel)
            rows += len(got)
            if i == 0:
                schema = sketch(got)
        out[ds["id"]] = {
            "path": ds["path"],
            "files": len(found),
            "bytes": sum((root / rel).stat().st_size for rel in found),
            "rows": rows,
            "schema": schema,
            "producer": ds["producer"],
            "consumers": sorted({consumer_track(n) for n in names}),
            "contract": ds.get("contract"),
            "last_commit": last_commit(root, found),
            "missing": not found,
            "description": ds["description"],
        }
    return out


# ── 4. Lineage ──────────────────────────────────────────────────────────────
def lineage(cat: dict, contracts: list[dict], datasets: list[dict] | None = None) -> dict:
    """Producers -> datasets -> consumers, with the contract and version on each edge.

    A producer edge carries the dataset's contract and its version; a consumer edge
    carries the version that consumer declares (ruling D2). A derived dataset also gets
    an edge from its source, carrying the source's contract: the migration reads
    tickets under tickets.v1 and writes tickets_v2. Datasets without a contract take
    their declared consumers, with no contract and no version.
    """
    by_contract = {c["info"]["id"]: c for c in contracts}
    listed = datasets if datasets is not None else DATASETS
    declared = {d["id"]: d.get("consumers", []) for d in listed}
    sources = {d["id"]: d["derived_from"] for d in listed if d.get("derived_from")}
    nodes: dict[str, dict] = {}
    edges = []
    for ds_id, entry in cat.items():
        producer, cid = entry["producer"], entry["contract"]
        governing = by_contract.get(cid) if cid else None
        nodes[producer] = {"id": producer, "kind": "track"}
        nodes[ds_id] = {"id": ds_id, "kind": "dataset", "missing": entry["missing"]}
        edges.append({"from": producer, "to": ds_id, "contract": cid,
                      "version": governing["info"]["version"] if governing else None})
        if ds_id in sources:
            src = cat[sources[ds_id]]["contract"]
            edges.append({"from": sources[ds_id], "to": ds_id, "contract": src,
                          "version": by_contract[src]["info"]["version"] if src in by_contract else None})
        if governing:
            readers = [(c["name"], cid, c["version"]) for c in governing["consumers"]]
        else:
            readers = [(name, None, None) for name in declared.get(ds_id, [])]
        for name, rid, version in readers:
            nodes[name] = {"id": name, "kind": "consumer", "track": consumer_track(name)}
            edges.append({"from": ds_id, "to": name, "contract": rid, "version": version})
    return {"nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "edges": sorted(edges, key=lambda e: (e["from"], e["to"]))}


def missing_versions(contracts: Path = CONTRACTS) -> list[str]:
    """One line per consumer whose declared version has no <id>.v<n>.yaml beside its contract."""
    out = []
    for path in sorted(Path(contracts).glob("*.yaml")):
        doc = C.load_contract(path)
        base = base_id(doc["info"]["id"])
        for c in doc["consumers"]:
            want = f"{base}.v{c['version']}.yaml"
            if not (Path(contracts) / want).is_file():
                out.append(f"{path.name}: consumer {c['name']} declares version {c['version']}, but {want} does not exist")
    return out


# ── 5. catalog.json and lineage.json ────────────────────────────────────────
def _text(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def build(root: Path = ROOT) -> tuple[dict, dict]:
    cat = catalog(root)
    return cat, lineage(cat, _contracts(Path(root) / "data" / "contracts"))


def write() -> tuple[dict, dict]:
    cat, lin = build()
    DERIVED.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(_text(cat), encoding="utf-8")
    LINEAGE.write_text(_text(lin), encoding="utf-8")
    return cat, lin


def _without_commits(doc: dict) -> dict:
    """Without git history there are no commits: compare everything but last_commit."""
    return {k: {**v, "last_commit": None} for k, v in doc.items()}


def check() -> int:
    """0 when both files are byte-identical to a rebuild; 1 naming the stale one."""
    history = Q.has_history(ROOT)
    if not history:
        print(Q.NO_HISTORY)
    cat, lin = build()
    stale = []
    for path, fresh in ((CATALOG, cat), (LINEAGE, lin)):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            stale.append(path.name)
            continue
        if text == _text(fresh):
            continue
        if path == CATALOG and not history and _without_commits(json.loads(text)) == _without_commits(fresh):
            continue
        stale.append(path.name)
    if stale:
        print(f"{', '.join(stale)} stale: run data/python/catalog.py")
        return 1
    print("catalog.json and lineage.json match")
    return 0


# ── 6. Command line ─────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Catalogue the shared datasets and write their lineage.")
    ap.add_argument("--check", action="store_true", help="fail if catalog.json or lineage.json drifted")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    cat, lin = write()
    for ds_id, e in cat.items():
        state = "MISSING" if e["missing"] else f"{e['files']} files, {e['rows']} rows, {e['bytes']} bytes"
        print(f"{ds_id}: {state} ({e['producer']} -> {', '.join(e['consumers']) or '-'})")
    print(f"wrote {CATALOG.name} ({len(cat)} datasets), {LINEAGE.name} ({len(lin['edges'])} edges)")
    return 1 if any(e["missing"] for e in cat.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
