# LLMs Track Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the LLMs track (Tuning, Evaluation, Judging): a ticket-triage eval toolkit in Python and Go, a LoRA script, real recorded data, nine interactive aids and ten lessons.

**Architecture:** One teaching-sized file per language (`llms/python/triage_lab.py`, `llms/go/main.go`) with 9 numbered sections that lessons quote, mirroring the harness. Prompts, rubric, tickets and saved responses are data files under `llms/data/` that both languages and the app read, so scores agree everywhere. The app loads that data at build time; aids show only recorded numbers.

**Tech Stack:** Python 3 + `anthropic` SDK, pytest; Go 1.27 + `anthropic-sdk-go` v1.84.0; Astro 7 + Preact + Vitest (existing app); `transformers` + `peft` for LoRA (Colab).

**Spec:** `docs/superpowers/specs/2026-10-25-ai-engineering-llms-design.md`

## Global Constraints

- Every lesson follows `docs/STYLE.md`; `npm test` must pass (section order, no `TODO`, glossary terms, colors only in `tokens.css`, valid UTF-8).
- Models: task `claude-haiku-5-5`, judge `claude-opus-5-5`; comparison also uses `claude-sonnet-5-5`. Prices per MTok input/output: Haiku 5.5 $0.10/$0.50, Sonnet 5.5 $2/$10, Opus 5.5 $4/$20.
- No `temperature`/`top_p`/`top_k` anywhere (current models reject them). Effort goes in `output_config.effort`; `null` effort means "omit it".
- Structured output via `output_config.format = {"type": "json_schema", "schema": ...}`. No forced `tool_choice` (400 on Opus/Sonnet 5.5).
- Default mode is `replay`. No test calls the API. A replay miss raises; it never falls back to a live call.
- Any paid run prints `estimate_cost` first, requires confirmation, and stops at `--max-cost`.
- Saved responses come only from real API runs. Test fixtures live in `llms/data/fixtures/` and are named as fixtures.
- The paid recording run (Task 6) requires the user's explicit approval of the printed estimate in chat. Stop and ask.
- Never commit `.vscode/`. Stage explicit paths. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Cross-language key drift:** Python and Go must compute the same cache key for the same request, including non-English ticket text. Pinned by a shared test vector in `expected_scores.json` (Tasks 2, 7).
2. **Score drift between Python, Go and the app:** the same responses must give the same accuracy, kappa and noise band in all three. Pinned by `expected_scores.json` (Tasks 3, 4, 7, 9).
3. **A stale replay after a prompt edit:** editing a prompt's text without renaming it must miss, not replay old answers. Pinned by including prompt text in the key (Task 2).
4. **Repeats collapsing into one answer:** three repeats of a config must be three distinct saved responses. Pinned by `rep` in the key (Task 2).
5. **Aids inventing numbers before a recording exists:** with an empty `responses/`, every data-driven aid shows the "No recorded run yet" state (Task 9).

---

## Shared data contracts (used by Tasks 1–10)

**`llms/data/prompts.json`**
```json
{
  "triage": {"v1_basic": "<system prompt>", "v2_rules": "...", "v3_examples": "..."},
  "judge": "<judge system prompt with {rubric} placeholder>",
  "rubric": [
    {"id": "addresses_issue", "text": "The reply addresses the customer's actual issue."},
    {"id": "tone", "text": "The tone fits the customer's mood and the issue's severity."},
    {"id": "no_false_promises", "text": "The reply makes no promise the company cannot keep (refunds, dates, fixes)."}
  ]
}
```

**Ticket line** (`tickets.jsonl`): `{"id":"t001","text":"...","category":"billing","priority":"high","tags":["angry_minor"],"split":"dev"}`

**Reply label line** (`reply_labels.jsonl`): `{"id":"r001","ticket_id":"t004","reply":"...","labels":{"addresses_issue":true,"tone":true,"no_false_promises":false}}`

**Cache key:** `sha256` hex of canonical JSON of
`{"kind","model","effort","prompt_version","prompt_text","input","rep"}` (triage: `input` = ticket text; judge: `prompt_version` = `"before"|"after"` order, `input` = `{"ticket": text, "reply": reply}`). Canonical = keys sorted, separators `,` and `:`, no HTML escaping, UTF-8 not `\u`-escaped. Python: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. Go: `map[string]any`, `json.Encoder` with `SetEscapeHTML(false)`, trailing newline trimmed.

**Saved response line:** `{"key","config":{kind,model,effort,prompt_version,rep},"case_id","output","stop_reason","input_tokens","output_tokens","latency_ms","error"}`. Triage `output` = `{"category","priority","reply"}`; judge `output` = `{"addresses_issue":{"pass":bool,"reason":str},"tone":{...},"no_false_promises":{...}}`; `output` is `null` when `stop_reason` is `refusal`/`max_tokens` or `error` is set.

**Response file name:** `responses/{kind}__{model}__{prompt_version}__{effort or "default"}.jsonl` (all reps in one file).

**`llms/data/fixtures/`:** `tickets.jsonl` (8 tickets covering 4 categories, both splits, one non-English), `responses/` (hand-made triage lines for `v1_basic` and `v2_rules`, 2 reps of `v2_rules`, one refusal, one error; judge lines for 4 labels in both orders), `reply_labels.jsonl` (4 lines), `expected_scores.json` (Task 3 defines its fields; Tasks 4 and 9 extend it).

---

### Task 1: Python lab skeleton, dataset and tickets

**Files:**
- Create: `llms/python/triage_lab.py` (sections 1–2 plus empty marked headers for 3–9), `llms/python/requirements.txt` (`anthropic`), `llms/python/tests/conftest.py`, `llms/python/tests/test_dataset.py`
- Create: `llms/data/tickets.jsonl`, `llms/data/prompts.json`, `llms/data/fixtures/tickets.jsonl`, `llms/data/responses/.gitkeep`

**Interfaces — Produces:** `TASK_MODEL`, `JUDGE_MODEL`, `PRICES: dict[str, tuple[float, float]]`, `DATA_DIR: Path`, `CATEGORIES`, `PRIORITIES`, `load_jsonl(path) -> list[dict]` (raises `ValueError("<file>:<line>: <reason>")`), `load_tickets(path=...) -> list[dict]`, `split(tickets, name) -> list[dict]`, `load_prompts(path=...) -> dict`.

- [ ] **Step 1: Write the tickets.** 60 synthetic "Lumen" (cloud notes app) tickets, ids `t001`–`t060`, ~40 `dev` / ~20 `test`, each of the 6 categories ≥ 6 times, ≥ 15 tagged with spec §5 edge tags, at least 2 `non_english`, 2 `injection`. Write `prompts.json` with three triage prompts of increasing quality (`v1_basic`: one line; `v2_rules`: category and priority definitions; `v3_examples`: v2 plus 3 short worked examples not drawn from the test split), the judge prompt and rubric.
- [ ] **Step 2: Write failing tests** in `test_dataset.py`:
  - `test_real_tickets_are_well_formed`: 60 tickets, unique ids, every `category` in `CATEGORIES`, every `priority` in `PRIORITIES`, `split` in `{dev,test}`, each category ≥ 6, edge-tagged ≥ 15.
  - `test_split_counts`: `len(split(t,"dev")) + len(split(t,"test")) == len(t)` and test ≥ 18.
  - `test_malformed_line_names_file_and_line`: a temp file whose 2nd line is `{bad` → `ValueError` whose message contains the file name and `:2:`.
  - `test_prompts_have_three_versions_and_rubric`: keys `v1_basic,v2_rules,v3_examples`; rubric ids `addresses_issue,tone,no_false_promises`; judge prompt contains `{rubric}`.
  - `test_examples_do_not_leak_test_tickets`: no test-split ticket text appears inside `v3_examples`.
- [ ] **Step 3: Run** `cd llms/python && pytest -q` → FAIL (missing functions).
- [ ] **Step 4: Implement** sections 1–2 of `triage_lab.py`, header comment in the harness's style, section markers `# ── N. Title ───…` for all 9 (3–9 empty for now).
- [ ] **Step 5: Run** `pytest -q` → PASS.
- [ ] **Step 6: Commit** `feat(llms): add triage lab skeleton, tickets and prompts`.

### Task 2: The task and saved-response model call (sections 3–4)

**Files:** Modify `llms/python/triage_lab.py`; Create `llms/python/tests/test_call.py`, fixture response files.

**Interfaces — Produces:**
- `TRIAGE_SCHEMA: dict` (JSON Schema: `category` enum, `priority` enum, `reply` string; all required; `additionalProperties: false`), `JUDGE_SCHEMA` (each rubric id → `{pass: bool, reason: string}`).
- `cache_key(kind, model, effort, prompt_version, prompt_text, input, rep) -> str`
- `response_path(kind, model, prompt_version, effort, root=RESPONSES_DIR) -> Path`
- `class ReplayMiss(Exception)` — message names kind, model, prompt version, case id and the command `python triage_lab.py record ...`.
- `call_model(params: dict, *, key, config, case_id, mode=MODE, client=None, root=RESPONSES_DIR) -> dict` (a saved-response line). `replay`: read file, return the line with matching key or raise `ReplayMiss`. `record`: call `client.messages.create(**params)`, append the line, return it. `live`: call without saving. Parses the first text block as JSON into `output`; sets `output=None` on `refusal`/`max_tokens`; on `anthropic.APIError` after retries sets `error=str(e)`.
- `triage(ticket, variant, *, model=TASK_MODEL, effort=None, rep=0, mode=MODE, client=None, root=...) -> dict` builds params (`system` = prompt text, one user message = ticket text, `max_tokens=2000`, `output_config` with `format` and, when set, `effort`) and calls `call_model`.

- [ ] **Step 1: Failing tests** in `test_call.py`:
  - `test_key_is_stable_and_order_independent`: same inputs → same 64-hex key; matches the `key_vector` in `fixtures/expected_scores.json`. Create that file in this step with the inputs of one non-English triage request and its key, computed independently with `hashlib` on the hand-written canonical string, so Go (Task 7) and the app can check against it.
  - `test_key_changes_with_prompt_text_and_rep`: changing `prompt_text` or `rep` changes the key.
  - `test_replay_hit_returns_saved_line` / `test_replay_miss_raises_with_record_hint` (message contains `record`).
  - `test_record_appends_and_replay_then_hits` with a fake client returning a text block of valid JSON; file has one line; second call in `replay` mode returns it without calling the client.
  - `test_refusal_and_max_tokens_have_null_output` and `test_api_error_is_recorded` with fake clients.
  - `test_triage_params_have_no_sampling_and_omit_null_effort`: capture params; no `temperature`; `output_config` has `format` and no `effort` when `effort=None`, `effort: "low"` when set.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** sections 3–4. **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(llms): add triage task and saved-response model calls`.

### Task 3: Code graders and reporting (sections 5 and 9)

**Files:** Modify `triage_lab.py`; Create `llms/python/tests/test_grading.py`; Create `llms/data/fixtures/expected_scores.json`.

**Interfaces — Produces:**
- `grade(ticket, line) -> dict` → `{"case_id","category_ok","priority_ok","both_ok","outcome"}`, `outcome` ∈ `ok|refusal|max_tokens|error`; non-`ok` outcomes have all `*_ok` false.
- `accuracy(grades, field) -> float` over `outcome == "ok"` plus `refusal`/`max_tokens` (counted wrong); `error` cases excluded.
- `confusion_matrix(pairs) -> dict[str, dict[str, int]]` (expected → predicted; predicted `"(none)"` for non-ok).
- `noise_band(n, reps=1) -> float` = `1 / sqrt(n * reps)`.
- `report(grades, reps=1) -> dict` → `{"n","errors","category","priority","both","band"}`.
- `compare(grades_a, grades_b) -> dict` → `{"a","b","diff","band","fixed":[ids],"broken":[ids],"significant": abs(diff) > band}` on `both_ok`.
- `gate(grades, baseline_grades, *, minimum=0.7) -> dict` → `{"passed","reasons":[...]}`; fails if `both` < `minimum` or `both` < baseline `both` − band.

- [ ] **Step 1: Extend `expected_scores.json`** (Task 2 created it with `key_vector`) by hand from the fixtures: `key_vector`, per config `{category, priority, both, errors, n}`, `compare_v1_v2 {diff, fixed, broken}`, `band_8_1`. Values computed by hand, not by the code under test.
- [ ] **Step 2: Failing tests** in `test_grading.py`: each function against the fixtures and `expected_scores.json` (exact equality, floats `pytest.approx`); `test_error_cases_excluded_from_accuracy`; `test_gate_fails_on_drop_beyond_band` and `test_gate_passes_within_band`.
- [ ] **Step 3–4:** implement, run → PASS. **Step 5: Commit** `feat(llms): add code graders, report, compare and gate`.

### Task 4: LLM judge and agreement (sections 6–7)

**Files:** Modify `triage_lab.py`; Create `llms/python/tests/test_judge.py`, `llms/data/reply_labels.jsonl` (30 lines), `llms/data/fixtures/reply_labels.jsonl`; extend `expected_scores.json` with `agreement` and `kappa` per criterion and `position_flips`.

**Interfaces — Produces:**
- `judge(ticket_text, reply, *, order="after", model=JUDGE_MODEL, mode=..., client=None, root=...) -> dict` (saved line). `order="before"` puts the rubric before the reply in the user message, `"after"` after it. `system` = judge prompt with rubric filled in.
- `verdicts(line) -> dict[str, bool | None]` (None when output is null).
- `agreement(judge_v, human_v, criterion) -> float`, `cohen_kappa(a: list[bool], b: list[bool]) -> float` (`(po − pe)/(1 − pe)`; when `pe == 1` return `1.0` if `po == 1` else `0.0`), `position_flips(before_v, after_v) -> int` per criterion.

- [ ] **Step 1: Draft `reply_labels.jsonl`:** 30 replies to dev tickets, varied quality, roughly half failing at least one criterion, labelled by the rubric. Create `llms/data/README.md` stating the labels are Claude-drafted and pending the learner's review (JSONL has no comments).
- [ ] **Step 2: Failing tests:** `test_cohen_kappa_known_values` (perfect agreement → 1.0; `[T,T,F,F]` vs `[T,F,T,F]` → 0.0; a hand-computed 0.5 case), `test_kappa_degenerate_all_same`, `test_agreement_and_flips_match_expected` (fixtures), `test_judge_order_changes_message_and_key` (fake client captures params), `test_judge_schema_has_every_rubric_id`.
- [ ] **Step 3–4:** implement, run → PASS. **Step 5: Commit** `feat(llms): add LLM judge and judge agreement`.

### Task 5: Runner, cost and CLI (sections 8–9 CLI)

**Files:** Modify `triage_lab.py`; Create `llms/python/tests/test_runner.py`.

**Interfaces — Produces:**
- `EST_TOKENS = {"triage": (900, 700), "judge": (1400, 900)}` (input, output incl. thinking).
- `call_cost(model, input_tokens, output_tokens) -> float`; `estimate_cost(kind, model, n_calls) -> float`.
- `run(cases, fn, *, reps=1, workers=8, max_cost=None) -> list[dict]` — `fn(case, rep) -> line`; thread pool; keeps input order; sums actual cost and raises `CostLimit` (with the lines so far) when `max_cost` is exceeded.
- `RECORDING_PLAN: list[dict]` — the spec §5 table as configs.
- CLI (`argparse`): `run --prompt --model --effort --split --reps --mode`, `compare --prompt --vs --split`, `judge --prompt --split`, `agreement`, `gate --prompt --baseline --split`, `record --plan all|<name> --max-cost`. Paid modes print the estimate and require `--yes` or an interactive `y`.

- [ ] **Step 1: Failing tests:** `test_call_cost_uses_prices` (Haiku 1M in + 1M out = 0.60), `test_estimate_matches_formula`, `test_run_keeps_order_with_slow_cases` (fake fn sleeps in reverse), `test_run_stops_at_max_cost` (raises `CostLimit`, partial lines present), `test_recording_plan_estimate_under_10` (sum of plan estimate < 10.0), `test_cli_replay_run_prints_report` (fixtures root via `--data` option; captures stdout; contains accuracy and band), `test_paid_mode_refuses_without_confirmation`.
- [ ] **Step 2–4:** implement, run full `pytest -q` → PASS. **Step 5: Commit** `feat(llms): add runner, cost estimate and CLI`.

### Task 6: Recording run (paid — requires user approval)

**Files:** Create `llms/data/responses/*.jsonl`, `llms/data/README.md` (what each file is, when and how it was recorded, total cost).

- [ ] **Step 1:** `cd llms/python && python triage_lab.py record --plan all --max-cost 10` without `--yes`, capture the printed estimate, and **stop: ask the user in chat to approve that figure.** Do not continue without a clear yes.
- [ ] **Step 2:** With approval and `ANTHROPIC_API_KEY` set by the user, run with `--yes`. Record the actual total cost printed.
- [ ] **Step 3:** `python triage_lab.py run --prompt v2_rules --split dev` (replay) prints a report with zero replay misses for every plan config; `pytest -q` still passes.
- [ ] **Step 4: Commit** `data(llms): record triage and judge responses` with the actual cost in the message body.

### Task 7: Go port

**Files:** Create `llms/go/go.mod` (module `triage-lab`, `anthropic-sdk-go v1.84.0`), `llms/go/main.go`, `llms/go/main_test.go`.

**Interfaces:** the same 9 sections and behaviour as Python, Go-idiomatic names (`cacheKey`, `callModel`, `grade`, `accuracy`, `confusionMatrix`, `noiseBand`, `report`, `compare`, `gate`, `judge`, `cohenKappa`, `positionFlips`, `run`, `estimateCost`). Model calls go through a `modelCaller` interface so tests use a fake. Runner: goroutines bounded by a semaphore of 8, results written by index, `sync.WaitGroup`. Use the SDK's `OutputConfig` (`Effort`, `Format`); if `Format` is missing in v1.84.0, set it with `option.WithJSONSet("output_config.format", ...)` and note it in a comment.

- [ ] **Step 1: Failing tests** in `main_test.go`: `TestCacheKeyMatchesPython` (the `key_vector`), `TestScoresMatchExpected` (every config in `expected_scores.json`), `TestKappaAndFlipsMatchExpected`, `TestReplayMissMessage`, `TestRunKeepsOrder`, `TestRunStopsAtMaxCost`, `TestNoSamplingParams`.
- [ ] **Step 2–4:** implement, `go vet ./... && go test ./...` → PASS. Also run `go run . run --prompt v2_rules --split dev` and confirm the report equals Python's for the recorded data.
- [ ] **Step 5: Commit** `feat(llms): add Go triage lab`.

### Task 8: LoRA script

**Files:** Create `llms/finetune/lora.py`, `llms/finetune/requirements.txt`, `llms/finetune/test_format.py`.

**Interfaces — Produces:** `to_example(ticket) -> {"prompt": str, "completion": str}` where completion is `{"category": ..., "priority": ...}` as compact JSON; `parse_completion(text) -> dict | None`; `main()` (Colab): load dev tickets, LoRA rank 8 / alpha 16 on `Qwen/Qwen2.5-0.5B-Instruct`, 3 epochs, generate on test split, write `predictions.jsonl` as saved-response lines so `grade` scores them. Heavy imports inside `main()` so the test needs only the standard library.

- [ ] **Step 1: Failing tests:** `test_example_completion_is_compact_json`, `test_parse_completion_handles_junk` (`"category: billing"` → None), `test_prediction_line_is_gradeable` (feed a fake prediction through `triage_lab.grade`).
- [ ] **Step 2–4:** implement, `python -m pytest llms/finetune -q` → PASS. **Step 5: Commit** `feat(llms): add LoRA fine-tuning script`.

### Task 9: App infrastructure

**Files:**
- Modify: `app/src/components/CodeTabs.astro` (`source: 'harness' | 'llms' | 'lora'`, `pythonOnly`; `lora` implies Python only), `app/src/components/FlowDiagram.astro` (add `eval-pipeline`; sources keyed by `source`), `app/src/lib/diagram.ts` (`Role` adds `dataset`, `grader`; `DStep.code` adds optional `source`), `app/src/styles/tokens.css` (`--role-dataset`, `--role-grader`, light and dark, ≥ 3:1 contrast on `--bg`), `app/tests/lessons.test.ts` (CodeTabs `section` must exist in the named source via `listSections`).
- Create: `app/src/data/diagrams/eval-pipeline.ts` (steps: 1 dataset, 2 task prompt, 3 model, 4 saved response, 5 code grader, 6 judge, 7 score, 8 report; code links to llms sections), `app/src/lib/llmsData.ts`, `app/tests/llmsdata.test.ts`.

**Interfaces — Produces (`llmsData.ts`):** pure functions mirroring Python (`grade`, `accuracy`, `confusionMatrix`, `noiseBand`, `report`, `compare`, `cohenKappa`, `agreement`, `positionFlips`, `callCost`) plus loaders `loadLlmsData(): LlmsData` built from `import.meta.glob('../../../llms/data/**/*.jsonl', { query: '?raw', import: 'default', eager: true })` and `prompts.json`. `LlmsData = { tickets, labels, prompts, runs: Run[], recorded: boolean }`; `Run = { config, lines }`. `recorded` is false when `responses/` has no JSONL.

- [ ] **Step 1: Failing tests:** `llmsdata.test.ts` computes every value in `expected_scores.json` from the fixture files (read with `node:fs`) and matches; `recorded` false for an empty set; `lessons.test.ts` new rule fails on `<CodeTabs source="llms" section={12} />` in a temp string check. Diagram validator passes for `eval-pipeline`.
- [ ] **Step 2–4:** implement, `npm test && npm run build` → PASS. **Step 5: Commit** `feat(app): add llms data loader, code sources, eval diagram and roles`.

### Task 10: Aids — Evaluation

**Files:** Create in `app/src/components/llms/`: `DatasetBrowser.tsx/.astro`, `ConfusionMatrix.tsx/.astro`, `EvalRunSim.tsx/.astro`, logic in `app/src/lib/llmsAids.ts`, tests `app/tests/llmsaids-eval.test.tsx`. Each `.astro` wrapper calls `loadLlmsData()` and passes only what the island needs; each island hydrates `client:visible`.

- `DatasetBrowser`: filters (category, priority, split, tag) as toggle buttons; list shows id, tags, first 140 chars; count line "12 of 60 tickets".
- `ConfusionMatrix` (`run` prop picks a recorded config): 6×7 grid (+ "(none)"), diagonal cells use `--good`, off-diagonal intensity by count; clicking a cell lists its tickets with the model's answer; keyboard-operable buttons.
- `EvalRunSim`: Step/Play/Reset like `LoopSimulator`; shows calls finishing in recorded latency order, running tokens and cost (`callCost`), then the 3 noise repeats with each score and the band.
- All three: when `recorded` is false, render "No recorded run yet" and the record command (except `DatasetBrowser`, which needs only tickets).

- [ ] **Step 1: Failing tests** for `llmsAids.ts` (`filterTickets`, `cellTickets`, `simFrames`) and render tests (filters change the count; clicking a cell lists the right ids; empty-data state text).
- [ ] **Step 2–4:** implement, `npm test && npm run build` → PASS; check both themes and 375px width in a browser. **Step 5: Commit** `feat(app): add evaluation aids`.

### Task 11: Aids — Judging

**Files:** `JudgeCard`, `AgreementView`, `LabelingPage` (`.tsx/.astro`) in `app/src/components/llms/`; logic added to `llmsAids.ts`; `app/tests/llmsaids-judge.test.tsx`.

- `JudgeCard`: picker over recorded judge cases; shows ticket, reply, three criteria with pass/fail chips and reasons.
- `AgreementView`: per criterion agreement % and kappa; a "Swap order" toggle switches judge verdicts between `after` and `before` runs and highlights flipped rows. Uses the learner's labels from browser storage (`aie:reply-labels`) when present, else `reply_labels.jsonl`, and says which.
- `LabelingPage`: one reply at a time, three pass/fail toggles prefilled from `reply_labels.jsonl`, saved to `aie:reply-labels` (try/catch); "Copy JSONL" fills a read-only textarea and copies it; progress "18 of 30 reviewed".

- [ ] **Step 1: Failing tests:** label merge (stored overrides file), JSONL export round-trips through the Python line shape, swap toggle flips the right rows, kappa shown equals `cohenKappa`.
- [ ] **Step 2–4:** implement, test, build, browser check. **Step 5: Commit** `feat(app): add judging aids`.

### Task 12: Aids — Tuning

**Files:** `PromptAB`, `CostQualityChart`, `LoraDiagram` in `app/src/components/llms/`; logic in `llmsAids.ts`; `app/tests/llmsaids-tune.test.tsx`. Load the `dataviz` skill before building the chart.

- `PromptAB`: two selects over recorded prompt versions; both scores with ± band; "difference is / is not larger than the noise band"; fixed and broken ticket lists.
- `CostQualityChart`: inline SVG scatter, x = cost per 1,000 tickets (log scale), y = `both` accuracy, one point per model/effort config, labelled; target accuracy slider draws a line and names the cheapest config above it. Text table fallback below for screen readers.
- `LoraDiagram`: matrix W (d×d) beside A (d×r) and B (r×d); rank slider 1–64 with d = 896 (Qwen2.5-0.5B hidden size); shows `2·d·r` vs `d²` trainable parameters and the percentage.

- [ ] **Step 1: Failing tests:** `cheapestAbove(points, target)`, `loraParams(896, 8)` = 14,336 vs 802,816, A/B verdict uses `compare().significant`.
- [ ] **Step 2–4:** implement, test, build, browser check. **Step 5: Commit** `feat(app): add tuning aids`.

### Task 13: Lessons 1–4 (Evaluation)

**Files:** `npm run new:lesson -- llms what "What an eval is"`, then `dataset "Building an eval set"`, `graders "Code graders"`, `runner "Running evals"`. Add new glossary terms (`eval`, `dev set`, `test set`, `grader`, `confusion matrix`, `noise band`, `structured output`) to `app/src/data/glossary.ts`.

Content per spec §6 table; numbers quoted in prose come from the recorded data (run the Python CLI in replay to get them). Each Try it uses replay mode and states the cost of a live run (from `estimate_cost`).

- [ ] **Step 1:** write the four lessons. **Step 2:** `npm test && npm run build` → PASS. **Step 3:** read each lesson in the browser, both themes. **Step 4: Commit** `content(llms): add evaluation lessons 1–4`.

### Task 14: Lessons 5–7 (Judging, prompt tuning)

**Files:** `judge "LLM as judge"`, `judge-check "Judging the judge"`, `prompt "Tuning the prompt"`; glossary: `LLM judge`, `rubric`, `Cohen's kappa`, `position bias`, `few-shot`.

Lesson 6 states plainly whether `reply_labels.jsonl` has been reviewed by the learner and links the LabelingPage.

- [ ] Steps as Task 13. **Commit** `content(llms): add judging and prompt-tuning lessons 5–7`.

### Task 15: Lessons 8–10, track live, publish

**Files:** `model "Tuning model and settings"`, `lora "Fine-tuning with LoRA"`, `capstone "Capstone: an eval gate"`; glossary: `effort`, `LoRA`, `fine-tuning`, `eval gate`; `app/src/content/tracks/llms.yaml` → `status: live`; `README.md` (LLMs section: run, test, record); `app/src/components/Home.astro` if it hard-codes the live track.

Lesson 9's results use illustrative numbers in a `Callout` titled "Illustrative numbers" until the learner runs Colab. Lesson 10's Try it runs `gate --prompt v1_basic --baseline v2_rules` and shows it failing.

- [ ] **Step 1:** write lessons; set track live. **Step 2:** `npm test && npm run build`, `pytest` in both Python dirs, `go test ./...` in both Go dirs → all PASS. **Step 3:** `npm run package`; browser check of every LLMs view in both themes and at 375px. **Step 4:** republish the artifact (same file path, map every `assets/*.js`). **Step 5: Commit** `content(llms): add lessons 8–10 and make the track live`.
