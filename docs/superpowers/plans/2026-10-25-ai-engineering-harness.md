# AI Engineering — App + Agent Harness Track Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the AI Engineering learning app (Astro) with the nine-lesson Agent Harness track, its five interactive components, and tested Python and Go reference harnesses, then publish it as a private claude.ai page.

**Architecture:** Runnable harness code in `harness/` is the source of truth; the Astro app in `app/` imports it raw at build time. Tracks and lessons are content collections (YAML + MDX) validated by zod schemas; pure logic lives in `app/src/lib/` and is unit-tested; Preact islands render the interactive parts. The build produces one page with hash-addressed lesson views, reshaped by a packaging script for the page host.

**Tech Stack:** Python 3.12 + `anthropic` + pytest; Go 1.27 + `github.com/anthropics/anthropic-sdk-go`; Node 25 / npm 11; `astro@7.4.2`, `@astrojs/mdx@8.1.1`, `@astrojs/preact@6.1.1`, `preact`; Vitest + jsdom + `@testing-library/preact`; Playwright (verification only).

**Spec:** `docs/superpowers/specs/2026-10-25-ai-engineering-harness-design.md`

## Global Constraints

- Model id everywhere: `claude-opus-5-5`. Harness constants: `MAX_TOKENS` 16000, `MAX_TURNS` 25, `CONTEXT_BUDGET` 150000, tool output cap 20000 chars, Go tool timeout 30s.
- Fallback beta: header `anthropic-beta: server-side-fallback-2026-07-01` and body `fallbacks: "default"`, behind `USE_FALLBACKS` / `useFallbacks`.
- Harness is append-only: never mutate an earlier message; compaction starts a new conversation from a summary.
- Section markers: `# ── N. Title` (Python), `// ── N. Title` (Go), N = 1..9, identical titles in both files.
- Astro 7 content layer: config in `app/src/content.config.ts` using `glob` from `astro/loaders`. If an API named here differs in 7.4.2, follow the installed version's docs and keep the names in this plan's Interfaces blocks.
- Lesson view ids: `<track>-<slug>` using only `[a-z0-9-]`; home view id `home`.
- localStorage keys: `aie:progress:v1` (progress), `aie:lang` (`python` | `go`). Every access in try/catch.
- Page `<title>`: `AI Engineering`. Fonts from Google Fonts only: Bricolage Grotesque (display), IBM Plex Sans (body), IBM Plex Mono (code), each with a system fallback stack.
- Colour: every colour a CSS custom property defined on bare `:root` (light), redefined under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {…} }` and `:root[data-theme="dark"] {…}`, both with `color-scheme: dark`; `body` sets an explicit background token.
- Layout: no horizontal page scroll at 400px; side gutter `padding-left: max(16px, env(safe-area-inset-left, 0px))` / right equivalent on the app wrapper; `<meta name="claude:viewport-fit-sides" content="cover">`.
- Allowed external hosts in the published page: `fonts.googleapis.com`, `fonts.gstatic.com`, `cdnjs.cloudflare.com`, `cdn.jsdelivr.net`, `unpkg.com`. Anything else fails packaging.
- Exact copy: simulator label `Simulated — no real model is called`; context meter label `Illustrative token estimates`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Deep link on first load** — opening `…#harness-loop` directly shows that lesson; an unknown token (`#harness-nope`, `#settings`) shows home without errors. Pinned in Task 10 (`resolveView` tests).
2. **Corrupt or blocked storage** — `aie:progress:v1` holding invalid JSON, or `localStorage` throwing, leaves the app usable with empty progress. Pinned in Task 5.
3. **Run to end while approval pending** — the simulator stops at the approval prompt and never auto-approves; Reset clears a pending approval. Pinned in Task 6.
4. **Bad tool input from the model** — wrong/missing argument names, malformed JSON, `../` and absolute paths become `is_error` results, never crashes or escapes the workspace. Pinned in Tasks 1 and 2.
5. **Islands inside hidden lesson views** — a simulator in a lesson that was hidden at load hydrates and works after navigating to it via the sidebar, under a nested URL path. Pinned in Task 20 (browser check).

---

### Task 1: Python harness — testable seams and offline tests

**Files:**
- Modify: `harness/python/harness.py` (sections 5, 7, 8)
- Create: `harness/python/tests/conftest.py`, `harness/python/tests/test_harness.py`, `harness/python/requirements.txt`

**Interfaces:**
- Produces (used by lessons via section markers; signatures must match):
  - `execute_tools(blocks: list, approver: Callable[[str, dict], bool] = approve) -> list[dict]`
  - `compact(messages: list[dict], call: Callable[..., Any] = call_model) -> list[dict]`
  - `run_agent(task: str, call: Callable[..., Any] = call_model, approver: Callable[[str, dict], bool] = approve) -> str`
  - Module globals tests patch: `WORKSPACE: Path`, `TRACE_FILE: Path`, `REGISTRY: dict[str, Tool]`.

- [ ] **Step 1: Write `requirements.txt`** containing `anthropic>=1.0` and `pytest>=8`; run `pip install -r harness/python/requirements.txt`.

- [ ] **Step 2: Write `conftest.py`**: set `os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")` before import; an autouse fixture `ws(tmp_path, monkeypatch)` that patches `harness.WORKSPACE` to `tmp_path/"ws"` (created) and `harness.TRACE_FILE` to `tmp_path/"trace.jsonl"`. Helpers: `block(name, input, id="tu_1")` → `SimpleNamespace(type="tool_use", id=..., name=..., input=...)`; `reply(stop_reason, *blocks, input_tokens=100)` → `SimpleNamespace(content=list(blocks), stop_reason=..., usage=SimpleNamespace(input_tokens=..., output_tokens=10))`; `text(s)` → `SimpleNamespace(type="text", text=s)`.

- [ ] **Step 3: Write failing tests in `test_harness.py`**

```python
def test_safe_path_rejects_escapes(ws):
    for bad in ["../x", "/etc/passwd", "a/../../x"]:
        with pytest.raises(PermissionError): harness.safe_path(bad)

def test_executor_keeps_order_and_matches_ids(ws):
    (harness.WORKSPACE / "a.txt").write_text("A")
    out = harness.execute_tools([block("read_file", {"path": "a.txt"}, "t1"),
                                 block("list_files", {"path": "."}, "t2")], approver=lambda *_: True)
    assert [r["tool_use_id"] for r in out] == ["t1", "t2"]
    assert out[0]["content"] == "A" and out[0]["is_error"] is False

def test_denied_unknown_and_bad_args_are_errors(ws):
    out = harness.execute_tools([block("write_file", {"path": "x", "content": "y"}, "t1"),
                                 block("nope", {}, "t2"),
                                 block("read_file", {"file": "a"}, "t3"),
                                 block("read_file", {"path": "../../etc/passwd"}, "t4")],
                                approver=lambda *_: False)
    assert all(r["is_error"] for r in out)
    assert out[0]["content"] == "The user denied this action."
    assert out[1]["content"] == "Unknown tool: nope"
    assert not (harness.WORKSPACE / "x").exists()

def test_read_file_caps_output(ws):  # 25_000 chars in → 20_000 out

def test_loop_runs_tools_then_returns_text(ws):
    replies = iter([reply("tool_use", block("list_files", {"path": "."})),
                    reply("end_turn", text("done"))])
    seen = []
    def fake(messages, **kw): seen.append([m["role"] for m in messages]); return next(replies)
    assert harness.run_agent("hi", call=fake, approver=lambda *_: True) == "done"
    assert seen[1] == ["user", "assistant", "user"]

def test_stop_reasons(ws):  # max_tokens → text + "\n[stopped: the response hit max_tokens]"; refusal → "[the model declined this request]"

def test_max_turns(ws, monkeypatch):  # MAX_TURNS=2, always tool_use → "[stopped: reached MAX_TURNS without finishing]"

def test_compaction_starts_fresh(ws):
    # first reply tool_use with input_tokens=CONTEXT_BUDGET+1, then summary reply, then end_turn
    # assert the third call's messages == [one user message] whose content starts with "Summary of earlier work:"
    # assert the summary call received tool_choice={"type": "none"} and max_tokens=4000

def test_trace_written(ws):  # after a run, every TRACE_FILE line is JSON with "event" in
    # {"session_start","model_request","model_response","tool_result","permission","compact","session_end"}
```

Because `call_model` emits `model_request`/`model_response`, fakes bypass it; `test_trace_written` asserts at least `session_start`, `tool_result`, `session_end`.

- [ ] **Step 4: Run** `cd harness/python && python -m pytest -q` — Expected: FAIL (`execute_tools() got an unexpected keyword argument 'approver'`).

- [ ] **Step 5: Implement** the three signature changes. `run_agent` passes `call` to `compact`; `execute_tools` uses `approver` instead of `approve`. Keep the section markers and comments; do not change behaviour beyond injection.

- [ ] **Step 6: Run** `python -m pytest -q` — Expected: all pass, no network.

- [ ] **Step 7: Commit** `harness/python` — `feat(harness): python reference harness with offline tests`.

---

### Task 2: Go harness — testable seams and offline tests

**Files:**
- Modify: `harness/go/main.go`
- Create: `harness/go/main_test.go`

**Interfaces:**
- Produces:
  - `type modelCaller func(ctx context.Context, p anthropic.MessageNewParams) (*anthropic.Message, error)`
  - `type approver func(name, args string) bool`
  - `executeTools(ctx context.Context, calls []call, ok approver) []anthropic.ContentBlockParamUnion`
  - `compact(ctx context.Context, messages []anthropic.MessageParam, model modelCaller) ([]anthropic.MessageParam, error)`
  - `runAgent(ctx context.Context, task string, model modelCaller, ok approver) (string, error)`
  - Package vars tests set: `workspace string`, `traceFile string` (new; replaces the literal `"trace.jsonl"`).
  - `main()` passes `callModel` and `approve`.

- [ ] **Step 1: Write failing tests** mirroring Task 1: `TestSafePathRejectsEscapes`, `TestExecutorOrderAndErrors` (denied → content `The user denied this action.`, unknown → `Unknown tool: nope`, malformed JSON input `{` → is_error, `../` path → is_error), `TestLoopToolsThenText`, `TestStopReasons` (`max_tokens`, `refusal` strings as in Task 1 with `maxTurns` wording `[stopped: reached maxTurns without finishing]`), `TestCompactionStartsFresh`. Build fake replies with `json.Unmarshal` into `anthropic.Message` from literal JSON (`{"id":"m","type":"message","role":"assistant","model":"claude-opus-5-5","content":[...],"stop_reason":"tool_use","usage":{"input_tokens":100,"output_tokens":10}}`). Inspect results by marshalling each `ContentBlockParamUnion` to JSON and reading `tool_use_id`, `is_error`, `content`. Each test sets `workspace = t.TempDir()` and `traceFile = filepath.Join(t.TempDir(), "trace.jsonl")`.

- [ ] **Step 2: Run** `cd harness/go && go test ./...` — Expected: FAIL (compile errors: too many arguments).

- [ ] **Step 3: Implement** the injected signatures; `maxTurns` becomes a `var` so a test can lower it.

- [ ] **Step 4: Run** `go vet ./... && go test ./...` — Expected: PASS.

- [ ] **Step 5: Verify parity**: `grep -c '^# ── ' harness/python/harness.py` and `grep -c '^// ── ' harness/go/main.go` both print `9`, and the titles after the numbers match line for line.

- [ ] **Step 6: Commit** `harness/go` — `feat(harness): go reference harness with offline tests`.

---

### Task 3: Astro scaffold, content schemas, track roadmap

**Files:**
- Create: `app/package.json`, `app/astro.config.mjs`, `app/tsconfig.json`, `app/vitest.config.ts`, `app/src/content/schemas.ts`, `app/src/content.config.ts`, `app/src/content/tracks/{harness,llms,ml,mcp,agents,data}.yaml`, `app/src/content/lessons/harness/.gitkeep`, `app/src/pages/index.astro` (placeholder), `app/tests/schemas.test.ts`

**Interfaces:**
- Produces: `trackSchema`, `lessonSchema` (zod, from `astro/zod`) in `src/content/schemas.ts`; collections `tracks` and `lessons`; npm scripts `dev`, `build` (`astro check && astro build`), `test` (`vitest run`), `package` (`node scripts/package.mjs`, added in Task 20).
- Lesson frontmatter: `{ track: string, order: number, slug: string (/^[a-z0-9-]+$/), title, goal, minutes: number, quiz: { question, options: string[] (2–5), answer: number, explanation }[] (2–3) }` with refinement `answer < options.length`.
- Track: `{ id, title, order, status: "live" | "planned", summary, subsections: string[] }`.

- [ ] **Step 1: Scaffold** `app/` with `npm create astro@7.4.2 -- app --template minimal --no-install --no-git --yes`, then `npm i astro@7.4.2 @astrojs/mdx@8.1.1 @astrojs/preact@6.1.1 preact` and `npm i -D vitest jsdom @testing-library/preact`. Config: `output: 'static'`, integrations mdx + preact, `build.inlineStylesheets: 'always'`, `vite.server.fs.allow: ['..']` (raw imports from `../harness`).

- [ ] **Step 2: Write failing `tests/schemas.test.ts`**: valid lesson parses; `answer: 3` with 3 options rejected; 1-item quiz rejected; slug `Loop!` rejected; planned track with `subsections: ["MLOps"]` parses; `status: "draft"` rejected.

- [ ] **Step 3: Run** `npm test` — Expected: FAIL (module not found).

- [ ] **Step 4: Implement** `schemas.ts`, `content.config.ts` (tracks: `glob({ pattern: "*.yaml", base: "src/content/tracks" })`; lessons: `glob({ pattern: "**/*.mdx", base: "src/content/lessons" })`), and the six YAML files with ids, order 1–6, titles and subsections exactly as spec §2's roadmap table; `harness` status `live`, others `planned`; one-sentence summaries.

- [ ] **Step 5: Run** `npm test && npm run build` — Expected: tests pass; build succeeds.

- [ ] **Step 6: Commit** — `feat(app): astro scaffold, content schemas, track roadmap`.

---

### Task 4: Code-section extractor

**Files:** Create `app/src/lib/regions.ts`, `app/tests/regions.test.ts`

**Interfaces:**
- Produces: `extractSection(source: string, n: number): string` — returns the lines from the marker `^(#|//) ── n\.` up to (not including) the next `── ` marker line or EOF, trailing blank lines trimmed; throws `Error("Section n not found")`. `listSections(source: string): { n: number, title: string }[]`.

- [ ] **Step 1: Failing tests**: on the real files (read with `fs` from `../harness/...`), `listSections` returns n 1..9 for both and titles match between them; `extractSection(py, 8)` starts with `# ── 8. The agent loop` and contains `def run_agent`; `extractSection(go, 5)` contains `func executeTools`; `extractSection(py, 10)` throws `Section 10 not found`.
- [ ] **Step 2: Run** `npm test -- regions` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `feat(app): code section extractor`.

---

### Task 5: Progress store

**Files:** Create `app/src/lib/progress.ts`, `app/tests/progress.test.ts`

**Interfaces:**
- Produces: `createProgressStore(storage?: Pick<Storage, "getItem" | "setItem"> | null)` → `{ isDone(id: string): boolean, markDone(id: string): void, bestScore(id: string): number | null, recordScore(id: string, correct: number, total: number): void, percent(ids: string[]): number, subscribe(fn: () => void): () => void }`. Default argument resolves `window.localStorage` inside try/catch, else `null` (memory only). Persists JSON `{ done: string[], scores: Record<string, number> }` under `aie:progress:v1`; score stored as fraction 0–1, best kept. `recordScore` with `correct === total` also marks done. `percent` rounds to an integer.

- [ ] **Step 1: Failing tests**: mark/read round trip through a fake storage; best score kept (0.5 then 1 then 0.5 → 1); full score marks done; `percent(["a","b","c"])` after one done → 33; storage whose `getItem` returns `"{not json"` → empty progress, no throw; storage whose methods throw → works in memory; `subscribe` callback fires on `markDone`.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** — `feat(app): progress store`.

---

### Task 6: Simulator engine and transcript validator

**Files:** Create `app/src/lib/simulator.ts`, `app/tests/simulator.test.ts`

**Interfaces:**
- Produces:
```ts
export type TextBlock = { type: "text"; text: string };
export type ToolUse = { type: "tool_use"; id: string; name: "list_files" | "read_file" | "write_file"; input: Record<string, string> };
export type ToolResult = { type: "tool_result"; tool_use_id: string; content: string; is_error: boolean };
export type Turn = { blocks: (TextBlock | ToolUse)[]; stop_reason: "tool_use" | "end_turn"; branches?: { denied?: Turn[] } };
export type Scenario = { id: string; title: string; task: string; workspace: Record<string, string>; turns: Turn[] };
export type ApiMessage = { role: "user" | "assistant"; content: string | (TextBlock | ToolUse | ToolResult)[] };
export type Policy = "ask-writes" | "allow-all";
export type SimState = { messages: ApiMessage[]; workspace: Record<string, string>; pending: ToolUse | null;
  done: boolean; stopReason: Turn["stop_reason"] | null; turn: number; tokens: number };
export function createSimulator(s: Scenario, policy: Policy): {
  state(): SimState; step(): SimState; approve(): SimState; deny(): SimState; runToEnd(): SimState; reset(policy?: Policy): SimState };
export function runFakeTool(ws: Record<string, string>, call: ToolUse): { content: string; is_error: boolean };
export function validateTranscript(messages: ApiMessage[]): string[];
export function estimateTokens(messages: ApiMessage[]): number; // ceil(JSON.stringify(messages).length / 4)
```
- Semantics: initial state has one user message (`s.task`). Each `step()` performs exactly one event: (a) append the next assistant turn, or (b) after a `tool_use` turn, append one user message holding every tool result in call order. A `write_file` under `ask-writes` sets `pending` and `step()` returns state unchanged until `approve()`/`deny()`; `approve()` runs all calls of that turn; `deny()` gives that call `{content: "The user denied this action.", is_error: true}`, runs the others, and switches the remaining turns to that turn's `branches.denied`. `runToEnd()` steps until `done` or `pending`. `done` after appending an `end_turn` turn. Fake tools mirror the harness: `list_files` (sorted names under the path, `"(empty directory)"`), `read_file` (missing → `FileNotFoundError: <path>`, is_error), `write_file` (mutates the state's workspace copy, returns `Wrote N characters to <path>`); paths containing `..` or starting with `/` → `PermissionError: <path> is outside the workspace`.
- `validateTranscript` rules (each violation one string): first message is user; roles alternate; every assistant `tool_use` id appears as a `tool_result.tool_use_id` in the immediately following user message, and that message contains only tool results; no `tool_result` without a matching preceding `tool_use`.

- [ ] **Step 1: Failing tests** with an inline two-turn scenario: step-by-step message counts (1 → 2 → 3 → 4, done); `ask-writes` pending blocks `step()` and `runToEnd()` (state.pending still set, messages unchanged); `reset()` clears pending and restores workspace; `deny()` produces the denial result and follows `branches.denied`; `allow-all` never sets pending; path traversal returns the PermissionError result; `validateTranscript` returns `[]` for a good transcript and a non-empty list for: two consecutive assistants, a missing tool_result, a stray tool_result.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** — `feat(app): loop simulator engine`.

---

### Task 7: Simulator scenarios

**Files:** Create `app/src/data/scenarios/index.ts` (exports `scenarios: Record<"parallel-read" | "approve-write" | "recover-error", Scenario>`), `app/tests/scenarios.test.ts`

**Interfaces:** Consumes `Scenario`, `createSimulator`, `validateTranscript` from Task 6.

- [ ] **Step 1: Failing test**: for every scenario × policy (`ask-writes`, `allow-all`) × decision (approve, deny when pending), driving `step/approve/deny` to `done` yields `validateTranscript(...) === []` and `done === true`. Plus: `parallel-read` has a turn with two `tool_use` blocks; `approve-write` final workspace `README.md` lacks the typo when approved and is unchanged when denied; `recover-error`'s first tool result has `is_error: true`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Write the three scenarios** per spec §6 with realistic short files (`notes.md`, `README.md` containing the typo `recieve`, a workspace without `config.yaml` but with `config.example.yaml`). Model text is brief and plausible; final turns are `end_turn`.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `feat(app): simulator scenarios`.

---

### Task 8: Compaction math

**Files:** Create `app/src/lib/compaction.ts`, `app/tests/compaction.test.ts`

**Interfaces:**
- Produces: constants `BUDGET = 150_000`, `WINDOW = 1_000_000`, `SYSTEM_TOKENS = 800`, `TOOL_SCHEMA_TOKENS = 1_100`, `SUMMARY_TOKENS = 3_000`, `TURN_SIZES = { short: 1_500, file: 25_000, tools: 8_000 }`; `type TurnKind = keyof typeof TURN_SIZES`; `type MeterState = { history: number[]; summary: number | null; compactions: number }`; `initialMeter(): MeterState`; `total(s: MeterState): number`; `addTurn(s: MeterState, kind: TurnKind): { state: MeterState; compacted: boolean }` — appends the turn; if `total > BUDGET` the returned state has `history: []`, `summary: SUMMARY_TOKENS`, `compactions + 1`, and `compacted: true`.

- [ ] **Step 1: Failing tests**: `total(initialMeter()) === 1_900`; five `file` turns → no compaction (126_900); sixth → compacted, total 4_900, compactions 1; inputs are not mutated.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** — `feat(app): context meter math`.

---

### Task 9: Diagram model and diagram data

**Files:** Create `app/src/lib/diagram.ts`, `app/src/data/diagrams/{harness-flow,request-response,tool-roundtrip}.ts`, `app/tests/diagrams.test.ts`

**Interfaces:**
- Produces:
```ts
export type Role = "user" | "harness" | "model" | "tool" | "memory" | "logs";
export type DNode = { id: string; label: string; role: Role; x: number; y: number; w?: number; h?: number };
export type DEdge = { id: string; from: string; to: string; label?: string; loop?: boolean };
export type DStep = { n: number; highlight: string[]; caption: string; code?: { file: "python" | "go"; section: number } };
export type Diagram = { id: string; title: string; width: number; height: number; nodes: DNode[]; edges: DEdge[]; steps: DStep[] };
export function validateDiagram(d: Diagram): string[];
```
- `validateDiagram`: ids unique; edge endpoints exist; highlights reference existing node or edge ids; steps numbered 1..k consecutively; nodes inside `width × height`; every `code.section` in 1..9.
- `harness-flow`: the 10 steps from spec §5 with captions in plain language, nodes: You, Entry/REPL, Context builder, Model (Claude API), Router (stop_reason), Permission gate, Tool executor, Tools (workspace), Context manager, Trace log (side rail); edge 9→3 marked `loop: true`; code links: step 2 → section 6, 4 → 6, 5 → 8, 6 → 4, 7 → 5, 8 → 8, 9 → 7, 10 → 9. `request-response` (lesson 2) and `tool-roundtrip` (lesson 3) are 4–6 step diagrams.

- [ ] **Step 1: Failing tests**: all three diagrams validate to `[]`; `harness-flow` has exactly 10 steps and one loop edge; a hand-broken diagram (dangling edge, step gap) returns ≥2 errors.
- [ ] **Step 2–4:** run (FAIL), implement + data, run (PASS).
- [ ] **Step 5: Commit** — `feat(app): diagram model and harness flow data`.

---

### Task 10: App shell, design tokens, routing, home view

**Files:** Create `app/src/styles/tokens.css`, `app/src/layouts/AppShell.astro`, `app/src/lib/router.ts`, `app/src/components/Sidebar.tsx`, `app/src/components/Callout.astro`, `app/src/components/Term.astro`, `app/src/data/glossary.ts`, `app/src/components/Home.astro`; Modify `app/src/pages/index.astro`; Test `app/tests/router.test.ts`

**Interfaces:**
- Produces: `resolveView(hash: string, known: Set<string>): string` (strips `#`; returns the token if known, else `"home"`); `viewId(track: string, slug: string): string`; `Term` props `{ t: keyof typeof glossary }` (unknown key = TypeScript/build error); `Callout` props `{ kind: "note" | "warning" | "go" }`.
- `index.astro`: loads tracks and lessons, renders `<AppShell>` with `Home` in `<section data-view="home">` and each lesson's rendered MDX in `<section data-view={viewId(...)} hidden>` wrapped by the Lesson frame (title, goal, minutes; quiz slot added in Task 12). An inline module script shows the section for `resolveView(location.hash, known)` on load and on `hashchange`, scrolls to top, and sets `document.title` to `<Lesson title> · AI Engineering` (home: `AI Engineering`).
- Sidebar (Preact, `client:load`): tracks in order; live track expands to lesson links (`href="#harness-loop"`) with done ticks and track percent from the progress store; planned tracks show title, subsections and a `Planned` chip, no links. Collapses to a disclosure button under 760px.
- Home: track catalog + roadmap from the YAML data, a "Start the Harness track" link to the first lesson.
- `tokens.css`: the Global Constraints colour structure; tokens at minimum `--bg --surface --ink --muted --line --accent` plus role tokens `--role-user --role-harness --role-model --role-tool --role-memory --role-logs`; font tokens `--font-display --font-body --font-mono`; neutral greys biased toward the accent hue.

- [ ] **Step 1: Failing `router.test.ts`**: `resolveView("#harness-loop", known)` → `"harness-loop"`; `"#harness-nope"`, `"#settings"`, `""`, `"#"` → `"home"`; `viewId("harness","loop")` → `"harness-loop"`.
- [ ] **Step 2–4:** run (FAIL), implement router, run (PASS).
- [ ] **Step 5: Build the shell, tokens, Sidebar, Home, Callout, Term, glossary** (terms at least: `harness`, `token`, `context window`, `system prompt`, `stop_reason`, `tool_use`, `tool_result`, `JSON Schema`, `compaction`, `goroutine`).
- [ ] **Step 6: Verify** `npm run build` passes; `npm run dev` and a Playwright load of `/` at 400px and 1280px wide shows home with six tracks, five `Planned` chips, and `document.documentElement.scrollWidth <= innerWidth`.
- [ ] **Step 7: Commit** — `feat(app): shell, routing, sidebar, home`.

---

### Task 11: CodeTabs

**Files:** Create `app/src/components/CodeTabs.astro`; Test via build + `app/tests/codetabs.test.ts`

**Interfaces:**
- Produces: `<CodeTabs section={n} goNote="…" />` or `<CodeTabs whole goNote="…" />`. Imports `../../../harness/python/harness.py?raw` and `../../../harness/go/main.go?raw`, extracts with `extractSection`, renders both via `astro:components` `<Code>` with `themes={{ light: "github-light", dark: "github-dark" }}`. Tab buttons (`role="tab"`, `aria-selected`) toggle panels; all instances sync to the choice stored in `aie:lang` and broadcast via a `aie:lang` `CustomEvent` on `window`. Copy button uses `navigator.clipboard.writeText` inside the click handler, on rejection selects the code text; button text changes to `Copied` for 1.5s. `goNote` renders under the Go panel as a `Callout kind="go"` titled `How Go does it`.
- Shiki dual-theme CSS must switch with the same three-state theme selectors as `tokens.css`.

- [ ] **Step 1: Failing test** `codetabs.test.ts`: exports a pure helper `pickLang(stored: string | null): "python" | "go"` from `src/lib/lang.ts` — `null`/`"rust"` → `"python"`, `"go"` → `"go"`.
- [ ] **Step 2–4:** run (FAIL), implement helper + component, run (PASS).
- [ ] **Step 5: Verify** a scratch MDX page using `<CodeTabs section={8} />` builds and shows `def run_agent` / `func runAgent`; remove the scratch page.
- [ ] **Step 6: Commit** — `feat(app): python/go code tabs from real harness files`.

---

### Task 12: Quiz

**Files:** Create `app/src/components/Quiz.tsx`, `app/src/lib/quiz.ts`, `app/tests/quiz.test.tsx`; Modify the Lesson frame in `index.astro` to render `<Quiz client:visible lessonId quiz />` from frontmatter plus a `Mark lesson done` button.

**Interfaces:**
- Produces: `score(quiz: QuizItem[], picks: (number | null)[]): { correct: number; total: number; passed: boolean }` (`passed` = all correct). Component: one radio group per question; `Check answers` reveals per-question right/wrong with the explanation; `Try again` resets picks; calls `recordScore(lessonId, correct, total)`.

- [ ] **Step 1: Failing tests**: `score` cases (all right → passed; one null → not passed); render test: pick the correct option for each question, click `Check answers`, expect text `3 of 3 correct` and the store's `isDone` true.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** — `feat(app): lesson quiz`.

---

### Task 13: FlowDiagram

**Files:** Create `app/src/components/FlowDiagram.tsx`, `app/tests/flowdiagram.test.tsx`

**Interfaces:**
- Consumes: `Diagram` (Task 9); code text passed as props `codePython: Record<number, string>`, `codeGo: Record<number, string>` (sections used by the diagram's steps, extracted in a wrapper `FlowDiagram.astro` that imports the harness files and renders the island with `client:visible`).
- Produces: `<FlowDiagram.astro diagram="harness-flow" />` (keys: `harness-flow | request-response | tool-roundtrip`), optional `mode="replay"` (capstone: code panel open by default).
- Behaviour per spec §6: SVG `viewBox` from diagram size inside an `overflow-x: auto` container; nodes coloured by role token with visible text labels; numbered badges; status text `Step N of K`; Prev/Next/Play/Reset; clickable step list; ArrowLeft/ArrowRight when the figure has focus; Play advances every 2.2s and stops at the end; loop edge dash animation disabled under `prefers-reduced-motion`; `Show code` toggles the linked section (language follows `aie:lang`).

- [ ] **Step 1: Failing render tests**: initial `Step 1 of 10`; Next twice → `Step 3 of 10`; Reset → `Step 1 of 10`; Prev at step 1 stays 1; clicking step 7 in the list → `Step 7 of 10` and the caption of step 7 is shown.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** — `feat(app): step-through flow diagram`.

---

### Task 14: LoopSimulator

**Files:** Create `app/src/components/LoopSimulator.tsx`, `app/tests/loopsimulator.test.tsx`

**Interfaces:**
- Consumes: `createSimulator`, `scenarios`, `estimateTokens`.
- Produces: `<LoopSimulator client:visible scenario="parallel-read" | "approve-write" | "recover-error" showPolicy?: boolean />`; a scenario picker lets the learner switch.
- Layout per spec §6: label `Simulated — no real model is called`; conversation panel (user task, assistant text, tool calls as `name(args)` chips, results with error styling); `messages` JSON panel (`<pre>`, newest entry highlighted, `overflow:auto`); state strip (`Turn`, `stop_reason`, `≈ tokens`). Three columns ≥1100px, stacked below. Controls: Step, Run to end, Reset, policy switch (`Ask before write tools` / `Allow everything`, when `showPolicy`); pending approval renders an inline card with the call and `Approve` / `Deny`.

- [ ] **Step 1: Failing render tests**: `approve-write` with ask policy — Run to end stops with the approval card visible; Deny → final state shows the denial result text `The user denied this action.` and `end_turn`; Reset → back to 1 message and no card.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** — `feat(app): loop simulator component`.

---

### Task 15: ContextMeter

**Files:** Create `app/src/components/ContextMeter.tsx`, `app/tests/contextmeter.test.tsx`

**Interfaces:** Consumes Task 8 exports. Produces `<ContextMeter client:visible />`.

- Behaviour per spec §6: label `Illustrative token estimates`; horizontal stacked bar scaled to `BUDGET × 1.2` with a marked budget line and a note giving the 1M window; segments system / tools / summary / history with legend and numbers (`tabular-nums`); buttons `Add short reply`, `Add large file read`, `Add several tool calls`, `Auto-run 20 turns`, `Reset`; on `compacted`, the history visibly folds into the summary segment (CSS transition, none under reduced motion) and a message explains summarize-and-start-fresh and why history is never edited.

- [ ] **Step 1: Failing render test**: six clicks of `Add large file read` → text `Compactions: 1` and the explanation message visible.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** — `feat(app): context window meter`.

---

### Task 16: Static aids — Timeline and TraceView

**Files:** Create `app/src/components/Timeline.astro`, `app/src/components/TraceView.astro`, `app/src/data/sample-trace.jsonl`, `app/src/lib/trace.ts`, `app/tests/trace.test.ts`

**Interfaces:**
- Produces: `<Timeline />` — SVG comparing three tool calls (300ms, 1200ms, 500ms) run sequentially (2000ms) vs in parallel (1200ms) with a labelled ms axis drawn to one scale. `parseTrace(text: string): TraceEvent[]` where `TraceEvent = { ts: number; event: string } & Record<string, unknown>`, throwing on a non-JSON line. `<TraceView />` renders the sample as a table (time offset, event, key fields) with annotations for `model_response` stop reasons, `permission`, `tool_result` errors and `compact`.
- `sample-trace.jsonl`: one realistic session in the harness's exact event shapes (fields as emitted by `emit` in `harness.py` section 2/5/6/7/8): session_start, 3× model_request/model_response, one permission, four tool_result (one `is_error: true`), session_end.

- [ ] **Step 1: Failing test**: `parseTrace(sample)` returns ≥10 events, first `session_start`, last `session_end`, every event name in the harness event set from Task 1; `parseTrace("{bad")` throws.
- [ ] **Step 2–4:** run (FAIL), implement + data, run (PASS).
- [ ] **Step 5: Commit** — `feat(app): timeline and trace view aids`.

---

### Task 17: Lessons 1–3

**Files:** Create `app/src/content/lessons/harness/01-what.mdx`, `02-model.mdx`, `03-tools.mdx`; Test `app/tests/lessons.test.ts`

**Interfaces:** Consumes CodeTabs, FlowDiagram, Callout, Term, Quiz frontmatter shape.

- Each lesson follows spec §5: Goal → Concept → Aid → Code → How Go does it → Quiz (frontmatter, 2–3 items) → Try it. Audience: knows Python, new to LLM APIs — define every API term on first use with `<Term>`. Plain, direct sentences; no Python-basics digressions.
- 01 `what`: model vs harness; the components; `<FlowDiagram diagram="harness-flow" />`; no CodeTabs. Try it: install and run `harness.py` with one read-only task.
- 02 `model`: API key setup (`export ANTHROPIC_API_KEY`), messages/roles, system prompt, tokens, one call, response shape; `<FlowDiagram diagram="request-response" />`; `<CodeTabs section={1} />`, `<CodeTabs section={6} />`.
- 03 `tools`: tool calling, JSON Schema, `tool_use`/`tool_result`, the model never runs code; `<FlowDiagram diagram="tool-roundtrip" />`; `<CodeTabs section={3} />`. Go note: struct + `init()` registration vs decorator.

- [ ] **Step 1: Failing `lessons.test.ts`** (reads MDX files with `fs` + a frontmatter parse via `gray-matter` dev dependency, validates with `lessonSchema`): every lesson parses; `order` values are unique and consecutive from 1; slugs match filenames after the `NN-` prefix; every `<CodeTabs section={n}>` in a lesson has n in 1..9; every `<FlowDiagram diagram="x">` names an existing diagram.
- [ ] **Step 2: Run** — FAIL (no lessons).
- [ ] **Step 3: Write the three lessons.**
- [ ] **Step 4: Run** `npm test && npm run build` — PASS.
- [ ] **Step 5: Commit** — `content(harness): lessons 1-3`.

---

### Task 18: Lessons 4–6

**Files:** Create `04-loop.mdx`, `05-permissions.mdx`, `06-executor.mdx`

- 04 `loop`: while loop; route on `stop_reason` (`tool_use`, `end_turn`, `max_tokens`, `refusal`); append order; `tool_use_id`; many calls per turn in one user message; `MAX_TURNS`; `<LoopSimulator scenario="parallel-read" />`; `<CodeTabs section={8} />`.
- 05 `permissions`: approval gate, `safe_path`, denial as error result, why approvals are sequential; `<LoopSimulator scenario="approve-write" showPolicy />`; `<CodeTabs section={4} />` and the `safe_path` part of section 3 explained in prose.
- 06 `executor`: threads vs goroutines (the main language contrast), `context.WithTimeout`, errors as information (`recover-error`), output caps; `<Timeline />`; `<LoopSimulator scenario="recover-error" />`; `<CodeTabs section={5} />`.

- [ ] **Step 1: Write the three lessons** (same rules as Task 17).
- [ ] **Step 2: Run** `npm test && npm run build` — PASS (Task 17's lesson test now covers 6 lessons).
- [ ] **Step 3: Commit** — `content(harness): lessons 4-6`.

---

### Task 19: Lessons 7–9

**Files:** Create `07-context.mdx`, `08-reliability.mdx`, `09-capstone.mdx`

- 07 `context`: window vs budget, token growth per turn, `usage.input_tokens`, summarize-and-restart, the append-only rule; `<ContextMeter />`; `<CodeTabs section={7} />`.
- 08 `reliability`: SDK retries (`max_retries=4` / `option.WithMaxRetries(4)`), error-handling order, `trace.jsonl`; `<TraceView />`; `<CodeTabs section={2} />`, `<CodeTabs section={9} />`.
- 09 `capstone`: `<FlowDiagram diagram="harness-flow" mode="replay" />`; `<CodeTabs whole />`; run both harnesses (`python harness.py`, `go run .`); exercises as a checklist: add a `search_files` tool, switch `write_file` to allowed-without-asking and back, print a per-session token-cost readout using `usage` and the model's per-million-token prices ($4 input / $20 output).

- [ ] **Step 1: Write the three lessons.**
- [ ] **Step 2: Run** `npm test && npm run build` — PASS; lesson test sees orders 1..9.
- [ ] **Step 3: Commit** — `content(harness): lessons 7-9`.

---

### Task 20: Packaging, browser verification, publish

**Files:** Create `app/scripts/package-lib.mjs`, `app/scripts/package.mjs`, `app/tests/package.test.ts`; Modify `app/package.json` (`package` script), create `README.md` at repo root (how to run harnesses, dev server, tests, package).

**Interfaces:**
- Produces: `transformHtml(html: string): string` — returns the contents of `<head>` (with `<meta charset>` and `<meta name="viewport">` removed, `<title>` first) followed by the contents of `<body>`, with no `<!doctype>`, `<html>`, `<head>`, `<body>` tags, and every `/_astro/` reference rewritten to `_astro/`. `rewriteChunk(js: string): string` — rewrites `"/_astro/` and `'/_astro/` to `"./` / `'./`. `findBlockedUrls(text: string): string[]` — absolute `http(s)://` URLs in `src=`, `href=`, `url(`, `import(`/`from` positions whose host is not in the Global Constraints allowlist (plain `<a href>` links to other sites are allowed and excluded).
- `package.mjs`: `dist/` → `publish/` (clean), applies the above to `index.html` and `_astro/*.js`, copies other assets, exits 1 listing any blocked URL, and adds `<meta name="claude:viewport-fit-sides" content="cover">` after `<title>` if absent.

- [ ] **Step 1: Failing `package.test.ts`**: a fixture HTML with doctype, head (charset, viewport, title, style, module script `/_astro/a.js`), body with `<astro-island component-url="/_astro/C.js">` → output has none of the wrapper tags, starts with `<title>`, contains `component-url="_astro/C.js"`; `rewriteChunk('import("/_astro/x.js")')` → `import("./x.js")`; `findBlockedUrls('<script src="https://esm.sh/x"></script><link href="https://fonts.googleapis.com/css2">')` → `["https://esm.sh/x"]`; `<a href="https://docs.anthropic.com">` → `[]`.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Package** `npm run build && npm run package` — Expected: exits 0, `publish/index.html` has no `<html` tag.
- [ ] **Step 6: Browser check (Review Focus 1 and 5)** — wrap `publish/` in a minimal shell (`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><body>` + contents) at `scratch/a/b/index.html` with `_astro/` beside it, serve the scratch root with `python3 -m http.server`, and with Playwright: open `/a/b/#harness-loop` directly → lesson 4 visible; click sidebar link to lesson 5 → simulator `Run to end` shows the approval card; open `/a/b/#nope` → home; console has no errors; 400px width → no horizontal scroll; repeat with `prefers-color-scheme: dark` and confirm text/background contrast visibly holds.
- [ ] **Step 7: Publish** with the Artifact tool: `file_path` `app/publish/index.html`, `files` mapping every `publish/_astro/*` path, `icon: "graduation"` (first publish only), description `An interactive masterclass on building AI agent harnesses, with Python and Go side by side.` Record the returned URL in `README.md`.
- [ ] **Step 8: Optional live smoke test — only if the user says yes (costs money).** With `ANTHROPIC_API_KEY` set, run each harness once on `List the files in the workspace` (with one file in `workspace/`); expected: one `list_files` call and a final answer, `trace.jsonl` ending in `session_end`.
- [ ] **Step 9: Commit** — `feat(app): packaging for page host; publish`.
