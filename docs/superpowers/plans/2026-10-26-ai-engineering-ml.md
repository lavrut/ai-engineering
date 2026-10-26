# ML Track Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Machine Learning track (with MLOps): a real-data text classifier, a small tracker/registry/gate, Python and Go model servers, drift monitoring, eight aids and eleven lessons.

**Architecture:** One small file per stage under `ml/` with numbered sections that lessons quote. Python scripts write small JSON reports to `ml/reports/`; the app reads those reports (and a vocabulary slice) at build time and never re-implements ML. The exported model is plain JSON that Python and Go both score identically.

**Tech Stack:** Python 3.12 (scikit-learn 1.8, numpy, FastAPI 0.136, uvicorn, httpx — all installed; mlflow optional, not installed), Go 1.27 standard library, Astro 7 + Preact + Vitest (existing app).

**Spec:** `docs/superpowers/specs/2026-10-26-ai-engineering-ml-design.md`

## Global Constraints

- Every lesson follows `docs/STYLE.md`; `npm test` must pass (section order, no TODO, glossary terms, colours only in `tokens.css`, valid UTF-8).
- Numbered sections: Python `# ── N. Title ───`, Go `// ── N. Title ───` (regex `^(?:#|//) ── (\d+)\.\s*(.*?)\s*─*\s*$`). Keep each file short enough to quote (target ≤ 400 lines; report if exceeded).
- Deterministic: fixed seed `SEED = 7` everywhere randomness exists; the committed sample and models reproduce the lessons' numbers exactly.
- No network in tests. The only network steps are the one-time CFPB fetch (Task 1) and the optional paid Claude recording (Task 11, requires the user's explicit approval of the printed estimate — stop and ask).
- Splits by `date` (date received): train 2019–2021, test 2022, stream 2023–2024 by month.
- Drift: PSI with epsilon `1e-4`, alert at `0.2`, months with fewer than `30` complaints shown but not alerted.
- Server contract (Python and Go identical): `POST /predict {"text": str}` → `{"label", "scores": {label: float}, "low_confidence": bool, "model_version"}`; `GET /health` → `{"status": "ok", "model_version"}`; empty text or > 20,000 characters → HTTP 400 `{"error": "text must be 1 to 20000 characters"}`.
- Reports (`ml/reports/*.json`) are the contract between Python and the app; each has a `"generated_by"` field naming the command that wrote it.
- Never commit `.vscode/`, `.superpowers/`, or the user's untracked `llms/python/Untitled.ipynb` / `.ipynb_checkpoints/`. Stage explicit paths; never `git commit --amend`. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Don't install packages into the user's base Python. If a task needs `mlflow`, use a throwaway venv under the session scratchpad.

## Review Focus

1. **Tokenizer drift across Python, Go and the browser:** the same text must give the same tokens and the same TF-IDF vector in all three, including apostrophes, digits, `XXXX` placeholders and non-ASCII. Pinned by a shared fixture `ml/data/fixtures/tokenize.json` (Tasks 2, 9, 12).
2. **Prediction parity:** Go and FastAPI return identical labels and scores (to 1e-9) on `ml/data/fixtures/predictions.json` (Tasks 3, 7, 9).
3. **Leakage across the time split:** IDF, vocabulary and model fit only on train; the test and stream never touch fitting. Pinned by a test that refits with test rows removed and gets identical IDF (Task 3).
4. **Registry corruption:** a crash mid-write must not leave a half-written `registry.json`; rollback with no previous production version fails clearly (Task 4).
5. **Drift false alarms from tiny months:** months under 30 complaints never alert (Task 8).

---

## Shared contracts

**Complaint line** (`ml/data/complaints.jsonl`): `{"id": "1234567", "date": "2021-03-14", "product": "<current name>", "product_raw": "<as published>", "narrative": "..."}`

**Model JSON** (`ml/models/<version>.json`): `{"version", "created", "data_version", "trained_on": {"from","to"}, "tokenizer": {"lowercase": true, "pattern": "[a-z0-9']+", "drop": ["xxxx", ...], "min_df": int, "max_features": int}, "vocabulary": [term...], "idf": [float...], "classes": [label...], "coef": [[float...]...], "intercept": [float...], "metrics": {...}}` — scoring: TF counts → × idf → L2 normalise → `coef · x + intercept` → softmax; `low_confidence` when the top probability < `0.5` or no known tokens.

**Reports** (`ml/reports/`): `dataset.json` (counts by year × category, sample snippets), `metrics.json` (per model: per-class P/R/F1, macro, weighted, accuracy, band, confusion, train vs test), `training_log.json` (logreg loss by iteration, top words per class at checkpoints), `drift.json` (per month: n, psi_words, psi_predictions, alert, top shifted words), `latency.json` (Python vs Go p50/p95 over a fixed request set), `llm_compare.json` (written only after the optional recording).

---

### Task 1: CFPB sample and fetch script

**Files:** Create `ml/data/fetch_sample.py`, `ml/data/complaints.jsonl`, `ml/data/category_map.json`, `ml/data/README.md`, `ml/data/fixtures/archive_rows.csv`, `ml/python/tests/test_fetch.py`, `ml/python/tests/conftest.py`, `ml/python/requirements.txt` (`scikit-learn`, `numpy`, `fastapi`, `uvicorn`, `httpx`, `pytest`; `mlflow` commented as optional).

**Interfaces — Produces:** `parse_rows(csv_rows) -> list[dict]` (complaint lines), `map_product(raw, mapping) -> str | None`, `stratified_sample(rows, per_year, seed) -> list[dict]`.

- [ ] **Step 1: Verify the archive** before writing code (revised 2026-10-26: the live API no longer carries narratives): read the CFPB narratives archive page, identify the export files covering 2019–2024, check their sizes (HEAD requests) and their CSV columns (stream only the start of one file). Record the files and columns in `ml/data/README.md`. Save a few rows of a real export, as published, to `fixtures/archive_rows.csv` for the parsing tests.
- [ ] **Step 2: Failing tests:** `test_parse_rows_keeps_required_fields`, `test_map_product_merges_renamed_categories` (an old and a new name map to one current name; an unknown name returns None), `test_stratified_sample_is_deterministic_and_balanced_by_month`.
- [ ] **Step 3: Implement** `fetch_sample.py` (sections: 1 Archive files, 2 Download with retries, checksum and a clear error, 3 Parse, 4 Map categories, 5 Sample, 6 Write). Choose the categories from real counts (5–7 with ≥ 60 complaints per year in every year 2019–2024 after mapping); target 600 per year.
- [ ] **Step 4: Run the fetch once**, commit the sample; README records archive files (names, URLs, sizes, SHA-256), fetch date, counts per year × category, dropped rows, licence (US public domain) and the scrubbing note.
- [ ] **Step 5:** `cd ml/python && python3 -m pytest -q` → PASS. **Commit** `data(ml): add CFPB complaint sample and fetch script`.

### Task 2: Features (train.py §1–3)

**Files:** Create `ml/python/train.py` (§1–3 plus empty markers 4–7), `ml/python/tests/test_features.py`, `ml/data/fixtures/tokenize.json`.

**Interfaces — Produces:** `SEED`, `PATHS`, `SPLITS`, `load_complaints(path)`, `split_by_time(rows) -> {"train","test","stream"}`, `tokenize(text, cfg) -> list[str]`, `fit_tfidf(docs, cfg) -> (vocabulary, idf)`, `transform(docs, vocabulary, idf) -> scipy.sparse`.

- [ ] **Step 1: Failing tests:** `test_split_by_time_boundaries` (2021-12-31 → train, 2022-01-01 → test, 2023-01-01 → stream); `test_tokenize_fixture` (every case in `tokenize.json`: apostrophes, digits, `XXXX/XX`, accents, punctuation, empty); `test_tfidf_matches_sklearn` (same vocabulary, idf and normalised vectors as `TfidfVectorizer(lowercase=True, token_pattern=..., smooth_idf=True, sublinear_tf=False, norm="l2", min_df=..., max_features=...)` with our tokenizer as `analyzer`, to 1e-12).
- [ ] **Step 2–4:** write `tokenize.json` by hand (input → expected tokens, ≥ 15 cases incl. non-ASCII), implement, PASS.
- [ ] **Step 5: Commit** `feat(ml): add time splits, tokenizer and TF-IDF`.

### Task 3: Models, metrics, export (train.py §4–7)

**Files:** Modify `ml/python/train.py`; Create `ml/python/tests/test_train.py`, `ml/data/fixtures/metrics_cases.json`, `ml/data/fixtures/predictions.json`, `ml/models/v1.json`, `ml/reports/dataset.json`, `ml/reports/metrics.json`, `ml/reports/training_log.json`.

**Interfaces — Produces:** `train_models(train_rows, cfg) -> dict`, `per_class_metrics(y_true, y_pred, classes) -> dict`, `noise_band(n) = 1/sqrt(n)`, `export_model(model, vocab, idf, meta) -> dict`, `score(model_json, text) -> {"label","scores","low_confidence"}` (pure Python, no scikit-learn — the reference Go and the browser must match), CLI `python train.py [--model logreg|nb] [--C 1.0] [--version v1]`.

- [ ] **Step 1: Failing tests:** `test_metrics_match_hand_fixture` (hand-computed P/R/F1, macro, weighted on `metrics_cases.json`); `test_no_leakage` (fit with test/stream rows present vs removed → identical vocabulary and idf); `test_score_matches_sklearn_predict_proba` (pure `score` equals the fitted logistic regression's probabilities to 1e-9 on 50 test texts); `test_export_round_trip`; `test_low_confidence_rules` (empty, all-unknown, top < 0.5); `test_reports_have_generated_by`.
- [ ] **Step 2–4:** implement; run `python train.py --model logreg --version v1` to write `v1.json` and the reports; write `predictions.json` (30 inputs incl. edge cases → expected label/scores from `score`) as the cross-language vector; PASS.
- [ ] **Step 5: Commit** `feat(ml): train, evaluate and export the classifier`.

### Task 4: Tracker, registry, gate (mlops.py)

**Files:** Create `ml/python/mlops.py`, `ml/python/tests/test_mlops.py`, `ml/runs/*.json`, `ml/registry.json`.

**Interfaces — Produces:** `log_run(params, metrics, model_path, data_version) -> run_id`, `list_runs()`, `compare_runs(a, b) -> dict`, `register(model_path) -> version`, `promote(version, stage)`, `rollback() -> version`, `gate(candidate_metrics, production_metrics, *, floor=0.5) -> {"passed","reasons"}` (macro-F1 gain > band and no per-class F1 below `floor`), CLI subcommands per spec.

- [ ] **Step 1: Failing tests:** run log round trip (includes model hash and data version); registry stage transitions; `test_rollback_without_previous_fails`; `test_atomic_write_survives_crash` (simulate failure between temp write and rename → old file intact); gate pass/fail on drop beyond band and on floor breach.
- [ ] **Step 2–4:** implement; log runs for NB and logreg with 2–3 values of C; register v1 and promote to production; PASS.
- [ ] **Step 5: Commit** `feat(ml): add run tracker, model registry and promotion gate`.

### Task 5: MLflow version (optional tool)

**Files:** Create `ml/python/with_mlflow.py`, `ml/python/tests/test_with_mlflow.py`.

- [ ] **Step 1:** test uses `pytest.importorskip("mlflow")`; checks a run is logged with the same params/metrics as `mlops.log_run` and the model registered with alias `production`, using a temporary tracking dir.
- [ ] **Step 2:** implement against current MLflow docs (verify APIs; aliases, not deprecated stages).
- [ ] **Step 3:** verify in a throwaway venv under the session scratchpad (`python3 -m venv`, install requirements + mlflow), report the output; base env must still pass with the test skipped.
- [ ] **Step 4: Commit** `feat(ml): show the same run with MLflow`.

### Task 6: FastAPI server

**Files:** Create `ml/serve/server.py`, `ml/python/tests/test_server.py`.

- [ ] **Step 1: Failing tests** with FastAPI's `TestClient`: health; predict matches `predictions.json`; 400 on empty and on 20,001 characters with the exact error body; startup fails clearly on a missing/corrupt model.
- [ ] **Step 2–4:** implement (loads the registry's production model; reuses `train.score`); PASS.
- [ ] **Step 5: Commit** `feat(ml): serve the model with FastAPI`.

### Task 7: Drift monitor (monitor.py)

**Files:** Create `ml/python/monitor.py`, `ml/python/tests/test_monitor.py`, `ml/data/fixtures/psi_cases.json`, `ml/reports/drift.json`.

**Interfaces — Produces:** `psi(expected_counts, actual_counts, eps=1e-4) -> float`, `monthly_drift(stream_rows, model_json, train_rows) -> list[dict]`, CLI `python monitor.py`.

- [ ] **Step 1: Failing tests:** PSI equals hand-computed `psi_cases.json` values (identical distributions → 0; zero buckets handled by epsilon); `test_small_months_never_alert`; `test_drift_report_months_and_fields`.
- [ ] **Step 2–4:** implement; run on the real stream; PASS. Report in the task report which months alert and why (real data).
- [ ] **Step 5: Commit** `feat(ml): add PSI drift monitoring`.

### Task 8: Go server and drift

**Files:** Create `ml/go/go.mod` (module `ml-serve`, no third-party deps), `ml/go/main.go`, `ml/go/main_test.go`, `ml/reports/latency.json`, `ml/python/bench.py` (latency of both servers on a fixed request set).

- [ ] **Step 1: Failing tests:** tokenizer equals `tokenize.json`; predictions equal `predictions.json` to 1e-9; PSI equals `psi_cases.json`; handlers via `httptest` match the server contract incl. the exact 400 body.
- [ ] **Step 2–4:** implement sections 1–7 per spec; `go vet` clean; PASS. Run `bench.py` (starts both servers on free local ports, 500 requests each, p50/p95) → `latency.json`; stop both servers.
- [ ] **Step 5: Commit** `feat(ml): add Go model server and drift command`.

### Task 9: LLM comparison infrastructure (no recording)

**Files:** Create `ml/python/compare_llm.py`, `ml/python/tests/test_compare_llm.py`.

- [ ] **Step 1: Failing tests:** replay miss raises; request building has structured output with the category enum and no sampling params; cost estimate and $10 cap; zero client calls without `--yes` (fake client); `llm_compare.json` written from replayed lines only.
- [ ] **Step 2–4:** implement by reusing the LLMs track's patterns (copy the minimal pieces; do not import across tracks): saved responses under `ml/data/llm_responses/`, latest line wins, `mode="replay"` default for every lookup. PASS.
- [ ] **Step 5: Commit** `feat(ml): add the Claude comparison in replay mode`.

### Task 10: App infrastructure

**Files:** Modify `app/src/components/CodeTabs.astro` (source registry), `FlowDiagram.astro`/`.tsx` and `app/src/lib/diagram.ts` as needed, `app/vitest.config.ts` and `app/astro.config.mjs` fs allow (`../ml`), `app/tests/lessons.test.ts`, `app/tests/style.test.ts` (text-integrity roots include `ml/`). Create `app/src/lib/codeSources.ts`, `app/src/lib/mlData.ts`, `app/tests/mldata.test.ts`, `app/src/data/diagrams/ml-pipeline.ts`, `ml-loop.ts`.

- [ ] **Step 1: Failing tests:** every existing lesson renders the same CodeTabs sections (snapshot of section text per lesson before/after); new sources resolve; a source without Go hides the Go tab; `mlData` loads `ml/reports/*.json` and a top-2,000 vocabulary slice; browser tokenizer in `mlData.ts` matches `tokenize.json`; diagrams validate.
- [ ] **Step 2–4:** implement; `npm test && npm run build` PASS.
- [ ] **Step 5: Commit** `feat(app): code source registry, ml data loader and diagrams`.

### Task 11: Optional Claude recording (paid — requires user approval)

- [ ] **Step 1:** `python compare_llm.py record` without `--yes`; capture the printed estimate; **stop and ask the user to approve it in chat.**
- [ ] **Step 2:** with approval, run with the key read from Keychain inline (never printed); verify replay; write `llm_compare.json`. If the user declines, lesson 6 keeps the "No recorded run yet" state.
- [ ] **Step 3: Commit** `data(ml): record the Claude comparison` with the actual cost.

### Task 12: Aids — ML basics

**Files:** `ComplaintExplorer`, `FeatureExplorer`, `TrainingStepper`, `MetricsView` in `app/src/components/ml/` (`.tsx` + `.astro`), logic in `app/src/lib/mlAids.ts`, tests `app/tests/mlaids-basics.test.tsx`.

- [ ] Follow the LLMs aids' pattern (small derived props, keyboard, live regions, colour never the only signal, both themes, 375px, browser check). Pin real numbers with literals from the reports. FeatureExplorer must tokenize in the browser exactly like Python (shared fixture). **Commit** `feat(app): add ML basics aids`.

### Task 13: Aids — MLOps

**Files:** `ModelVsLlm`, `RunTable`, `RegistryTimeline`, `DriftChart` in `app/src/components/ml/`, logic in `mlAids.ts`, tests `app/tests/mlaids-ops.test.tsx`. Load the `dataviz` skill for the charts if available.

- [ ] Same standards as Task 12; `ModelVsLlm` shows "No recorded run yet" without `llm_compare.json`. **Commit** `feat(app): add MLOps aids`.

### Task 14: Lessons 1–6

- [ ] `npm run new:lesson -- ml <slug> "<Title>"` for what, data, features, training, evaluating, vs-llm (titles per spec §6); glossary terms (`feature`, `TF-IDF`, `tokenizer`, `logistic regression`, `Naive Bayes`, `overfitting`, `precision`, `recall`, `F1`, `macro average`, `data leakage`, `time split`). Every number from the reports or replays, with provenance for anything else; Try it free and runnable. `npm test && npm run build` PASS; browser check. **Commit** `content(ml): add ML basics lessons 1–6`.

### Task 15: Lessons 7–11, live, publish

- [ ] Lessons tracking, registry, serving, drift, capstone; glossary (`experiment tracking`, `model registry`, `drift`, `PSI`, `promotion gate`, `rollback`); `ml.yaml` → `live`; README ML section (setup, train, serve both servers, monitor, optional MLflow, optional recording); all suites (app, llms, harness, ml Python and Go) PASS; `npm run package`; browser check of every ML view and that existing tracks are unchanged. Controller republishes the artifact after review. **Commit** `content(ml): add MLOps lessons 7–11 and make the track live`.
