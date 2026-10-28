# AI Engineering: AI Agents track (design)

Date: 2026-10-27
Status: approved; built on `feat/agents-track` (2026-10-27–28). §10 records how the open items were resolved.
Builds on: the Harness, LLMs, ML and MCP tracks, `docs/STYLE.md`, `~/ai-engineering-kit/APP-DESIGN-TEMPLATE.md`, and the verified facts in `docs/superpowers/research/2026-10-27-agents-facts.md`.

## 1. Intent

**Outcome.** The learner can:
- choose between a workflow and an agent;
- build the five standard patterns (chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer);
- build a multi-agent desk, and say when more agents hurt;
- rebuild the same desk in LangGraph and in the Claude Agent SDK;
- record and replay any agent at the HTTP level;
- evaluate agents fairly and choose which one to ship.

**Audience.** The same learner, who has finished the Harness, LLMs, ML and MCP tracks.

**Success criteria.**
- The hand-built patterns and the supervisor give identical results in Python and Go on a shared contract (with a fake model).
- All three engines run the same desk on the same tickets through the same MCP servers. Each replays free from recordings via the proxy.
- The eval suite scores every engine on task success, trajectory, cost, latency and safety. The capstone gate picks one and explains why.
- All 11 lessons pass the style tests. `agents.yaml` is set to live, and the page is republished.

**Stated by the user:**
- Scope covers design patterns, multi-agent systems, agent frameworks and evaluating agents.
- The running example is the Lumen support desk.
- The frameworks are LangGraph and the Claude Agent SDK.
- Go covers the hand-built parts; the framework lessons are Python only.
- The approach is A: three engines plus one replay proxy.

**Assumptions (accepted):**
- Free and offline by default.
- Pinned versions.
- Paid recordings run only after the user approves each printed estimate. Haiku 5.5 is the default, and each run has a $10 cap.

## 2. Scope

**In scope:**
- `agents/`: the proxy, the hand-built desk (Python and Go), the two framework desks, the eval suite, recordings and derived data.
- 4 aids and 2 diagrams.
- 11 lessons, `agents.yaml` set to live, and README and STYLE updates.

**Out of scope:**
- Hosted agent platforms.
- LangSmith or any paid tracing.
- Long-term memory stores.
- A TypeScript version.
- Any other track.

## 3. Architecture

```
agents/
├── proxy/                record/replay proxy (Python, stdlib + httpx): listens on 127.0.0.1,
│                         forwards to api.anthropic.com only in record mode; requests keyed by
│                         SHA-256 of a canonical request (method, path, body minus volatile fields)
├── desk/python/          patterns.py, supervisor.py on the Track 1 harness + MCP clients
├── desk/go/              the same, numbered sections matching
├── frameworks/           langgraph_desk.py, sdk_desk.py
├── evals/                tickets.jsonl (subset of Lumen tickets + t061), scorers, run_evals.py
├── data/recordings/      one store for all engines (proxy format)
├── data/derived/         small JSON for the app
├── fixtures/contract.jsonl  hand-built desk contract (fake model), shared by Python and Go
└── requirements.txt      langgraph==1.2.18, langchain-anthropic==1.7.6, langchain==1.4.7,
                          claude-agent-sdk==0.2.174, mcp==2.3.0, anthropic, httpx, pytest
```

**Tools.** Every engine uses the MCP track's servers, `lumen-read` and `lumen-ops`. Writes stay confirmed and single-use, and the untrusted-read rule applies to every engine.

**Proxy.**
- Every engine sets `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`.
- In replay mode, the default, a missed key returns an error naming the record command. A replay is never forwarded.
- In record mode, the proxy:
  - prints an estimate and asks y/N;
  - uses the API key from the environment only, and never logs it;
  - enforces a per-call cap: it rejects a call when spent plus worst-case exceeds `--max-cost`;
  - stores the full response, streaming included, so replay reproduces the stream exactly.
- Stored recordings contain no auth headers.

**Agent SDK settings.** The SDK desk runs with:
- `tools=[]` (built-ins off);
- `setting_sources=[]` (hermetic, not reading the user's settings);
- an explicit model;
- the MCP servers attached;
- `permission_mode` set so the server's confirmation is still the gate.

## 4. Components

**Patterns** (`patterns.py` + Go). Each pattern is a small function with a numbered section:
- chain: triage, then draft;
- route: by category, to a specialised prompt;
- parallel: lookups at the same time, then an aggregate;
- orchestrator-workers: a lead splits the work and workers do it;
- evaluator-optimizer: draft, then a judge from the LLMs track rubric, then revise, at most N rounds.

**Supervisor.** A supervisor agent with four workers: triage, research (lumen-read), drafter and reviewer. Handoffs go through tool calls, and state is shared through an explicit, logged state object. Single-agent and multi-agent versions run the same tickets, for the comparison in lesson 6.

**LangGraph desk.**
- The single agent uses `create_agent`.
- The supervisor is built as tools, as LangChain now recommends; the langgraph-supervisor package is archived.
- MCP tools come through `langchain-mcp-adapters` `MultiServerMCPClient`. The task-1 spike verifies this.
- Human-in-the-loop uses `HumanInTheLoopMiddleware`. lumen-ops confirmation still decides the write.

**Agent SDK desk.**
- `query()` / `ClaudeSDKClient` with the MCP servers.
- Subagents for workers.
- Hooks for logging and the untrusted-read rule.
- The trace comes from the message stream, and cost from `total_cost_usd`. The lesson notes that this is a client-side estimate.

**Eval suite.**
- Ticket set: about 12 Lumen tickets plus t061.
- Scorers:
  - task success: correct category, reply passes the judge rubric (replayed);
  - trajectory: the expected tools called, no forbidden tools, no write without confirmation;
  - cost and tokens, and latency from the recording;
  - safety: t061 must not cause a write.
- Noise bands follow the LLMs-track method.
- The output is `engine_scores.json`.
- The capstone gate is a threshold plus the noise band, as in the LLMs track.

## 5. Lessons

| # | Slug | Title | Subsection | Aid |
|---|---|---|---|---|
| 1 | workflows | Workflows or agents? | Design | `FlowDiagram` `agent-patterns` |
| 2 | chain-route | Chaining and routing | Design | `PatternStepper` |
| 3 | parallel | Parallel work and orchestrator-workers | Design | `PatternStepper` |
| 4 | evaluator | Evaluator-optimizer | Design | `PatternStepper` |
| 5 | multi-agent | A multi-agent support desk | Multi-agent | `AgentTrace` |
| 6 | more-agents | When more agents hurt | Multi-agent | `CostCompare` |
| 7 | replay | Record and replay any agent | Tools | `FlowDiagram` `replay-proxy` |
| 8 | langgraph | The desk in LangGraph | Frameworks | `AgentTrace` |
| 9 | agent-sdk | The desk in the Claude Agent SDK | Frameworks | `AgentTrace` |
| 10 | evals | Evaluating agents | Evals | `EngineScorecard` |
| 11 | capstone | Capstone: ship the best desk | All | `EngineScorecard` + gate |

- Each lesson follows `docs/STYLE.md`.
- Lessons 8 and 9 are Python only, each with a `<Callout kind="go">` explaining why.
- Every number comes from recordings, derived data or command output.
- Lesson 1 cites Anthropic's "Building effective agents" taxonomy as the source of the five patterns.

## 6. App

- **Code sources:** `agents-proxy`, `agents-patterns` and `agents-supervisor` (Python + Go), and `agents-langgraph`, `agents-sdk` and `agents-evals` (Python).
- **Diagrams:** `agent-patterns` and `replay-proxy`.
- **Aids:** `PatternStepper`, `AgentTrace`, `CostCompare` and `EngineScorecard`. They follow STYLE.md, read from `agents/data/derived`, and show "No recorded run yet" plus the command until recordings exist.
- **Page budget:** new props under about 200 KB.

## 7. Error handling

- **Proxy:**
  - A replay miss returns a 4xx with the record command. It is never forwarded.
  - A record-mode cap or decline exits without calling.
  - An upstream error is recorded as an error only if the user wants it, and is not replayed silently.
- **Engines:** a tool error is surfaced in the trace. A failed confirmation writes nothing, as in the MCP track.
- **Evals:** a missing recording means that engine's row reads "not recorded", never a fabricated score.

## 8. Testing

- **Proxy:** hashing stability, volatile-field exclusion, replay hit and miss, streaming replay byte-exactness, the cap, and no key in recordings.
- **Hand-built desk:** the shared contract with a fake model, in Python and Go, identical.
- **Frameworks:** they run in replay through the proxy, and the tests assert the trajectory, never the exact prose.
- **Evals:** the scorers are checked against fixtures.
- **App:** Vitest.
- **No API calls in tests.**

## 9. Cost and setup

- A venv at `.venv-agents`, plus Go 1.27.2. The MCP servers come from the MCP track.
- Free by default.
- Recordings are made in sets, and each set's printed estimate and worst case is approved by the user first. The expected total is a few dollars on Haiku 5.5.

## 10. Open items (resolved at build time; see `docs/superpowers/research/2026-10-27-agents-rulings.md`)

- **Task 1 spike:**
  - The Agent SDK's bundled CLI honours `ANTHROPIC_BASE_URL` and runs through the proxy, streaming included (spike, `docs/superpowers/research/2026-10-27-agents-spike.md`). The fallback was not needed.
  - `MultiServerMCPClient` does not connect: `langchain-mcp-adapters` 0.3.2 requires `mcp<2` and cannot import against the pinned `mcp==2.3.0` (checked 2026-10-27). The LangGraph desk takes its tools from the gated `open_desk_tools` path instead, and lesson 8 shows the adapter as text with the conflict stated (ruling R31). This supersedes the `langchain-mcp-adapters` line in §4.
  - LangGraph and langchain-anthropic replay byte-exact through the proxy once the proxy drops idle keep-alive connections at the end of a run (ruling R59).
- **Recordings:** approved by the user per printed estimate; the final store is one fresh round (every call live, timing and usage stored per recording, rulings R62–R65). Total spend for the track: $0.5663.
- **Gate outcome:** with 13 tickets no engine clears the capstone gate (threshold 0.7 minus the 1/√n band); lesson 11 teaches that result rather than lowering the bar (ruling R66).
