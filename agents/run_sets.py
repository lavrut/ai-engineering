"""
run_sets — what agents/run.py runs and what recording it would cost: the recording sets, the desks and the
per-ticket call caps (section 1), and the up-front estimate a paid run prints before it asks y/N (section 2).
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

AGENTS = Path(__file__).resolve().parent
REPO = AGENTS.parent
for sub in ("desk/python", "frameworks", "proxy"):
    sys.path.insert(0, str(AGENTS / sub))

import desk_core as core  # noqa: E402
import proxy  # noqa: E402
from triage_lab import PRICES, call_cost  # noqa: E402  (the LLMs lab's prices, import only)

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
