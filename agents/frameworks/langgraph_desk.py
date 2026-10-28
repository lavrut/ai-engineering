#!/usr/bin/env python3
"""langgraph_desk — the Lumen desk rebuilt in LangGraph: one agent, and a supervisor built as tools.

    langgraph_single(ticket)     -> {reply, stop, trace, calls, tool_calls, tool_results, interrupts, errors}
    langgraph_supervisor(ticket) -> the same, plus {handoffs, state}

The same desk as agents/desk/python/supervisor.py (the hand-built reference), on LangChain's
`create_agent` (which runs on LangGraph; `create_react_agent` is deprecated) and its middleware. The
behaviour is meant to match, so lesson 8 can put the two side by side:

- Tools come from desk_core's `open_desk_tools` (one MCP session per ticket, R18), wrapped as LangChain
  tools, so every call goes through the harness permission gate, the untrusted-read guard and the
  per-session lock. LangChain is NOT connected to the MCP servers for execution: an adapter-built tool
  would call the server directly and skip that gate.
- The session is marked untrusted before the first model call (R24): the ticket text is in it.
- The write (`label_ticket`) also raises LangGraph's own interrupt (`HumanInTheLoopMiddleware`); the
  scripted `approve` answers it. After that the harness guard asks the same scripted person, and the
  server's lumen-ops confirmation (`elicit`) still decides the write.
- The supervisor's workers are tools its agent calls (langgraph-supervisor is archived; LangChain now
  recommends this). The shared state, the worker prompts and their checks are the hand-built ones,
  imported. The ops gate is middleware that rebuilds the supervisor's tool list every model call
  (label_ticket only once the reviewer passed the current draft), plus a check when the tool runs:
  label_ticket must have been offered in the request that asked for it, the review must still be
  passed, and its arguments must be this ticket and the triaged category (R27). A new draft or a new
  category clears the review (R30).
- Sibling tool calls run one at a time in the order the model asked (`max_concurrency=1`; LangGraph
  otherwise runs them in parallel threads), as R19 requires of every engine.

Real runs go through the record/replay proxy (agents/proxy/proxy.py). ChatAnthropic is pointed at it with
`base_url=PROXY_URL` in code: ChatAnthropic would otherwise fall back to the ANTHROPIC_API_URL, then the
ANTHROPIC_BASE_URL environment variable, and the explicit argument beats both, so a stray variable cannot
send traffic elsewhere. The key is always the dummy (the proxy injects the real one in record mode), and
`max_retries=0` (a retry would be another paid call). CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC is a Claude
Code setting and does nothing here: LangChain sends no traffic but the model calls.

Where LangGraph shows through (lesson 8 can quote these):
- A tool name the model made up gets LangGraph's error, "<name> is not a valid tool, try one of [...]".
  That list is every tool registered on the agent, so before the review passes it names label_ticket even
  though the request did not offer it. The run-time gate still refuses such a call. Such calls are not MCP
  calls: they go to the run's `errors`, never `tool_calls` (R35).
- The interrupt is asked once per model turn, before any of that turn's tools run. A label beside a
  handoff in the same turn (say a drafter's) can be asked about and approved, then refused when it runs,
  because the handoff cleared the review first.
- A call rejected at the interrupt never runs. It is logged in tool_calls when the interrupt is answered,
  so before that turn's sibling tool calls, and its tool_results entry is the marker "rejected at the
  interrupt: <tool>", not the framework's ToolMessage text (R36).

MCP adapter: `langchain_mcp_adapters.MultiServerMCPClient` (0.3.2, the latest) requires mcp<2 and fails to
import against the track's pinned mcp==2.3.0, so it is not installed and this module does not use it.
"""
from __future__ import annotations

import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desk" / "python"))

from langchain.agents import create_agent  # noqa: E402
from langchain.agents.middleware import AgentMiddleware, HumanInTheLoopMiddleware, InterruptOnConfig, hook_config  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage  # noqa: E402
from langchain_core.tools import StructuredTool, ToolException  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

import desk_core as core  # noqa: E402
import supervisor as sv  # noqa: E402  (the hand-built desk: state, workers, gates; import only)
from desk_core import Trace, open_desk_tools  # noqa: E402
from patterns import ASCII_WS, MAX_WORKER_STEPS, RULES  # noqa: E402

# ── 1. Configuration ────────────────────────────────────────────────────────
WRITE_TOOL = "label_ticket"
RUN_CONFIG = {"max_concurrency": 1}   # sibling tool calls one at a time, in the order asked (R19)
WORKER_TOOLS = ("triage", "research", "drafter", "reviewer")   # the supervisor's tools, one per worker
WORKER_DESCRIPTION = {
    "triage": "Hand a task to the triage worker: it finds the ticket's category and priority.",
    "research": "Hand a task to the research worker: it looks things up with the read tools and reports the facts.",
    "drafter": "Hand a task to the drafter: it writes the reply to the customer.",
    "reviewer": "Hand a task to the reviewer: it checks the current draft against the rubric.",
}
TASK_SCHEMA = {"type": "object", "properties": {
    "task": {"type": "string", "description": "What the worker should do, in one or two sentences."}},
    "required": ["task"]}
SUPERVISOR_SYSTEM = (f"You supervise the Lumen support desk. Your tools are your workers: triage, then research if "
                     f"facts are needed, then drafter, then reviewer. If the reviewer fails the draft, ask the drafter "
                     f"again with the reasons. label_ticket appears in your tools only once the reviewer has passed "
                     f"the current draft; use it to label the ticket with its category. When done, answer with one "
                     f"short line for the log. {RULES}")
# Mirrors the inline string in supervisor.py's _research; a later cleanup may lift both to a shared constant.
RESEARCH_SYSTEM = ("You research a Lumen support ticket with the read tools. Report the facts you found in two or "
                   f"three sentences. {RULES}")   # the hand-built research worker's words


def default_model() -> Any:
    """Claude through the proxy: base_url in code, the dummy key, no retries (module docstring)."""
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(base_url=core.PROXY_URL, model=core.MODEL, max_tokens=core.MAX_TOKENS,
                         max_retries=0, api_key=core.DUMMY_KEY)


def _new_run() -> dict:
    return {"trace": Trace(), "calls": [], "tool_calls": [], "tool_results": [], "interrupts": [], "errors": []}


def _model_name(model: Any) -> str:
    return getattr(model, "model", None) or type(model).__name__


def _text(message: BaseMessage) -> str:
    """The message's text blocks joined, trimmed of ASCII whitespace only (as patterns._text)."""
    content = message.content
    if isinstance(content, str):
        return content.strip(ASCII_WS)
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip(ASCII_WS)


def _model_record(run: dict, agent: str, model: Any, message: Any, start: float) -> None:
    """A Trace model record from the AIMessage's usage_metadata (None where the model gave none)."""
    usage = getattr(message, "usage_metadata", None) or {}
    run["trace"].add(agent=agent, kind="model", name=_model_name(model), tokens_in=usage.get("input_tokens"),
                     tokens_out=usage.get("output_tokens"), ms=round((time.perf_counter() - start) * 1000))


# ── 2. Desk tools as LangChain tools ────────────────────────────────────────
def desk_tool(spec: dict, run: dict, run_tool: Callable, agent: str,
              refuse: Callable[[dict], str | None] | None = None) -> StructuredTool:
    """One MCP tool from open_desk_tools as a LangChain tool. Every call goes through `run_tool`: the
    permission gate, the guard and the session lock. `refuse` is a code gate that runs first; a refused
    call never reaches run_tool. A failure is a ToolException, so the ToolMessage has status "error"."""
    name = spec["name"]

    def fn(**args: Any) -> str:
        reason = refuse(args) if refuse else None
        if reason is not None:
            raise ToolException(reason)
        start = time.perf_counter()
        out, is_error = run_tool(name, args)
        run["trace"].add(agent=agent, kind="tool", name=name, ms=round((time.perf_counter() - start) * 1000))
        if is_error:
            raise ToolException(out)
        return out

    return StructuredTool(name=name, description=spec.get("description") or name, args_schema=spec["input_schema"],
                          func=fn, handle_tool_error=True)


# ── 3. The agent loop: calls, offered tools, the cap, the tool log ──────────
class DeskLoop(AgentMiddleware):
    """Middleware for one agent: logs each model call as {agent, role, tools} with a Trace model record,
    narrows the offered tools with `offer` (rebuilt every call), stops after `cap` model calls with a
    kind "stop" record (R29), and logs every tool call that ran in tool_calls/tool_results.
    `skip` names tools that are neither MCP calls nor errors (the supervisor's worker tools are handoffs)."""

    def __init__(self, run: dict, agent: str, role: str, cap: int, cap_name: str,
                 offer: Callable[[list], list] | None = None, skip: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.run, self.agent, self.role, self.cap, self.cap_name = run, agent, role, cap, cap_name
        self.offer, self.skip = offer, skip
        self.n, self.stopped, self.offered = 0, False, set()

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict | None:
        if self.n >= self.cap:
            self.stopped = True
            self.run["trace"].add(agent=self.agent, kind="stop", name=self.cap_name)
            return {"jump_to": "end"}
        return None

    def wrap_model_call(self, request: Any, handler: Callable) -> Any:
        tools = self.offer(list(request.tools)) if self.offer else list(request.tools)
        request = request.override(tools=tools)
        self.offered = {t.name for t in tools}
        self.n += 1
        self.run["calls"].append({"agent": self.agent, "role": self.role, "tools": sorted(self.offered)})
        start = time.perf_counter()
        response = handler(request)
        messages = getattr(response, "result", None) or [response]
        _model_record(self.run, self.agent, request.model, next((m for m in messages if isinstance(m, AIMessage)), None), start)
        return response

    def wrap_tool_call(self, request: Any, handler: Callable) -> Any:
        """R35: tool_calls holds MCP calls only (lumen-read and lumen-ops names), as in every engine. A
        worker tool is a handoff (logged in `handoffs`); any other name the model made up goes to `errors`
        with LangGraph's own error text."""
        result = handler(request)
        call = request.tool_call
        content = getattr(result, "content", None)
        if call["name"] in core.READ_TOOLS | core.OPS_TOOLS:
            self.run["tool_calls"].append({"agent": self.agent, "tool": call["name"], "args": call["args"],
                                           "is_error": getattr(result, "status", None) == "error"})
            self.run["tool_results"].append(content)
        elif call["name"] not in self.skip:
            self.run["errors"].append({"agent": self.agent, "tool": call["name"], "args": call["args"], "result": content})
        return result


def _hitl_decision(approve: str) -> dict:
    """The scripted person's answer to LangGraph's interrupt: the same `approve` the harness guard uses."""
    return {"type": "approve"} if approve == "accept" else {"type": "reject"}


def _hitl(when: Callable[[Any], bool] | None = None) -> HumanInTheLoopMiddleware:
    """LangGraph's own pause before label_ticket runs (batched: once per model turn, before any tool of
    that turn runs, so the order of the turn's calls is kept). `when` skips calls the code gate refuses."""
    config = InterruptOnConfig(allowed_decisions=["approve", "reject"],
                               description="label_ticket writes a label. Approve the write?")
    if when is not None:
        config["when"] = when
    return HumanInTheLoopMiddleware({WRITE_TOOL: config})


def drive(graph: Any, messages: list[dict], run: dict, agent: str, approve: str) -> list[BaseMessage]:
    """Invoke an agent graph, answering each interrupt with the scripted person, until it finishes.
    Each question and answer is logged in `interrupts`; a rejected call never runs, so it is logged
    in tool_calls here (is_error) with the framework's rejection text."""
    config = {**RUN_CONFIG, "configurable": {"thread_id": uuid.uuid4().hex}}
    out = graph.invoke({"messages": messages}, config)
    while out.get("__interrupt__"):
        (pause,) = out["__interrupt__"]        # batched mode: one interrupt per model turn
        decisions = []
        for req in pause.value["action_requests"]:
            decision = _hitl_decision(approve)
            decisions.append(decision)
            run["interrupts"].append({"agent": agent, "tool": req["name"], "args": req["args"], "decision": decision["type"]})
            if decision["type"] == "reject":
                run["tool_calls"].append({"agent": agent, "tool": req["name"], "args": req["args"], "is_error": True})
                run["tool_results"].append(f"rejected at the interrupt: {req['name']}")
        out = graph.invoke(Command(resume={"decisions": decisions}), config)
    return out["messages"]


def _last_text(messages: list[BaseMessage]) -> str:
    ai = [m for m in messages if isinstance(m, AIMessage)]
    return _text(ai[-1]) if ai else ""


# ── 4. The single agent ─────────────────────────────────────────────────────
def langgraph_single(ticket: dict, *, model: Any = None, approve: str = "decline", elicit: str = "decline",
                     labels_path: Path | None = None) -> dict:
    """One create_agent with every desk tool, capped at MAX_TURNS model calls. As the hand-built single
    agent: the only gates on the write are the interrupt, the guard and the server's confirmation.
    `labels_path` defaults to a temporary file, so a test or a stray run never writes the real one."""
    model = model if model is not None else default_model()
    run = _new_run()
    with tempfile.TemporaryDirectory() as tmp, open_desk_tools(
            ("read", "ops"), guard=True, approve=approve, elicit=elicit,
            labels_path=labels_path or Path(tmp) / "labels.jsonl") as (schema, run_tool, session):
        session.read_untrusted.set()     # R24: the ticket text is in the first message
        loop = DeskLoop(run, "single", "act", sv.MAX_TURNS, "max_turns")
        graph = create_agent(model, [desk_tool(s, run, run_tool, "single") for s in schema],
                             system_prompt=sv.SINGLE_SYSTEM, middleware=[loop, _hitl()], checkpointer=InMemorySaver())
        messages = drive(graph, sv._ticket_msg(ticket), run, "single", approve)
    reply, stop = (sv.STOPPED, "max_turns") if loop.stopped else (_last_text(messages), "end_turn")
    return {"reply": reply, "stop": stop, **run}


# ── 5. The supervisor, built as tools ───────────────────────────────────────
def _shim(model: Any, run: dict) -> Callable:
    """The hand-built workers' `call(messages, system=, tools=, trace=, agent=)` on a LangChain chat model,
    for the workers without tools (triage, drafter, reviewer): one model.invoke, an Anthropic-shaped reply."""
    def call(messages: list[dict], *, system: str, tools: Any = None, trace: Trace | None = None,
             agent: str = "desk", **_: Any) -> SimpleNamespace:
        if tools:
            raise ValueError("the LangGraph shim serves the workers without tools only")
        start = time.perf_counter()
        reply = model.invoke([{"role": "system", "content": system}, *messages])
        _model_record(run, agent, model, reply, start)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=_text(reply))])
    return call


def langgraph_supervisor(ticket: dict, *, model: Any = None, approve: str = "decline", elicit: str = "decline",
                         labels_path: Path | None = None) -> dict:
    """A create_agent supervisor whose tools are the four workers (and label_ticket once the review passed).

    Why: the LangChain-recommended supervisor (agents as tools) with the hand-built desk's state and gates,
    so lesson 8 compares frameworks, not desks. The research worker is its own create_agent with the
    lumen-read tools only; triage, drafter and reviewer are single model calls through the hand-built
    worker functions. The reply is the reviewed draft when the review passed, else the supervisor's last
    text (STOPPED at the cap)."""
    model = model if model is not None else default_model()
    run = {**_new_run(), "handoffs": []}
    state = {"ticket_id": ticket["id"], "category": None, "findings": None, "draft": None, "review": None}
    with tempfile.TemporaryDirectory() as tmp, open_desk_tools(
            ("read", "ops"), guard=True, approve=approve, elicit=elicit,
            labels_path=labels_path or Path(tmp) / "labels.jsonl") as (schema, run_tool, session):
        session.read_untrusted.set()     # R24: the ticket text is in the first message
        read_specs = [s for s in schema if s["name"] in core.READ_TOOLS]
        ops_specs = [s for s in schema if s["name"] in core.OPS_TOOLS]
        call = _shim(model, run)

        def research(run_: dict, call_: Callable, ticket_: dict, state_: dict, task: str, **_: Any) -> tuple[str, dict]:
            loop = DeskLoop(run, "research", sv.ROLE["research"], MAX_WORKER_STEPS, "max_worker_steps")
            graph = create_agent(model, [desk_tool(s, run, run_tool, "research") for s in read_specs],
                                 system_prompt=RESEARCH_SYSTEM, middleware=[loop])
            messages = graph.invoke({"messages": sv._task_msg(ticket, state, task)}, dict(RUN_CONFIG))["messages"]
            found = next((t for t in reversed([_text(m) for m in messages if isinstance(m, AIMessage)]) if t), "")
            return found, {"findings": found}

        workers = {**sv.RUN_WORKER, "research": research}

        def handoff(worker: str) -> StructuredTool:
            def fn(**args: Any) -> str:
                task = args.get("task")
                start = time.perf_counter()
                try:
                    if not isinstance(task, str):
                        raise ValueError("handoff: task must be a string")
                    out, update = workers[worker](run, call, ticket, state, task)
                except ValueError as e:
                    out, update = str(e), None
                sv._apply(run, state, worker, task, update, round((time.perf_counter() - start) * 1000))
                if update is None:
                    raise ToolException(out)
                return out
            return StructuredTool(name=worker, description=WORKER_DESCRIPTION[worker], args_schema=TASK_SCHEMA,
                                  func=fn, handle_tool_error=True)

        def offer(tools: list) -> list:      # the gate, rebuilt for every supervisor model call
            return [t for t in tools if t.name not in core.OPS_TOOLS or sv._passed(state)]

        loop = DeskLoop(run, "supervisor", "supervise", sv.MAX_TURNS, "max_turns", offer=offer, skip=WORKER_TOOLS)

        def refuse(args: dict) -> str | None:
            """At run time: offered in the request that asked for it, the review still passed (a handoff
            earlier in the same response may have cleared it), and bound to the state (R27)."""
            if WRITE_TOOL not in loop.offered or not sv._passed(state):
                return f"Unknown tool: {WRITE_TOOL}"
            return sv._unbound(SimpleNamespace(name=WRITE_TOOL, input=args), state)

        tools = [handoff(w) for w in WORKER_TOOLS] + [desk_tool(s, run, run_tool, "supervisor", refuse) for s in ops_specs]
        graph = create_agent(model, tools, system_prompt=SUPERVISOR_SYSTEM, checkpointer=InMemorySaver(),
                             middleware=[loop, _hitl(when=lambda req: refuse(req.tool_call["args"]) is None)])
        messages = drive(graph, sv._ticket_msg(ticket), run, "supervisor", approve)
    stop = "max_turns" if loop.stopped else "end_turn"
    text = sv.STOPPED if loop.stopped else _last_text(messages)
    reply = state["draft"] if sv._passed(state) else text
    return {"reply": reply, "stop": stop, **run, "state": dict(state)}


# ── 6. The engine ───────────────────────────────────────────────────────────
if "langgraph" not in core.ENGINES:     # importing twice (a test reload) must not register twice
    core.register("langgraph", core.Engine(single=langgraph_single, supervisor=langgraph_supervisor))
