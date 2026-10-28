#!/usr/bin/env python3
"""sdk_desk — the Lumen desk rebuilt in the Claude Agent SDK: one agent, and a supervisor whose workers are SDK subagents.

    sdk_single(ticket)     -> {reply, stop, trace, calls, tool_calls, errors, cost_usd, usage}
    sdk_supervisor(ticket) -> the same, plus {handoffs, state}

The same desk as agents/desk/python/supervisor.py (the hand-built reference) and langgraph_desk.py, on the Agent
SDK, which runs the Claude Code CLI bundled in the wheel as a subprocess (`query()`). The behaviour is meant to
match, so lesson 8 can put the three side by side. What each part does, and what the SDK forced:

- Options are hermetic (section 2): `setting_sources=[]` (no user or project settings, CLAUDE.md, skills),
  `strict_mcp_config` (only our MCP servers), an explicit `model=MODEL`, and per run an empty temporary
  `CLAUDE_CONFIG_DIR` (no login, memory or transcript of the machine is read or kept) and an empty temporary
  working directory. The working directory matters: run inside a git repository, the CLI puts that repository's
  status (branch, changed files, recent commits) into every subagent's first message, which would leak the
  machine's state into requests and change the replay key with every commit (verified). Outside one it adds
  none; `includeGitInstructions: false` (`settings=`) turns it off as well, in case the directory changes. The
  single agent runs with `tools=[]`: no built-in tool at all. The supervisor needs `tools=["Agent"]`: with
  `tools=[]` the CLI drops the Agent tool too, so no subagent can run (verified against the bundled CLI
  2.1.310, offline, with a local stub of the API). Agent is the delegation tool only (no file, shell or web),
  and the PreToolUse hook refuses any subagent_type but the four workers, including the CLI's own "fork".
- The CLI's environment is scrubbed (R44). The SDK starts the CLI with the parent's whole environment under
  `options.env`, so `options.env` sets every inherited variable empty except PATH, TMPDIR and the locale, and always
  empties the ones that could send traffic past the proxy, switch the provider, or add a credential or a header
  (NEUTRALISE: the HTTP(S)/ALL proxies, CLAUDE_CODE_USE_BEDROCK/VERTEX/..., ANTHROPIC_AUTH_TOKEN,
  ANTHROPIC_CUSTOM_HEADERS, ANTHROPIC_API_URL, ...). HOME is the run's empty config directory.
- `permission_mode="dontAsk"` with `allowed_tools` set to exactly our MCP tools (and Agent): the CLI denies
  anything else without asking, and never prompts. So the SDK's permission layer approves nothing we did not
  name, and the decisions that matter are ours: the PreToolUse hook (the code gates and the guard), then the
  harness gate inside run_tool, then lumen-ops's own confirmation. `bypassPermissions` would let through any
  tool the CLI offers; `default` behaves like dontAsk for an SDK app since CLI 2.1.302, but dontAsk says so.
- Tools go through the gated path (section 3): the desk tools are in-process MCP servers (`@tool` and
  `create_sdk_mcp_server`, named lumen-read and lumen-ops) whose handlers call desk_core's `run_tool`, so the
  harness permission gate, the untrusted-read guard and the per-session lock apply (one session per ticket,
  R18). The SDK is NOT given mcp/python/read_server.py or ops_server.py as external servers: the CLI would
  call them directly and skip that gate.
- The session is marked untrusted before the model is called (R24): the ticket text is in the prompt.
- The guard is also a PreToolUse hook: a write after untrusted text asks the scripted person (`approve`) and is
  denied on a no, before the SDK calls the tool. A yes is asked again by run_tool's harness gate (the same
  scripted answer), then lumen-ops asks for its own confirmation (`elicit`): two layers of asking, as LangGraph
  has (R32).
- The supervisor's workers are SDK subagents (`agents=`, one AgentDefinition each; research gets the lumen-read
  tools, the others none). The hook rewrites every Agent call before it runs: the prompt becomes the
  hand-built desk's task message (the ticket, the state, the supervisor's task), and `run_in_background`
  becomes false (the CLI otherwise launches subagents in the background and hands back "launched"; verified).
  The PostToolUse hook reads the subagent's final text and runs the hand-built worker's own checks on it
  (triage's category and priority, the reviewer's verdict), then merges the update into the shared state and
  logs it (sv._apply). A new draft or a new category clears the review (R30). The supervisor still reads the
  subagent's own report as the Agent result (a PostToolUse hook cannot replace an Agent's output; verified), so a
  failed check reaches it as hook additional context ("The triage handoff failed and changed nothing: ...").
- The ops gate is enforced when the tool runs: the CLI offers a session one tool list for the whole run (no
  per-turn change), so label_ticket is always in the supervisor's tools and the hook refuses it unless (1) the
  review is passed now, (2) the handoff that passed it was in an earlier model response than the label (the
  hand-built "offered in the request that asked for it": a label beside the reviewer's handoff is refused),
  and (3) its arguments are this ticket and the triaged category (R27). For the same reason the supervisor is
  offered the read tools (the MCP servers belong to the session); the hook refuses them (they are research's),
  as it refuses a label from a worker.
- R35/R37: `tool_calls` holds lumen-read and lumen-ops calls only; a call to any other name, or an Agent call
  to an unknown worker, goes to `errors` as {agent, tool, args, result}, with the CLI's error text.
- R29: the main loop is capped by `max_turns=MAX_TURNS`, research by `maxTurns=MAX_WORKER_STEPS`; each cap
  leaves a kind "stop" Trace record.

The trace is built from the message stream (section 5), with `forward_subagent_text=True`: without it the CLI
streams only a subagent's tool_use and tool_result messages, so a text-only worker response (every triage,
drafter and reviewer call) would leave no trace (verified). A "model" record per model response (the CLI splits
one response into one AssistantMessage per block; they share a message id), with `tokens_in` from that
response's usage; `tokens_out` and `ms` are None because the CLI's per-message output_tokens is a placeholder
(it read 1 where the response had 5; verified) and the stream carries no per-call timing. Totals per model,
subagents included, are in `usage` (ResultMessage.model_usage). A "tool" record per desk tool that ran, with
ms from PostToolUse's duration_ms; a "handoff" record per delegation, where its result comes back. `cost_usd`
is ResultMessage.total_cost_usd: the CLI's client-side estimate from its own price table, not billing. The
proxy's ledger is the cost of record (the spike saw it read 2.0 for a canned reply).

Real runs go through the record/replay proxy: ANTHROPIC_BASE_URL is PROXY_URL in the CLI's environment
(`env=`, spike S1), the key is the dummy (the proxy injects the real one in record mode, and drops any auth
header the CLI sends), CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 removes the title helper and the HEAD probe
(R9), CLAUDE_CODE_MAX_OUTPUT_TOKENS=4000 makes max_tokens 4000 (R3), and CLAUDE_CODE_MAX_RETRIES=0 stops the
CLI retrying (it made 11 requests for one 500 by default, 1 with this; R17). A replay miss or the cost cap
raises desk_core.ProxyError. Known CLI behaviours: it repeats a request that got a 404 once (harmless in
replay, where nothing is forwarded), and its 404 text names the model, not the replay miss, so this module
reads the status instead.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
from claude_agent_sdk import (AgentDefinition, AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultError,
                              ResultMessage, ToolResultBlock, ToolUseBlock, UserMessage, create_sdk_mcp_server,
                              query, tool)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desk" / "python"))

import desk_core as core  # noqa: E402
import supervisor as sv  # noqa: E402  (the hand-built desk: state, workers, gates; import only)
from desk_core import Trace, mcp_agent, open_desk_tools  # noqa: E402
from patterns import ASCII_WS, MAX_WORKER_STEPS, RULES  # noqa: E402
from triage_lab import _judge_system, load_prompts  # noqa: E402  (the LLMs lab, import only)

# ── 1. Configuration ────────────────────────────────────────────────────────
WRITE_TOOL = "label_ticket"
SERVERS = {"read": "lumen-read", "ops": "lumen-ops"}              # in-process MCP server names
AGENT_TOOLS = ("Agent", "Task")      # the delegation tool: "Agent" in tool_use blocks, "Task" in the init list
PERMISSION_MODE = "dontAsk"          # not in allowed_tools: denied, never asked (module docstring)
CLI_SETTINGS = json.dumps({"includeGitInstructions": False})   # no git status in any prompt (module docstring)
SEEN_TIMEOUT = 10.0                  # seconds the ops gate waits for the stream to place a label request
SDK_ENV = {
    "ANTHROPIC_BASE_URL": core.PROXY_URL,
    "ANTHROPIC_API_KEY": core.DUMMY_KEY,                 # the CLI needs a key to start; the proxy injects the real one
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",     # no title helper, no HEAD probe (R9)
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(core.MAX_TOKENS),   # max_tokens 4000 instead of 128000 (R3)
    "CLAUDE_CODE_MAX_RETRIES": "0",                      # a retry would be another paid call (R17)
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",              # spike S4
    "CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS": "1",      # no general-purpose subagent: only the four workers
    "CLAUDE_CODE_ENTRYPOINT": "sdk-py",                  # pinned: it is in the prompt's billing header (the key)
}
# R44: the SDK starts the CLI with {**os.environ, **options.env}, so the parent's environment leaks into the child
# unless options.env overrides it. These would send traffic past the proxy, change the provider, or add a
# credential or header; they are always set empty. Every other inherited variable is blanked too, except ENV_KEEP.
NEUTRALISE = (
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_GATEWAY", "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_IDENTITY_TOKEN", "ANTHROPIC_IDENTITY_TOKEN_FILE", "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_AUTH_TOKEN", "ANTHROPIC_AWS_API_KEY", "ANTHROPIC_API_URL", "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_BEDROCK_MANTLE_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_AWS_BASE_URL", "ANTHROPIC_GOOGLE_CLOUD_BASE_URL",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)
ENV_KEEP = ("PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE")   # what the CLI may still inherit as it is
WORKER_DESCRIPTION = {
    "triage": "Finds the ticket's category and priority.",
    "research": "Looks things up with the read tools and reports the facts.",
    "drafter": "Writes the reply to the customer.",
    "reviewer": "Checks the current draft against the rubric.",
}
SUPERVISOR_SYSTEM = (f"You supervise the Lumen support desk. Give work to your workers with the Agent tool "
                     f"(subagent_type triage, then research if facts are needed, then drafter, then reviewer); the "
                     f"prompt is the task, in one or two sentences. If the reviewer fails the draft, ask the drafter "
                     f"again with the reasons. Once the reviewer has passed the current draft, label the ticket with "
                     f"its category using label_ticket; before that it is refused. When done, answer with one short "
                     f"line for the log. {RULES}")
RESEARCH_SYSTEM = ("You research a Lumen support ticket with the read tools. Report the facts you found in two or "
                   f"three sentences. {RULES}")   # the hand-built research worker's words
DRAFTER_SYSTEM = f"You write the reply to a Lumen support ticket. Two or three sentences. {RULES}"   # as sv._drafter


def mcp_name(name: str) -> str:
    """The CLI's name for a desk tool: mcp__<server>__<tool>."""
    return f"mcp__{SERVERS['ops' if name in core.OPS_TOOLS else 'read']}__{name}"


def desk_name(name: str) -> str | None:
    """The desk tool behind an mcp__lumen-...__ name, or None for anything else."""
    for server in SERVERS.values():
        prefix = f"mcp__{server}__"
        if name.startswith(prefix) and name[len(prefix):] in core.READ_TOOLS | core.OPS_TOOLS:
            return name[len(prefix):]
    return None


# ── 2. Options: hermetic ────────────────────────────────────────────────────
def sdk_env(config_dir: str, parent: dict[str, str] | None = None) -> dict[str, str]:
    """The CLI's environment as options.env, which the SDK lays over the parent's (R44): every inherited variable
    but ENV_KEEP set empty, NEUTRALISE set empty whether inherited or not, then SDK_ENV, and this run's own empty
    config dir as both CLAUDE_CONFIG_DIR and HOME (nothing under the user's home is read)."""
    parent = dict(os.environ) if parent is None else parent
    blank = {k: "" for k in parent if k not in ENV_KEEP and k != "CLAUDECODE"}   # the SDK drops CLAUDECODE itself
    return {**blank, **{k: "" for k in NEUTRALISE}, **SDK_ENV, "CLAUDE_CONFIG_DIR": config_dir, "HOME": config_dir}


def cli_dirs(tmp: str) -> tuple[str, str]:
    """(config dir, working dir) for one run, both empty and inside `tmp`. The working dir is no git repository
    and holds nothing of the machine's, and it is a real path: given a symlinked one (macOS's /tmp) the CLI adds
    an "Additional working directories" line the proxy does not mask."""
    root = Path(tmp).resolve()
    config, work = root / "config", root / "work"
    config.mkdir()
    work.mkdir()
    return str(config), str(work)


def build_options(*, system: str, tools: list[str], allowed: list[str], servers: dict, hooks: dict,
                  tmp: str, agents: dict[str, AgentDefinition] | None = None) -> ClaudeAgentOptions:
    """Every option the desks set, in one place, so the tests can assert them exactly."""
    config_dir, work_dir = cli_dirs(tmp)
    return ClaudeAgentOptions(
        tools=tools, allowed_tools=allowed, setting_sources=[], settings=CLI_SETTINGS, strict_mcp_config=True,
        mcp_servers=servers, permission_mode=PERMISSION_MODE, model=core.MODEL, system_prompt=system,
        max_turns=sv.MAX_TURNS, hooks=hooks, agents=agents, forward_subagent_text=True,
        env=sdk_env(config_dir), cwd=work_dir)


def worker_agents() -> dict[str, AgentDefinition]:
    """The four workers as subagents, with the hand-built workers' system prompts. Research gets the lumen-read
    tools and MAX_WORKER_STEPS model calls; the others no tool and one call."""
    prompts = load_prompts()
    triage = prompts["triage"]["v2_rules"] + '\n\nAnswer with JSON only: {"category": "<category>", "priority": "<priority>"}'
    read = sorted(mcp_name(n) for n in core.READ_TOOLS)

    def define(name: str, prompt: str, tools: list[str], turns: int) -> AgentDefinition:
        return AgentDefinition(description=WORKER_DESCRIPTION[name], prompt=prompt, tools=tools, model=core.MODEL,
                               maxTurns=turns, background=False)
    return {"triage": define("triage", triage, [], 1),
            "research": define("research", RESEARCH_SYSTEM, read, MAX_WORKER_STEPS),
            "drafter": define("drafter", DRAFTER_SYSTEM, [], 1),
            "reviewer": define("reviewer", _judge_system(prompts), [], 1)}


# ── 3. Desk tools as in-process MCP servers (the gated path) ────────────────
def desk_servers(schema: list[dict], run_tool: Callable[[str, dict], tuple[str, bool]]) -> dict[str, Any]:
    """lumen-read and lumen-ops as SDK MCP servers whose handlers call run_tool: the harness gate, the guard and
    the session lock decide every call. run_tool blocks (the MCP bridge waits on its own loop), so it runs in a
    worker thread. Returns {server name: config} for ClaudeAgentOptions.mcp_servers."""
    def handler(name: str) -> Callable:
        async def call(args: dict) -> dict:
            text, is_error = await anyio.to_thread.run_sync(run_tool, name, args)
            return {"content": [{"type": "text", "text": text}], "is_error": is_error}
        return call

    servers = {}
    for key, server in SERVERS.items():
        names = core.READ_TOOLS if key == "read" else core.OPS_TOOLS
        tools = [tool(s["name"], s.get("description") or s["name"], s["input_schema"])(handler(s["name"]))
                 for s in schema if s["name"] in names]
        servers[server] = create_sdk_mcp_server(server, tools=tools)
    return servers


# ── 4. Hooks: the guard, the code gates, the handoffs ───────────────────────
def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def _reply(text: str) -> SimpleNamespace:
    """An Anthropic-shaped reply holding `text`, so the hand-built workers' checks can read a subagent's answer."""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _response_text(response: Any) -> str:
    """The text of a tool response: the Agent tool's {"content": [...]}, an MCP content list, or a string."""
    if isinstance(response, dict):
        response = response.get("content", "")
    if isinstance(response, list):
        return "".join(b.get("text", "") for b in response if isinstance(b, dict) and b.get("type") == "text")
    return response if isinstance(response, str) else ""


class DeskHooks:
    """PreToolUse and PostToolUse for one desk run. `main` is "single" or "supervisor"; `state` is the shared
    state (supervisor only). The hooks run in the SDK's control-request tasks. The CLI runs a response's Agent
    calls concurrently (verified), so the supervisor takes one handoff at a time (R42): a second Agent call while
    one is running is refused. Other tools run in order, and run_tool's lock serializes the MCP calls anyway.
    Every hook fails closed (R43): an exception in PreToolUse is a deny (the SDK would otherwise report a
    control error and the CLI would run the tool; verified), and one in PostToolUse is a failed handoff."""

    def __init__(self, main: str, run: dict, session: Any, approve: str, ticket: dict,
                 state: dict | None = None, stream: "StreamLog | None" = None) -> None:
        self.main, self.run, self.session, self.ticket, self.state, self.stream = main, run, session, ticket, state, stream
        self.human = mcp_agent.scripted_human(approve)
        self.tools = {t.name: t for t in session.tools}
        self.pending: dict[str, tuple[str, str, float]] = {}   # Agent tool_use id -> (worker, task, start)
        self.pass_id: str | None = None                        # the Agent call whose review is the current pass
        self.durations: dict[str, int | None] = {}             # tool_use id -> duration_ms (the tool ran)
        self.handoff_records: dict[str, dict] = {}             # Agent tool_use id -> its Trace handoff record

    def matchers(self) -> dict:
        return {"PreToolUse": [HookMatcher(hooks=[self.pre])], "PostToolUse": [HookMatcher(hooks=[self.post])],
                "PostToolUseFailure": [HookMatcher(hooks=[self.post_failure])]}

    def _allowed(self, agent: str) -> set[str]:
        """The desk tools each agent may call: the hand-built desks' tool lists."""
        if agent == "single":
            return core.READ_TOOLS | core.OPS_TOOLS
        return {"supervisor": core.OPS_TOOLS, "research": core.READ_TOOLS}.get(agent, set())

    async def pre(self, data: dict, tool_use_id: str | None, ctx: Any) -> dict:
        try:
            return await self._pre(data, tool_use_id, ctx)
        except Exception as e:  # R43: a hook that fails must never let the tool run
            return _deny(f"The desk's PreToolUse hook failed, so the call is refused: {type(e).__name__}: {e}")

    async def _pre(self, data: dict, tool_use_id: str | None, _ctx: Any) -> dict:
        name, args = data.get("tool_name", ""), data.get("tool_input") or {}
        agent = (data.get("agent_type") or "subagent") if data.get("agent_id") else self.main   # who is calling
        tool_use_id = tool_use_id or data.get("tool_use_id") or ""
        if name in AGENT_TOOLS:
            if self.main != "supervisor" or agent != "supervisor":
                return _deny(f"Unknown tool: {name}")
            return self._handoff(tool_use_id, args)
        bare = desk_name(name)
        if bare is None:
            return _deny(f"Unknown tool: {name}")
        if bare not in self._allowed(agent):
            return _deny(f"Unknown tool: {bare}")
        if bare == WRITE_TOOL and agent == "supervisor":
            reason = await self._ops_gate(tool_use_id, args)
            if reason is not None:
                return _deny(reason)
        tool_ = self.tools.get(bare)
        if tool_ is not None and mcp_agent.needs_human(tool_, read_untrusted=self.session.read_untrusted.is_set(), guard=True):
            if not self.human(bare, args):          # the guard: a write after untrusted text needs a yes
                return _deny("The user denied this action.")
        return {}

    async def _ops_gate(self, tool_use_id: str, args: dict) -> str | None:
        """label_ticket from the supervisor: the review passed now, by a handoff in an earlier model response
        than this one, and the arguments bound to the state (R27). The stream places the request; a request
        it has not placed in SEEN_TIMEOUT is refused (fails closed)."""
        if not sv._passed(self.state) or self.pass_id is None or self.stream is None:
            return f"Unknown tool: {WRITE_TOOL}"
        here = await self.stream.response_of(tool_use_id, SEEN_TIMEOUT)
        passed_in = self.stream.response_index.get(self.pass_id)
        if here is None or passed_in is None or here <= passed_in:
            return f"Unknown tool: {WRITE_TOOL}"
        return sv._unbound(SimpleNamespace(name=WRITE_TOOL, input=args), self.state)

    def _handoff(self, tool_use_id: str, args: dict) -> dict:
        """An Agent call from the supervisor. An unknown worker is no handoff (R37: it goes to errors from the
        stream). A non-string task or a review with no draft is a failed handoff (logged, state unchanged, R26).
        Otherwise the prompt becomes the hand-built task message and the subagent runs in the foreground."""
        worker, task = args.get("subagent_type"), args.get("prompt")
        if not (isinstance(worker, str) and worker in sv.RUN_WORKER):
            who = worker if isinstance(worker, str) else sv._pyjson(worker)
            return _deny(f"Unknown worker: {who}. Workers: {', '.join(sv.WORKERS)}")
        if self.pending:   # R42: one handoff at a time (pending is set below, with no await in between)
            busy = next(iter(self.pending.values()))[0]
            self._apply(tool_use_id, worker, task, None, 0)
            return _deny(f"One handoff at a time: the {busy} handoff is still running. Ask {worker} again once it has finished.")
        reason = ("handoff: task must be a string" if not isinstance(task, str) else
                  "reviewer: there is no draft to review" if worker == "reviewer" and self.state["draft"] is None else None)
        if reason is not None:
            self._apply(tool_use_id, worker, task, None, 0)
            return _deny(reason)
        self.pending[tool_use_id] = (worker, task, time.perf_counter())
        prompt = self._worker_prompt(worker, task)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "updatedInput": {**args, "prompt": prompt, "run_in_background": False}}}

    def _worker_prompt(self, worker: str, task: str) -> str:
        """What the worker reads: the hand-built desk's message for it (sv._task_msg; the reviewer's own)."""
        if worker == "reviewer":   # the text of sv._reviewer's message, with the current draft
            return (f"<ticket>\n{self.ticket['text']}\n</ticket>\n\n<reply>\n{self.state['draft']}\n</reply>\n\n"
                    f"Answer with JSON only: an object with the keys {', '.join(sv.RUBRIC_IDS)}, each "
                    f"{{\"pass\": true|false, \"reason\": \"...\"}}.")
        return sv._task_msg(self.ticket, self.state, task)[0]["content"]

    async def post(self, data: dict, tool_use_id: str | None, ctx: Any) -> dict:
        try:
            return await self._post(data, tool_use_id, ctx)
        except Exception as e:  # R43: never leave a handoff half-applied; it failed and changed nothing
            return self._post_failed(tool_use_id or data.get("tool_use_id") or "", f"{type(e).__name__}: {e}")

    def _post_failed(self, tool_use_id: str, reason: str) -> dict:
        if tool_use_id in self.pending:
            worker, task, start = self.pending.pop(tool_use_id)
            self._apply(tool_use_id, worker, task, None, round((time.perf_counter() - start) * 1000))
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                       "additionalContext": f"The desk's PostToolUse hook failed; the call changed nothing: {reason}"}}

    async def _post(self, data: dict, tool_use_id: str | None, _ctx: Any) -> dict:
        tool_use_id = tool_use_id or data.get("tool_use_id") or ""
        self.durations[tool_use_id] = data.get("duration_ms")
        if tool_use_id not in self.pending:
            return {}
        worker, task, start = self.pending.pop(tool_use_id)
        text = _response_text(data.get("tool_response")).strip(ASCII_WS)
        scratch = {"trace": Trace(), "calls": [], "tool_calls": [], "errors": []}
        try:   # the hand-built worker's own checks, fed the subagent's answer instead of a model call
            out, update = sv.RUN_WORKER[worker](scratch, lambda *_a, **_k: _reply(text), self.ticket, self.state, task,
                                                read_schema=[], run_tool=None)
        except Exception as e:   # a ValueError is a failed check; anything else is a failed handoff too (never unknown)
            out, update = (str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"), None
        self._apply(tool_use_id, worker, task, update, round((time.perf_counter() - start) * 1000))
        if update is None:   # the CLI keeps the subagent's text as the result; the model reads this beside it
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                           "additionalContext": f"The {worker} handoff failed and changed nothing: {out}"}}
        return {}

    async def post_failure(self, data: dict, tool_use_id: str | None, _ctx: Any) -> dict:
        tool_use_id = tool_use_id or data.get("tool_use_id") or ""
        self.durations[tool_use_id] = data.get("duration_ms")
        try:
            if tool_use_id in self.pending:
                worker, task, start = self.pending.pop(tool_use_id)
                self._apply(tool_use_id, worker, task, None, round((time.perf_counter() - start) * 1000))
        except Exception:  # R43: nothing to refuse after the fact; the handoff is already logged as failed or not at all
            pass
        return {}

    def _apply(self, tool_use_id: str, worker: str, task: Any, update: dict | None, ms: int) -> None:
        """sv._apply on the shared state; its Trace record is kept and placed in the trace at the handoff's
        result (section 5). A passed review remembers which handoff passed it; anything that clears it forgets."""
        logged = {"handoffs": self.run["handoffs"], "trace": Trace()}
        sv._apply(logged, self.state, worker, task, update, ms)
        self.handoff_records[tool_use_id] = logged["trace"].to_list()[-1]
        if update is not None and "review" in update:
            self.pass_id = tool_use_id if sv._passed(self.state) else None
        if not sv._passed(self.state):
            self.pass_id = None


# ── 5. The logs and the trace, from the message stream ──────────────────────
class StreamLog:
    """Reads the SDK message stream in order. Places every main-agent tool request in its model response
    (response_index: the order of responses, for the ops gate) and keeps the messages for the trace and logs."""

    def __init__(self, main: str, role: str, offered: list[str]) -> None:
        self.main, self.role, self.offered = main, role, offered
        self.messages: list[Any] = []
        self.response_index: dict[str, int | None] = {}   # main-agent tool_use id -> index of its model response
        self._responses: dict[str, int] = {}         # main-agent message id -> index
        self._events: dict[str, anyio.Event] = {}
        self.result: ResultMessage | None = None

    def _event(self, tool_use_id: str) -> anyio.Event:
        return self._events.setdefault(tool_use_id, anyio.Event())

    async def response_of(self, tool_use_id: str, timeout: float) -> int | None:
        with anyio.move_on_after(timeout):
            await self._event(tool_use_id).wait()
        return self.response_index.get(tool_use_id)

    def feed(self, message: Any) -> None:
        self.messages.append(message)
        if isinstance(message, ResultMessage):
            self.result = message
        if isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
            # no message id: the response cannot be placed, so the ops gate refuses its label (R43)
            key = message.message_id
            index = None if key is None else self._responses.setdefault(key, len(self._responses))
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    self.response_index[block.id] = index
                    self._event(block.id).set()

    def build(self, run: dict, hooks: DeskHooks | None) -> None:
        """Fill run's trace, calls, tool_calls and errors from the stream (see trace_from_stream)."""
        trace_from_stream(self.messages, run, main=self.main, role=self.role, offered=self.offered,
                          durations=hooks.durations if hooks else {}, handoffs=hooks.handoff_records if hooks else {})


def _result_text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return content if isinstance(content, str) else ""


def trace_from_stream(messages: list[Any], run: dict, *, main: str, role: str, offered: list[str],
                      durations: dict[str, int | None] | None = None, handoffs: dict[str, dict] | None = None) -> None:
    """The R2 Trace and the run's logs from an SDK message stream.

    - model: one record per model response (message id), agent = the main agent or the worker whose Agent call
      the message belongs to (parent_tool_use_id); tokens_in from its usage; tokens_out and ms None (module
      docstring). Each also logs a `calls` entry {agent, role, tools}: tools are what that agent is offered for
      the whole run (the SDK cannot vary them per call).
    - tool: one record per desk tool that ran (PostToolUse saw it), ms from its duration_ms.
    - handoff: the hook's record (sv._apply's name and ms), placed where the Agent call's result comes back.
    - stop: the main loop's cap (the result's error_max_turns), and research's (MAX_WORKER_STEPS model
      responses, the last one still asking for tools).
    tool_calls: every desk tool request, in stream order, with is_error from its result. errors: any other name,
    and an Agent call that got no handoff record (an unknown worker), with the result's text (R35/R37)."""
    durations, handoffs = durations or {}, handoffs or {}
    trace: Trace = run["trace"]
    worker_of: dict[str, str] = {}          # Agent tool_use id -> worker name
    uses: dict[str, tuple[str, ToolUseBlock]] = {}
    seen: set[str] = set()
    steps: dict[str, list[bool]] = {}       # research Agent id -> per response: did it ask for tools
    tools_of = {"triage": [], "drafter": [], "reviewer": [], "research": sorted(core.READ_TOOLS)}
    for m in messages:
        if isinstance(m, AssistantMessage):
            parent = m.parent_tool_use_id
            agent = main if parent is None else worker_of.get(parent, "subagent")
            key = (parent, m.message_id or f"#{id(m)}")
            if key not in seen:
                seen.add(key)
                usage = m.usage or {}
                trace.add(agent=agent, kind="model", name=m.model, tokens_in=usage.get("input_tokens"))
                run["calls"].append({"agent": agent, "role": role if parent is None else sv.ROLE.get(agent, agent),
                                     "tools": offered if parent is None else tools_of.get(agent, [])})
                if agent == "research":
                    steps.setdefault(parent, []).append(False)
            for block in m.content:
                if isinstance(block, ToolUseBlock):
                    uses[block.id] = (agent, block)
                    if block.name in AGENT_TOOLS and parent is None:
                        worker_of[block.id] = block.input.get("subagent_type") if isinstance(block.input.get("subagent_type"), str) else "subagent"
                    if agent == "research" and parent in steps:
                        steps[parent][-1] = True
        elif isinstance(m, UserMessage) and isinstance(m.content, list):
            for block in m.content:
                if not isinstance(block, ToolResultBlock) or block.tool_use_id not in uses:
                    continue
                agent, use = uses[block.tool_use_id]
                bare = desk_name(use.name)
                if bare is not None:
                    if block.tool_use_id in durations:
                        trace.add(agent=agent, kind="tool", name=bare, ms=durations[block.tool_use_id])
                    run["tool_calls"].append({"agent": agent, "tool": bare, "args": use.input, "is_error": bool(block.is_error)})
                elif use.name in AGENT_TOOLS and block.tool_use_id in handoffs:
                    trace.add(**handoffs[block.tool_use_id])
                    if steps.get(block.tool_use_id, [False])[-1] and len(steps[block.tool_use_id]) >= MAX_WORKER_STEPS:
                        trace.add(agent="research", kind="stop", name="max_worker_steps")
                else:
                    run["errors"].append({"agent": agent, "tool": use.name, "args": use.input,
                                          "result": _result_text(block.content)})
        elif isinstance(m, ResultMessage) and m.subtype == "error_max_turns":
            trace.add(agent=main, kind="stop", name="max_turns")


# ── 6. Running one query ────────────────────────────────────────────────────
Client = Callable[[str, ClaudeAgentOptions], AsyncIterator[Any]]


def default_client(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
    """The bundled CLI as a subprocess. Tests pass their own client (a fake transport), never this one."""
    return query(prompt=prompt, options=options)


def _proxy_error(result: ResultMessage) -> None:
    """A replay miss (404) or the cost cap (402) from the proxy: raise ProxyError, as desk_core.claude does.
    The CLI's own text for a 404 names the model, so the status decides."""
    status = result.api_error_status
    if status in (402, 404):
        what = "replay miss" if status == 404 else "cost cap"
        text = f"proxy answered {status} ({what}): {result.result}"
        print(text, file=sys.stderr)
        raise core.ProxyError(text)


async def _drive(client: Client, prompt: str, options: ClaudeAgentOptions, log: StreamLog) -> None:
    """Consume the stream into `log`. The CLI exits non-zero after an error result and the SDK raises
    ResultError; a max_turns stop is an expected end here, anything else is raised."""
    try:
        async for message in client(prompt, options):
            log.feed(message)
    except ResultError:
        if log.result is None or log.result.subtype != "error_max_turns":
            if log.result is not None:
                _proxy_error(log.result)
            raise
    if log.result is None:
        raise RuntimeError("the Agent SDK ended without a result message")
    if log.result.is_error and log.result.subtype != "error_max_turns":
        _proxy_error(log.result)
        raise RuntimeError(f"the Agent SDK run failed: {log.result.result or log.result.subtype}")


def _finish(run: dict, log: StreamLog) -> tuple[str, str]:
    """(text, stop) from the result; cost and usage into run."""
    result = log.result
    run["cost_usd"] = result.total_cost_usd       # the CLI's client-side estimate, not billing
    run["usage"] = result.model_usage
    if result.subtype == "error_max_turns":
        return sv.STOPPED, "max_turns"
    return (result.result or "").strip(ASCII_WS), "end_turn"


def _new_run() -> dict:
    return {"trace": Trace(), "calls": [], "tool_calls": [], "errors": [], "cost_usd": None, "usage": None}


# ── 7. The single agent ─────────────────────────────────────────────────────
def sdk_single(ticket: dict, *, approve: str = "decline", elicit: str = "decline", labels_path: Path | None = None,
               client: Client | None = None) -> dict:
    """One Agent SDK agent with every desk tool, capped at MAX_TURNS. As the hand-built single agent: the only
    gates on the write are the guard (hook, then harness) and the server's confirmation.
    `labels_path` defaults to a temporary file, so a test or a stray run never writes the real one."""
    client = client or default_client
    run = _new_run()
    with tempfile.TemporaryDirectory() as tmp, open_desk_tools(
            ("read", "ops"), guard=True, approve=approve, elicit=elicit,
            labels_path=labels_path or Path(tmp) / "labels.jsonl") as (schema, run_tool, session):
        session.read_untrusted.set()     # R24: the ticket text is in the prompt
        servers = desk_servers(schema, run_tool)
        hooks = DeskHooks("single", run, session, approve, ticket)
        names = sorted(mcp_name(s["name"]) for s in schema)
        options = build_options(system=sv.SINGLE_SYSTEM, tools=[], allowed=names, servers=servers,
                                hooks=hooks.matchers(), tmp=tmp)
        log = StreamLog("single", "act", sorted(s["name"] for s in schema))
        anyio.run(_drive, client, sv._ticket_msg(ticket)[0]["content"], options, log)
    log.build(run, hooks)
    reply, stop = _finish(run, log)
    return {"reply": reply, "stop": stop, **run}


# ── 8. The supervisor, with subagents ───────────────────────────────────────
def sdk_supervisor(ticket: dict, *, approve: str = "decline", elicit: str = "decline", labels_path: Path | None = None,
                   client: Client | None = None) -> dict:
    """A supervisor agent that delegates to four SDK subagents through the Agent tool.

    Why: the SDK's own multi-agent shape (subagents) with the hand-built desk's state and gates, so lesson 8
    compares frameworks, not desks. The shared state and the workers' checks are the hand-built ones; the gates
    run in hooks (section 4). The reply is the reviewed draft when the review passed, else the supervisor's last
    text (STOPPED at the cap)."""
    client = client or default_client
    run = {**_new_run(), "handoffs": []}
    state = {"ticket_id": ticket["id"], "category": None, "findings": None, "draft": None, "review": None}
    with tempfile.TemporaryDirectory() as tmp, open_desk_tools(
            ("read", "ops"), guard=True, approve=approve, elicit=elicit,
            labels_path=labels_path or Path(tmp) / "labels.jsonl") as (schema, run_tool, session):
        session.read_untrusted.set()     # R24: the ticket text is in the prompt
        servers = desk_servers(schema, run_tool)
        offered = sorted([AGENT_TOOLS[0]] + [s["name"] for s in schema])
        log = StreamLog("supervisor", "supervise", offered)
        hooks = DeskHooks("supervisor", run, session, approve, ticket, state=state, stream=log)
        allowed = [AGENT_TOOLS[0]] + sorted(mcp_name(s["name"]) for s in schema)
        options = build_options(system=SUPERVISOR_SYSTEM, tools=[AGENT_TOOLS[0]], allowed=allowed, servers=servers,
                                hooks=hooks.matchers(), tmp=tmp, agents=worker_agents())
        anyio.run(_drive, client, sv._ticket_msg(ticket)[0]["content"], options, log)
    log.build(run, hooks)
    text, stop = _finish(run, log)
    reply = state["draft"] if sv._passed(state) else text
    return {"reply": reply, "stop": stop, **run, "state": dict(state)}


# ── 9. The engine ───────────────────────────────────────────────────────────
if "sdk" not in core.ENGINES:     # importing twice (a test reload) must not register twice
    core.register("sdk", core.Engine(single=sdk_single, supervisor=sdk_supervisor))
