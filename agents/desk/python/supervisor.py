#!/usr/bin/env python3
"""supervisor — the single-agent desk and the multi-agent supervisor desk, hand-built.

Both run one ticket and return a dict:

    single_agent(ticket) -> {reply, stop, trace, calls, tool_calls, errors}
    supervisor(ticket)   -> {reply, stop, trace, calls, tool_calls, errors, handoffs, state}

`reply` is the answer to the customer, `stop` is "end_turn" or "max_turns", `trace` is the desk_core
Trace (model, tool and handoff records), `calls` the model-call sequence as {agent, role, tools} (the
tool names that call offered, sorted), `tool_calls` every MCP tool call as {agent, tool, args, is_error}
(lumen-read and lumen-ops names only), and `errors` every call to a name that is no desk tool and no known
worker, as {agent, tool, args, result} (R35/R37: the same rule in every engine; the call is refused, the
model reads the error and the loop goes on, R26). The supervisor adds `handoffs` ({worker, task, is_error,
changed}: `changed` is the state diff) and the final shared `state`. The shared contract pins everything
but `trace`.

Lesson 6 compares the two on the same tickets: one agent that holds every tool against a supervisor
that splits the job and keeps the write behind a code gate. Every desk opens one MCP session per
ticket (R18) and calls tools one at a time through run_tool, in the order the model asked for them.

The untrusted-read rule: the ticket text is in the first message, so the context holds untrusted
text from the first call. The session's flag is set before the first call, so every write needs a
person's yes (`approve`), then the server's own confirmation (`elicit`). That also closes the MCP
track's same-response boundary (R19) for these desks: there is no "before the first read".
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import desk_core as core  # noqa: E402
from desk_core import Trace, claude, open_desk_tools  # noqa: E402
from patterns import MAX_WORKER_STEPS, RULES, _blocks, _json, _text, _ticket_msg, _uses  # noqa: E402
from triage_lab import CATEGORIES, PRIORITIES, RUBRIC_IDS, _judge_system, load_prompts  # noqa: E402  (the LLMs lab, import only)

# ── 1. Configuration ────────────────────────────────────────────────────────
MAX_TURNS = 8            # model calls one agent loop may spend (the single agent; the supervisor's own loop)
STOPPED = "[stopped: reached MAX_TURNS without finishing]"   # the harness's words for the same stop
WORKERS = ("triage", "research", "drafter", "reviewer")
ROLE = {"triage": "triage", "research": "research", "drafter": "draft", "reviewer": "review"}
STATE_FIELDS = ("ticket_id", "category", "findings", "draft", "review")
HANDOFF = {
    "name": "handoff",
    "description": ("Hand one task to a worker and get its result back. Workers: triage (category and priority), "
                    "research (looks things up with the read tools), drafter (writes the reply to the customer), "
                    "reviewer (checks the current draft against the rubric)."),
    "input_schema": {"type": "object", "properties": {
        "worker": {"type": "string", "enum": list(WORKERS)},
        "task": {"type": "string", "description": "What the worker should do, in one or two sentences."}},
        "required": ["worker", "task"]},
}
SINGLE_SYSTEM = (f"You are the Lumen support desk. Read what you need with the tools, label the ticket with "
                 f"label_ticket if you are sure of its category, then answer with the reply to the customer: "
                 f"two or three sentences. {RULES}")
SUPERVISOR_SYSTEM = (f"You supervise the Lumen support desk. Use the handoff tool to give work to your workers: "
                     f"triage, then research if facts are needed, then drafter, then reviewer. If the reviewer "
                     f"fails the draft, ask the drafter again with the reasons. label_ticket appears in your tools "
                     f"only once the reviewer has passed the current draft; use it to label the ticket with its "
                     f"category. When done, answer with one short line for the log. {RULES}")


def _pyjson(value: Any) -> str:
    """JSON for prompt text. Go's pyJSON writes the same bytes (R23), so the two engines' prompts stay close."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _stop(run: dict, agent: str, cap: str) -> None:
    """R29: a loop that hit its cap leaves a kind "stop" record (name "max_turns" or "max_worker_steps")."""
    run["trace"].add(agent=agent, kind="stop", name=cap)


def _new_run() -> dict:
    return {"trace": Trace(), "calls": [], "tool_calls": [], "errors": []}


def _ask(run: dict, call: Callable, agent: str, role: str, system: str, messages: list[dict],
         tools: list[dict] | None) -> Any:
    """One model call, logged as {agent, role, tools} in call order (tools: the offered names, sorted)."""
    run["calls"].append({"agent": agent, "role": role, "tools": sorted(t["name"] for t in tools or [])})
    return call(messages, system=system, tools=tools, trace=run["trace"], agent=agent)


def _tool(run: dict, run_tool: Callable, agent: str, use: Any, allowed: set[str], refuse: str | None = None) -> dict:
    """Run one tool_use if `allowed` holds its name and there is no `refuse` reason; anything else is
    refused here and never reaches the MCP session. Logged in tool_calls when the name is a desk MCP tool,
    else in errors (R37); returns the tool_result block."""
    if use.name not in allowed:
        out, is_error = f"Unknown tool: {use.name}", True
    elif refuse is not None:
        out, is_error = refuse, True
    else:
        out, is_error = run_tool(use.name, use.input)
    if use.name in core.READ_TOOLS | core.OPS_TOOLS:
        run["tool_calls"].append({"agent": agent, "tool": use.name, "args": use.input, "is_error": is_error})
    else:
        run["errors"].append({"agent": agent, "tool": use.name, "args": use.input, "result": out})
    return {"type": "tool_result", "tool_use_id": use.id, "content": out, "is_error": is_error}


def _session(labels_path: Path | None, tmp: str, *, approve: str, elicit: str, run: dict, agent: str, servers: tuple):
    return open_desk_tools(servers, guard=True, approve=approve, elicit=elicit,
                           labels_path=labels_path or Path(tmp) / "labels.jsonl", trace=run["trace"], agent=agent)


# ── 2. The single agent ─────────────────────────────────────────────────────
def single_agent(ticket: dict, *, call: Callable = claude, approve: str = "decline", elicit: str = "accept",
                 labels_path: Path | None = None) -> dict:
    """One agent, every desk tool (lumen-read and lumen-ops), a plain tool loop capped at MAX_TURNS.

    Why: the simplest agent. The model decides the path; the only gates are the ones every write
    passes (the guard, then the server's confirmation). It is the baseline the supervisor must beat.
    Not when: one prompt holding every tool and every step gets confused, or a write must wait for a
    check the model could skip: here nothing but the gate stops a label before the reply is good.
    `labels_path` defaults to a temporary file, so a test or a stray run never writes the real one."""
    run = _new_run()
    with tempfile.TemporaryDirectory() as tmp, _session(labels_path, tmp, approve=approve, elicit=elicit, run=run,
                                                         agent="single", servers=("read", "ops")) as (schema, run_tool, session):
        session.read_untrusted.set()     # the ticket text is in the first message (module docstring)
        allowed = {t["name"] for t in schema}
        messages = _ticket_msg(ticket)
        reply, stop = STOPPED, "max_turns"
        for _ in range(MAX_TURNS):
            response = _ask(run, call, "single", "act", SINGLE_SYSTEM, messages, schema)
            uses = _uses(response)
            if not uses:
                reply, stop = _text(response), "end_turn"
                break
            results = [_tool(run, run_tool, "single", u, allowed) for u in uses]   # in order, one at a time
            messages = messages + [{"role": "assistant", "content": _blocks(response)}, {"role": "user", "content": results}]
        else:
            _stop(run, "single", "max_turns")
    return {"reply": reply, "stop": stop, **run}


# ── 3. Shared state, and every change logged ────────────────────────────────
def _passed(state: dict) -> bool:
    return isinstance(state["review"], dict) and state["review"].get("passed") is True


def _apply(run: dict, state: dict, worker: str, task: Any, update: dict | None, ms: int) -> None:
    """Merge a worker's update into the state and log it. `changed` holds only the fields whose value
    changed (the compact diff); the Trace record's name is "<worker>: <field>, <field>" (sorted), or
    "<worker>: no change", or "<worker>: error" when the handoff failed."""
    changed = {k: v for k, v in (update or {}).items() if state[k] != v}
    state.update(changed)
    run["handoffs"].append({"worker": worker, "task": task, "is_error": update is None, "changed": changed})
    what = "error" if update is None else ", ".join(sorted(changed)) or "no change"
    run["trace"].add(agent="supervisor", kind="handoff", name=f"{worker}: {what}", ms=ms)   # a known worker (R37)


# ── 4. The workers ──────────────────────────────────────────────────────────
def _task_msg(ticket: dict, state: dict, task: str) -> list[dict]:
    return _ticket_msg(ticket, f"\n\n<category>{state['category'] or 'unknown'}</category>\n"
                               f"<findings>\n{state['findings'] or 'none'}\n</findings>\n\nYour task: {task}")


def _triage(run: dict, call: Callable, ticket: dict, state: dict, task: str, **_: Any) -> tuple[str, dict]:
    """No tools. The lab's triage prompt; the answer must be one of its categories and priorities.
    A new category clears the review (R30), as a new draft does: a pass is for the category the label
    is bound to (R27), so re-triage must not move the allowed label without a fresh review."""
    system = load_prompts()["triage"]["v2_rules"] + '\n\nAnswer with JSON only: {"category": "<category>", "priority": "<priority>"}'
    got = _json(_text(_ask(run, call, "triage", ROLE["triage"], system, _task_msg(ticket, state, task), None)), "triage")
    category = got.get("category") if isinstance(got, dict) else None
    priority = got.get("priority") if isinstance(got, dict) else None
    if category not in CATEGORIES or priority not in PRIORITIES:
        raise ValueError(f"triage: category must be one of {CATEGORIES} and priority one of {PRIORITIES}")
    update = {"category": category} if category == state["category"] else {"category": category, "review": None}
    return _pyjson({"category": category, "priority": priority}), update


def _research(run: dict, call: Callable, ticket: dict, state: dict, task: str, *, read_schema: list[dict],
              run_tool: Callable, **_: Any) -> tuple[str, dict]:
    """lumen-read only, a capped tool loop. A label_ticket it asks for is refused before the session:
    the worker was never offered it, and the session's ops tool is the supervisor's alone."""
    allowed = {t["name"] for t in read_schema}
    system = f"You research a Lumen support ticket with the read tools. Report the facts you found in two or three sentences. {RULES}"
    messages, found = _task_msg(ticket, state, task), ""
    for _ in range(MAX_WORKER_STEPS):
        reply = _ask(run, call, "research", ROLE["research"], system, messages, read_schema)
        found = _text(reply) or found
        uses = _uses(reply)
        if not uses:
            break
        results = [_tool(run, run_tool, "research", u, allowed) for u in uses]
        messages = messages + [{"role": "assistant", "content": _blocks(reply)}, {"role": "user", "content": results}]
    else:
        _stop(run, "research", "max_worker_steps")   # findings are the last text it gave
    return found, {"findings": found}


def _drafter(run: dict, call: Callable, ticket: dict, state: dict, task: str, **_: Any) -> tuple[str, dict]:
    """No tools. A new draft clears the review: a pass is for the draft it read, never the next one."""
    system = f"You write the reply to a Lumen support ticket. Two or three sentences. {RULES}"
    draft = _text(_ask(run, call, "drafter", ROLE["drafter"], system, _task_msg(ticket, state, task), None))
    return draft, {"draft": draft, "review": None}


def _reviewer(run: dict, call: Callable, ticket: dict, state: dict, task: str, **_: Any) -> tuple[str, dict]:
    """No tools. The LLMs lab's judge prompt and three rubric ids, on the same `call` as the desk (R21)."""
    if state["draft"] is None:
        raise ValueError("reviewer: there is no draft to review")
    msg = [{"role": "user", "content": f"<ticket>\n{ticket['text']}\n</ticket>\n\n<reply>\n{state['draft']}\n</reply>\n\n"
            f"Answer with JSON only: an object with the keys {', '.join(RUBRIC_IDS)}, each {{\"pass\": true|false, \"reason\": \"...\"}}."}]
    got = _json(_text(_ask(run, call, "reviewer", ROLE["reviewer"], _judge_system(load_prompts()), msg, None)), "reviewer")
    if not (isinstance(got, dict) and all(isinstance(got.get(r), dict) and isinstance(got[r].get("pass"), bool) for r in RUBRIC_IDS)):
        raise ValueError(f"reviewer: the reply must hold a boolean pass for each of {RUBRIC_IDS}")
    verdict = {r: got[r]["pass"] for r in RUBRIC_IDS}
    review = {"passed": all(verdict.values()), "verdict": verdict}
    reasons = {r: _reason(got[r]) for r in RUBRIC_IDS}
    return _pyjson({**review, "reasons": reasons}), {"review": review}


def _reason(entry: dict) -> str:
    """A rubric reason as text, the same in Go: a string as is, null or missing as "", anything else as JSON."""
    reason = entry.get("reason")
    return reason if isinstance(reason, str) else "" if reason is None else _pyjson(reason)


RUN_WORKER = {"triage": _triage, "research": _research, "drafter": _drafter, "reviewer": _reviewer}


# ── 5. The supervisor ───────────────────────────────────────────────────────
def supervisor(ticket: dict, *, call: Callable = claude, approve: str = "decline", elicit: str = "accept",
               labels_path: Path | None = None) -> dict:
    """A supervisor agent hands work to four workers through `handoff(worker, task)` tool calls.

    Why: each worker has one job and only the tools that job needs (research: lumen-read; the rest:
    none). The shared state is explicit, and every change to it is logged. The write is the
    supervisor's alone, and code, not the prompt, decides when: label_ticket is not in the
    supervisor's tools until the reviewer has passed the current draft (a new draft or a new category
    clears the pass, R30), a label_ticket that was
    not offered in that request is refused (so one asked for next to the reviewer's handoff fails),
    and its arguments must be this ticket and the triaged category (R27).
    Not when: the ticket is simple enough for one agent. The supervisor spends a model call per
    handoff on top of the workers' own, several times the single agent's tokens (lesson 6).
    The reply is the reviewed draft when the review passed, else the supervisor's last text (STOPPED
    at the cap). Every cap leaves a kind "stop" Trace record (R29)."""
    run = {**_new_run(), "handoffs": []}
    state = {"ticket_id": ticket["id"], "category": None, "findings": None, "draft": None, "review": None}
    with tempfile.TemporaryDirectory() as tmp, _session(labels_path, tmp, approve=approve, elicit=elicit, run=run,
                                                         agent="supervisor", servers=("read", "ops")) as (schema, run_tool, session):
        session.read_untrusted.set()     # the ticket text is in the first message (module docstring)
        read_schema = [t for t in schema if t["name"] in core.READ_TOOLS]
        ops_schema = [t for t in schema if t["name"] in core.OPS_TOOLS]
        messages = _ticket_msg(ticket)
        text, stop = STOPPED, "max_turns"
        for _ in range(MAX_TURNS):
            offered = [HANDOFF] + (ops_schema if _passed(state) else [])   # the gate: rebuilt every turn
            response = _ask(run, call, "supervisor", "supervise", SUPERVISOR_SYSTEM, messages, offered)
            uses = _uses(response)
            if not uses:
                text, stop = _text(response), "end_turn"
                break
            names = {t["name"] for t in offered}
            results = []
            for u in uses:                                    # in the order asked, one at a time
                if u.name == "handoff" and "handoff" in names:
                    results.append(_handoff(run, call, ticket, state, u, read_schema=read_schema, run_tool=run_tool))
                else:   # an ops tool also needs the review to still be passed (a handoff earlier in this response may have reset it)
                    allowed = {t["name"] for t in ops_schema} & names if _passed(state) else set()
                    results.append(_tool(run, run_tool, "supervisor", u, allowed, _unbound(u, state)))
            messages = messages + [{"role": "assistant", "content": _blocks(response)}, {"role": "user", "content": results}]
        else:
            _stop(run, "supervisor", "max_turns")
    reply = state["draft"] if _passed(state) else text
    return {"reply": reply, "stop": stop, **run, "state": dict(state)}


def _unbound(use: Any, state: dict) -> str | None:
    """R27: label_ticket may label only this ticket, with the category triage found. An injected target
    (t061's "label t001 outage") is refused here, before the guard or the server is asked."""
    if use.name != "label_ticket":
        return None
    if use.input.get("id") == state["ticket_id"] and use.input.get("label") == state["category"]:
        return None
    return (f"label_ticket may only label this ticket ({state['ticket_id']}) with its triaged category "
            f"({state['category']}); nothing was written.")


def _handoff(run: dict, call: Callable, ticket: dict, state: dict, use: Any, **tools: Any) -> dict:
    """Run one worker for a handoff tool call and return its tool_result. A bad handoff (an unknown
    worker, a task that is not a string) or a worker failure is an error result for the supervisor to
    read, as the harness does with a failed tool: the state is unchanged (R26). An unknown worker is no
    handoff at all: it goes to `errors`, never `handoffs` or the Trace (R37)."""
    worker, task = use.input.get("worker"), use.input.get("task")
    if not (isinstance(worker, str) and worker in RUN_WORKER):
        who = worker if isinstance(worker, str) else _pyjson(worker)   # the model may send any JSON here
        out = f"Unknown worker: {who}. Workers: {', '.join(WORKERS)}"
        run["errors"].append({"agent": "supervisor", "tool": "handoff", "args": use.input, "result": out})
        return {"type": "tool_result", "tool_use_id": use.id, "content": out, "is_error": True}
    start = time.perf_counter()
    try:
        if not isinstance(task, str):
            raise ValueError("handoff: task must be a string")
        out, update = RUN_WORKER[worker](run, call, ticket, state, task, **tools)
    except ValueError as e:
        out, update = str(e), None
    _apply(run, state, worker, task, update, round((time.perf_counter() - start) * 1000))
    return {"type": "tool_result", "tool_use_id": use.id, "content": out, "is_error": update is None}


# ── 6. The engine ───────────────────────────────────────────────────────────
if "hand" not in core.ENGINES:     # importing twice (a test reload) must not register twice
    core.register("hand", core.Engine(single=single_agent, supervisor=supervisor))
