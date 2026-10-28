#!/usr/bin/env python3
"""derive — turn the committed traces into the small JSON files the app's lesson aids read.

    python agents/derive.py            # write agents/data/derived/{patterns,traces,cost_compare,sets}.json
    python agents/derive.py --check    # exit 1, naming the file, when a regenerated file differs from the one on disk

Input (read only): agents/data/traces/<set>/<engine>-<desk>-<ticket>.json (agents/run.py), the recordings folder
agents/data/recordings/<set>/ (file names only), agents/data/derived/engine_scores.json (agents/evals/run_evals.py;
never rewritten here: --check runs run_evals.py's own --check), and the tickets (agents/evals/tickets.jsonl).

Output:
- patterns.json      PatternStepper: the five patterns x t001, t007, t061 x hand, hand-go, step by step.
- traces.json        AgentTrace: single and supervisor x the four engines x a few tickets, step by step.
- cost_compare.json  CostCompare (lesson 6, "when more agents hurt"): every engine x desk over all 13 tickets.
- sets.json          lesson 7 (record and replay): each recording set, its runs, calls, cost and store.

Rules every file keeps:
- Cost and latency come only from a trace's proxy_calls ledger (rulings R62, R64): the sum of cost_usd and of ms
  of the recorded calls. Never the run's `spent` or top-level `cost_usd` (what that run paid or estimated) and
  never a trace record's `ms` (the local wall clock; about 1 ms for a store hit). No ledger: None, not 0.
- A run with no trace is {"recorded": false, "command": <the record command>}, never a made-up number.
- Deterministic: sorted keys, stable order, ensure_ascii=False, compact separators, one trailing newline;
  relative paths only.
- The four files together stay under BUDGET_BYTES (the spec's page budget, §6).

Reading one trace (the excerpt caps, a run's steps, its ledger totals) is derive_read.py, beside this file.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

AGENTS = Path(__file__).resolve().parent
REPO = AGENTS.parent
sys.path.insert(0, str(AGENTS))

import run  # noqa: E402  (agents/run.py: the sets, the trace layout, the record command)

# Sections 1 and 2 of derive_read.py: the excerpt caps and reading a trace, under the names derive.<name> has always had.
from derive_read import (ARGS_CAP, COST_DIGITS, FENCE, MAX_COST, REPLY_CAP, STEP_CAP, WRITE_TOOLS,  # noqa: E402,F401
                         _cost, _num, _sum, args_text, excerpt, ledger, load, model_step, not_recorded, steps_of,
                         tool_step, totals)


core = run.core

# ── 1. Configuration ────────────────────────────────────────────────────────
TRACES_DIR = run.TRACES_DIR
STORE_DIR = run.STORE_DIR
OUT_DIR = AGENTS / "data" / "derived"
SCORES_FILE = "engine_scores.json"
FILES = ("patterns.json", "traces.json", "cost_compare.json", "sets.json")
BUDGET_BYTES = 200 * 1024                          # spec §6: new props under about 200 KB, all four files together

PATTERNS = run.PATTERNS                            # chain, route, parallel, orchestrate, evaluate_optimize
PATTERN_ENGINES = ("hand", "hand-go")
PATTERN_TICKETS = run.PATTERN_TICKETS              # t001, t007, t061
ENGINES = run.ENGINE_NAMES                         # hand, hand-go, langgraph, sdk
DESKS = run.DESKS                                  # single, supervisor
DESK_TICKETS = run.ALL_TICKETS                     # the 13 eval tickets
TRACE_TICKETS = ("t001", "t007", "t061")           # AgentTrace's fixed tickets, plus the longest supervisor loop
PLANNED_LOOP = "t010"                              # the plan's guess for the longest drafter -> reviewer loop

RULINGS = {
    "R62": "cost and latency come from the recording: the sum of the trace's proxy_calls cost_usd and ms, served "
           "again on replay; never what one run paid (spent, cost_usd) or measured locally (a record's ms)",
    "R64": "the ledger keeps input, cache-write, cache-read and output tokens apart; cache writes are priced at "
           "1.25x and cache reads at 0.10x the input price, so prompt caching shows as a lower cost",
    "R66": "the gate stays as built (0.7 minus the 1/sqrt(n) band); with 13 tickets no engine clears it. That is "
           "the real result and is shown as it is, never adjusted",
}


# ── 2. patterns.json (PatternStepper) ───────────────────────────────────────
def _verdict(v: Any) -> str | None:
    if isinstance(v, dict):
        return ", ".join(f"{k}: {str(x).lower()}" for k, x in sorted(v.items()))
    return excerpt(v, STEP_CAP)


def pattern_results(pattern: str, calls: list[dict], result: dict) -> tuple[list[str | None], str | None]:
    """What each model call produced (in output.calls order) and the pattern's final reply."""
    out: list[str | None] = [None] * len(calls)
    agents = [c.get("agent") for c in calls]
    if pattern in ("chain", "route"):
        first = result.get("category" if pattern == "chain" else "route")
        if out:
            out[0] = excerpt(first, STEP_CAP)
        if len(out) > 1:
            out[-1] = excerpt(result.get("reply"), STEP_CAP)
        return out, result.get("reply")
    if pattern == "parallel":
        lookups = result.get("lookups") or []
        for i, a in enumerate(agents):
            if str(a).startswith("lookup-"):
                n = int(str(a).split("-")[1]) - 1
                if 0 <= n < len(lookups):
                    lk = lookups[n]
                    out[i] = excerpt(f"{lk.get('tool')}({args_text(lk.get('args')) or ''})", STEP_CAP)
            elif a == "summarizer":
                out[i] = excerpt(result.get("summary"), STEP_CAP)
        return out, result.get("summary")
    if pattern == "orchestrate":
        results = result.get("results") or []
        for i, a in enumerate(agents):
            if a == "orchestrator":
                out[i] = excerpt("; ".join(str(p) for p in result.get("plan") or []), STEP_CAP)
            elif a == "synthesizer":
                out[i] = excerpt(result.get("reply"), STEP_CAP)
            elif str(a).startswith("worker-") and (i + 1 == len(agents) or agents[i + 1] != a):
                n = int(str(a).split("-")[1]) - 1     # a worker's result belongs to its last call
                if 0 <= n < len(results):
                    out[i] = excerpt(results[n], STEP_CAP)
        return out, result.get("reply")
    if pattern == "evaluate_optimize":
        rounds = result.get("rounds") or []
        w = j = 0
        for i, a in enumerate(agents):
            if a == "writer":
                out[i] = excerpt((rounds[w] if w < len(rounds) else {}).get("draft"), STEP_CAP)
                w += 1
            elif a == "judge":
                out[i] = _verdict((rounds[j] if j < len(rounds) else {}).get("verdict"))
                j += 1
        return out, result.get("final")
    return out, result.get("reply")


def pattern_run(doc: dict, pattern: str) -> dict:
    output = doc["output"]
    result = output.get("result") or {}
    calls = output.get("calls") or []
    per_call, final = pattern_results(pattern, calls, result)
    tool_args = result.get("lookups") if pattern == "parallel" else []
    steps = steps_of(doc, tool_args=tool_args, handoffs=[])
    i = 0
    for s in steps:
        if s["kind"] == "model":
            if i < len(calls):
                s["role"] = calls[i].get("role")
            s["result"] = per_call[i] if i < len(per_call) else None
            i += 1
    t = totals(doc)
    return {"steps": steps, "reply": excerpt(final, REPLY_CAP), "calls": t["calls"], "cost_usd": t["cost_usd"],
            "ms": t["ms"], "errors": len(doc.get("errors") or [])}


def build_patterns(traces_dir: Path) -> dict:
    patterns: dict[str, dict] = {}
    notes: dict[str, dict] = {}
    for p in PATTERNS:
        by_ticket: dict[str, dict] = {}
        compared: dict[str, dict] = {}
        for tid in PATTERN_TICKETS:
            cell: dict[str, dict] = {}
            for engine in PATTERN_ENGINES:
                set_name = "patterns" if engine == "hand" else "patterns-go"
                doc = load(traces_dir, set_name, engine, f"pattern:{p}", tid)
                cell[engine] = pattern_run(doc, p) if doc else not_recorded(engine, f"pattern:{p}", [tid])
            by_ticket[tid] = cell
            if all("calls" in cell[e] for e in PATTERN_ENGINES):
                compared[tid] = {e: cell[e]["calls"] for e in PATTERN_ENGINES}
        patterns[p] = by_ticket
        same = all(len(set(v.values())) == 1 for v in compared.values()) if compared else None
        differ = [f"{t}: Python {v['hand']}, Go {v['hand-go']}" for t, v in compared.items() if v["hand"] != v["hand-go"]]
        notes[p] = {"same_calls": same, "by_ticket": compared,
                    "note": ("no ticket recorded in both languages" if same is None else
                             "Python and Go made the same number of model calls on every ticket" if same else
                             "Python and Go made different numbers of model calls (" + "; ".join(differ) + "): "
                             "the recorded replies differed (a judge passed sooner, a worker took more tool turns), so a loop "
                             "ran a different number of times")}
    return {"about": about(
        "PatternStepper: the five hand-built patterns, step by step, in Python (hand) and Go (hand-go)",
        ["agents/data/traces/patterns/hand-<pattern>-<ticket>.json",
         "agents/data/traces/patterns-go/hand-go-<pattern>-<ticket>.json"],
        steps="each trace record in order: model (agent, role, tokens, cost and ms from the matching proxy_calls "
              f"entry, result: what that call produced, at most {STEP_CAP} characters) and tool (name, args); "
              "a parallel pattern's tool args come from its lookups, an orchestrator's workers' tool args are not "
              "logged (null). A worker's result sits on its last call",
        reply=f"the pattern's final reply (summary for parallel, final for evaluate_optimize), at most {REPLY_CAP} "
              "characters",
        notes="per pattern: did Python and Go make the same number of model calls on each ticket recorded in both"),
        "patterns": patterns, "notes": notes, "tickets": list(PATTERN_TICKETS), "engines": list(PATTERN_ENGINES)}


# ── 3. traces.json (AgentTrace) ─────────────────────────────────────────────
def loop_length(doc: dict) -> int:
    """How many times the supervisor sent a draft to the reviewer."""
    return sum(1 for h in doc["output"].get("handoffs") or [] if h.get("worker") == "reviewer")


def longest_loop(traces_dir: Path) -> tuple[str, dict[str, int]]:
    """The ticket whose supervisor runs, summed over the engines, looped drafter -> reviewer most. Ties go to the
    plan's ticket, then the lowest id."""
    counts: dict[str, int] = {}
    for tid in DESK_TICKETS:
        docs = [load(traces_dir, f"desk-{e}", e, "supervisor", tid) for e in ENGINES]
        counts[tid] = sum(loop_length(d) for d in docs if d)
    if not any(counts.values()):
        return PLANNED_LOOP, counts
    best = max(counts.values())
    tied = sorted(t for t, c in counts.items() if c == best)
    return (PLANNED_LOOP if PLANNED_LOOP in tied else tied[0]), counts


def build_traces(traces_dir: Path, scores: dict | None) -> dict:
    loop, counts = longest_loop(traces_dir)
    tickets = sorted(set(TRACE_TICKETS) | {loop})
    runs: dict[str, dict] = {}
    for desk in DESKS:
        for engine in ENGINES:
            for tid in tickets:
                doc = load(traces_dir, f"desk-{engine}", engine, desk, tid)
                if doc is None:
                    entry = not_recorded(engine, desk, [tid])
                else:
                    t = totals(doc)
                    entry = {"steps": steps_of(doc), "reply": excerpt(doc["output"].get("reply"), REPLY_CAP),
                             "stop": doc["output"].get("stop"), "totals": t,
                             "score": score_row(scores, engine, desk).get("runs", {}).get(tid)}
                runs.setdefault(desk, {}).setdefault(engine, {})[tid] = entry
    texts = {t["id"]: t for t in core.TICKETS}
    loop_note = (f"{loop} is the ticket whose supervisor runs looped drafter -> reviewer longest "
                 f"({counts.get(loop, 0)} reviews over the four engines)")
    if loop != PLANNED_LOOP:
        loop_note += f"; the plan named {PLANNED_LOOP} ({counts.get(PLANNED_LOOP, 0)} reviews), so {loop} is shown instead"
    return {"about": about(
        "AgentTrace: the single and supervisor desks in all four engines, step by step, on a few tickets",
        ["agents/data/traces/desk-<engine>/<engine>-<desk>-<ticket>.json", f"agents/data/derived/{SCORES_FILE}",
         "agents/evals/tickets.jsonl"],
        tickets=f"{', '.join(TRACE_TICKETS)} (t061 is the prompt injection), plus {loop_note}",
        steps="each trace record in order: model (tokens, cost and ms from the matching proxy_calls entry), tool "
              f"(name, args at most {ARGS_CAP} characters; write: refused or written for label_ticket, the only write; "
              "the guard's scripted person says no, so every write here is refused), handoff (worker, task at most "
              f"{ARGS_CAP} characters, the state keys it changed), stop (max_turns) and error. The LangGraph and SDK "
              "desks log a refused write only in tool_calls: it is placed by its order there, or before the final "
              "model call, with traced: false",
        score=f"the scorer's per-run row for that ticket, copied from {SCORES_FILE}",
        totals="calls, cost_usd and ms are the ledger's (R62); tokens_in is uncached input, cache_read and "
               "cache_write the cached parts (R64)"),
        "tickets": tickets, "loop_ticket": loop, "loop_reviews": counts,
        "ticket_text": {t: {"category": texts[t].get("category"), "text": excerpt(texts[t].get("text"), STEP_CAP)}
                        for t in tickets if t in texts},
        "runs": runs}


# ── 4. cost_compare.json (CostCompare, lesson 6) ────────────────────────────
def score_row(scores: dict | None, engine: str, desk: str) -> dict:
    return ((scores or {}).get("scores") or {}).get(engine, {}).get(desk) or {}


def quantile(values: list[float], q: float) -> float:
    """Linear interpolation between the closest ranks (statistics.quantiles' inclusive method)."""
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def spread(values: list) -> dict:
    return {"sum": sum(values), "mean": _num(sum(values) / len(values))}


def desk_row(traces_dir: Path, engine: str, desk: str, scores: dict | None) -> dict:
    docs = {t: load(traces_dir, f"desk-{engine}", engine, desk, t) for t in DESK_TICKETS}
    missing = [t for t, d in docs.items() if d is None]
    if missing:
        return {**not_recorded(engine, desk, missing), "missing": missing}
    tots = {t: totals(d) for t, d in docs.items()}
    srow = score_row(scores, engine, desk)
    sruns = srow.get("runs") or {}
    calls = [x["calls"] for x in tots.values()]
    costs = [x["cost_usd"] for x in tots.values()]
    ms = [x["ms"] for x in tots.values()]
    full_cost, full_ms = None not in costs, None not in ms
    tok = {k: [x[k] for x in tots.values()] for k in ("tokens_in", "tokens_out", "cache_read")}
    return {"n": len(docs),
            "calls_per_ticket": {"mean": _num(statistics.mean(calls)), "min": min(calls), "max": max(calls)},
            "tokens": {k2: (spread(v) if None not in v else None)
                       for k2, v in (("in", tok["tokens_in"]), ("out", tok["tokens_out"]), ("cache_read", tok["cache_read"]))},
            "mean_cost_usd": _cost(statistics.mean(costs)) if full_cost else None,
            "median_cost_usd": _cost(statistics.median(costs)) if full_cost else None,
            "p50_ms": _num(statistics.median(ms)) if full_ms else None,
            "p90_ms": _num(quantile(ms, 0.9)) if full_ms else None,
            "success_rate": srow.get("success_rate"), "band": srow.get("band"), "status": srow.get("status"),
            "tickets": [{"ticket": t, "cost_usd": tots[t]["cost_usd"], "ms": tots[t]["ms"], "calls": tots[t]["calls"],
                         "success": (sruns.get(t) or {}).get("task_success")} for t in DESK_TICKETS]}


def ratio(a: float | None, b: float | None) -> float | None:
    return None if a is None or b in (None, 0) else round(a / b, 3)


def build_cost_compare(traces_dir: Path, scores: dict | None) -> dict:
    engines: dict[str, dict] = {}
    for engine in ENGINES:
        rows = {desk: desk_row(traces_dir, engine, desk, scores) for desk in DESKS}
        single, sup = rows["single"], rows["supervisor"]
        both = single.get("recorded") is not False and sup.get("recorded") is not False
        sd = (None if not both or single.get("success_rate") is None or sup.get("success_rate") is None
              else round(sup["success_rate"] - single["success_rate"], 6))
        rows["supervisor_vs_single"] = None if not both else {
            "cost": ratio(sup["mean_cost_usd"], single["mean_cost_usd"]),
            "latency": ratio(sup["p50_ms"], single["p50_ms"]),
            "calls": ratio(sup["calls_per_ticket"]["mean"], single["calls_per_ticket"]["mean"]),
            "success_delta": sd,
            "band": single.get("band")}
        engines[engine] = rows
    gate = (scores or {}).get("gate")
    return {"about": about(
        "CostCompare (lesson 6, when more agents hurt): every engine's single and supervisor desks on all 13 tickets",
        ["agents/data/traces/desk-<engine>/<engine>-<desk>-<ticket>.json", f"agents/data/derived/{SCORES_FILE}"],
        calls="model calls per ticket = the run's proxy_calls entries",
        tokens="sums and per-ticket means of the ledger's input (uncached), output and cache_read tokens (R64)",
        cost="mean and median over tickets of each run's ledger cost (R62)",
        latency="p50 (median) and p90 over tickets of each run's ledger ms (R62); p90 interpolates linearly "
                "between the closest ranks",
        success=f"success_rate, band and status copied from {SCORES_FILE}; per ticket, task_success",
        supervisor_vs_single="per engine: supervisor / single for mean cost, p50 latency and mean calls; "
                             "success_delta = supervisor - single success_rate, to read against the band "
                             "(a difference smaller than the band is not evidence)",
        gate=f"copied from {SCORES_FILE}: with 13 tickets no engine clears it (R66)"),
        "engines": engines, "gate": gate, "tickets": list(DESK_TICKETS)}


# ── 5. sets.json (lesson 7, record and replay) ──────────────────────────────
def set_tickets(spec: run.SetSpec) -> tuple[str, ...]:
    return PATTERN_TICKETS if spec.desks[0].startswith("pattern:") else DESK_TICKETS


def set_docs(traces_dir: Path, name: str, spec: run.SetSpec) -> list[dict | None]:
    """Every expected run of a set (desks x tickets), its trace or None."""
    return [load(traces_dir, name, spec.engine, d, t) for d in spec.desks for t in set_tickets(spec)]


def used_recordings(present: list[dict]) -> dict[str, Any]:
    """R67: the distinct recordings the runs used (their proxy_calls keys), key -> its cost; also what
    agents/prune.py keeps (R73)."""
    used: dict[str, Any] = {}
    for d in present:
        for c in ledger(d):
            used.setdefault(c.get("key"), c.get("cost_usd"))
    return used


def store_keys(store_dir: Path, name: str) -> set[str]:
    store = Path(store_dir) / name
    return {f.stem for f in store.glob("*.json")} if store.is_dir() else set()


def judge_set(traces_dir: Path, store_dir: Path) -> dict:
    """The judge set (R73): the judge recordings the current traces' scores use, and the store files they do not."""
    if str(AGENTS / "evals") not in sys.path:
        sys.path.insert(0, str(AGENTS / "evals"))
    import run_evals
    wanted = run_evals.judge_keys(traces_dir)
    on_disk = store_keys(store_dir, run_evals.JUDGE_SET)
    return {"recordings": len(on_disk & wanted), "missing": len(wanted - on_disk), "orphans": len(on_disk - wanted),
            "record": "python agents/evals/run_evals.py --record-judge"}


def build_sets(traces_dir: Path, store_dir: Path) -> dict:
    sets: dict[str, dict] = {}
    for name, spec in sorted(run.SETS.items()):
        docs = set_docs(traces_dir, name, spec)
        present = [d for d in docs if d]
        costs = [c.get("cost_usd") for d in present for c in ledger(d)]
        used = used_recordings(present)
        on_disk = store_keys(store_dir, name)
        sets[name] = {"engine": spec.engine, "desks": [d.removeprefix("pattern:") for d in spec.desks],
                      "tickets": len(set_tickets(spec)), "expected_runs": len(docs), "runs": len(present),
                      "complete": len(present) == len(docs),
                      "model_calls": sum(len(ledger(d)) for d in present),
                      "recorded_cost_usd": _cost(_sum(costs)),
                      "distinct_recorded_cost_usd": _cost(_sum(list(used.values()))),
                      "recordings": len(set(used) & on_disk), "missing": len(set(used) - on_disk),
                      "orphans": len(on_disk - set(used)),
                      "replay": f"python agents/run.py --set {name}",
                      "record": f"python agents/run.py --set {name} --mode record --max-cost {MAX_COST}"}
    return {"about": about(
        "Lesson 7 (record and replay): each recording set, what it holds and what it cost to record",
        ["agents/data/traces/<set>/", "agents/data/recordings/<set>/"],
        runs="traces present out of expected_runs (desks x tickets); complete when every run has a trace",
        model_calls="the proxy_calls entries over the set's runs",
        recorded_cost_usd="the ledger cost over the set's runs (R62): what the recorded calls cost, counted per "
                          "run, so a recording that serves two runs counts twice; not what the recording session "
                          "paid (a store hit is free)",
        distinct_recorded_cost_usd="the ledger cost of each distinct recording the runs used, counted once",
        recordings="R67: the distinct recordings the set's runs used (distinct proxy_calls keys over its traces); "
                   "a replay of the set serves exactly these",
        missing="the keys the set's runs (or, for the judge set, the current requests) reference that the store "
                "does not hold: a replay of those calls would miss; 0 when the store is whole",
        orphans="R67: the store files for the set that no run references (superseded by a later re-record); "
                "replay never reads them, and agents/prune.py deletes them (R73)",
        judge="the eval's judge set (agents/evals/run_evals.py): recordings = the judge requests the current "
              "traces ask for that the store holds; orphans = judge store files no current request uses (R73)",
        replay="free: replays every model call from the store and runs the real lumen tools; a request the store "
               "does not hold stops with a 404 naming the record command, never a paid call",
        record="paid, after the user approves the printed estimate (add --dry-run to see it); the judge set is "
               "recorded by agents/evals/run_evals.py --record-judge"),
        "sets": sets, "judge": judge_set(traces_dir, store_dir)}


# ── 6. All four files ───────────────────────────────────────────────────────
def about(what: str, sources: list[str], **fields: str) -> dict:
    return {"what": what, "sources": sources, "rulings": RULINGS,
            "budget": f"the four derived files together stay under {BUDGET_BYTES // 1024} KB (spec §6), written as "
                      "compact JSON (indented they would be about 237 KB); "
                      f"excerpts are capped at {STEP_CAP} (step), {REPLY_CAP} (reply) and {ARGS_CAP} (args, task) "
                      "characters",
            "not_recorded": '{"recorded": false, "command": ...} where a trace is missing, never a number',
            **fields}


def build(traces_dir: Path = TRACES_DIR, store_dir: Path = STORE_DIR, scores: dict | None = None) -> dict[str, dict]:
    traces_dir, store_dir = Path(traces_dir), Path(store_dir)
    return {"patterns.json": build_patterns(traces_dir),
            "traces.json": build_traces(traces_dir, scores),
            "cost_compare.json": build_cost_compare(traces_dir, scores),
            "sets.json": build_sets(traces_dir, store_dir)}


def render(doc: dict) -> str:
    """Compact (no indentation): indented, the four files are about 237 KB, over the budget; compact they fit
    with every excerpt at its cap. Sorted keys keep it deterministic; read it with `jq .`."""
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"


def read_scores(out_dir: Path) -> dict | None:
    path = Path(out_dir) / SCORES_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── 7. Entry point ──────────────────────────────────────────────────────────
def main(argv: list[str] | None = None, *, traces_dir: Path = TRACES_DIR, store_dir: Path = STORE_DIR,
         out_dir: Path = OUT_DIR, evals_check: Callable[[], int] | None = None) -> int:
    """`evals_check` (tests only) stands in for run_evals.py --check, which scores the real traces."""
    p = argparse.ArgumentParser(description="Write the app's derived lesson data from the traces (free).")
    p.add_argument("--check", action="store_true", help="exit 1 if a derived file differs from the traces")
    args = p.parse_args(argv)
    out_dir = Path(out_dir)
    texts = {name: render(doc) for name, doc in build(traces_dir, store_dir, read_scores(out_dir)).items()}
    total = 0
    for name, text in texts.items():
        size = len(text.encode("utf-8"))
        total += size
        print(f"{name:<18} {size / 1024:7.1f} KB")
    print(f"{'total':<18} {total / 1024:7.1f} KB (budget {BUDGET_BYTES // 1024} KB)")
    if total > BUDGET_BYTES:
        print(f"derive: over budget: {total} bytes > {BUDGET_BYTES}; shorten the excerpts or the tickets")
        return 1
    if args.check:
        drifted = [n for n, text in texts.items()
                   if not (out_dir / n).is_file() or (out_dir / n).read_text(encoding="utf-8") != text]
        if evals_check is None:
            evals_check = lambda: _run_evals_check(Path(traces_dir), Path(store_dir), out_dir)  # noqa: E731
        scores_drifted = evals_check() != 0      # run_evals prints its own line for engine_scores.json
        for n in drifted:
            print(f"{n} has drifted from the traces; regenerate with: "
                  + ("python agents/evals/run_evals.py" if n == SCORES_FILE else "python agents/derive.py"))
        if drifted or scores_drifted:
            return 1
        print("derived files match the traces")
        return 0
    for name, text in texts.items():
        (out_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (out_dir / name).write_text(text, encoding="utf-8")
        print(f"wrote {run.rel(out_dir / name)}")
    return 0


def _run_evals_check(traces_dir: Path, store_dir: Path, out_dir: Path) -> int:
    """run_evals.py's own --check (replay only): engine_scores.json is never rewritten here."""
    if str(AGENTS / "evals") not in sys.path:
        sys.path.insert(0, str(AGENTS / "evals"))
    import run_evals
    return run_evals.main(["--check"], traces_dir=traces_dir, store_dir=store_dir, out=out_dir / SCORES_FILE)


if __name__ == "__main__":
    sys.exit(main())
