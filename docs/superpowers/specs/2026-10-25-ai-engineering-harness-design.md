# AI Engineering — App + Agent Harness Track: Design

Date: 2026-10-25
Status: Approved in conversation; awaiting written-spec review

## 1. Intent

**Outcome.** The learner understands how an AI agent harness works and operates, end to end, well enough to explain every step of the flow and to run and modify a real harness.

**Audience.** One learner (the owner), comfortable with Python, new to LLM APIs and tool calling. Lessons do not teach Python basics; they do teach messages, roles, tokens, `stop_reason`, and tool calling from scratch.

**Success criteria.**
- The learner can narrate the 10-step harness flow (section 5) from memory.
- The learner runs both reference harnesses (Python and Go) locally against the Claude API.
- The learner completes the capstone exercises (add a tool, change the approval policy, add a token-cost readout).
- All nine Harness lessons are live in the app with quizzes passing their content checks.

**Stated by the user:** step-by-step masterclass; Python code with a second, more efficient language alongside for comparison; numbered end-to-end diagrams; an interactive app named "AI Engineering" with current and future lessons and interactive aids; one track at a time, tracks grow over time; Astro.

**Assumptions (accepted):** the second language is Go; the harness is a hand-written loop on the Claude API (no framework); future tracks appear as a visible roadmap until built.

## 2. Scope

**In scope (this sub-project):**
- The AI Engineering app shell (Astro), built to host many tracks.
- The Agent Harness track: nine lessons, fully written.
- Reference harness code in Python and Go, with offline tests.
- Roadmap entries for five planned tracks.

**Out of scope:** content for any track other than Harness. Each later track gets its own design → spec → plan cycle and reuses this app's lesson format and components.

**Track roadmap (in order):**

| Order | Track id | Title | Subsections | Status |
|---|---|---|---|---|
| 1 | `harness` | Agent Harness | (nine lessons, section 5) | live |
| 2 | `llms` | LLMs | Tuning, Evaluation, Judging | planned |
| 3 | `ml` | Machine Learning | MLOps | planned |
| 4 | `mcp` | MCP | Clients, Servers, Apps, Tools | planned |
| 5 | `agents` | AI Agents | Multi-agent systems | planned |
| 6 | `data` | Data Fabric & Data Contracts | Data Fabric, Data Contracts | planned |

## 3. Architecture

```
masterclass/
├── harness/                      runnable reference code (source of truth)
│   ├── python/harness.py         + tests/ (pytest)
│   └── go/main.go, go.mod        + main_test.go
├── app/                          Astro project
│   ├── astro.config.mjs          static output, MDX, Preact integration
│   ├── src/content.config.ts     collection schemas: tracks, lessons
│   ├── src/content/tracks/*.yaml one file per track
│   ├── src/content/lessons/<track>/NN-<slug>.mdx
│   ├── src/data/diagrams/*.ts    FlowDiagram data
│   ├── src/data/scenarios/*.ts   LoopSimulator scenarios
│   ├── src/components/           FlowDiagram, LoopSimulator, ContextMeter,
│   │                             Quiz, CodeTabs, Callout, Term
│   ├── src/lib/                  pure logic: simulator engine, compaction
│   │                             math, code-region extractor, progress store
│   ├── src/layouts/              AppShell, Lesson
│   ├── src/pages/index.astro     single entry: catalog, roadmap, all lessons
│   └── scripts/package.mjs       dist/ → publish/ for the page host
└── docs/superpowers/specs/       this file
```

**Stack:** Astro (static output) + `@astrojs/mdx` + `@astrojs/preact`; TypeScript for components and lib; Shiki (built into Astro) for build-time highlighting with light and dark themes; Vitest for unit tests.

**Track schema** (`tracks/*.yaml`): `id`, `title`, `order`, `status: live | planned`, `summary`, `subsections: string[]`.

**Lesson schema** (MDX frontmatter): `track`, `order`, `slug`, `title`, `goal`, `minutes`, `quiz: { question, options[], answer (index), explanation }[]` (2–3 items).

**Adding a track later** = write `tracks/<id>.yaml` (already present as planned), add `lessons/<id>/*.mdx`, set `status: live`. No shell or component changes required.

**Routing.** One page. Each lesson is a view addressed by a bare hash token `<track>-<slug>` (e.g. `#harness-loop`); the home view is the default. Tokens use only letters, digits, `-`.

**Islands.** Only FlowDiagram, LoopSimulator, ContextMeter, Quiz, CodeTabs (tab switching/copy) and progress UI hydrate; lesson prose is static HTML.

## 4. Reference harness code

Both implementations have the same nine numbered sections, which the lessons map onto:

1. Configuration (model `claude-opus-5-5`, `MAX_TOKENS` 16000, `MAX_TURNS` 25, `CONTEXT_BUDGET` 150000, fallbacks flag, workspace)
2. Observability — `emit(event, data)` appends JSON lines to `trace.jsonl`
3. Tool registry — Python decorator / Go `register(Tool{...})` in `init()`; tools `list_files`, `read_file`, `write_file` (needs approval); `safe_path` workspace sandbox
4. Permission gate — interactive y/N approval, logged
5. Tool executor — approvals asked sequentially, approved calls run in parallel (ThreadPoolExecutor / goroutines + WaitGroup), results in original order, failures returned as `is_error` results; Go adds a per-tool `context.WithTimeout`
6. Model call — Messages API with tools and system prompt; server-side refusal fallback enabled via beta header `server-side-fallback-2026-07-01` + `fallbacks: "default"` (flag `USE_FALLBACKS`)
7. Context management — when `usage.input_tokens` exceeds the budget, ask for a summary (`tool_choice: none`) and start a fresh conversation from it; never edit earlier turns (append-only)
8. Agent loop — route on `stop_reason`: `tool_use` → execute and continue; `end_turn` → return text; `max_tokens` → return text with notice; `refusal` → notice; cap at `MAX_TURNS`
9. Entry point — REPL; error handling order: rate limit → API status error → connection error

Current draft files (`harness/python/harness.py`, `harness/go/main.go`) already implement this and compile; implementation adds tests and any refactoring needed for testability (injectable model client and approver).

Section markers in source are comment lines of the form `# ── N. Title` (Python) and `// ── N. Title` (Go); CodeTabs extracts a section by number.

## 5. Harness track content

Every lesson: Goal → Concept → Aid (where useful) → Code (Python | Go tabs from real files) → "How Go does it" note → Quiz (2–3) → Try it (local exercise).

| # | Slug | Title | Covers | Code sections | Aid |
|---|---|---|---|---|---|
| 1 | `what` | What a harness is | Model vs harness; the components; the 10-step flow | — | FlowDiagram (full) |
| 2 | `model` | Talking to a model | API key; messages/roles; system prompt; tokens; one call; response shape | 1, 6 | Annotated request/response diagram |
| 3 | `tools` | Tools | Tool calling; JSON Schema; `tool_use`/`tool_result`; harness runs code, not the model; registry | 3 | One-round-trip diagram |
| 4 | `loop` | The agent loop | While loop; `stop_reason` routing; append order; `tool_use_id`; multiple calls per turn; `MAX_TURNS` | 8 | LoopSimulator |
| 5 | `permissions` | Permissions & sandboxing | Approval gate; `safe_path`; denial as error result; sequential approvals | 3 (`safe_path`), 4 | LoopSimulator (approve/deny) |
| 6 | `executor` | Running tools well | Parallelism (threads vs goroutines); timeouts; errors as information; output caps | 5 | Sequential vs parallel timeline |
| 7 | `context` | Context management | Context window; token growth; `usage.input_tokens`; summarize-and-restart; append-only rule | 7 | ContextMeter |
| 8 | `reliability` | Reliability & observability | SDK retries; error order; `trace.jsonl`; reading a trace | 2, 9 | Annotated sample trace |
| 9 | `capstone` | Capstone: run your harness | Full walkthrough; diagram replay linked to code; run both; exercises | all | FlowDiagram (replay) + checklist |

**The 10-step flow** (FlowDiagram data for lessons 1 and 9):
1. User types a task.
2. Harness builds the request: system prompt + history + tool schemas.
3. Request sent to the model.
4. Model returns text and/or `tool_use` blocks, plus `stop_reason`.
5. Router: `tool_use` → continue; `end_turn` → step 10.
6. Permission gate checks risky tools.
7. Executor runs tools (parallel, sandboxed).
8. Results appended as `tool_result` in one user message.
9. Context manager checks budget, compacts if needed → back to step 3.
10. Final answer shown to the user.
Observability logs every step (side rail in the diagram).

## 6. Interactive components

All components take plain data so later tracks reuse them with new data files.

**FlowDiagram.** Props: `diagram` = `{ nodes: {id, label, role, x, y}[], edges: {from, to, label?}[], steps: {n, highlight: string[], caption, code?: {file, section}}[] }`. Roles: `user | harness | model | tool | memory | logs`, each with a token colour plus a text label. SVG with numbered step badges; Prev / Next / Play / Reset and a clickable step list; arrow keys step; loop-back edge animates (static under reduced motion); "Show code" reveals the linked section.

**LoopSimulator.** Engine in `src/lib/simulator.ts` (pure, tested). Scenario = `{ id, title, task, workspace: Record<path, content>, turns: Turn[] }`; `Turn` = `{ blocks: (text | tool_use)[], stop_reason, branches?: { denied?: Turn[] } }`. Fake tools execute against the scenario workspace. Three scenarios: `parallel-read` (two tool calls in one turn), `approve-write` (read then approval-gated write with approve/deny endings), `recover-error` (missing file → `is_error` → model lists files and recovers). Panels: conversation, live `messages` JSON with newest entry highlighted, state strip (turn, `stop_reason`, token estimate). Controls: Step, Run to end, Reset, policy switch (ask before write tools / allow all), inline Approve/Deny. Visible "Simulated — no real model is called" label.

**ContextMeter.** Stacked bar: system prompt, tool schemas, history, new turn; budget line (150K) and window limit (1M). Add-turn buttons (short reply ≈1.5K, large file read ≈25K, several tool calls ≈8K) and auto-run 20 turns. Over budget → history folds into a summary block (≈3K) with an explanation of append-only restart. Labelled as illustrative estimates. Math in `src/lib/compaction.ts`.

**Quiz.** Reads lesson frontmatter. Single choice, instant feedback with explanation, retry allowed. Lesson completes when the quiz is passed or the learner marks it done.

**CodeTabs.** Props: `section?: number`, `whole?: boolean`, `goNote`. Imports `harness/python/harness.py` and `harness/go/main.go` raw at build time; `src/lib/regions.ts` extracts a numbered section. Tab choice remembered globally. Copy button (clipboard with select-text fallback).

**Progress.** `src/lib/progress.ts`: per-lesson done flag and best quiz score in `localStorage`, every access in try/catch, app fully usable without storage. Sidebar ticks per lesson and percent per track.

**Shared.** Keyboard operable, visible focus, `prefers-reduced-motion` respected, colour tokens defined for light and dark (system preference and explicit `data-theme`), explicit body background, no horizontal page scroll at 400px.

## 7. Hosting and build

- Published as a private claude.ai artifact, republished to the same URL as tracks are added.
- The host wraps the main page in its own document skeleton and allows scripts only from its CDN allowlist or the artifact's own files; external stylesheets only from Google Fonts.
- `scripts/package.mjs` converts `dist/` → `publish/`: strips `<!doctype>/<html>/<head>/<body>` wrappers from `index.html` (keeping `<title>`, meta, styles at the top), inlines CSS (`build.inlineStylesheets: 'always'`), rewrites `/_astro/…` to relative paths, and fails if any URL targets a non-allowlisted host.
- Fonts from Google Fonts with fallback stacks.
- Escape hatch: if the single page grows too heavy, deploy the same project as a multi-page static site (e.g. Vercel) without code changes.

**Commands:** `npm run dev`, `npm run build` (includes `astro check`), `npm run package`, `npm test`.

## 8. Error handling

- Content errors (missing frontmatter, bad quiz answer index, unknown code section, unknown track) fail the build via schemas and the region extractor.
- Unknown hash token → home view with no error.
- Storage unavailable → progress silently in-memory for the session.
- Simulator: an invalid scenario fails its unit test, never at runtime.
- Harness: tool exceptions become `is_error` results; API errors handled in the REPL per section 4.9.

## 9. Testing

- `astro check` — types and content schemas.
- Vitest (test-first): simulator engine (stepping, branching, reset); every scenario's `messages` obeys API rules (alternating roles, each `tool_use` answered by a matching `tool_result` in the next user message); compaction math; region extractor; progress store with storage throwing.
- Python harness: `pytest` — `safe_path` rejects escapes; executor preserves order; denied and unknown tools return `is_error`; `stop_reason` routing with a fake model client. No network.
- Go harness: `go vet`, `go build`, `go test` for the same cases.
- Optional live smoke test of each harness against the API, only with the user's go-ahead (costs money).
- One rendered preview at desktop and phone widths, light and dark, before each publish.

## 10. Open items

None.
