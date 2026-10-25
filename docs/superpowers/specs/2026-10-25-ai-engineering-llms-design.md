# AI Engineering — LLMs Track (Tuning, Evaluation, Judging): Design

Date: 2026-10-25
Status: Approved in conversation; awaiting written-spec review
Builds on: `2026-10-25-ai-engineering-harness-design.md` (app, lesson format, components) and `docs/STYLE.md` (the content standard)

## 1. Intent

**Outcome.** The learner can measure an LLM feature with numbers and improve it on purpose: build an eval set, grade outputs with code and with an LLM judge, check the judge against their own labels, and show whether a prompt, model or setting change helped or hurt. They also understand what fine-tuning weights does and when it is worth it.

**Audience.** The same learner, who has finished the Harness track: comfortable with Python, messages, tool calls and `stop_reason`. Lessons do not re-teach those.

**Success criteria.**
- The learner runs the triage eval in replay mode (free) in Python and Go and gets identical scores.
- The learner labels reply drafts in the app and computes the judge's agreement with their own labels.
- The learner shows with a noise band whether one prompt version beats another.
- The learner runs the capstone eval gate and sees it fail on a deliberately worse prompt.
- All ten LLMs lessons are live and pass the style-guide tests; the track's status is `live`.

**Stated by the user:** track subsections Tuning, Evaluation, Judging; tuning covers both API-side tuning (first) and fine-tuning weights (one LoRA lesson); Go as the second language where it fits; support-ticket triage as the running example; one teaching-sized toolkit per language (approach A).

**Assumptions (accepted):** offline replay is the default so exercises cost nothing; saved responses come only from real API runs; synthetic tickets are acceptable and labelled as such; the judge runs on a stronger model than the task.

## 2. Scope

**In scope:**
- Reference code: `llms/python/triage_lab.py`, `llms/go/main.go`, `llms/finetune/lora.py`, with tests.
- Data: tickets, reply labels, and saved responses from one approved recording run.
- Ten lessons in `app/src/content/lessons/llms/`, following `docs/STYLE.md`.
- Nine new interactive aids and the shared-component changes they need.
- `llms.yaml` switches to `live` when all lessons pass.

**Out of scope:** committed model weights; hosted training; evaluating the Harness-track agent (a later AI Agents topic); any track other than LLMs.

## 3. Architecture

```
masterclass/
├── llms/
│   ├── python/triage_lab.py        9 numbered sections (§4)
│   ├── python/tests/               pytest, no API calls
│   ├── python/requirements.txt     anthropic
│   ├── go/main.go, main_test.go    the same 9 sections
│   ├── finetune/lora.py            Python only; runs in Colab
│   ├── finetune/requirements.txt   transformers, peft, datasets, torch
│   └── data/
│       ├── tickets.jsonl           ~60 synthetic tickets (§5)
│       ├── reply_labels.jsonl      ~30 reply drafts with the learner's pass/fail labels
│       ├── fixtures/               small hand-made response set for tests
│       └── responses/              saved real responses, one JSONL per run config
└── app/
    ├── src/content/lessons/llms/   01-…10-*.mdx
    ├── src/components/             new aids (§7)
    ├── src/lib/                    their logic, plus a loader for llms/data
    └── src/data/diagrams/eval-pipeline.ts
```

The app reads `llms/python/triage_lab.py`, `llms/go/main.go` and `llms/data/` at build time, the same way it already reads the harness sources. Nothing is fetched at runtime.

## 4. Reference code

`llms/python/triage_lab.py` (about 300 lines), sections marked `# ── N. Title` like the harness:

1. **Configuration.** `TASK_MODEL = "claude-haiku-5-5"`, `JUDGE_MODEL = "claude-opus-5-5"`, `MODE` (`replay` default, `record`, `live`), data paths, `PRICES` per million tokens (Haiku 5.5 $0.10/$0.50, Sonnet 5.5 $2/$10, Opus 5.5 $4/$20).
2. **Dataset.** Load `tickets.jsonl`; `split("dev" | "test")`.
3. **The task.** `PROMPTS` dict of named versions (`v1_basic`, `v2_rules`, `v3_examples`); `triage(ticket, variant, model, effort)` returns `{category, priority, reply}` via structured outputs (`output_config={"format": {"type": "json_schema", ...}}`), with `effort` in `output_config`. No `temperature`: current models reject sampling parameters.
4. **Model call with saved responses.** Key = SHA-256 of the canonical request JSON. `replay` reads `responses/`; a miss raises `ReplayMiss` naming the request and the command to record it. `record` calls the API and appends; `live` calls without saving. Every stored line has the key, the response, input/output tokens and latency.
5. **Code graders.** Exact match on `category` and `priority`; `accuracy`; `confusion_matrix`. Refusals and `max_tokens` cut-offs are their own outcome, not a wrong label.
6. **LLM judge.** `judge(ticket, reply, order)` scores the reply against a three-criterion rubric (addresses the issue; appropriate tone; makes no promise the company cannot keep) and returns structured pass/fail per criterion with a one-sentence reason. `order` puts the rubric before or after the reply for the swap check.
7. **Judge agreement.** `agreement(judge, human)` and `cohen_kappa(...)` per criterion; `position_flips(...)` counts verdicts that change when the order swaps.
8. **Runner.** `run(cases, config, reps)` runs cases in a thread pool, records tokens, cost and errors; API failures after SDK retries (`max_retries=4`) mark a case `error`, excluded from accuracy and counted in the report. `estimate_cost(config, cases)` runs before any paid mode; `--max-cost` stops a run when reached.
9. **Report and compare.** `report(results)` with accuracy ± a noise band of `1/sqrt(n·reps)`; `compare(a, b)` lists tickets that flipped each way; `gate(results, thresholds)` returns pass/fail for the capstone. CLI: `python triage_lab.py run|compare|judge|gate ...`.

`llms/go/main.go` mirrors every section; the runner uses goroutines with a `WaitGroup`. Both read the same files and must produce identical scores on the same saved responses.

`llms/finetune/lora.py` (Python only): format dev tickets as instruction/answer pairs; LoRA (rank 8 default) on a ~0.5B-parameter open instruct model (default `Qwen/Qwen2.5-0.5B-Instruct`) with `transformers` + `peft`; generate on the test split; write predictions in the same shape so section 5's graders score them. Runs on a free Colab GPU in about 10 minutes.

## 5. Data

**`tickets.jsonl`**, about 60 synthetic tickets for a fictional cloud notes app, "Lumen". Fields: `id`, `text`, `category` (`billing`, `bug`, `account`, `how_to`, `feature_request`, `outage`), `priority` (`low`, `normal`, `high`, `urgent`), `tags`, `split` (~40 `dev`, ~20 `test`). Each category appears at least 6 times. About a quarter are tagged edge cases: `multi_issue`, `sarcasm`, `non_english`, `angry_minor`, `injection`, `very_short`, `very_long`. Lesson 2 names the synthetic origin as a weakness and shows how to add real tickets.

**`reply_labels.jsonl`**, about 30 reply drafts with pass/fail per rubric criterion. Claude drafts the labels; the learner reviews and corrects them in `LabelingPage` and commits the export, so lesson 6 compares the judge with the learner's own labels.

**`responses/`**, from one recording run that the learner approves before it starts:

| Run | Model | Effort | Prompt | Cases |
|---|---|---|---|---|
| Prompt versions | Haiku 5.5 | default | v1, v2, v3 | all 60 |
| Noise | Haiku 5.5 | default | v2 | all 60, 2 more repeats |
| Model and effort | Sonnet 5.5, Opus 5.5 | low, high | best prompt | dev 40 |
| Judge | Opus 5.5 | default | — | 30 labeled replies, both orders; test replies from the best prompt |

Estimated cost $5–10, mostly Sonnet/Opus output and thinking tokens. `estimate_cost` prints the exact figure first; the run is capped with `--max-cost 10`.

**LoRA:** no weights committed. Lesson 9 shows the learner's own Colab result, or example numbers labelled as illustrative until they run it.

## 6. Lessons

Each lesson follows `docs/STYLE.md`: Concept, See it, The code, How Go does it (where Go differs), Try it.

| # | Slug | Title | Subsection | Code sections | Aid |
|---|---|---|---|---|---|
| 1 | what | What an eval is | Evaluation | 1 | `FlowDiagram` `eval-pipeline` |
| 2 | dataset | Building an eval set | Evaluation | 2 | `DatasetBrowser` |
| 3 | graders | Code graders | Evaluation | 3, 5 | `ConfusionMatrix` |
| 4 | runner | Running evals | Evaluation | 4, 8 | `EvalRunSim` |
| 5 | judge | LLM as judge | Judging | 6 | `JudgeCard` |
| 6 | judge-check | Judging the judge | Judging | 7 | `AgreementView`, `LabelingPage` |
| 7 | prompt | Tuning the prompt | Tuning | 3, 9 | `PromptAB` |
| 8 | model | Tuning model and settings | Tuning | 1, 3 | `CostQualityChart` |
| 9 | lora | Fine-tuning with LoRA | Tuning | `finetune/lora.py` (Python only) | `LoraDiagram` |
| 10 | capstone | Capstone: an eval gate | All | 9, whole file | `FlowDiagram` replay |

Every Try it runs in replay mode by default and states what a live run would cost.

## 7. App changes

**Shared components:**
- `CodeTabs` gains `source: 'harness' | 'llms'` (default `harness`) and `pythonOnly`. The lessons test checks section numbers exist in the named source.
- `FlowDiagram` gains the `eval-pipeline` diagram. Two role tokens are added in light and dark: `--role-dataset`, `--role-grader`. The judge uses `--role-model`.
- `src/lib/llmsData.ts` loads tickets, labels and responses at build time and computes scores with the same formulas as section 5/7/9.

**New aids** (logic in `src/lib/`, thin component, tests):

| Aid | Behavior |
|---|---|
| `DatasetBrowser` | Filter by category, priority, split, tag |
| `ConfusionMatrix` | Expected × predicted grid; clicking a cell lists its tickets and answers |
| `EvalRunSim` | Steps a run: parallel calls, running token and cost totals, three repeats moving the score |
| `JudgeCard` | A reply, the rubric, and per-criterion verdict with reason |
| `AgreementView` | Learner vs judge labels: agreement %, kappa, swap-order toggle showing flips |
| `LabelingPage` | Review and correct reply labels; saves in browser storage; exports JSONL to commit |
| `PromptAB` | Two versions: score ± noise band, tickets flipped each way |
| `CostQualityChart` | Accuracy vs cost per 1,000 tickets for each model/effort, with a target line |
| `LoraDiagram` | W beside A and B; rank slider shows trainable vs full parameter counts |

Until the recording run exists, aids that need responses show "No recorded run yet" and the command to record one. They never show invented numbers.

## 8. Error handling

- Replay miss: `ReplayMiss` with the request summary and the record command; no silent API call.
- Paid modes: print `estimate_cost` and ask to confirm; stop at `--max-cost`.
- `refusal` and `max_tokens`: recorded as failed cases with the reason.
- API errors after SDK retries: case marked `error`, excluded from accuracy, counted in the report.
- Malformed data lines: load fails naming the file and line.

## 9. Testing

- **Python (pytest):** graders, confusion matrix, kappa and agreement, noise band, compare, gate, replay hit and miss, cost estimate and cap, runner with a fake model. No API calls.
- **Go (go test):** the same cases on `data/fixtures/`; a shared expected-scores file both languages must match.
- **App (vitest):** each aid's logic; lessons pass the style-guide tests; `llmsData.ts` scores match the shared expected-scores file.
- **LoRA:** the data-formatting function only.

## 10. Open items

- The recording run needs the learner's API key and explicit approval of the printed estimate.
- The learner reviews `reply_labels.jsonl` before lesson 6 is final.
- Lesson 9's result section uses illustrative numbers until the learner runs the Colab training.
