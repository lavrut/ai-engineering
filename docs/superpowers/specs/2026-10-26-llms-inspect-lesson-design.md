# AI Engineering: LLMs lesson 11, "The same eval with a framework" (design)

Date: 2026-10-26
Status: design approved in conversation; waiting for review of this written spec
Builds on: the LLMs track design (`2026-10-25-ai-engineering-llms-design.md`), `docs/STYLE.md`, and `llms/python/triage_lab.py`

## 1. Intent

**Outcome.** The learner runs the LLMs track's triage eval a second time, through an eval framework. They get the same numbers as the hand-built lab, and they can explain:
- what a framework standardises: dataset, solver, scorer, metrics, epochs and logs;
- what it leaves to them: replay, cost caps and the judge position check;
- when to reach for one.

**Audience.** The same learner, who has finished LLMs lessons 1–10.

**Success criteria.**
- In replay mode (free, offline), the Inspect tasks reproduce the lab's accuracy for every prompt version and repeat, and its judge pass rates, exactly. A test pins this.
- The learner runs `inspect eval` and opens `inspect view` on their own machine.
- Lesson 11 passes the style-guide tests. The app builds and the page is republished.

**Stated by the user:**
- Add this lesson after the ML track.
- Use Inspect AI.
- Rebuild the task, the code graders and the judge.
- Get answers from a replay provider over the saved responses (approach A).
- App and agent frameworks belong in the AI Agents track.

**Assumptions (accepted):**
- No new paid run.
- The live path is documented with its cost but never run.
- Python only: Go has no Inspect equivalent.
- `inspect-ai` is installed only in a venv, never the base Python.

## 2. Scope

**In scope:**
- `llms/inspect/triage_inspect.py`, `llms/inspect/summarize.py`, `llms/inspect/requirements.txt` and its tests.
- `llms/data/inspect_summary.json`.
- The `llms-inspect` code source.
- The `inspect-pipeline` diagram.
- The `FrameworkCompare` aid.
- Lesson `11-framework.mdx`, and README and STYLE.md updates.

**Out of scope:**
- Rebuilding the capstone gate in Inspect.
- promptfoo or DeepEval.
- A Go version.
- New recordings.
- Changing `triage_lab.py`'s behaviour. It is imported, not edited.

## 3. Architecture

```
llms/inspect/
├── triage_inspect.py     7 numbered sections (§4)
├── summarize.py          Inspect logs → llms/data/inspect_summary.json
├── requirements.txt      inspect-ai==0.3.285 (plus the lab's anthropic)
└── tests/test_inspect.py parity with triage_lab; skipped without inspect-ai
```

`triage_inspect.py` imports from `triage_lab.py`: the dataset loader, the prompts, the schemas, `triage_key` and `judge_key`, and the response-file reader. That gives the replay key one definition. Inspect logs go to `llms/inspect/logs/`, which is git-ignored. Only the derived summary is committed.

## 4. `triage_inspect.py`

Sections are marked `# ── N. Title`.

1. **Configuration.** The models (`claude-haiku-5-5` for the task, `claude-opus-5-5` for the judge), the data paths, and the pinned Inspect version. If the installed `inspect_ai.__version__` differs, it prints a warning.
2. **Dataset.** `tickets.jsonl` becomes `Sample(input=text, target=category, id=ticket id, metadata={priority, split, tags})`, with an optional filter by split.
3. **Replay provider.** A custom model API registered with `@modelapi("replay")` and used as `--model replay/<model>`.
   - It identifies the prompt version by matching the system message against `prompts.json`. The user message is the ticket text.
   - The solver passes the repeat number through the request metadata. The mechanism is chosen in the plan, from what Inspect 0.3.285 supports.
   - It rebuilds the lab's `triage_key` or `judge_key` and returns the saved `output` as the completion text (JSON). It reports the saved token counts and stop reason.
   - A missing key raises an error naming the case and the lab's record command. It never makes a paid call.
   - A saved refusal or `max_tokens` line comes back with that stop reason.
4. **Solver.** It sets the system prompt for the chosen `variant`, asks for structured output with the lab's `TRIAGE_SCHEMA`, and calls generate. It sends no temperature, the same as the lab.
5. **Code scorers.** `category_match` and `priority_match` are exact-match scorers, reported with Inspect's `accuracy()` and `stderr()`. Refusals, cut-offs and invalid JSON score as incorrect, and their reason is recorded in the score metadata. The parity test also checks the lab's answered-only figures.
6. **Judge scorer.** `rubric_judge` is a model-graded scorer. It calls the judge model (through replay by default) with the lab's judge system prompt and the user message in the "after" order. It returns pass or fail for each of `addresses_issue`, `tone` and `no_false_promises`, with a mean per criterion. It runs on the v2_rules replies that have saved verdicts.
7. **Tasks.** `@task triage(variant="v2_rules", split=None)` and `@task triage_judged()`. The lab's three repeats of v2 map to `--epochs 3`. The docstring gives the canonical commands, for example:
   ```
   inspect eval llms/inspect/triage_inspect.py@triage -T variant=v1_basic --model replay/claude-haiku-5-5
   inspect eval ...@triage -T variant=v2_rules --epochs 3 --model replay/claude-haiku-5-5
   inspect eval ...@triage_judged --model replay/claude-haiku-5-5 --model-role grader=replay/claude-opus-5-5
   ```
   The exact flag for the judge model is fixed in the plan. The paid path swaps `replay/` for `anthropic/`, with the cost stated (from the lab's `estimate_cost`) and an approval needed.

## 5. Summary and data

`summarize.py` reads the latest replay logs and writes `llms/data/inspect_summary.json` for v1, v2 and v3, plus the judge run. For each run it records:
- Inspect's accuracy and stderr;
- the lab's figure;
- `match: true/false`;
- the log file name and the Inspect version.

The app reads only this file.

## 6. App

- **Code source:** `llms-inspect: { python: 'llms/inspect/triage_inspect.py' }`, with no Go tab.
- **Diagram:** `inspect-pipeline`, five steps (Dataset → Solver → Model (replay provider) → Scorers → Log/Metrics). Each step links to its section of `triage_inspect.py`, and its note names the matching section of the lab. It uses the existing role tokens (`--role-dataset`, `--role-harness`, `--role-model`, `--role-grader`, `--role-logs`).
- **Aid:** `FrameworkCompare` (`components/llms/`).
  - A table with one row per run and metric: the lab's value and Inspect's value, a match mark that is a symbol plus text (never colour alone), and the stderr.
  - A "what the framework did / what we still own" list, read from the summary.
  - Its logic lives in `src/lib/llmsAids.ts` (or a new module if that file would pass 400 lines), with tests. Before the summary exists it shows "No recorded run yet" plus the summarize command.
- **Lesson** `app/src/content/lessons/llms/11-framework.mdx`, "The same eval with a framework":
  - **Concept:** frameworks standardise the pieces of an eval; the lab's sections map onto Inspect's; and what a framework hides from you.
  - **See it:** the diagram, then `FrameworkCompare`.
  - **The code:** `CodeTabs source="llms-inspect"`, sections 3, 5 and 6, with walkthrough bullets.
  - No "How Go does it" section. A one-line callout explains why.
  - **Try it:** create the venv, run the canonical commands, run `inspect view`, and change a prompt version to see a replay miss. The paid live command is shown with its cost.
  - A quiz with 2–3 questions.
- **Docs:** a README entry for the venv and commands. STYLE.md's components and code-sources rows.

## 7. Error handling

- **Replay miss:** an error naming the case, model, version, repeat and the record command.
- **Wrong Inspect version:** a warning; the run proceeds.
- **`inspect-ai` missing:** the tests skip with "pip install -r llms/inspect/requirements.txt in a venv", and `summarize.py` exits with that message.
- **Summary mismatch:** `summarize.py` writes `match: false` and exits non-zero, so a broken parity can't ship quietly.

## 8. Testing

- **Python** (venv with `inspect-ai==0.3.285`):
  - The replay provider returns the lab's saved line for known keys and raises on a miss.
  - The dataset sample count and metadata are correct.
  - The parity check: every v1, v2 (3 epochs) and v3 run, split by split, equals `triage_lab` replay results exactly, and the judge pass rates per criterion equal the lab's.
  - `summarize.py` on a fixture log.
- **App (Vitest):**
  - The `FrameworkCompare` logic and its "no run" state.
  - The diagram steps link to sections that exist.
  - The lesson passes the style tests.
  - Summary numbers are pinned as literals.
- No test calls the API.

## 9. Cost and setup

- Free. Everything replays from saved responses.
- The live path costs what the lab's estimate prints. It is shown in the lesson and never run without approval.
- Setup: `python -m venv .venv-inspect && .venv-inspect/bin/pip install -r llms/inspect/requirements.txt`. The venv is git-ignored.

## 10. Open items

- Resolved 2026-10-26 by a free probe with Inspect 0.3.285 (a dummy provider, `--epochs 3`, no API calls):
  - The solver calls `generate(state, extra_headers={"x-replay-rep": str(state.epoch - 1)}, response_schema=...)`. The custom provider receives both unchanged in `config`.
  - `state.epoch` counts from 1, so the lab's rep is `epoch - 1`.
  - The judge scorer uses `get_model(role="grader")`, set with `model_roles={"grader": "replay/claude-opus-5-5"}` (CLI `--model-role grader=...`).
  - The public header is the documented path. The internal `sample_state()` also works, but it is not public API, so it is not used.
