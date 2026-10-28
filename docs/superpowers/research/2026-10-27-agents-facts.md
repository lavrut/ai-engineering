# Agent frameworks facts sheet (as of 2026-10-27)

Legend: [PyPI] = https://pypi.org/pypi/<pkg>/json queried today. [local] = installed into a venv at
a scratch venv
and inspected/run there (no API key, no network model calls). [doc] = page fetched today.
A doc summary came from a fetch tool that paraphrases pages; where it looked wrong I say so.

## 1. LangGraph

### Versions and floors
- langgraph 1.2.18 (uploaded 2026-10-26), Python >=3.10. [PyPI] https://pypi.org/project/langgraph/
- langchain 1.4.7, langchain-core 1.7.0, langchain-anthropic 1.7.6 (2026-10-12), all Python >=3.10. [PyPI/local]
- langgraph-checkpoint 4.3.1, langgraph-prebuilt 1.1.1, langgraph-sdk 0.4.10 (pulled in by langgraph). [local]
- Extras: langgraph-checkpoint-sqlite 3.1.2, langgraph-checkpoint-postgres 3.2.0, langchain-mcp-adapters 0.3.2
  (2026-08-06), langgraph-supervisor 0.0.31 (2025-11-19), langgraph-swarm 0.1.0 (2025-12-04), deepagents 0.7.32
  (needs Python >=3.11), langsmith 0.14.14. [PyPI]
- Install: `pip install -U langgraph` https://docs.langchain.com/oss/python/langgraph/overview
- Monorepo/releases: https://github.com/langchain-ai/langgraph/releases ; API ref https://reference.langchain.com/python/langgraph/

### Core API (verified by running a graph with a fake model [local])
- `from langgraph.graph import StateGraph, START, END`; `StateGraph(State)`; `add_node(name, fn)`; `add_edge(a, b)`;
  `add_conditional_edges(node, router, [targets])`; `.compile(checkpointer=..., cache=..., store=...)`.
  https://docs.langchain.com/oss/python/langgraph/graph-api
- State schema: TypedDict (main), dataclass, or Pydantic. Reducers via `Annotated[list[str], operator.add]`;
  keys without a reducer overwrite. `MessagesState` / `add_messages` reducer for chat. `Overwrite(...)` bypasses a reducer.
- Dynamic routing: `Send(node, state)` for map-reduce fan-out; `Command(update=, goto=, graph=, resume=)`.
  Do not mix static edges and Command routing on the same node (both paths may run). Same doc as above.
- Run: `invoke`, `stream` (`stream_mode="values"` full state, `"updates"` per-node deltas), async variants.
  `recursion_limit` config, default 1000 as of v1.0.6, raises `GraphRecursionError`. Same doc.
- Per-node `cache_policy` and `compile(cache=...)` exist (langgraph.types.CachePolicy) [local]: this is node-result
  caching, not model caching.

### Prebuilt agents: IMPORTANT CHANGE
- `langgraph.prebuilt.create_react_agent` still imports but emits `LangGraphDeprecatedSinceV10`:
  "create_react_agent has been moved to `langchain.agents`. ... use `from langchain.agents import create_agent`.
  Deprecated in LangGraph V1.0 to be removed in V2.0." [local, ran it]
- `AgentState` in langgraph.prebuilt is also deprecated in favor of `langchain.agents.AgentState`. [local source]
- Replacement: `from langchain.agents import create_agent` (langchain 1.4.7). Runs on LangGraph; returns a compiled graph.
  `create_agent(model, tools=[...], checkpointer=..., middleware=[...])`. [local]
  https://docs.langchain.com/oss/python/langgraph/overview (LangChain agents run on LangGraph)
- Middleware available in langchain.agents.middleware [local]: HumanInTheLoopMiddleware, SummarizationMiddleware,
  ToolCallLimitMiddleware, ModelCallLimitMiddleware, ModelRetryMiddleware, ToolRetryMiddleware, ModelFallbackMiddleware,
  TodoListMiddleware, PIIMiddleware, ContextEditingMiddleware, ShellToolMiddleware, ToolErrorMiddleware, others.
- Verified offline: `create_agent(FakeToolCallingModel, tools=[add2])` ran model -> tool -> model and returned
  Human/AI(tool_calls)/Tool/AI messages. [local]

### Checkpointers and memory
- `InMemorySaver` (langgraph.checkpoint.memory, `MemorySaver` is an alias) for tests; SQLite saver (package
  langgraph-checkpoint-sqlite) for local dev; `PostgresSaver`/`AsyncPostgresSaver` (langgraph-checkpoint-postgres) for
  production, call `.setup()`. Select a thread with `config={"configurable": {"thread_id": "..."}}`.
  https://docs.langchain.com/oss/python/langgraph/persistence
- Long-term cross-thread memory: a Store, e.g. `InMemoryStore` (langgraph.store.memory), passed to `compile(store=...)`. Same doc.
- Persistence enables HITL, time travel, fault tolerance (same doc; that page does not detail get_state_history).

### Human-in-the-loop
- `from langgraph.types import interrupt, Command`. `interrupt(value)` pauses; resume with
  `graph.invoke(Command(resume=...), config)` on the same thread_id. Requires a checkpointer.
  Rules: node restarts from the top on resume (earlier side effects must be idempotent); no bare try/except around
  interrupt (it works by raising); keep interrupt order stable; JSON-serializable payloads only.
  https://docs.langchain.com/oss/python/langgraph/interrupts
- Verified [local]: first invoke returned `__interrupt__: [Interrupt(value='approve?', id=..., response_schema=None)]`;
  second invoke with `Command(resume="yes")` finished with log `['a','b:yes']`.
- Agent-level HITL: `HumanInTheLoopMiddleware` in `create_agent`. [local]

### Multi-agent patterns and packages
- Docs now list five patterns: subagents (agents as tools under a main agent), handoffs (tool calls change state/active agent),
  skills (load prompts on demand), router, custom LangGraph workflow. https://docs.langchain.com/oss/python/langchain/multi-agent
  That page names no supervisor/swarm packages.
- langgraph-supervisor: README says "We now recommend using the supervisor pattern directly via tools rather than this
  library for most use cases"; the repo was archived by its owner on 2026-09-20 and is read-only. Not marked "deprecated"
  in words. https://github.com/langchain-ai/langgraph-supervisor-py (fetch-tool paraphrase; re-check by eye).
  API: `create_supervisor([agents], model=..., prompt=...)` then `.compile()`. Last PyPI release 0.0.31, 2025-11-19.
- langgraph-swarm 0.1.0 (2025-12-04) exists on PyPI; I did NOT verify its repo status. Treat as legacy.
- Deep Agents (package `deepagents`, https://docs.langchain.com/oss/python/deepagents/overview) is the higher-level
  harness (subagents, skills, planning, virtual filesystem). Python >=3.11. Not studied further.

### MCP
- `pip install langchain-mcp-adapters` (0.3.2). [local] exports `MultiServerMCPClient(connections: dict, *, callbacks,
  tool_interceptors, tool_name_prefix=False, handle_tool_errors=True)`, methods `get_tools`, `get_resources`, `get_prompt`,
  `get_server_info`, `session`; connection types Stdio, SSE, StreamableHttp, Websocket; helpers `load_mcp_tools`.
  Tools are async: use `await client.get_tools()` then pass to `create_agent`. Repo: https://github.com/langchain-ai/langchain-mcp-adapters
- DISCREPANCY: the docs page https://docs.langchain.com/oss/python/langchain/mcp was summarized by my fetch tool as
  using `langchain.mcp.MCPAdapter` and `pip install "langchain[mcp]"`. I did not confirm this against the live page text,
  and `MultiServerMCPClient` is what the installed package exports. Trust the installed package; re-read the page by eye.

### Claude via ChatAnthropic
- `pip install langchain-anthropic`; `from langchain_anthropic import ChatAnthropic`; `ChatAnthropic(model="claude-haiku-5-5")`;
  key from env `ANTHROPIC_API_KEY`. Constructing it without a key did NOT raise in my run (construction is lazy; the
  failure would come at call time; I did not make a call). [local]
  https://docs.langchain.com/oss/python/integrations/chat/anthropic
- That page's example IDs include claude-sonnet-5, claude-opus-5-5, claude-fable-5; on several current models a
  non-default temperature/top_p/top_k returns HTTP 400. Same page.
- Current model IDs (Anthropic): claude-haiku-5-5 ($0.10 in / $0.50 out per MTok "from"), claude-sonnet-5-5 ($2/$10),
  claude-opus-5-5 ($4/$20), claude-fable-5-1 ($10/$50). IDs are pinned snapshots, no date suffix.
  https://platform.claude.com/docs/en/about-claude/models/overview  (so `claude-haiku-5-5` is a real ID)
- `bind_tools`, prompt caching via `cache_control`, token details in `usage_metadata["input_token_details"]`. Same page.

### Tracing
- LangSmith is the documented tracer: `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`; docs say "sign up (for free)", no
  limits stated. https://docs.langchain.com/oss/python/langchain/observability
- LangSmith is NOT required to run anything: the overview's hello-world runs without it
  (https://docs.langchain.com/oss/python/langgraph/overview) and my graph/agent runs used none. [local]
- Free local alternatives (not described in the docs page I fetched, but standard LangChain core): `BaseCallbackHandler`
  (`on_tool_start`, etc.) and `stream_mode="updates"`; also the message list returned by `invoke` contains every AIMessage
  tool_call and ToolMessage (seen in my run). Optional paid/hosted: LangSmith Deployment, Managed Deep Agents.

## 2. Claude Agent SDK

### Package, versions, runtime
- PyPI name: `claude-agent-sdk` 0.2.174 (uploaded 2026-10-24), Python >=3.10, Development Status classifier "Alpha".
  Deps: anyio, jsonschema, mcp>=1.23,<3, sniffio. Optional extra `otel` (opentelemetry-api>=1.20). [PyPI/local]
  https://pypi.org/project/claude-agent-sdk/ ; repo https://github.com/anthropics/claude-agent-sdk-python
- Needs NO separate Claude Code install or Node: the wheel bundles a native Claude Code binary
  (`claude_agent_sdk/_bundled/claude`, CLI 2.1.310, ~263 MB installed; `--version` printed "2.1.310 (Claude Code)"). [local]
  Doc: both SDKs bundle a native binary; exception is a sdist install (e.g. ARM64 Windows), then install Claude Code and it
  is found on PATH. https://code.claude.com/docs/en/agent-sdk/quickstart ; override with `ClaudeAgentOptions(cli_path=...)`.
  (Node is only needed if you launch npx-based MCP servers.)
- Docs home: https://code.claude.com/docs/en/agent-sdk/overview (platform.claude.com/docs/en/agent-sdk/* redirects there).
  The SDK = "Claude Code as a library"; it runs the Claude Code binary as a subprocess.
- Rename history: formerly "Claude Code SDK"; migration guide https://code.claude.com/docs/en/agent-sdk/migration-guide.md

### Core API [local exports + doc]
- `query(*, prompt: str | AsyncIterable, options=None, transport=None) -> AsyncIterator[Message]`; one session per call
  unless `resume` / `continue_conversation`. `ClaudeSDKClient(options)` as async context manager: `connect`, `query`,
  `receive_response()` (to ResultMessage), `receive_messages()`, `interrupt`, `set_permission_mode`, `set_model`,
  `get_mcp_status`, `get_context_usage`, `rewind_files`. https://code.claude.com/docs/en/agent-sdk/python
- `ClaudeAgentOptions` notable fields [local types.py]: tools, allowed_tools, disallowed_tools, system_prompt, mcp_servers,
  strict_mcp_config, permission_mode, can_use_tool, model, fallback_model, max_turns, max_budget_usd, hooks, agents,
  output_format (json_schema), env, cwd, cli_path, setting_sources, resume, session_id, include_partial_messages, thinking,
  effort, sandbox, plugins, enable_file_checkpointing.
- Message types: UserMessage, AssistantMessage, SystemMessage (subtype `init` carries tools/mcp_servers/model/permissionMode),
  ResultMessage, StreamEvent, RateLimitEvent, ConversationResetMessage; blocks TextBlock, ThinkingBlock, ToolUseBlock,
  ToolResultBlock, ServerToolUse/ResultBlock. ResultMessage.subtype: success, error_during_execution, error_max_turns,
  error_max_budget_usd, error_max_structured_output_retries. Python doc page above.

### Custom tools (in-process MCP)
- `@tool(name, description, input_schema)` where schema is `{"a": float}`, TypedDict, or JSON Schema; handler is
  `async def f(args) -> {"content":[{"type":"text","text":...}]}`. `create_sdk_mcp_server(name, version="1.0.0", tools=[...])`.
  Register under `mcp_servers={"calc": server}` and allow `mcp__calc__add` (naming `mcp__<server>__<tool>`). Python doc page above.
  Tools run inside your Python process (no subprocess server). In-process server connect counts toward the first-turn
  MCP wait (default 30 s). https://code.claude.com/docs/en/agent-sdk/mcp

### External MCP servers
- stdio: `{"command": "npx", "args": [...], "env": {...}}`; HTTP: `{"type": "http", "url": ..., "headers": {...}}`;
  SSE: `{"type": "sse", ...}`. Allow with `allowed_tools=["mcp__server__*"]` (wildcard only after a literal
  `mcp__<server>__` prefix). MCP tools need explicit permission. No interactive OAuth: pass a token in `headers`.
  Tool search is on by default. `strict_mcp_config` / `ENABLE_CLAUDEAI_MCP_SERVERS=false` to avoid claude.ai connectors.
  https://code.claude.com/docs/en/agent-sdk/mcp

### Subagents
- `agents={"name": AgentDefinition(description=, prompt=, tools=[...], model="sonnet"|"haiku"|full id|"inherit", ...)}` plus
  `"Agent"` in allowed_tools. AgentDefinition field names are camelCase in Python (`disallowedTools`, `maxTurns`,
  `permissionMode`, `mcpServers`). Tool is called "Agent" in tool_use blocks but "Task" in the init tools list (match both).
  Messages from inside a subagent carry `parent_tool_use_id`. A built-in `general-purpose` subagent always exists unless
  `CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS=1`. Subagents run in the background by default (status async_launched) unless
  Claude needs the result or `background=False`-style prompting; subagents can nest (default depth 3, concurrency 20; set
  via env `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` / `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`). Parent sees only the final
  message. https://code.claude.com/docs/en/agent-sdk/subagents
- Not-yet-in-Python: `autoCompactWindow`, `omitClaudeMd` AgentDefinition fields; Workflow tool is TypeScript-only. Same page.

### Hooks
- `hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[cb])]}`; callback `async def cb(input_data, tool_use_id, context)`
  returns `{}` to allow or `{"hookSpecificOutput": {"hookEventName":..., "permissionDecision":"deny","permissionDecisionReason":...}}`.
  Python hook events: PreToolUse, PostToolUse, PostToolUseFailure, UserPromptSubmit, Stop, SubagentStop, SubagentStart,
  PreCompact, PermissionRequest, Notification. SessionStart/SessionEnd and ~20 others are TypeScript-only (Python gets
  them only as settings.json shell hooks). Matching hooks run in parallel; deny > defer > ask > allow.
  https://code.claude.com/docs/en/agent-sdk/hooks  (My local `dir(claude_agent_sdk)` shows extra hook-output types like
  PostToolBatch, so the installed SDK may be ahead of the doc table; check types.py if needed.)

### Permissions and disabling built-in tools
- Modes (Python literal, verified [local]): default, acceptEdits, plan, bypassPermissions, dontAsk, auto.
  Since Claude Code v2.1.302 an app starts in `default`, which DENIES anything needing approval unless allowed by rule,
  hook, or can_use_tool. Evaluation order: hooks -> deny rules -> ask rules -> mode -> allow rules -> can_use_tool.
  https://code.claude.com/docs/en/agent-sdk/permissions
- `allowed_tools` only auto-approves; it does NOT restrict what exists, and does not constrain bypassPermissions.
  `disallowed_tools=["Bash"]` removes the tool definition from the request; `["Bash(rm *)"]` denies matching calls in
  every mode; `["*"]` removes all tools. Same page.
- FULLY disabling built-ins: `ClaudeAgentOptions(tools=[])` ("Disable all built-in tools", types.py docstring). Verified
  [local]: with `tools=[]`, the init SystemMessage reported `'tools': []`. Add only your `mcp__...` tools via allowed_tools.
  For a locked-down headless agent doc suggests `allowed_tools` + `permission_mode="dontAsk"`.
- Caveat: default setting_sources loads user, project and local settings, CLAUDE.md, skills, auto-memory, and
  ~/.claude.json. For a hermetic course environment pass `setting_sources=[]` and env `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`
  (and consider `CLAUDE_CONFIG_DIR`). My no-key run still listed host slash commands in init. [local]
  https://code.claude.com/docs/en/agent-sdk/claude-code-features

### Trace of messages and tool calls
- Iterate the stream: AssistantMessage.content has ToolUseBlock (name, input, id); the following UserMessage has
  ToolResultBlock. Add `include_partial_messages=True` for StreamEvents. Hooks (PreToolUse/PostToolUse, tool_use_id correlates)
  give a structured audit log. Sessions persist as transcripts; helpers `list_sessions`, `get_session_messages`,
  `get_subagent_messages` in the installed package [local]. `otel` extra exists. https://code.claude.com/docs/en/agent-sdk/sessions

### Cost and usage
- `ResultMessage.total_cost_usd`, `.usage` (dict; main loop only, EXCLUDES subagents), `.model_usage` (per model, includes
  subagents), `.num_turns`, `.duration_ms`. Per-step usage on AssistantMessage (dedupe by message_id); per-step output_tokens
  is a placeholder, read output tokens from the result. Cost is a CLIENT-SIDE ESTIMATE from a bundled price table, not
  billing. `max_budget_usd` is a client-side cap that can overshoot. Resumed sessions report the whole session's spend.
  https://code.claude.com/docs/en/agent-sdk/cost-tracking

### Model selection
- `ClaudeAgentOptions(model="claude-haiku-5-5")` (also `fallback_model`, `set_model()` mid-session; subagent `model=` accepts
  aliases or full IDs). My no-key run echoed `'model': 'claude-haiku-5-5'` in the init message. [local]

### Headless with only ANTHROPIC_API_KEY?
- Docs: set `ANTHROPIC_API_KEY` in the process env; the SDK does not read .env files. Also supports Bedrock, Vertex,
  Foundry, Claude Platform on AWS via env flags. Third parties may not offer claude.ai login/rate limits in products.
  https://code.claude.com/docs/en/agent-sdk/quickstart , https://code.claude.com/docs/en/agent-sdk/overview
- NOT RUN with a key (rule). What I verified without any key/token (HOME pointed at an empty dir, ANTHROPIC_* and
  CLAUDE_CODE_OAUTH_TOKEN unset): the CLI starts, emits init SystemMessage, then AssistantMessage text "Not logged in ·
  Please run /login" with error='authentication_failed', then ResultMessage(is_error=True, total_cost_usd=0, num_turns=1),
  and `query()` raises `ResultError("Claude Code returned an error result: Not logged in ... (exit code: 1)")`.
  Failure is clean, offline, free, and arrives in about 40 ms. [local]
- Caution: a developer machine logged in to Claude Code would silently use that login (subscription) instead of the API
  key unless env is controlled; the course should set the key explicitly and test with HOME/CLAUDE_CONFIG_DIR isolated.
  Not verified end to end.

## 3. Record and replay options

### LangChain/LangGraph
- Fake models in langchain_core.language_models [local]: `GenericFakeChatModel(messages=iter([...]))` (AIMessage or str),
  `FakeListChatModel(responses=[...])` (cycles), `FakeMessagesListChatModel`, `ParrotFakeChatModel`, plus FakeListLLM etc.
  Official guidance: https://docs.langchain.com/oss/python/langchain/test/unit-testing
  - `create_agent` calls `bind_tools`, which GenericFakeChatModel lacks: subclass and `return self` (I did this; it works).
  - Iterator is single-use: build it fresh per test (function-scoped fixture).
  - Pair with `InMemorySaver` + a fixed `thread_id` for multi-turn tests.
- LLM response cache: `langchain_core.globals.set_llm_cache(...)`; core ships `InMemoryCache` and `BaseCache`.
  `SQLiteCache`, `SQLAlchemyCache`, RedisCache etc. live in `langchain-community` 0.4.2 (`langchain_community/cache.py`)
  [wheel inspected]. Verified [local]: with InMemoryCache set, a second identical `invoke` on an exhausted fake model
  returned the cached "hello". Caching keys on prompt + model params, so it is a replay-by-prompt, not a transcript replay.
  Caveat: I did not test whether the cache is hit by ChatAnthropic tool-calling agents on every step or with
  nondeterministic message ids; verify before relying on it.
- The unit-testing page says it does not cover VCR/cassettes. HTTP-level record/replay (vcrpy 9.2.0, Python >=3.11;
  pytest-recording 0.14.0) is the usual approach for real provider calls; not documented by LangChain on that page. [PyPI]
- LangGraph checkpoints: deterministic resume of a graph's state, not a model-response replay.

### Claude Agent SDK
- No fake model, mock mode, record/replay, or response cache found in the package. The only `testing` subpackage is
  `claude_agent_sdk.testing.run_session_store_conformance` (for SessionStore implementers). [local]
- Options for offline/free/deterministic runs:
  1. Gateway/base-URL redirect: Claude Code honors `ANTHROPIC_BASE_URL` (documented for gateways; any gateway exposing a
     supported API format works). Pass via `ClaudeAgentOptions(env={"ANTHROPIC_BASE_URL": ...})`. A local stub server that
     serves recorded Messages-API responses would give replay. I did NOT build or run this (it would need a dummy key);
     feasibility from docs only. https://code.claude.com/docs/en/llm-gateway , https://code.claude.com/docs/en/llm-gateway-protocol.md
     Claude Code sends many internal requests (helpers, titles, compaction) and uses streaming, so a replay stub is non-trivial.
  2. Custom `Transport` (`query(..., transport=...)`; `Transport` is exported) to feed canned message streams. Not tested.
  3. Replay the CAPTURED MESSAGE STREAM instead of the model: save each yielded message (they are dataclasses) to JSONL and
     have the course UI/analysis code consume it; or read stored transcripts with `get_session_messages`. This replays
     the trace, not the agent. Cheapest and fully deterministic.
  4. Unit-test your @tool handlers and hook callbacks directly as plain async functions (no model needed).

## 4. Anthropic design-pattern guidance
- Canonical source is still "Building effective agents" (Anthropic Engineering, 2024-12-19; the page itself notes much of the
  tooling has since changed). https://www.anthropic.com/engineering/building-effective-agents
  - "Agentic systems" umbrella; WORKFLOWS = LLMs and tools orchestrated through predefined code paths; AGENTS = LLMs that
    dynamically direct their own process and tool use.
  - Five workflow patterns: prompt chaining, routing, parallelization (sectioning and voting), orchestrator-workers,
    evaluator-optimizer. (Fetch-tool paraphrase confirmed those exact five names.)
  - Advice: find the simplest solution, add complexity only when it demonstrably improves outcomes; use frameworks
    cautiously because abstraction layers obscure prompts; start with raw LLM APIs; show planning steps; invest in tool design.
- I found no newer post that replaces this taxonomy. Newer posts build on it:
  - Effective context engineering for AI agents (compaction, note-taking, sub-agents, minimal tool sets):
    https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
  - Harness design for long-running apps (2026-03-24 per search snippet; planner/generator/evaluator; separate the judge
    from the doer): https://www.anthropic.com/engineering/harness-design-long-running-apps
  - Scaling Managed Agents (2026-04-08 per snippet; brain/hands/session separation; harness assumptions go stale):
    https://www.anthropic.com/engineering/managed-agents
  (Search-snippet dates were flagged as inconsistent; confirm on the pages.)
- Anthropic's current doc comparison of surfaces: Agent SDK (Claude Code loop as a library), Client SDK + tool runner,
  Managed Agents (hosted harness). https://code.claude.com/docs/en/agent-sdk/overview

## 5. Multi-agent and evaluation guidance
- "How we built our multi-agent research system" (2025-06-13): orchestrator-worker; lead Opus 4 + Sonnet 4 subagents beat
  single-agent Opus 4 by 90.2% on an internal eval; agents use about 4x chat tokens, multi-agent about 15x; token usage
  explained about 80% of BrowseComp variance. Fits breadth-first, parallelizable, high-value tasks; poor fit for shared-
  context, tightly dependent work and most coding. Eval: start with about 20 queries, LLM judge with rubric (accuracy,
  citations, completeness, sources, tool efficiency), judge outcomes not exact steps, human testing for edge cases; full
  tracing; checkpoints/resume for stateful agents. https://www.anthropic.com/engineering/multi-agent-research-system
- "Demystifying evals for AI agents" (2026-01-09): vocabulary task / trial / grader / transcript / outcome; grader types code,
  model, human; pass@k (at least one success) vs pass^k (all k succeed; 75% per trial gives about 42% for 3 in a row);
  start with 20 to 50 tasks from real failures; isolate each trial; grade outputs not paths; give LLM judges an "Unknown"
  option; read transcripts; watch for saturation. https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Agent SDK doc on subagent spend: use `model_usage` / `total_cost_usd` for whole-tree cost; `usage` undercounts. (cost-tracking page above)
- LangChain angle: five multi-agent patterns page above; LangChain's own eval tooling is LangSmith (not studied).

## 6. Open questions
1. Exact LangChain MCP doc page content (MCPAdapter vs MultiServerMCPClient). Re-read https://docs.langchain.com/oss/python/langchain/mcp by eye.
2. langgraph-swarm repo status (archived like supervisor?) and whether a docs-blessed handoff recipe replaces it.
3. Does a real keyed Agent SDK run in a clean HOME with only ANTHROPIC_API_KEY work end to end with `tools=[]`, `setting_sources=[]`,
   and an SDK MCP tool? Needs one approved paid call (Haiku 5.5 is cheapest, from $0.10/$0.50 per MTok). Not run.
4. Is `ANTHROPIC_BASE_URL` redirection to a local replay stub practical for the Agent SDK (streaming format, helper requests)?
   Needs a spike; would require a placeholder key value. Not run.
5. Does langchain-core's LLM cache hit reliably across steps of a ChatAnthropic `create_agent` loop (tool-call messages with ids)?
6. Is the Python SDK's hook-event list (doc table: 10 events) the same as the installed 0.2.174 `HookEvent` type? I read the
   Literal in types.py: it lists the same 10. The extra HookSpecificOutput classes exported suggest TS parity types only.
7. Dates on the 2026 Anthropic posts (search snippets were inconsistent) and whether "Effective harnesses for long-running
   agents" is the canonical citation for agent harness design.
8. LangSmith free-tier limits (not stated on the fetched page) if the course wants to mention hosted tracing.
9. Whether `claude-agent-sdk` stays "Alpha" and what its deprecation policy is (0.2.x semver; moves fast: CLI 2.1.310 bundled).
