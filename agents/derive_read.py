"""
derive_read — reading one trace for agents/derive.py: excerpts capped so the derived files stay small, a run's
steps in order with the proxy's ledger joined in (R62, R64), and the record command for a run with no trace.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import run  # agents/run.py, beside this file: the trace layout and the record command

# ── 1. Configuration: excerpts and costs ────────────────────────────────────
MAX_COST = 10                                      # the record command's --max-cost (run.py's default cap)
WRITE_TOOLS = ("label_ticket",)                    # lumen-ops: the only tool that changes anything
STEP_CAP = 240                                     # a step's result excerpt
REPLY_CAP = 400                                    # a run's final reply excerpt
ARGS_CAP = 120                                     # a tool call's arguments, a handoff's task, an error
COST_DIGITS = 8
FENCE = "```"                                       # a Markdown code fence; an excerpt never leaves one open


# ── 2. Reading a trace ──────────────────────────────────────────────────────
def excerpt(text: Any, cap: int) -> str | None:
    """At most `cap` characters, whitespace collapsed; a cut ends in an ellipsis (counted in the cap). A cut never
    leaves a ``` code fence open: it cuts before the open fence, or, when the excerpt starts with it, closes it."""
    if text is None:
        return None
    s = re.sub(r"\s+", " ", text if isinstance(text, str) else json.dumps(text, sort_keys=True, ensure_ascii=False)).strip()
    if len(s) <= cap:
        return s
    head = s[: cap - 1]
    if s[cap - 1] == "`":                 # never split a fence marker: back off over the backticks at the cut
        head = head.rstrip("`")
    head = head.rstrip()
    if head.count(FENCE) % 2 == 0:
        return head + "…"
    before = head[: head.rfind(FENCE)].rstrip()
    if before:                            # the cut falls inside a fenced block: cut before its opening fence
        return before + "…"
    # The excerpt is the block: close it, when the cap leaves room for its opening fence and "… ```"; else drop it.
    kept = head[: max(cap - len("… " + FENCE), 0)].rstrip("` ")
    return kept + ("… " + FENCE if kept.count(FENCE) % 2 else "…")


def args_text(args: Any) -> str | None:
    """A tool call's arguments as `k=v, k=v`, capped at ARGS_CAP."""
    if not isinstance(args, dict):
        return excerpt(args, ARGS_CAP)
    return excerpt(", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in sorted(args.items())), ARGS_CAP)


def load(traces_dir: Path, set_name: str, engine: str, desk: str, ticket: str) -> dict | None:
    """The run's trace, or None when it is missing, unreadable, incomplete or written for another run."""
    path = run.trace_path(Path(traces_dir), set_name, engine, desk, ticket)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if not isinstance(doc, dict) or doc.get("incomplete"):
        return None
    if (doc.get("set"), doc.get("engine"), doc.get("desk"), doc.get("ticket")) != (set_name, engine, desk, ticket):
        return None
    if not isinstance(doc.get("output"), dict) or not isinstance(doc.get("trace"), list):
        return None
    return doc


def not_recorded(engine: str, desk: str, tickets: list[str] | tuple[str, ...]) -> dict:
    return {"recorded": False,
            "command": run.record_command(engine, desk, ",".join(tickets), None) + f" --max-cost {MAX_COST}"}


def ledger(doc: dict) -> list[dict]:
    calls = doc.get("proxy_calls")
    return calls if isinstance(calls, list) else []


def _sum(values: list) -> Any:
    """None when there is nothing, or when any value is unknown (never a partial sum)."""
    return None if not values or any(v is None for v in values) else sum(values)


def _cost(x: float | None) -> float | None:
    return None if x is None else round(float(x), COST_DIGITS)


def _num(x: float | None, digits: int = 1) -> float | int | None:
    """Rounded; an integral value is written as an int, so the file reads 200, not 200.0."""
    if x is None:
        return None
    x = round(float(x), digits)
    return int(x) if x == int(x) else x


def totals(doc: dict) -> dict:
    """The run's calls, cost and latency from its ledger (R62), and its tokens (R64)."""
    calls = ledger(doc)
    return {"calls": len(calls),
            "cost_usd": _cost(_sum([c.get("cost_usd") for c in calls])),
            "ms": _sum([c.get("ms") for c in calls]),
            "tokens_in": _sum([c.get("input_tokens") for c in calls]),
            "tokens_out": _sum([c.get("output_tokens") for c in calls]),
            "cache_read": _sum([c.get("cache_read_input_tokens") for c in calls]),
            "cache_write": _sum([c.get("cache_creation_input_tokens") for c in calls])}


def model_step(record: dict, call: dict | None) -> dict:
    """A model call: who made it, and its tokens, cost and time from the matching ledger entry (the i-th model
    record is the i-th proxy call). The record's own tokens and ms are not used (the Agent SDK logs partial
    counts; ms is the local clock)."""
    call = call or {}
    return {"kind": "model", "agent": record.get("agent"), "name": record.get("name"),
            "tokens_in": call.get("input_tokens"), "tokens_out": call.get("output_tokens"),
            "cache_read": call.get("cache_read_input_tokens"),
            "cost_usd": _cost(call.get("cost_usd")), "ms": call.get("ms")}


def tool_step(agent: str | None, call: dict | None, name: str, *, traced: bool) -> dict:
    """A tool call. A write (label_ticket) says whether it went through or was refused: in these runs the
    guard's scripted person says no (approve decline), so a write that is_error was refused. `traced` is False
    when the engine logged the call only in output.tool_calls and we placed it in the order."""
    call = call or {}
    step = {"kind": "tool", "agent": call.get("agent") or agent, "name": name, "args": args_text(call.get("args")),
            "is_error": call.get("is_error"), "traced": traced}
    if name in WRITE_TOOLS:
        step["write"] = "refused" if call.get("is_error") else "written" if call else None
    return step


def steps_of(doc: dict, *, tool_args: list[dict] | None = None, handoffs: list[dict] | None = None) -> list[dict]:
    """The run's ordered steps, from its trace records (R2), with the ledger, the tool calls and the hand-offs
    joined in by order. Tool calls the trace does not hold (refused writes in the LangGraph and SDK desks) are
    placed where their order in tool_calls puts them, or before the final model call, marked traced: False."""
    calls = ledger(doc)
    tool_calls = list(tool_args if tool_args is not None else doc["output"].get("tool_calls") or [])
    handoffs = list(handoffs if handoffs is not None else doc["output"].get("handoffs") or [])
    steps: list[dict] = []
    m = h = t = 0
    for r in doc["trace"]:
        kind = r.get("kind")
        if kind == "model":
            steps.append(model_step(r, calls[m] if m < len(calls) else None))
            m += 1
        elif kind == "tool":
            j = next((k for k in range(t, len(tool_calls)) if tool_calls[k].get("tool") == r.get("name")), None)
            if j is None:
                steps.append(tool_step(r.get("agent"), None, r.get("name"), traced=True))
                continue
            for skipped in tool_calls[t:j]:
                steps.append(tool_step(None, skipped, skipped.get("tool"), traced=False))
            steps.append(tool_step(r.get("agent"), tool_calls[j], r.get("name"), traced=True))
            t = j + 1
        elif kind == "handoff":
            ho = handoffs[h] if h < len(handoffs) else {}
            h += 1
            steps.append({"kind": "handoff", "agent": r.get("agent"), "name": r.get("name"),
                          "worker": ho.get("worker"), "task": excerpt(ho.get("task"), ARGS_CAP),
                          "changed": sorted(ho.get("changed") or {}), "is_error": ho.get("is_error")})
        else:
            steps.append({"kind": kind, "agent": r.get("agent"), "name": r.get("name")})
    rest = [tool_step(None, c, c.get("tool"), traced=False) for c in tool_calls[t:]]
    if rest:
        last_model = max((i for i, s in enumerate(steps) if s["kind"] == "model"), default=len(steps))
        last_tool = max((i for i, s in enumerate(steps) if s["kind"] == "tool"), default=-1)
        at = last_model if last_model > last_tool else last_tool + 1
        steps[at:at] = rest
    for e in doc.get("errors") or []:
        steps.append({"kind": "error", "text": excerpt(e, ARGS_CAP)})
    return steps
