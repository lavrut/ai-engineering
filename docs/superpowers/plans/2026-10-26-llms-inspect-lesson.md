# LLMs lesson 11 (the same eval with Inspect AI): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the LLMs triage eval (task, code graders, judge) in Inspect AI, answer from the saved responses through a replay provider, prove the numbers equal the hand-built lab's, and teach it in lesson 11.

**Architecture:** `llms/inspect/triage_inspect.py` imports the lab (`llms/python/triage_lab.py`) for its data, prompts, schemas and replay keys. It registers a `replay` model API that finds each saved answer by the lab's own key. `summarize.py` runs the four replay evals and writes `llms/data/inspect_summary.json`. The app reads only that file, in a new `FrameworkCompare` aid, next to a new `inspect-pipeline` diagram.

**Tech Stack:** Python 3.12, `inspect-ai==0.3.285` (venv only), pytest; Astro 7 + Preact + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-26-llms-inspect-lesson-design.md`

## Global Constraints

- `inspect-ai==0.3.285`, pinned exactly in `llms/inspect/requirements.txt`. Install it only in the venv `.venv-inspect/` at the repo root (git-ignored), never in the base Python.
- `llms/python/triage_lab.py` is imported, never edited. Its behaviour and tests stay unchanged.
- No API calls anywhere: not in the code paths run by the plan, not in tests. The live path (`--model anthropic/...`) is documented only.
- Replay never falls back to a paid call. A miss raises an error naming the case and the lab's record command.
- Repeat number: the solver sends `extra_headers={"x-replay-rep": str(state.epoch - 1)}`. A missing header means rep 0. Do not use the internal `sample_state()`.
- The judge model comes from `get_model(role="grader")`, set with `model_roles={"grader": "replay/claude-opus-5-5"}` (CLI `--model-role grader=replay/claude-opus-5-5`).
- Inspect logs go to `llms/inspect/logs/` (git-ignored). Only `llms/data/inspect_summary.json` is committed.
- App rules: follow `docs/STYLE.md`.
  - Logic lives in `src/lib/` with tests; components stay thin.
  - Tokens only, no hex values. The match mark is a symbol plus text, never colour alone.
  - `llmsAids.ts` is already 677 lines, so the new logic goes in new modules.
- Every number in the lesson and in the aid's tests comes from `inspect_summary.json` or the lab's replay output, pinned as literals.
- Commits: stage explicit paths; never amend; never commit `.vscode/`, `.superpowers/`, `*.ipynb` scratch notebooks or `.ipynb_checkpoints/`.

**Lab numbers to match (replay output from `triage_lab.py run|judge`, taken 2026-10-26):**

| Run | Split | Epochs | Category | Priority |
|---|---|---|---|---|
| v1_basic | dev / test / all | 1 | 0.925 / 0.85 / 0.9 | 0.775 / 0.65 / 0.7333… (44/60) |
| v2_rules | dev / test / all | 3 | 1.0 / 1.0 / 1.0 | 0.9667 (116/120) / 0.95 (57/60) / 0.9611 (173/180) |
| v3_examples | dev / test / all | 1 | 1.0 / 1.0 / 1.0 | 0.975 / 0.95 / 0.9667 (58/60) |

Judge (Opus, rubric after the reply, v2_rules rep-0 test replies, n = 20): `addresses_issue` 14/20, `tone` 19/20, `no_false_promises` 19/20. The judge file has 73 lines including 3 error lines; the latest line for a key wins, as in the lab.

## Review Focus

1. **Ticket text that Inspect might normalise** (non-ASCII, multiple paragraphs, trailing spaces): every one of the 60 tickets must replay with no miss. *Pinned by Task 2's parity test over `split=None`.*
2. **A reply containing blank lines or the word "Reply:"** inside the judge message: the provider must still recover the exact ticket and reply. *Pinned by Task 3's `test_judge_parse_reply_with_blank_lines`.*
3. **A saved error line**: the sample errors in Inspect and is left out of the accuracy, as the lab leaves it out. *Pinned by Task 1's `test_replay_error_line_raises`.*
4. **Running from another working directory**: paths resolve from the file, not the cwd. *Pinned by Task 4's `test_summarize_from_other_cwd`.*
5. **An edited prompt** (the learner changes `prompts.json` text): a clear miss naming the record command, never a silent wrong answer. *Pinned by Task 1's `test_unknown_system_prompt_misses`.*

---

### Task 1: Replay provider and dataset (sections 1–3)

**Files:**
- Create: `llms/inspect/triage_inspect.py` (sections 1–3 now; later tasks add 4–7)
- Create: `llms/inspect/requirements.txt`: `inspect-ai==0.3.285`, `anthropic>=1.0`, `pytest>=8`, and `brotli>=1.2` with the same comment as `llms/python/requirements.txt`
- Create: `llms/inspect/tests/conftest.py`: puts `llms/python` and `llms/inspect` on `sys.path`, and calls `pytest.importorskip("inspect_ai", reason="pip install -r llms/inspect/requirements.txt in a venv")`
- Create: `llms/inspect/tests/test_inspect.py`
- Modify: `.gitignore`: add `.venv-inspect/` and `llms/inspect/logs/`

**Interfaces:**
- Consumes (from `triage_lab`): `DATA_DIR`, `TASK_MODEL`, `JUDGE_MODEL`, `load_tickets`, `load_prompts`, `load_jsonl`, `response_path`, `triage_key`, `judge_key`, `_judge_system`, `ReplayMiss`, `TRIAGE_SCHEMA`, `JUDGE_SCHEMA`, `RUBRIC_IDS`.
- Produces:
  - `INSPECT_VERSION = "0.3.285"`
  - `REP_HEADER = "x-replay-rep"`
  - `dataset(split: str | None = None, root=DATA_DIR) -> list[Sample]`, where each Sample is `Sample(input=text, target=category, id=ticket id, metadata={"priority", "split", "tags"})`
  - `saved_line(kind, model, prompt_version, effort, key, root) -> dict`: the latest line for the key, or raises `ReplayMiss`
  - the model API registered as `@modelapi(name="replay")`, whose class is `ReplayAPI(ModelAPI)` and which takes `-M root=<data root>` (default `DATA_DIR`)

- [ ] **Step 1: Write the failing tests** in `test_inspect.py`:
  - `test_dataset_counts`: `len(dataset())==60`, `len(dataset("dev"))==40` and `len(dataset("test"))==20`. The first sample's `target` equals its ticket's category, and its metadata has priority, split and tags.
  - `test_replay_returns_saved_triage`: through `get_model("replay/claude-haiku-5-5")`, generate with the system prompt `prompts["triage"]["v2_rules"]`, user content = the text of ticket `t003`, and header rep `"1"`. The completion parses to the same dict as the saved line's `output` for that key with rep 1. `output.stop_reason == "stop"` maps from `end_turn`. Usage input and output tokens equal the saved ones.
  - `test_missing_header_is_rep_0`: the same request without the header returns rep 0's saved output.
  - `test_unknown_system_prompt_misses`: system text "edited prompt" raises an error whose message contains `python triage_lab.py record`.
  - `test_replay_error_line_raises`: build a tmp data root (copy `tickets.jsonl` and `prompts.json`, and write one triage response line for `t003` v1_basic rep 0 with `"error": "boom"`). Generating with `-M root=tmp` raises an error whose message contains `boom`.
- [ ] **Step 2: Run them to make sure they fail.** Run `.venv-inspect/bin/python -m pytest llms/inspect/tests -q` from the repo root (create the venv first: `python3 -m venv .venv-inspect && .venv-inspect/bin/pip install -r llms/inspect/requirements.txt`). Expected: FAIL (module missing).
- [ ] **Step 3: Implement sections 1–3.** Mark sections `# ── N. Title ───…` in the lab's style.
  - **The section-1 docstring** explains replay vs live.
  - **Version check:** if `inspect_ai.__version__ != INSPECT_VERSION`, warn once.
  - **The provider** loads `prompts.json` from `root`.
    - **Triage:** if the system message equals a `prompts["triage"][v]`, it's triage with variant v. Key = `triage_key(user_text, v, model_name, None, rep, prompts)`, from file `response_path("triage", model, v, None, root)`.
    - **Judge:** if the system message equals `_judge_system(prompts)`, it's judge. Parse the user message (Task 3 adds this branch; in Task 1 it raises `NotImplementedError`).
    - **Anything else:** raise `ReplayMiss` naming the model and the first 60 characters of the system text, plus `Record it: python triage_lab.py record ...`.
    - **Returning a saved line:** a line with `error` raises `RuntimeError(f"saved response errored: {error}")`. Otherwise build `ModelOutput.from_content(model, json.dumps(output))` with `usage` from the saved token counts, `stop_reason` mapped (`end_turn`→`stop`, `max_tokens`→`max_tokens`, `refusal`→`content_filter`), and `output` `None`→ an empty completion.
- [ ] **Step 4: Run the tests.** Expected: 5 passed. The lab suite (`cd llms/python && python -m pytest -q`) still shows 77 passed.
- [ ] **Step 5: Commit** `feat(llms): Inspect replay provider and dataset`.

### Task 2: Solver, code scorers and the triage task (sections 4, 5, 7)

**Files:**
- Modify: `llms/inspect/triage_inspect.py`
- Modify: `llms/inspect/tests/test_inspect.py`

**Interfaces:**
- Produces:
  - `triage_solver(variant: str) -> Solver`
  - `category_match() -> Scorer` and `priority_match() -> Scorer`, both with `metrics=[accuracy(), stderr()]`
  - `@task triage(variant: str = "v2_rules", split: str | None = None) -> Task`
  - `LAB_COMMANDS: dict[str, str]`: the lab command that gives the same number, per run id (used by Task 4)

- [ ] **Step 1: Write the failing tests:**
  - `test_parity_code_graders`, parametrised over the table in Global Constraints (variant × split, epochs 3 for v2_rules, else 1). Run `inspect_ai.eval(triage(variant=v, split=s), model="replay/claude-haiku-5-5", epochs=e, log_dir=tmp, display="none")`. Check:
    - the status is `success`;
    - the category and priority accuracy metrics equal **both** the lab's replay result (computed in the test from `triage_lab.triage`, `grade` and `report` over the same split and reps) **and** the pinned literal, to `abs < 1e-9`.
  - `test_no_sampling_params`: the solver's generate call sets no temperature, top_p or top_k. Assert the replay request's config has them as `None`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **§4 Solver:** insert `ChatMessageSystem(prompts["triage"][variant])` first. Call `generate(state, extra_headers={REP_HEADER: str(state.epoch - 1)}, response_schema=ResponseSchema(name="triage", json_schema=JSONSchema.model_validate(TRIAGE_SCHEMA)))`.
  - **§5 Scorers:** parse `state.output.completion` as JSON. Category is correct iff it parses and `category == target.text`; priority uses `state.metadata["priority"]`. An empty completion or invalid JSON scores 0, with `metadata={"outcome": "refusal" | "max_tokens" | "invalid_json"}` from the stop reason.
  - **§7 `triage` task:** `Task(dataset=dataset(split), solver=triage_solver(variant), scorer=[category_match(), priority_match()])`. The module docstring lists the canonical commands from the spec §4.7, including `--epochs 3` for v2_rules, and the paid swap `replay/` → `anthropic/` with "costs money; the lab's estimate: python triage_lab.py record --dry-run" (use the lab's real dry-run flag name from `build_parser`).
- [ ] **Step 4: Run the tests.** Expected: all pass (9 parity cases).
- [ ] **Step 5: Commit** `feat(llms): Inspect triage task matches the lab`.

### Task 3: Judge scorer and the judged task (section 6)

**Files:** modify `llms/inspect/triage_inspect.py` and `tests/test_inspect.py`.

**Interfaces:**
- Produces:
  - `parse_judge_message(text: str, rubric: str) -> tuple[str, str, str]`, returning `(ticket, reply, order)`
  - `rubric_judge() -> Scorer`: value `{rid: 1.0 | 0.0}` for the three `RUBRIC_IDS`, metrics `{rid: [mean()]}`; metadata `{"graded": bool, "verdicts": {rid: bool | None}, "reasons": {rid: str}}`
  - `@task triage_judged() -> Task`: dataset = test split; solver = `triage_solver("v2_rules")`; scorer = `rubric_judge()`

- [ ] **Step 1: Write the failing tests:**
  - `test_judge_parse_reply_with_blank_lines`: build the "after" message the way the lab does (`Ticket:\n{t}\n\nReply:\n{r}\n\nRubric:\n{rubric}`), with a reply containing `"\n\nReply:\nsecond"`. The function returns `(t, r, "after")` exactly. Do the same for the "before" order.
  - `test_parity_judge`: `eval(triage_judged(), model="replay/claude-haiku-5-5", model_roles={"grader": "replay/claude-opus-5-5"}, log_dir=tmp, display="none")` gives means 0.7, 0.95 and 0.95. The graded count is 20, matching the lab (`triage_lab.judge` over the v2_rules rep-0 test replies).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Parsing:** the message starts with `"Ticket:\n"`. The rubric part `"Rubric:\n" + rubric` is known exactly, so find it: if the message ends with `"\n\nRubric:\n" + rubric`, the order is after; if it contains `"\n\nRubric:\n" + rubric + "\n\nReply:\n"`, the order is before. Then split the ticket from the reply at the **first** `"\n\nReply:\n"` (after the rubric, in the before case). Ticket texts contain no `"\n\nReply:\n"`; assert that over the 60 tickets in the test.
  - **The provider's judge branch:** key = `judge_key(ticket, reply, order, model_name, prompts)`, from file `response_path("judge", model, order, None, root)`.
  - **The scorer:** it skips samples whose completion has no reply (value 0, `graded False`). It builds the lab's "after" message, calls `get_model(role="grader").generate([ChatMessageSystem(_judge_system(prompts)), ChatMessageUser(msg)], config=GenerateConfig(response_schema=…JUDGE_SCHEMA…))`, and reads the verdicts. A judge line with no output gives `graded False` and every verdict `None`.
- [ ] **Step 4: Run the tests.** Expected: pass.
- [ ] **Step 5: Commit** `feat(llms): Inspect judge scorer matches the lab`.

### Task 4: summarize.py and the committed summary

**Files:**
- Create: `llms/inspect/summarize.py`
- Create: `llms/data/inspect_summary.json` (generated)
- Modify: `llms/inspect/tests/test_inspect.py`

**Interfaces:**
- Produces: `build_summary(log_dir: Path) -> dict` and `main(argv=None) -> int`. `python llms/inspect/summarize.py` runs the four canonical replay evals into `llms/inspect/logs/` and writes the summary. It exits 1 and prints a message if any row has `match: false`, and exits 2 with the pip hint if `inspect_ai` is missing.
- The summary's JSON shape, which Task 5 consumes:
  ```json
  {"inspect_version": "0.3.285", "lab": "llms/python/triage_lab.py", "split": "all",
   "runs": [{"id": "v1_basic", "label": "v1_basic, all 60 tickets", "task": "triage", "epochs": 1,
             "command": "<inspect eval ...>", "lab_command": "<python triage_lab.py ...>",
             "rows": [{"metric": "category", "lab": 0.9, "inspect": 0.9, "stderr": 0.0391, "n": 60, "match": true}]}],
   "framework_gives": ["..."], "we_still_own": ["..."]}
  ```
  - Runs: `v1_basic`, `v2_rules` (epochs 3), `v3_examples` (split all) and `judge` (`triage_judged`; rows `addresses_issue`, `tone`, `no_false_promises`, n 20).
  - Numbers are rounded to 4 decimals; `match` is computed before rounding.
  - `framework_gives` has 4 items: dataset and sample format, epochs and reducers, scorers with metrics and stderr, logs and `inspect view`.
  - `we_still_own` has 4 items: replay keys, cost estimate and cap, the judge position check, deciding which numbers matter.

- [ ] **Step 1: Write the failing tests:**
  - `test_summary_rows_match`: `build_summary` on a tmp log dir produced by running the four evals gives every `match` true. Its `v2_rules` category row has `lab == inspect == 1.0` and `n == 60`.
  - `test_summarize_from_other_cwd`: run `main([])` with cwd = tmp and an output path override `--out tmp/summary.json`; the file exists and parses.
  - `test_mismatch_exits_1`: a fake log whose metric differs makes `main` return 1.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement**, reading logs with `inspect_ai.log.read_eval_log`. The lab numbers come from `triage_lab` replay calls, never typed in. Then run `.venv-inspect/bin/python llms/inspect/summarize.py` to write `llms/data/inspect_summary.json`.
- [ ] **Step 4: Run the whole Inspect suite and the lab suite.** Expected: all pass, and the lab still shows 77 passed.
- [ ] **Step 5: Commit** `feat(llms): Inspect summary, committed` (stage `summarize.py`, the test, and `inspect_summary.json`).

### Task 5: App: code source, diagram and the FrameworkCompare aid

**Files:**
- Modify: `app/src/lib/codeSources.ts`: add `'llms-inspect': { python: 'llms/inspect/triage_inspect.py' }` to the type and the registry
- Modify: `app/astro.config.mjs`: the fs allow-list already includes `../llms`, so there's nothing to change. Confirm it.
- Create: `app/src/data/diagrams/inspect-pipeline.ts`, and register it in `FlowDiagram.astro` and `tests/diagrams.test.ts`
- Create: `app/src/lib/inspectData.ts` (build time: `loadInspectSummary(): InspectSummary | null` via `import.meta.glob('../../../llms/data/inspect_summary.json', { eager: true })`)
- Create: `app/src/lib/frameworkCompare.ts` (pure, browser-safe)
- Create: `app/src/components/llms/FrameworkCompare.astro` and `FrameworkCompare.tsx`
- Create: `app/tests/frameworkcompare.test.tsx`

**Interfaces:**
- `frameworkCompare.ts` exports:
  - the type `InspectSummary`, matching Task 4's JSON
  - `compareRows(summary, runId) -> {metric, lab: string, inspect: string, stderr: string, n: number, match: boolean, mark: '✓ match' | '✗ differs'}[]`, with percentages to 1 decimal, e.g. `'90.0%'`, and stderr as `'± 3.9 pts'`
  - `allMatch(summary) -> boolean`
  - `runOptions(summary) -> {id, label}[]`
- The `inspect-pipeline` diagram: nodes `dataset` (role dataset), `solver` (harness), `replay` ("Model (replay provider)", model), `scorers` (grader), `log` ("Log and metrics", logs). There are 5 steps. Their code links are `source: 'llms-inspect'` sections 2, 4, 3, 5 and 7 (step 4 also mentions the judge in §6). Each caption names the matching lab section, e.g. "In the lab: section 4."

- [ ] **Step 1: Write the failing tests:**
  - `compareRows` on a summary fixture built from the real `inspect_summary.json`: v1_basic gives category `'90.0%'` for lab and Inspect alike, with mark `'✓ match'`.
  - `allMatch` is true. A copied fixture with one changed number gives false and a `'✗ differs'` row.
  - The component renders a radio group of the 4 runs, a table captioned "Lab vs Inspect", and both lists. With `summary={null}` it renders the "No recorded run yet" state with the command `python llms/inspect/summarize.py`.
  - In the diagram tests, every step's section exists in `llms-inspect` (the existing generic test covers this once the diagram is registered), and there are 5 steps.
- [ ] **Step 2: Run them.** `cd app && npx vitest run tests/frameworkcompare.test.tsx tests/diagrams.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement**, following `PromptAB.astro`'s wrapper pattern and `NoRecordedRun.tsx`. Accessibility: the radio group is labelled; the table has a caption; a live region announces the selected run; the match mark is text plus a symbol.
- [ ] **Step 4: Run the full app tests.** `npm test`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(app): FrameworkCompare aid and inspect-pipeline diagram`.

### Task 6: Lesson 11, docs and republish prep

**Files:**
- Create: `app/src/content/lessons/llms/11-framework.mdx` via `cd app && npm run new:lesson -- llms framework "The same eval with a framework"`
- Modify: `app/src/data/glossary.ts`: add any missing terms used with `<Term>`: `eval-framework`, `solver`, `scorer`, `epoch` (check that the existing keys don't already cover them)
- Modify: `docs/STYLE.md`: the code-sources row (`"llms-inspect"`) and a components row for `<FrameworkCompare />`
- Modify: `README.md`: a new "### The same eval with Inspect (optional)" under "## Run the LLMs lab", covering the venv setup, the canonical commands, `inspect view`, and `summarize.py`
- Modify: `app/tests/__snapshots__/codesources.test.ts.snap` (update only the new lesson's entries, with `npx vitest run tests/codesources.test.ts -u`, and review the diff)

- [ ] **Step 1: Write the lesson** per spec §6 and STYLE.md's anatomy:
  - **Concept:** a table mapping lab sections 2, 3, 4, 5, 6 and 8 to Inspect's dataset, solver, model API, scorer and epochs/log.
  - **See it:** `<FlowDiagram diagram="inspect-pipeline" />`, then `<FrameworkCompare client:visible />` (as an .astro wrapper, like the other aids). Say what to look for before each, and what they showed after.
  - **The code:** `<CodeTabs source="llms-inspect" section={3} />`, `{5}` and `{6}`, each followed by walkthrough bullets that start with code in backticks.
  - **Python only:** a `<Callout kind="go">` explaining there is no Go Inspect and that the lab's Go version remains the parity check. No "How Go does it" section.
  - **Try it:** the venv, the `inspect eval` commands, `inspect view`, and editing a prompt to see the replay miss. The live command is labelled "costs money" with the lab's dry-run estimate command.
  - **Quiz:** 2–3 questions.
  - Every number is copied from `llms/data/inspect_summary.json`.
- [ ] **Step 2: Run** `cd app && npm test && npm run build && npm run package`. Expected: all pass, with no TODO left.
- [ ] **Step 3: Run every suite:**
  - `ml/python` and `ml/go`;
  - `llms/python` (77);
  - `harness/python` (9);
  - `.venv-inspect/bin/python -m pytest llms/inspect/tests -q`.

  Expected: all green.
- [ ] **Step 4: Commit** `feat(llms): lesson 11, the same eval with a framework` (stage the lesson, glossary, STYLE.md, README.md and the snapshot explicitly).
