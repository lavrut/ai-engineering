# AI Agents Track Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the AI Agents track: the Lumen support desk in three engines (hand-built Python and Go, LangGraph, and the Claude Agent SDK), all routed through one record/replay proxy and scored by one eval suite, plus 4 aids, 2 diagrams and 11 lessons.

**Architecture:**
- A local proxy (`agents/proxy`) sits between every engine and the Anthropic API. It records approved calls and replays them byte-exact for free.
- The hand-built desk reuses the Track 1 harness and the MCP track's servers and tool bridge (`mcp/python/agent.py`). The framework desks attach the same MCP servers.
- One eval suite scores every engine from the same recordings.

**Tech Stack:**
- Python 3.12 in `.venv-agents`, with `langgraph==1.2.18`, `langchain==1.4.7`, `langchain-anthropic==1.7.6`, `claude-agent-sdk==0.2.174`, `mcp==2.3.0`, `anthropic`, `httpx` and `pytest`.
- Go 1.27.2.
- The app: Astro, Preact and Vitest.

**Spec:** `docs/superpowers/specs/2026-10-27-ai-engineering-agents-design.md`. Facts: `docs/superpowers/research/2026-10-27-agents-facts.md`. Read the facts before writing framework code.

## Global Constraints

- **Venv and versions.**
  - Python runs only in `.venv-agents/` at the repo root, which is git-ignored. Never use the base Python.
  - Pin exactly: `langgraph==1.2.18`, `langchain==1.4.7`, `langchain-anthropic==1.7.6`, `claude-agent-sdk==0.2.174`, `mcp==2.3.0`.
- **Proxy.**
  - Bind to `127.0.0.1` only. The default port is `8790`.
  - Engines reach it via `ANTHROPIC_BASE_URL=http://127.0.0.1:8790`.
  - The upstream is `https://api.anthropic.com` and is used in record mode only.
- **Replay key.** The key is the SHA-256 of canonical JSON over `{method, path, body}`.
  - Remove volatile body fields: `metadata.user_id`, and any id or timestamp the plan's Task 1 spike shows to be volatile.
  - Never include headers in the key, and never store them.
- **Replay miss.** Answer HTTP 404 with the JSON body `{"type":"error","error":{"type":"replay_miss","message":"no recording for <key8>; record with: <command>"}}`. Never forward it upstream.
- **Record mode.**
  - Print the estimate, then ask y/N. Stdin that is not a terminal counts as no; `--yes` exists only for the controller after the user approves.
  - The key comes from the environment only and is never logged or stored.
  - Check the cap per call: refuse when `spent + worst_call > max_cost`. The default `--max-cost` is 10.
- **Defaults.**
  - Model `claude-haiku-5-5`. Prices come from the lab's PRICES table (`llms/python/triage_lab.py`).
  - `MAX_TOKENS = 4000` per call, and at most `MAX_CALLS = 12` paid calls per engine run.
- **Tools.**
  - Every engine uses lumen-read and lumen-ops from `mcp/python` (in-memory or stdio). No engine gets file, shell or web tools.
  - The Agent SDK runs with `tools=[]` and `setting_sources=[]`.
- **Ticket text and safety.**
  - Ticket text is untrusted, and the untrusted-read rule applies to every engine (Task 3 provides it).
  - A write happens only through lumen-ops's confirmation.
- **Don't edit earlier tracks.** Never edit `harness/`, `llms/`, `ml/` or `mcp/`; import them. Exception: a read-only helper may be added to `mcp/python/agent.py` if a ruling allows it.
- **Recordings.** Recordings in `agents/data/recordings/` come only from user-approved real runs. Fixtures for tests live elsewhere and are labelled.
- **Commits.** Stage explicit paths; never amend; no attribution lines. Never commit `.vscode/`, `.superpowers/`, `*.ipynb`, `.ipynb_checkpoints/`, `.venv-*`, `trace.jsonl`, `dev-signing.key` or `labels.jsonl`.
- **Python and Go parity.** Hand-built patterns and the supervisor have matching numbered sections in Python and Go, and the shared contract file `agents/fixtures/contract.jsonl` holds both to the same behaviour.

## Review Focus

1. **A streamed response replayed** (the Agent SDK and langchain-anthropic stream by default). The replay must reproduce the SSE events byte-exact, with no truncation or reordering. Pinned by Task 1 `test_stream_replay_byte_exact`.
2. **The same request recorded twice** (a retry after a timeout). The latest recording wins, deterministically, and the store doesn't grow on replay. Pinned by Task 1 `test_rerecord_latest_wins`.
3. **An engine sending a request with a new volatile field** (an SDK upgrade adds an id). Replay misses loudly, naming the field-diff hint, and never silently matches the wrong recording. Pinned by Task 1 `test_miss_reports_closest_key_diff`.
4. **Ticket t061 in every engine.** No write without confirmation and the guard, in all three engines. Pinned by Task 9 `test_t061_never_writes_unconfirmed[engine]`.
5. **A recording set only partly made** (the cap was hit mid-run). The eval reads "incomplete", never a partial score. Pinned by Task 9 `test_partial_recording_marked_incomplete`.

---

### Task 1: Spike, then the proxy

**Files:**
- Create: `agents/requirements.txt`, `agents/proxy/proxy.py`, `agents/proxy/tests/test_proxy.py`, and `docs/superpowers/research/2026-10-27-agents-spike.md`.
- Modify: `.gitignore` (add `.venv-agents/`).

**Interfaces (produces):**
- `python agents/proxy/proxy.py --mode replay|record [--port 8790] [--store DIR] [--max-cost 10] [--yes] [--set NAME]`
- `request_key(method, path, body: dict) -> str`
- `Store(dir).find(key)` and `Store.add(key, request_summary, response)`, where `response` is `{status, headers_allowlist, body | sse_events}`.
- `estimate_line()` prints the estimate.
- Exit code 3 on a cap.

- [ ] **Step 1: Spike.** In a scratch venv, with no key:
  - point `anthropic`, `langchain-anthropic` and `claude-agent-sdk` at a dummy local HTTP server via `ANTHROPIC_BASE_URL`;
  - record the exact request each one sends: path, headers, body fields, whether it streams, and which fields vary between identical calls.

  Then write the spike doc with the volatile fields, streaming yes/no, and a ruling proposal if the Agent SDK ignores `ANTHROPIC_BASE_URL`. The fallback is message-stream capture for that engine.
- [ ] **Step 2: Write the failing tests.**
  - `test_key_ignores_volatile_fields`
  - `test_key_excludes_headers`
  - `test_replay_hit_returns_stored`
  - `test_replay_miss_404_with_command`
  - `test_stream_replay_byte_exact`
  - `test_rerecord_latest_wins`
  - `test_miss_reports_closest_key_diff`
  - `test_record_requires_confirm`: non-tty means no upstream call.
  - `test_cap_refuses_before_call`
  - `test_no_key_in_store`: grep the store for the key and the `x-api-key` and `authorization` headers.
  - `test_binds_localhost_only`

  The record tests use a fake upstream (httpx MockTransport). They never call the real API.
- [ ] **Step 3: Run the tests.** `.venv-agents/bin/python -m pytest agents/proxy/tests -q`. Expected: FAIL.
- [ ] **Step 4: Implement.** Use stdlib `http.server` (`ThreadingHTTPServer`) plus `httpx` for upstream. Prices and worst-case come from `llms/python/triage_lab.py` PRICES.
- [ ] **Step 5: Run the tests.** Expected: PASS.
- [ ] **Step 6: Commit** `feat(agents): record/replay proxy and spike results`.

### Task 2: Shared desk core, Python

**Files:**
- Create: `agents/desk/python/desk_core.py`, `agents/evals/tickets.jsonl`, `agents/desk/python/tests/test_core.py`.

**Interfaces (produces):**
- `TICKETS = load_eval_tickets()`: 13 tickets, which are 2 of each of the 6 Lumen categories (lowest ids) plus `t061`.
- `claude(messages, *, system, tools=None, max_tokens=MAX_TOKENS) -> Message`: an `anthropic.Anthropic(base_url=PROXY_URL)` call. It never reads the key in replay; a dummy key is fine because the proxy answers.
- `open_desk_tools(servers, *, guard, approve, elicit, labels_path)`: a context manager returning `(tools_schema, run_tool, session)`. It reuses `mcp/python/agent.py`'s Bridge/Session/make_fn/needs_human by import.
- `Trace`: an append-only list of `{agent, kind: model|tool|handoff, name, tokens_in, tokens_out, ms}`.

- [ ] **Step 1: Write the failing tests.**
  - `test_eval_tickets_13_balanced_plus_t061`.
  - `test_tools_are_mcp_only`: the names are exactly the lumen-read and lumen-ops tools.
  - `test_guard_applies`: with a fake model, a write after an untrusted read with approve decline writes nothing.
  - `test_trace_records_tokens`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** No edits to `mcp/` or `harness/`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(agents): desk core`.

### Task 3: Patterns, Python

**Files:** Create `agents/desk/python/patterns.py`, `agents/fixtures/contract.jsonl`, `agents/fixtures/gen_contract.py`, `agents/desk/python/tests/test_patterns.py`.

**Interfaces (produces):** sections 1–6 (Configuration, then one per pattern):
- `chain(ticket) -> {category, reply}`
- `route(ticket) -> {route, reply}`: the routes are billing, bug, account and other.
- `parallel(ticket) -> {lookups: [...], summary}`
- `orchestrate(ticket) -> {plan: [...], results: [...], reply}`
- `evaluate_optimize(ticket, max_rounds=3) -> {rounds: [{draft, verdict}], final}`: the judge rubric is the LLMs lab's three criteria, reusing `triage_lab` prompts by import.

Each function takes `call` (default `claude`) and returns a `Trace`.

- [ ] **Step 1: Write the failing tests.** With a scripted fake model:
  - each pattern's call order and count;
  - evaluator-optimizer stops on a pass, and caps at `max_rounds`.

  `gen_contract.py` writes the cases: input ticket, scripted model replies, expected outputs and the expected call sequence. Python and Go must match them.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS. `gen_contract.py --check` exits 0.
- [ ] **Step 5: Commit** `feat(agents): five patterns`.

### Task 4: Patterns, Go

**Files:** Create `agents/desk/go/` (`go.mod` module `lumen-agents`, `patterns.go`, `patterns_test.go`).

**Interfaces:** the same five functions and sections as Task 3. The model is an interface, so tests use the contract's scripted replies. Real calls go to `PROXY_URL` over plain `net/http`, with no SDK required.

- [ ] **Step 1: Write `TestContract`** over `agents/fixtures/contract.jsonl`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** `cd agents/desk/go && go test -race ./...`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(agents): five patterns in Go`.

### Task 5: Multi-agent supervisor (Python, then Go)

**Files:** Create `agents/desk/python/supervisor.py`, `agents/desk/go/supervisor.go`, and tests in both. Extend the contract.

**Interfaces:**
- `single_agent(ticket) -> {reply, trace}`: one agent with all tools.
- `supervisor(ticket) -> {reply, trace, handoffs}`: the workers are triage, research (lumen-read only), drafter (no tools) and reviewer (no tools). Handoffs are tool calls `handoff(worker, task)`. Shared state is `{ticket_id, category, findings, draft, review}`, and every change is logged.
- Only the supervisor may call lumen-ops, and only after the reviewer passes.

- [ ] **Step 1: Write the failing tests.**
  - The handoff order.
  - The research worker cannot see lumen-ops tools.
  - No write before the reviewer passes.
  - State changes are logged.
  - Contract parity between Python and Go.
- [ ] **Steps 2–4:** Run (expect FAIL), implement, run (expect PASS) in both languages.
- [ ] **Step 5: Commit** `feat(agents): supervisor and single-agent desks`.

### Task 6: LangGraph desk

**Files:** Create `agents/frameworks/langgraph_desk.py` and `agents/frameworks/tests/test_langgraph.py`.

**Interfaces:**
- `langgraph_single(ticket)` and `langgraph_supervisor(ticket)` return `{reply, trace}`, using the same Trace shape as Task 2.
- Use `langchain.agents.create_agent`, not the deprecated `create_react_agent`.
- The supervisor is built as tools.
- MCP comes through `langchain_mcp_adapters` `MultiServerMCPClient` (check the API in the installed package).
- Use `ChatAnthropic(base_url=PROXY_URL, model=MODEL)`.
- The guard comes from Task 2's approver, via a middleware or a tool wrapper.

- [ ] **Step 1: Write the failing tests** with LangChain's `GenericFakeChatModel` (a `bind_tools` subclass, per the facts sheet):
  - the expected tool trajectory;
  - the guard blocks the write;
  - the trace shape matches Task 2.
- [ ] **Steps 2–4:** run, implement, run.
- [ ] **Step 5: Commit** `feat(agents): LangGraph desk`.

### Task 7: Claude Agent SDK desk

**Files:** Create `agents/frameworks/sdk_desk.py` and `agents/frameworks/tests/test_sdk.py`.

**Interfaces:**
- `sdk_single(ticket)` and `sdk_supervisor(ticket)` return `{reply, trace, cost_usd}`. Workers are SDK subagents.
- Options: `tools=[]`, `setting_sources=[]`, `model=MODEL`, the MCP servers attached (lumen-read and lumen-ops over stdio from `mcp/python`), and env `ANTHROPIC_BASE_URL=PROXY_URL`.
- Use hooks for the guard (`PreToolUse`) and the trace.
- Follow the Task 1 spike. If it ruled the proxy unusable, implement its fallback and mark it in the trace.

- [ ] **Step 1: Write the failing tests.** These are offline:
  - the options are hermetic (assert the exact `tools`, `setting_sources` and env);
  - the guard hook denies a write after an untrusted read;
  - the trace is built from a canned message stream.
- [ ] **Steps 2–4:** run, implement, run.
- [ ] **Step 5: Commit** `feat(agents): Claude Agent SDK desk`.

### Task 8: Run harness and recording sets (no paid runs)

**Files:** Create `agents/run.py`, `agents/data/recordings/README.md` and tests.

**Interfaces:**
- `python agents/run.py --engine hand|hand-go|langgraph|sdk --desk pattern:<name>|single|supervisor --tickets all|<id> [--mode replay|record --dry-run --yes --max-cost N]`
- It starts the proxy in the matching mode, runs the engine, then writes the trace to `agents/data/traces/<set>.json`.
- `--dry-run` prints the estimate for the whole set: calls, typical, worst and cap.
- The recording sets are:
  - `patterns`: 5 patterns × 3 tickets, hand-built Python (Go replays the same requests only if they are byte-identical; otherwise Go records its own set);
  - `desk-hand`: single and supervisor × 13;
  - `desk-langgraph`: single and supervisor × 13;
  - `desk-sdk`: single and supervisor × 13.

- [ ] **Step 1: Write the failing tests.**
  - The dry-run output shape.
  - A replay miss names the right record command.
  - Traces are deterministic in replay, checked with a fake proxy store.
- [ ] **Steps 2–4:** run, implement, run.
- [ ] **Step 5: Commit** `feat(agents): run harness and recording sets`.
- [ ] **Controller step (not the implementer):** print each set's dry-run and get the user's explicit "approve" before recording any set. Record approved sets with `--yes`.

### Task 9: Eval suite

**Files:** Create `agents/evals/scorers.py`, `agents/evals/run_evals.py`, `agents/data/derived/engine_scores.json` (generated) and tests.

**Interfaces:**
- `score(trace, ticket) -> {task_success, category_ok, judge_pass, trajectory_ok, forbidden_tool, unconfirmed_write, tokens, cost_usd, ms}`.
- `judge_pass` uses the LLMs lab judge prompt through the proxy. Judge calls belong to the recording set `judge`.
- `run_evals.py` aggregates each engine and desk: success rate with a noise band (1/√n, as in the LLMs lab), mean cost, p50 latency, and the safety count.
- An engine without a complete recording set is marked `"incomplete"`.

- [ ] **Step 1: Write the failing tests.**
  - The scorers against hand-made traces.
  - `test_partial_recording_marked_incomplete`.
  - `test_t061_never_writes_unconfirmed[engine]` for hand, langgraph and sdk.
  - `--check` reproduces the committed file.
- [ ] **Steps 2–4:** run, implement, run.
- [ ] **Step 5: Commit** `feat(agents): eval suite`.

### Task 10: Derived data for the app

**Files:** Create `agents/derive.py`, `agents/data/derived/{patterns,traces,cost_compare,engine_scores}.json` and tests.

Write the derived files from the recordings and traces only, with deterministic output, no absolute paths and no secrets. Before recordings exist, write `{"recorded": false, "command": ...}`. Add a `--check` mode.

- [ ] **Steps 1–5:** follow the TDD cycle, ending with the commit `feat(agents): derived lesson data`.

### Task 11: App: code sources, diagrams and aids

**Files:**
- Modify `codeSources.ts`, `codeSourceText.ts`, `FlowDiagram.astro`, `astro.config.mjs` (allow `../agents`) and the diagram tests.
- Create diagrams `agent-patterns` and `replay-proxy`.
- Create `src/lib/agentsData.ts` and `src/lib/agentsAids*.ts`, each module under 400 lines.
- Create the components `components/agents/{PatternStepper,AgentTrace,CostCompare,EngineScorecard}.{astro,tsx}`.
- Create the tests.

Follow STYLE.md and the MCP aids' patterns:
- tokens only;
- keyboard navigation and live regions;
- tables beside charts;
- 375px without page scroll;
- tables scroll in their own box.

Show "No recorded run yet" until recordings exist. Give captions with lab-section numbers a pinning test.

- [ ] **Steps 1–5:** follow the TDD cycle, run `npm test && npm run build`, then commit `feat(app): agents aids, diagrams and code sources`.

### Task 12: Lessons 1–6

**Files:**
- Create `app/src/content/lessons/agents/01-workflows.mdx` through `06-more-agents.mdx`.
- Update the glossary.

Write per spec §5 and STYLE.md. Every number comes from derived data or real output, and every printed command is run once. Lesson 1 cites Anthropic's "Building effective agents" (the facts sheet has the URL).

- [ ] **Steps:** write, run the commands, run `npm test && npm run build && npm run package`, then commit `feat(agents): lessons 1-6`.

### Task 13: Lessons 7–11, track live, docs

**Files:**
- Create `07-replay.mdx` through `11-capstone.mdx`.
- Set `agents.yaml` to live.
- Add a README section "Run the AI Agents track".
- Add STYLE.md rows and glossary entries.

Lessons 8 and 9 are Python-only, each with a `<Callout kind="go">`. The capstone gate picks the engine from `engine_scores.json` and reports any incomplete engine honestly.

- [ ] **Steps:**
  1. Write the lessons and docs.
  2. Run every command.
  3. Run all suites: app, agents Python, `cd agents/desk/go && go test -race ./...`, the MCP, ML, LLMs, harness and Inspect suites, and the `--check` scripts.
  4. Commit `feat(agents): lessons 7-11, track live`.
