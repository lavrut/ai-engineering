# AI Engineering style guide

The standard every track and lesson follows, so the course reads and looks the same as it grows. Rules marked **(tested)** are checked by `npm test` in `app/`; a lesson that breaks one fails the build.

## Start from a template

| Making | Do this |
|---|---|
| A new track | Copy `app/templates/track.yaml` to `app/src/content/tracks/<id>.yaml` and fill it in. |
| A new lesson | `cd app && npm run new:lesson -- <track> <slug> "<Title>"` creates `src/content/lessons/<track>/NN-<slug>.mdx` from `app/templates/lesson.mdx` with the next number. |

Then replace every `TODO`. **(tested)** No `TODO` may remain in a lesson.

## Tracks

- One YAML file per track in `app/src/content/tracks/`. Its `id` matches the file name and the lesson folder.
- `status: planned` until the first lesson ships, then `live`. Planned tracks list their `subsections`.
- `summary` is one sentence ending with a period.
- Build one track at a time. A track ships when all its lessons pass the tests.

## Lesson anatomy

Every lesson has these H2 sections, in this order: **(tested)**

1. **Concept** explains the idea in plain words before any code.
2. **See it** holds an interactive aid or diagram. Say what to watch for before it and what it showed after it.
3. **The code** shows the real code, then walks through it as a bulleted list, one bullet per line or call that matters. Each bullet starts with the code in backticks.
4. **How Go does it** is optional. Include it only where the second language differs in a way worth teaching. For a track that pairs other languages, use `How <Language> does it`. **(tested)** It sits between The code and Try it.
5. **Try it** is something the reader runs on their own machine: the file to save, the command, and what they should see.

Use H3s inside a section when it runs long. No other H2s.

**Frontmatter (tested against `lessonSchema`):**

- `track`, `order` and `slug` match the path `lessons/<track>/NN-<slug>.mdx`, with `order` counting up from 1. **(tested)**
- `title` is sentence case and short: "Context management", not "Managing The Context Window".
- `goal` is one sentence starting with a verb, naming what the reader can do afterwards.
- `minutes` is an honest reading-and-trying estimate.
- `quiz` has 2 or 3 questions with 4 options each. Each question tests understanding, not recall of a number. Each `explanation` says why the answer is right.

## Writing

- Write for someone who knows Python and has never called an LLM API.
- Short sentences. One idea per paragraph. Active voice.
- Introduce a term once with `<Term t="..." />`; the key must exist in `app/src/data/glossary.ts`. **(tested)**
- Code identifiers, file names, commands and field names go in backticks.
- Name exact values (`MAX_TURNS = 25`, `20,000 characters`) rather than "a limit".
- Say what costs money. Any step that calls the API says so before the command.
- No marketing words ("powerful", "seamless", "simply").

## Components

Use these instead of hand-built HTML, so every lesson looks the same:

| Need | Component |
|---|---|
| Real code with Python/Go tabs | `<CodeTabs section={N} goNote="..." />` (N must be a numbered section of the source **(tested)**) or `<CodeTabs whole />`. `source` names an entry in `app/src/lib/codeSources.ts`: `"harness"` (default), `"llms"` (the eval lab), `"lora"`, `"llms-inspect"` (the lab rebuilt in Inspect, Python only, sections 1–7), or the ML files `"ml-train"`, `"ml-mlops"`, `"ml-mlflow"`, `"ml-serve"`, `"ml-monitor"`, `"ml-fetch"`, `"ml-llm"`, or the MCP files `"mcp-read"` (lumen-read, sections 1–6), `"mcp-ops"` (lumen-ops, 1–6; Python also has 7), `"mcp-client"` (1–6), `"mcp-auth"` (the resource-server checks, 1–3), all Python + Go with matching numbers, and the Python-only `"mcp-auth-server"` (the test AS, 1–5), `"mcp-app"` (ops_server.py section 7, the App), `"mcp-agent"` (1–9) and `"mcp-simulate"` (the simulated model, 1–4), or the AI Agents files `"agents-patterns"` (1–6) and `"agents-supervisor"` (1–6), Python + Go with matching numbers, and the Python-only `"agents-proxy"` (1–7), `"agents-langgraph"` (1–6), `"agents-sdk"` (1–9), `"agents-evals"` (scorers.py, 1–7) and `"agents-run-evals"` (run_evals.py, 1–8; the gate is 6). A source with no Go file shows only Python; `pythonOnly` hides the Go tab. `ml-serve` and `ml-monitor` number their Go sections differently, so they need `goSection={M}` too **(tested)** |
| A caveat or gotcha | `<Callout kind="note" \| "warning" \| "go" title="...">` |
| A glossary term | `<Term t="..." />` |
| A numbered flow | `<FlowDiagram diagram="..." />` (the name must exist in `src/data/diagrams/` **(tested)**) |
| A stepping loop | `<LoopSimulator client:visible scenario="..." />` |
| Other aids | `<ContextMeter client:visible />`, `<Timeline />`, `<TraceView />` |
| LLMs eval aids (`components/llms/`) | `<DatasetBrowser />`, `<ConfusionMatrix run="..." />`, `<EvalRunSim run="..." />`: .astro wrappers that read `llms/data` at build time; `run` is a response file name without `.jsonl` |
| LLMs judging aids (`components/llms/`) | `<JudgeCard initial="r001" />` (the 30 labelled replies; opt in to judged test replies with `groups={['labelled', 'v2_rules']}` or `groups={['v2_rules']}`, each adding ~25 KB), `<AgreementView />`, `<LabelingPage />`: judge lines found by cache key; the learner's reply labels live in localStorage `aie:reply-labels`, and AgreementView uses them when present |
| LLMs tuning aids (`components/llms/`) | `<PromptAB a="v1_basic" b="v2_rules" />` (Haiku prompt versions on dev, verdict from `compare().significant`), `<CostQualityChart prompt="v2_rules" />` (one point per recorded model/effort config, log-scale cost per 1,000 tickets, ± noise band, data table), `<LoraDiagram initial={8} />` (data-free, d = 896, rank 1–64) |
| LLMs framework aid (`components/llms/`) | `<FrameworkCompare />` (inspect_summary.json: the lab's value beside Inspect's for each run and metric, a match mark that is a symbol plus text, the standard error, and what the framework gives and what we still own; "No recorded run yet" plus the summarize command without the summary): .astro wrapper that reads `llms/data` at build time; logic in `lib/frameworkCompare.ts` |
| ML basics aids (`components/ml/`) | `<ComplaintExplorer />` (sample balance per year beside the real CFPB shares, split badges, report snippets), `<FeatureExplorer />` (tokens, unknown words, exact TF-IDF, label and top words per class from the production model's explorer slice; shows its disclosure note), `<TrainingStepper />` (training_log.json checkpoints, loss + penalty, top words), `<MetricsView />` (metrics.json: three models, train/test, band, confusion matrix): .astro wrappers that read `ml/` at build time; logic in `lib/mlAids.ts`; islands import only `mlExplore.ts`/`mlAids.ts` |
| MLOps aids (`components/ml/`) | `<ModelVsLlm />` (llm_compare.json: v1 vs the LLM on the same 200 complaints, gap against the band, per-class F1, cost, latency with both notes, the caveat and the Go server from latency.json; "No recorded run yet" plus the record command without the report), `<RunTable />` (ml/runs: sort by a metric, diff two runs, registry version per run), `<RegistryTimeline />` (registry.json history and stages, the gate's numbers, a labelled in-browser SIMULATION of promote-anyway and rollback), `<DriftChart />` (drift.json: monthly/quarterly PSI per signal with the calibrated threshold, null band and 0.2 convention, alert shapes, in-sample overlay, top shifted words, class shares, table view): same pattern as the basics aids |
| MCP aids (`components/mcp/`) | `<McpMessageView />` (messages.json: real JSON-RPC pairs from lumen-read, stepped), `<ToolBudget />` (tool_budget.json: tools and estimated tokens per server's `tools/list`, chars/4, labelled an estimate), `<ElicitationStepper />` (elicitation.json: label_ticket's two round trips, accept and decline, state shown as `<sealed state>`), `<AppPreview />` (app_snapshot.json: the real App HTML in a `sandbox="allow-scripts"` iframe with a CSP, the page plays the host and refuses tools/call), `<InjectionLab />` (injection.json: one row per real capstone recording, plus a separate table under a "Simulated runs" heading for the `simulated` block, each row badged "simulated" and its model marked "not a real model"; "No recorded run yet" plus the record commands without recordings): .astro wrappers that read `mcp/data/derived/` (written by `mcp/python/derive.py`, never by hand) at build time; logic in one module per aid group (`lib/mcpTypes.ts`, `mcpMessages.ts`, `mcpElicitation.ts`, `mcpAppPreview.ts`, `mcpInjection.ts`), re-exported by `lib/mcpAids.ts`; data loading in `lib/mcpData.ts` (build time only) |
| AI Agents aids (`components/agents/`) | `<PatternStepper patterns={['chain', 'route']} />` (patterns.json: a hand-built pattern's recorded run step by step; pattern, ticket and Python/Go pickers; Python-vs-Go table and call-count note), `<AgentTrace engines={['hand', 'hand-go']} />` (traces.json: a desk run step by step; desk, engine and ticket pickers; handoffs, refused writes and the t061 injection marked in words; totals, with the SDK's own estimate beside the ledger cost on SDK runs, and the scorer's row), `<CostCompare />` (cost_compare.json: single vs supervisor per engine, bars plus a table and the ratios read against the band), `<EngineScorecard />` (engine_scores.json: the gate verdict as written, success with its band against the 70% line, a table, per-ticket drill-down with the Claude Agent SDK's own cost estimate beside the ledger cost on SDK runs; `drillDown={false}` for a page that needs only the verdict and the table). Each shows "No recorded run yet" plus the record command for a run with `recorded: false`. A table of plain numbers needs no aid: lesson 7's recording-set table is MDX that reads `loadAgentsData().sets` at build time (`import { loadAgentsData } from '../../../lib/agentsData'`), and its numbers are pinned like any lesson's. The whole course is one page, so every lesson's props ship together: narrow each aid to what the lesson shows (the budget test in `tests/agentsaids-evals.test.tsx` measures them as Astro writes them). .astro wrappers that read `agents/data/derived/` at build time; logic in `lib/agentsAids*.ts` (one module per aid, re-exported by `lib/agentsAids.ts`); data loading in `lib/agentsData.ts` |

A new aid goes in `app/src/components/`, with its logic in `app/src/lib/` and a test in `app/tests/`.

## Visual design

- **Colors:** every color is a token in `app/src/styles/tokens.css`, with a light and a dark value. **(tested)** No hex colors anywhere else; use `var(--token)`.
  - Surfaces and text: `--bg`, `--surface`, `--surface-2`, `--ink`, `--muted`, `--line`.
  - Action and status: `--accent`, `--accent-ink`, `--good`, `--warn`, `--bad`.
  - Diagram roles, used the same way in every diagram: `--role-user`, `--role-harness`, `--role-model`, `--role-tool`, `--role-memory`, `--role-logs`, `--role-dataset`, `--role-grader`. Each has at least 3:1 contrast on `--bg` in both themes. **(tested)**
- **Type:** Bricolage Grotesque for headings (`--font-display`), IBM Plex Sans for body (`--font-body`), IBM Plex Mono for code and labels (`--font-mono`). Sizes come from the `--text-*` tokens.
- **Shape:** corners use `--radius`. Prose stays within `--prose-w`.
- **Themes:** readers pick Light, Dark or Auto in the sidebar. Check every new component in both themes and at phone width (375px).

## Code

- **Reference harnesses:** `harness/python/harness.py` and `harness/go/main.go` keep the same numbered sections, marked `# ── N. Title` and `// ── N. Title`. Lessons quote code by section number, so renumbering a section means updating the lessons.
- **LLMs lab:** `llms/python/triage_lab.py` and `llms/go/main.go` keep the same numbered sections 1–10; `llms/finetune/lora.py` (Python only) has sections 1–5; `llms/inspect/triage_inspect.py` (Python only) has sections 1–7. A `FlowDiagram` step links to one with `code.source`.
- **AI Agents:** `agents/desk/python/patterns.py` and `supervisor.py` keep the same numbered sections 1–6 as `agents/desk/go/patterns.go` and `supervisor.go`. The Python-only files number their own: the proxy's `agents/proxy/proxy.py` 1–5, `proxy_cost.py` 1, `proxy_handler.py` 1–2 and `proxy_server.py` 1, `agents/frameworks/langgraph_desk.py` 1–6, `sdk_desk.py` 1–9, `agents/evals/scorers.py` 1–7, `run_evals.py` 1–7 (the gate is 6) and `judge_set.py` 1.
- **Python:** standard library plus the official SDK, type hints, 4-space indent.
- **Go:** `gofmt` formatting, standard library plus the official SDK.
- **App:** TypeScript, 2-space indent, single quotes. Pure logic lives in `src/lib/` with tests; components stay thin.
- **Tests never call the API.** They use a fake model.

## Before you commit

```sh
cd app && npm test && npm run build
cd harness/python && pytest
cd harness/go && go test ./...
```
