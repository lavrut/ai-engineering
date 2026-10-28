#!/usr/bin/env python3
"""run — one command runs any engine on any desk, through the record/replay proxy, and writes the traces.

    python agents/run.py --engine hand|hand-go|langgraph|sdk --desk pattern:<name>|pattern:all|single|supervisor|all
                         [--tickets all|<id>[,<id>...]] [--mode replay|record] [--refresh] [--dry-run] [--yes]
                         [--max-cost N] [--max-calls N]
    python agents/run.py --set patterns|patterns-go|desk-hand|desk-langgraph|desk-sdk [...]

Replay (the default) is free: each ticket gets its own proxy run (R25) answering from
agents/data/recordings/<set>/, and a request with no recording stops the run with the command that records it.
`--dry-run` prints what recording the set would cost (calls, typical, worst case, the cap) and calls nothing.
`--mode record` is paid: it needs ANTHROPIC_API_KEY in the environment (read by the proxy only, never by an
engine), prints the estimate, asks y/N once (`--yes` answers yes: only after the user approved), then records
ticket by ticket, with the cap checked by the proxy before every call. The engines always use the dummy key.
`--refresh` (record mode only) re-records: a request already in the store goes upstream again, paid, and its new
recording replaces the old one.

Each finished run writes agents/data/traces/<set>/<engine>-<desk>-<ticket>.json: the R2 trace records plus the
run's output, errors, the engine's own cost_usd (the Agent SDK's estimate; None elsewhere), the proxy's spend, and
`proxy_calls`, the proxy's ledger (R62): per model call, the recording's upstream ms and tokens and their cost. The
eval reads cost and latency from it, so a replay (or a store hit while recording) costs and takes what the recorded
call did. Replaying a set rebuilds its traces, ledger included.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

AGENTS = Path(__file__).resolve().parent
REPO = AGENTS.parent
for sub in ("desk/python", "frameworks", "proxy"):
    sys.path.insert(0, str(AGENTS / sub))

import desk_core as core  # noqa: E402
import proxy  # noqa: E402
from triage_lab import PRICES, call_cost  # noqa: E402  (the LLMs lab's prices, import only)
from lumen_core import LABELS_PATH  # noqa: E402  (mcp/data/labels.jsonl, git-ignored; on sys.path via desk_core)

# ── 1. Configuration ────────────────────────────────────────────────────────
TRACES_DIR = AGENTS / "data" / "traces"
STORE_DIR = proxy.STORE_DIR                         # agents/data/recordings; one subdirectory per set
GO_DIR = AGENTS / "desk" / "go"
MODEL = core.MODEL
APPROVE = "decline"     # the person the guard asks says no (as the MCP track's capstone-guard recording)
ELICIT = "accept"       # lumen-ops' own confirmation would say yes: only the guard stops a write
PATTERNS = ("chain", "route", "parallel", "orchestrate", "evaluate_optimize")
PATTERN_TICKETS = ("t001", "t007", "t061")          # the contract's tickets: two billing tickets and the injection
DESKS = ("single", "supervisor")
ALL_TICKETS = tuple(t["id"] for t in core.TICKETS)  # the 13 eval tickets
DEFAULT_MAX_CALLS = 12                              # MAX_CALLS per proxy run, i.e. per ticket (R25)
MAX_CALLS_LIMIT = 20                                # R25, R61: a desk that needs more may have up to 20
SUPERVISOR_MAX_CALLS = 20                           # R61: typical 11-12 calls; the recorded runs that hit 16 wanted 17-21
MAX_TURNS_SINGLE = 8                                # supervisor.MAX_TURNS: the single agent cannot make more calls
GO_TIMEOUT = 1800                                   # seconds one hand-go run may take
NONESSENTIAL = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}   # R9: set for every engine run


@dataclass(frozen=True)
class SetSpec:
    """One recording set: one proxy --set store subdirectory."""
    name: str
    engine: str
    desks: tuple[str, ...]
    tickets: tuple[str, ...]


SETS: dict[str, SetSpec] = {
    "patterns": SetSpec("patterns", "hand", tuple(f"pattern:{p}" for p in PATTERNS), PATTERN_TICKETS),
    # R23: Go's requests are not byte-identical to Python's, so Go records its own set.
    "patterns-go": SetSpec("patterns-go", "hand-go", tuple(f"pattern:{p}" for p in PATTERNS), PATTERN_TICKETS),
    "desk-hand": SetSpec("desk-hand", "hand", DESKS, ALL_TICKETS),
    "desk-langgraph": SetSpec("desk-langgraph", "langgraph", DESKS, ALL_TICKETS),
    "desk-sdk": SetSpec("desk-sdk", "sdk", DESKS, ALL_TICKETS),
    "desk-hand-go": SetSpec("desk-hand-go", "hand-go", DESKS, ALL_TICKETS),
}
ENGINE_NAMES = ("hand", "hand-go", "langgraph", "sdk")


def set_for(engine: str, desk: str) -> str:
    """The recording set an (engine, desk) run belongs to."""
    if engine not in ENGINE_NAMES:
        raise ValueError(f"unknown engine {engine!r}")
    if desk.startswith("pattern:"):
        if engine not in ("hand", "hand-go"):
            raise ValueError(f"the patterns are hand-built only; {engine} builds the desks (single, supervisor)")
        return "patterns" if engine == "hand" else "patterns-go"
    if desk not in DESKS:
        raise ValueError(f"unknown desk {desk!r}")
    return f"desk-{engine}"


# ── 2. The estimate ─────────────────────────────────────────────────────────
# Typical model calls per run. Patterns: the call sequences in agents/fixtures/contract.jsonl (chain 2, route 2,
# parallel 3 lookups + 1 summary; orchestrate 5 there: plan, 3 one-call workers, synthesis; evaluate_optimize
# 2, 4 and 6 there: 4). Desks: the Task 5 contract trajectories (single 3-4, supervisor 11-12), assumed the same
# for LangGraph and the Agent SDK (they build the same desk; their tests' trajectories match it).
TYPICAL_CALLS = {"pattern:chain": 2, "pattern:route": 2, "pattern:parallel": 4, "pattern:orchestrate": 5,
                 "pattern:evaluate_optimize": 4, "single": 3, "supervisor": 12}
# The most calls the code allows (before the proxy's cap): orchestrate 1 + 3 workers x 3 steps + 1; evaluate 1 +
# 3 judges + 2 revisions; single MAX_TURNS; the supervisor is bounded only by the cap (8 turns, each may hand off).
CODE_MAX_CALLS = {"pattern:chain": 2, "pattern:route": 2, "pattern:parallel": 4, "pattern:orchestrate": 11,
                  "pattern:evaluate_optimize": 6, "single": MAX_TURNS_SINGLE, "supervisor": 10**6}
CHARS_PER_TOKEN = 4          # the proxy's rule (estimate_input_tokens)
TOOL_USE_TOKENS = 500        # the API's tool-use system prompt, on every request with tools
# R52: the Agent SDK's requests, measured. The real bundled CLI ran each desk on t001 against a local stub (offline, no
# API) and the proxy's own rule (estimate_input_tokens: the request JSON at 4 characters per token) was applied to
# the first request it sent: the CLI's system prompt, environment block, billing header and tool list included.
# The SDK estimate adds (that measure + TOOL_USE_TOKENS - our own first-request estimate for t001) to every call,
# so it never prints less than the CLI really sends. Re-measure when the CLI version changes.
SDK_MEASURED = {"cli": "2.1.310 (claude-agent-sdk 0.2.174)", "date": "2026-10-27", "ticket": "t001",
                "first_request_tokens": {"single": 984, "supervisor": 2534}}
EST_OUTPUT = 400             # typical output per call
EST_GROWTH = 600             # typical input each later call of a run adds (a reply and its tool results)
WORST_GROWTH = proxy.MAX_TOKENS + 2000   # worst: every reply uses MAX_TOKENS and adds 2000 tokens of tool results


def max_calls_for(desk: str, override: int | None) -> int:
    """The proxy's --max-calls for one ticket: 12, or 20 for the supervisor (R25, R61), unless overridden."""
    if override is not None:
        return override
    return SUPERVISOR_MAX_CALLS if desk == "supervisor" else DEFAULT_MAX_CALLS


def _first_request_chars(desk: str, ticket: dict, schemas: dict[str, dict]) -> tuple[int, bool]:
    """(characters, has tools) of a desk's first request: its system prompt, the ticket message and the tool schemas."""
    import patterns as pt
    import supervisor as sv
    prompts = pt.load_prompts()
    systems = {"pattern:chain": prompts["triage"]["v2_rules"], "pattern:evaluate_optimize": pt._judge_system(prompts),
               "single": sv.SINGLE_SYSTEM, "supervisor": sv.SUPERVISOR_SYSTEM}
    tools = {"pattern:parallel": [schemas["get_ticket"]],
             "pattern:orchestrate": [schemas[n] for n in sorted(core.READ_TOOLS)],
             "single": [schemas[n] for n in sorted(schemas)],
             "supervisor": [sv.HANDOFF, schemas["label_ticket"]]}.get(desk, [])
    text = systems.get(desk, pt.ROUTE_STYLE["billing"] + " " + pt.RULES) + pt._ticket_msg(ticket)[0]["content"]
    return len(text) + len(json.dumps(tools)), bool(tools)


def _schemas() -> dict[str, dict]:
    """The desk tools' schemas, from the in-memory servers (free: no API, nothing written)."""
    with tempfile.TemporaryDirectory() as tmp, \
            core.open_desk_tools(("read", "ops"), guard=True, labels_path=Path(tmp) / "labels.jsonl") as (schema, _r, _s):
        return {t["name"]: t for t in schema}


def sdk_overhead(desk: str, schemas: dict[str, dict]) -> int:
    """Tokens the Agent SDK adds to each call (SDK_MEASURED), never negative."""
    ticket = next(t for t in core.TICKETS if t["id"] == SDK_MEASURED["ticket"])
    chars, has_tools = _first_request_chars(desk, ticket, schemas)
    ours = chars // CHARS_PER_TOKEN + (TOOL_USE_TOKENS if has_tools else 0)
    return max(0, SDK_MEASURED["first_request_tokens"][desk] + TOOL_USE_TOKENS - ours)


def estimate_run(engine: str, desk: str, ticket: dict, max_calls: int, schemas: dict[str, dict]) -> dict:
    """Typical and worst dollars for one run. Input grows each call; the worst case also takes every call the
    cap allows, each using MAX_TOKENS of output (the proxy's worst_call rule)."""
    chars, has_tools = _first_request_chars(desk, ticket, schemas)
    first = chars // CHARS_PER_TOKEN + (TOOL_USE_TOKENS if has_tools else 0)
    first += sdk_overhead(desk, schemas) if engine == "sdk" else 0
    typical_n = min(TYPICAL_CALLS[desk], max_calls)
    # R52: only the hand-built engines are counted at their code's limits; the frameworks at the per-ticket cap
    max_n = min(CODE_MAX_CALLS[desk], max_calls) if engine in ("hand", "hand-go") else max_calls
    typical = sum(call_cost(MODEL, first + i * EST_GROWTH, EST_OUTPUT) for i in range(typical_n))
    worst = sum(call_cost(MODEL, first + i * WORST_GROWTH, proxy.MAX_TOKENS) for i in range(max_n))
    return {"first": first, "typical_calls": typical_n, "max_calls": max_n, "typical": typical, "worst": worst}


def estimate_set(spec: SetSpec, max_calls: int | None, tickets: tuple[str, ...] | None = None) -> dict:
    """The set's totals and one row per desk (summed over its tickets)."""
    schemas = _schemas()
    by_id = {t["id"]: t for t in core.TICKETS}
    rows: dict[str, dict] = {}
    for desk in spec.desks:
        cap = max_calls_for(desk, max_calls)
        row = rows.setdefault(desk, {"cap": cap, "runs": 0, "typical_calls": 0, "max_calls": 0, "typical": 0.0, "worst": 0.0})
        for tid in tickets or spec.tickets:
            e = estimate_run(spec.engine, desk, by_id[tid], cap, schemas)
            row["runs"] += 1
            for k in ("typical_calls", "max_calls", "typical", "worst"):
                row[k] += e[k]
    total = {k: sum(r[k] for r in rows.values()) for k in ("runs", "typical_calls", "max_calls", "typical", "worst")}
    return {**total, "rows": rows}


def print_estimate(spec: SetSpec, *, tickets: tuple[str, ...], max_calls: int | None, max_cost: float) -> dict:
    e = estimate_set(spec, max_calls, tickets)
    p_in, p_out = PRICES[MODEL]
    print(f"Recording set {spec.name}: engine {spec.engine}, {' + '.join(spec.desks)} x {len(tickets)} tickets "
          f"({e['runs']} runs), {MODEL}")
    print(f"  prices ${p_in:.2f} input / ${p_out:.2f} output per million tokens (llms/python/triage_lab.py PRICES)")
    print(f"  approve {APPROVE}, elicit {ELICIT}; one proxy run per ticket (R25), each with its own --max-calls")
    print(f"  {'desk':<28}{'runs':>5}{'--max-calls':>13}{'typical calls':>15}{'max calls':>11}{'typical $':>11}{'worst $':>10}")
    for desk, r in e["rows"].items():
        print(f"  {desk:<28}{r['runs']:>5}{r['cap']:>13}{r['typical_calls']:>15}{r['max_calls']:>11}"
              f"{r['typical']:>11.4f}{r['worst']:>10.4f}")
    limit = ("all its code allows (MAX_TURNS, MAX_WORKER_STEPS...) or its --max-calls, whichever is lower"
             if spec.engine in ("hand", "hand-go") else "its --max-calls; a framework's own limits are not counted")
    print(f"  calls: typical {e['typical_calls']}, max {e['max_calls']} (max: every run makes {limit})")
    print(f"  typical cost ${e['typical']:.4f}; worst case ${e['worst']:.4f} (every call at {proxy.MAX_TOKENS} output "
          f"tokens, input growing {WORST_GROWTH} per call)")
    print(f"  cap ${max_cost:.2f} for the whole set, checked by the proxy before every call"
          + ("; the worst case is over the cap, so a run stops there and the set is marked incomplete"
             if e["worst"] > max_cost else ""))
    print(f"  sources: typical calls from agents/fixtures/contract.jsonl (patterns, hand-built desks), assumed equal "
          f"for LangGraph and the Agent SDK; input ~{CHARS_PER_TOKEN} characters per token of the first request, "
          f"+{TOOL_USE_TOKENS} with tools, +{EST_GROWTH} per later call"
          + ("; Agent SDK +" + "/".join(f"{sdk_overhead(d, _schemas())}" for d in spec.desks if d in DESKS)
             + f" tokens per call ({'/'.join(d for d in spec.desks if d in DESKS)}; measured on CLI {SDK_MEASURED['cli']}, "
             f"{SDK_MEASURED['date']})" if spec.engine == "sdk" else ""))
    return e


# ── 3. Engines ──────────────────────────────────────────────────────────────
def engine(name: str) -> core.Engine:
    """The registered Engine (importing its module registers it; hand-go is registered here)."""
    if name not in core.ENGINES:
        if name == "hand":
            import supervisor  # noqa: F401
        elif name == "langgraph":
            import langgraph_desk  # noqa: F401
        elif name == "sdk":
            import sdk_desk  # noqa: F401
        elif name == "hand-go":
            core.register("hand-go", core.Engine(single=lambda t, **kw: go_run("single", t, **kw),
                                                 supervisor=lambda t, **kw: go_run("supervisor", t, **kw)))
    if name not in core.ENGINES:
        raise ValueError(f"unknown engine {name!r}")
    return core.ENGINES[name]


_GO_BIN: list[Path] = []


def go_binary() -> Path:
    """Build cmd/handgo once per process into a temporary directory (removed at exit)."""
    if not _GO_BIN:
        out = Path(tempfile.mkdtemp(prefix="handgo-"))
        atexit.register(shutil.rmtree, out, True)
        subprocess.run(["go", "build", "-o", str(out / "handgo"), "./cmd/handgo"], cwd=GO_DIR, check=True)
        _GO_BIN.append(out / "handgo")
    return _GO_BIN[0]


class GoRunError(RuntimeError):
    def __init__(self, message: str, proxy_status: int) -> None:
        super().__init__(message)
        self.proxy_status = proxy_status


def go_env() -> dict[str, str]:
    """The Go engine's environment: nothing inherited but PATH, TMPDIR and the locale (no key, no proxy
    variables reach it; it calls the proxy URL it is given), plus R9's flag."""
    keep = {k: os.environ[k] for k in ("PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE") if k in os.environ}
    return {**keep, **NONESSENTIAL}


def go_run(desk: str, ticket: dict, *, approve: str, elicit: str, labels_path: Path) -> dict:
    """One hand-go run: cmd/handgo against core.PROXY_URL; the MCP servers run under this Python (.venv-agents)."""
    args = [str(go_binary()), "-desk", desk, "-proxy", core.PROXY_URL, "-python", sys.executable,
            "-approve", approve, "-elicit", elicit, "-labels", str(labels_path)]
    # its own process group, so a timeout (or any failure here) kills the MCP server grandchildren too
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         env=go_env(), start_new_session=True)
    try:
        stdout, stderr = p.communicate(json.dumps({"id": ticket["id"], "text": ticket["text"]}), timeout=GO_TIMEOUT)
    except BaseException as e:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        if isinstance(e, subprocess.TimeoutExpired):
            raise RuntimeError(f"hand-go timed out after {GO_TIMEOUT} s; its process group was killed") from None
        raise
    sys.stderr.write(stderr)
    try:
        doc = json.loads(stdout)
    except ValueError:
        raise RuntimeError(f"hand-go exited {p.returncode} without a result") from None
    if p.returncode != 0 or "error" in doc:
        raise GoRunError(doc.get("error", f"exit {p.returncode}"), int(doc.get("proxy_status") or 0))
    out = doc["output"]
    if desk.startswith("pattern:"):
        return {"result": out, "calls": doc["calls"], "trace": doc["trace"], "errors": []}
    return {**out, "calls": doc["calls"], "trace": doc["trace"]}


def run_pattern(engine_name: str, desk: str, ticket: dict, *, labels_path: Path) -> dict:
    """The five patterns are read-only (no approve or elicit to pass); parallel and orchestrate get labels_path for
    their lumen-read session. Parallel runs workers=1: the same requests as workers=3, in a fixed trace order."""
    name = desk.removeprefix("pattern:")
    if engine_name == "hand-go":
        return go_run(desk, ticket, approve=APPROVE, elicit=ELICIT, labels_path=labels_path)
    import patterns as pt
    fn = getattr(pt, name)
    kw: dict[str, Any] = {}
    if name in ("parallel", "orchestrate"):
        kw["labels_path"] = labels_path
    if name == "parallel":
        kw["workers"] = 1
    r = fn(ticket, **kw)
    return {"result": r.output, "calls": r.calls, "trace": r.trace, "errors": []}


@contextmanager
def pointed_at(url: str) -> Iterator[None]:
    """Point every engine at this proxy run's URL (only differs from PROXY_URL in tests, on a free port), with
    R9's flag in the environment, then restore both."""
    saved_url, saved_env = core.PROXY_URL, {k: os.environ.get(k) for k in NONESSENTIAL}
    sdk = sys.modules.get("sdk_desk")
    saved_sdk = sdk.SDK_ENV["ANTHROPIC_BASE_URL"] if sdk else None
    core.PROXY_URL = url
    if sdk:
        sdk.SDK_ENV["ANTHROPIC_BASE_URL"] = url
        if sdk.SDK_ENV.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") != "1":   # R9/R39: the SDK's own env must have it
            raise RuntimeError("sdk_desk.SDK_ENV lost CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1")
    os.environ.update(NONESSENTIAL)
    try:
        yield
    finally:
        core.PROXY_URL = saved_url
        if sdk:
            sdk.SDK_ENV["ANTHROPIC_BASE_URL"] = saved_sdk
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def harness_trace_in(tmp: str) -> Iterator[None]:
    """The guard's scripted person appends to the harness's trace.jsonl; keep it out of the working directory."""
    harness = core.mcp_agent.load_harness()
    saved = harness.TRACE_FILE
    harness.TRACE_FILE = Path(tmp) / "trace.jsonl"
    try:
        yield
    finally:
        harness.TRACE_FILE = saved


def run_engine(engine_name: str, desk: str, ticket: dict, *, labels_path: Path) -> dict:
    if desk.startswith("pattern:"):
        return run_pattern(engine_name, desk, ticket, labels_path=labels_path)
    fn = getattr(engine(engine_name), desk)
    return fn(ticket, approve=APPROVE, elicit=ELICIT, labels_path=labels_path)    # R33: always explicit


# ── 3b. Pre-flight: the tools work before anything is spent ────────────────
# R59: classify_complaint failed in real recordings (fastapi was missing from .venv-agents), and the models read
# "Error executing tool" as the answer. Every lumen-read tool is called once in memory before a set starts.
PREFLIGHT = (("get_ticket", {"id": "t001"}), ("search_tickets", {"query": "refund"}),
             ("classify_complaint", {"text": "I was charged twice"}))


INSTALL = ".venv-agents/bin/pip install -r agents/requirements.txt"


def preflight() -> str | None:
    """None if every lumen-read tool answers without an error, else what failed (an exception included)."""
    try:
        return _preflight()
    except Exception as e:        # the servers would not even open: the same clean failure, no traceback
        return f"{type(e).__name__}: {e}"


def _preflight() -> str | None:
    with tempfile.TemporaryDirectory(prefix="lumen-preflight-") as tmp, harness_trace_in(tmp), \
            core.open_desk_tools(("read",), guard=False, labels_path=Path(tmp) / "labels.jsonl") as (_s, run_tool, _x):
        for name, args in PREFLIGHT:
            out, is_error = run_tool(name, args)
            if is_error:
                return f"{name}({json.dumps(args)}) failed: {out[:300]}"
    return None


# ── 4. Traces ───────────────────────────────────────────────────────────────
def log_path(traces_dir: Path, set_name: str) -> Path:
    """The proxy's log for a set (git-ignored), so a failure's cause is kept even when stderr is not (R59)."""
    path = Path(traces_dir) / set_name / "proxy.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def trace_path(traces_dir: Path, set_name: str, engine_name: str, desk: str, ticket_id: str) -> Path:
    return Path(traces_dir) / set_name / f"{engine_name}-{desk.removeprefix('pattern:')}-{ticket_id}.json"


def trace_doc(*, set_name: str, engine_name: str, desk: str, ticket_id: str, mode: str, result: dict,
              spent: float, paid_calls: int, proxy_calls: list[dict] | None = None) -> dict:
    """R2 records plus output (the run's own result: a desk's reply, stop, calls, tool_calls...; a pattern's
    {result, calls}), errors, cost_usd, the proxy's spend and its ledger (proxy_calls, R62). In replay every
    record's ms is None: it is the engine's wall clock, the local proxy's in replay, and leaving it out keeps
    replayed traces byte-stable. The recorded upstream latency is in proxy_calls instead."""
    result = dict(result)
    trace = result.pop("trace")
    records = trace.to_list() if hasattr(trace, "to_list") else [dict(r) for r in trace]
    if mode == "replay":
        records = [{**r, "ms": None} for r in records]
    errors = result.pop("errors", [])
    cost = result.pop("cost_usd", None)
    return {"set": set_name, "engine": engine_name, "desk": desk, "ticket": ticket_id, "mode": mode, "model": MODEL,
            "approve": APPROVE, "elicit": ELICIT, "trace": records, "output": result, "errors": errors,
            "cost_usd": cost, "spent": round(spent, 6), "paid_calls": paid_calls,
            "proxy_calls": [dict(c) for c in proxy_calls or []]}


def comparable(doc: dict) -> dict:
    """A trace without what differs between a recording run and its replay: mode, the engine's latency, spend,
    and whether each ledger call was replayed. The ledger is compared as a multiset (sorted): calls a desk makes
    concurrently may be served in another order."""
    out = {k: v for k, v in doc.items() if k not in ("mode", "spent", "paid_calls")}
    out["trace"] = [{**r, "ms": None} for r in doc["trace"]]
    if "proxy_calls" in doc:
        out["proxy_calls"] = sorted(({**c, "replayed": None} for c in doc["proxy_calls"]),
                                    key=lambda c: json.dumps(c, sort_keys=True))
    return out


def dump(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def write_trace(path: Path, doc: dict) -> str:
    """Write the trace. A replay never replaces a record-mode trace (it holds the real spend): it checks the run
    matches it instead. A record-mode trace written before the ledger existed (R62) gets the replay's
    proxy_calls added when the rest matches. Returns what happened, for the run's line."""
    if doc["mode"] == "replay" and path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("mode") == "record":
            if "proxy_calls" not in old and comparable(old) == comparable({k: v for k, v in doc.items()
                                                                           if k != "proxy_calls"}):
                _write(path, {**old, "proxy_calls": doc["proxy_calls"]})
                return "matches the recorded trace (kept); added its proxy_calls"
            if comparable(old) == comparable(doc):
                return "matches the recorded trace (kept)"
            raise RuntimeError(f"the replayed run differs from the recorded trace {rel(path)}; kept the recording's")
    _write(path, doc)
    return f"wrote {rel(path)}"


def _write(path: Path, doc: dict) -> None:
    """Atomically: a temporary file, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump(doc), encoding="utf-8")
    os.replace(tmp, path)


def rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


# ── 5. One ticket, one proxy run ────────────────────────────────────────────
def record_command(engine_name: str, desk: str, ticket_id: str, max_calls: int | None) -> str:
    """The command that records exactly this run (named in a replay miss)."""
    cmd = f"python agents/run.py --engine {engine_name} --desk {desk} --tickets {ticket_id} --mode record"
    return cmd + (f" --max-calls {max_calls}" if max_calls is not None else "")


def _is_proxy_refusal(e: BaseException) -> bool:
    if isinstance(e, (core.ProxyError, GoRunError)):
        return True
    status = getattr(e, "status_code", None)    # anthropic.APIStatusError (LangGraph's ChatAnthropic raises it)
    return status in (402, 404)


@dataclass
class Outcome:
    ok: bool
    spent: float
    cap_hit: bool
    line: str


CLOSE_WAIT = 30.0     # seconds run.py waits for in-flight proxy calls before closing the upstream client


def wait_idle(server: Any, timeout: float) -> bool:
    """Poll every 50 ms until no proxy handler is busy (bounded by `timeout`). True if idle."""
    deadline = time.monotonic() + timeout
    while True:
        with server.lock:
            busy = any(getattr(h, "busy", False) for h in server.connections.values())
        if not busy:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def capped(server: Any, label: str, e: BaseException | None) -> Outcome:
    """A run the proxy refused a call to is incomplete and writes no trace. This ticket's call limit (--max-calls)
    stops this ticket only; the dollar cap stops the set (Outcome.cap_hit)."""
    why = f" ({e})" if e is not None else ""
    if server.paid_calls >= server.max_calls:
        return Outcome(False, server.spent, False,
                       f"{label}: stopped at --max-calls {server.max_calls}; incomplete, no trace{why}")
    return Outcome(False, server.spent, True, f"{label}: stopped by the cost cap; incomplete, no trace{why}")


def run_ticket(*, engine_name: str, desk: str, ticket: dict, set_name: str, mode: str, max_cost: float,
               max_calls: int | None, port: int, store_dir: Path, traces_dir: Path, approved: bool,
               transport: Any = None, refresh: bool = False) -> Outcome:
    """One ticket through its own proxy run. `approved` (no default) is the user's yes to the paid calls: main
    passes proxy.confirm(--yes); record mode without it builds no server (make_server's gate, R13). `refresh`
    re-records what the store already holds (R62), atomically per ticket (R65): the proxy stages the new
    recordings, and they replace the old ones only when the run finished ok (its trace written); otherwise they
    are discarded and the old recordings and trace stay, consistent with each other."""
    cmd = record_command(engine_name, desk, ticket["id"], max_calls)
    engine(engine_name)                 # import (and so register) the engine first, so pointed_at sees its module
    server = proxy.make_server(mode, port, store_dir, set_name=set_name, max_cost=max_cost,
                               max_calls=max_calls_for(desk, max_calls), transport=transport,
                               yes=approved, record_command=cmd,
                               estimate=False,             # main printed the set's estimate once
                               log_file=log_path(traces_dir, set_name), refresh=refresh)
    out = _run_ticket(server, engine_name=engine_name, desk=desk, ticket=ticket, set_name=set_name, mode=mode,
                      traces_dir=traces_dir, cmd=cmd)
    if server.refresh:
        if out.ok:
            server.commit_staged()
        else:
            n = server.discard_staged()
            out = Outcome(out.ok, out.spent, out.cap_hit, f"{out.line}; refresh: {n} new recording(s) discarded, "
                                                          "the old recordings and trace are kept")
    return out


def _run_ticket(server: Any, *, engine_name: str, desk: str, ticket: dict, set_name: str, mode: str,
                traces_dir: Path, cmd: str) -> Outcome:
    """Run the engine against this ticket's proxy, close the proxy, and write the trace when the run is usable."""
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    label = f"{engine_name} {desk} {ticket['id']}"
    try:
        with tempfile.TemporaryDirectory(prefix="lumen-run-") as tmp, harness_trace_in(tmp), \
                pointed_at(f"http://{proxy.HOST}:{server.server_address[1]}"):
            labels = LABELS_PATH if mode == "record" else Path(tmp) / "labels.jsonl"   # R33
            try:
                result = run_engine(engine_name, desk, ticket, labels_path=labels)
            except Exception as e:
                if server.cap_hit:
                    return capped(server, label, e)
                if mode == "replay" and _is_proxy_refusal(e):
                    print(f"run: {label}: replay miss ({e})", file=sys.stderr)
                    return Outcome(False, 0.0, False, f"{label}: replay miss; record with: {cmd}")
                return Outcome(False, server.spent, False, f"{label}: failed: {type(e).__name__}: {e}")
    finally:
        server.shutdown()
        server.server_close()           # drops idle connections; one mid-request still answers
        if server.client is not None:
            if not wait_idle(server, CLOSE_WAIT):   # let an in-flight paid call get its reply recorded first
                print(f"run: a proxy handler was still busy after {CLOSE_WAIT:g} s; closing anyway", file=sys.stderr)
            server.client.close()
        thread.join(timeout=10)
    if server.cap_hit:                  # an engine that swallowed the refusal: still incomplete, still no trace
        return capped(server, label, None)
    if server.misses:                   # a miss an engine absorbed (the SDK's sub-agents do): incomplete (R59)
        print(f"run: {label}: {server.misses} replay miss(es) absorbed by the engine", file=sys.stderr)
        return Outcome(False, 0.0, False, f"{label}: replay miss; record with: {cmd}")
    doc = trace_doc(set_name=set_name, engine_name=engine_name, desk=desk, ticket_id=ticket["id"], mode=mode,
                    result=result, spent=server.spent, paid_calls=server.paid_calls, proxy_calls=server.calls)
    path = trace_path(traces_dir, set_name, engine_name, desk, ticket["id"])
    try:
        what = write_trace(path, doc)
    except RuntimeError as e:
        return Outcome(False, server.spent, False, f"{label}: {e}")
    models = sum(1 for r in doc["trace"] if r["kind"] == "model")
    return Outcome(True, server.spent, server.cap_hit, f"{label}: ok, {models} model calls, spent ${server.spent:.4f}; {what}")


# ── 6. Entry point ──────────────────────────────────────────────────────────
def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run an engine on a desk through the record/replay proxy (replay by default).")
    p.add_argument("--set", dest="set_name", choices=sorted(SETS), help="a whole recording set")
    p.add_argument("--engine", choices=ENGINE_NAMES)
    p.add_argument("--desk", help="pattern:<name>, pattern:all, single, supervisor or all")
    p.add_argument("--tickets", default="all", help="all, or ticket ids separated by commas")
    p.add_argument("--mode", choices=["replay", "record"], default="replay")
    p.add_argument("--refresh", action="store_true",
                   help="record mode: re-record even what the store holds (every call is paid)")
    p.add_argument("--dry-run", action="store_true", help="print the recording estimate and exit; calls nothing")
    p.add_argument("--yes", action="store_true", help="approve the paid calls without asking (only after the user approved)")
    p.add_argument("--max-cost", type=float, default=proxy.DEFAULT_MAX_COST, help="dollars for the whole run of the set")
    p.add_argument("--max-calls", type=int, default=None,
                   help=f"paid calls per ticket (default {DEFAULT_MAX_CALLS}, supervisor {SUPERVISOR_MAX_CALLS}; at most {MAX_CALLS_LIMIT})")
    p.add_argument("--port", type=int, default=proxy.DEFAULT_PORT, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def resolve(args: argparse.Namespace) -> tuple[SetSpec, tuple[str, ...], tuple[str, ...]]:
    """(set, desks, tickets) from --set or --engine/--desk, narrowed by --tickets."""
    if args.set_name:
        spec = SETS[args.set_name]
        desks = spec.desks
    else:
        if not (args.engine and args.desk):
            raise ValueError("give --set NAME, or --engine and --desk")
        if args.desk == "pattern:all":
            desks = tuple(f"pattern:{p}" for p in PATTERNS)
        elif args.desk == "all":
            desks = DESKS
        else:
            if args.desk.startswith("pattern:") and args.desk.removeprefix("pattern:") not in PATTERNS:
                raise ValueError(f"unknown pattern {args.desk!r}; one of {', '.join(PATTERNS)}")
            desks = (args.desk,)
        names = {set_for(args.engine, d) for d in desks}
        spec = SETS[names.pop()]
    if args.tickets == "all":
        tickets = spec.tickets
    else:
        tickets = tuple(t.strip() for t in args.tickets.split(",") if t.strip())
        unknown = [t for t in tickets if t not in ALL_TICKETS]
        if unknown:
            raise ValueError(f"unknown ticket(s) {', '.join(unknown)}; the eval tickets are {', '.join(ALL_TICKETS)}")
    return spec, desks, tickets


REFRESH_NOTE = ("  --refresh: recordings already in the store are ignored, so every call is paid and re-recorded "
                "(the estimate above already assumes every call is paid); a ticket's new recordings replace the old "
                "only when it finishes ok. After a stop, refresh only the failed tickets with --tickets <id>,<id>")


def set_command(args: argparse.Namespace, spec: SetSpec, desks: tuple[str, ...], tickets: tuple[str, ...]) -> str:
    """The exact record command for what this invocation selected. R71: never with --yes, so a pasted command still
    asks y/N; --yes is for a scripted run after the user approved the estimate."""
    if args.set_name and tickets == spec.tickets:
        cmd = f"python agents/run.py --set {spec.name}"
    else:
        desk = args.desk if not args.set_name else ("pattern:all" if desks[0].startswith("pattern:") else "all")
        cmd = f"python agents/run.py --engine {spec.engine} --desk {desk} --tickets {','.join(tickets) if tickets != spec.tickets else 'all'}"
    cmd += " --mode record" + (" --refresh" if args.refresh else "") + f" --max-cost {args.max_cost:g}"
    if args.max_calls is not None:
        cmd += f" --max-calls {args.max_calls}"
    return cmd


def main(argv: list[str] | None = None, *, store_dir: Path = STORE_DIR, traces_dir: Path = TRACES_DIR,
         transport: Any = None) -> int:
    """`transport` (tests only) is the proxy's fake upstream for record mode."""
    args = parse_args(argv)
    try:
        spec, desks, tickets = resolve(args)
    except ValueError as e:
        print(f"run: {e}")
        return 2
    if args.refresh and args.mode != "record":
        print("run: --refresh re-records, so it needs --mode record (and the same approval as any paid run)")
        return 2
    if args.max_calls is not None and not 1 <= args.max_calls <= MAX_CALLS_LIMIT:
        print(f"run: --max-calls must be 1 to {MAX_CALLS_LIMIT} (R25)")
        return 2
    if spec.engine == "hand-go" and shutil.which("go") is None:
        print("run: the hand-go engine needs the Go toolchain (go) on PATH")
        return 2
    sub = SetSpec(spec.name, spec.engine, desks, tickets)
    if not args.dry_run:
        failed_tool = preflight()      # before any estimate, server or socket (R59)
        if failed_tool is not None:
            print(f"run: pre-flight tool check failed: {failed_tool}. Run `{INSTALL}` and try again. "
                  f"Nothing was called.")
            return 2
    if args.dry_run:
        print_estimate(sub, tickets=tickets, max_calls=args.max_calls, max_cost=args.max_cost)
        if args.refresh:
            print(REFRESH_NOTE)
        print("Record (paid; run only after the user's explicit approve):")
        print(f"  {set_command(args, spec, desks, tickets)}")
        print("Dry run: nothing was called.")
        return 0
    if args.mode == "record":
        if not os.environ.get("ANTHROPIC_API_KEY"):    # before any estimate, server or socket
            print("run: record mode needs ANTHROPIC_API_KEY in the environment. Nothing was called.")
            return 2
        print_estimate(sub, tickets=tickets, max_calls=args.max_calls, max_cost=args.max_cost)
        if args.refresh:
            print(REFRESH_NOTE)
    approved = args.mode == "record" and proxy.confirm(args.yes)      # asked once for the set
    if args.mode == "record" and not approved:
        return 1
    by_id = {t["id"]: t for t in core.TICKETS}
    spent, failed = 0.0, 0
    for desk in desks:
        for tid in tickets:
            left = args.max_cost - spent
            if args.mode == "record" and left <= 0:
                print(f"Stopped: the set's cap ${args.max_cost:.2f} is spent; the rest of {spec.name} is incomplete.")
                return 3
            out = run_ticket(engine_name=spec.engine, desk=desk, ticket=by_id[tid], set_name=spec.name,
                             mode=args.mode, max_cost=left, max_calls=args.max_calls, port=args.port,
                             store_dir=Path(store_dir), traces_dir=Path(traces_dir), approved=approved,
                             transport=transport, refresh=args.refresh)
            spent += out.spent
            print(out.line, flush=True)
            if out.cap_hit:
                print(f"Stopped by the cost cap after ${spent:.4f}; set {spec.name} is incomplete. Recordings so far are saved.")
                return 3
            failed += not out.ok
    if args.mode == "record":
        print(f"Set {spec.name}: spent ${spent:.4f} in total.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
