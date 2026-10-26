# AI Engineering — Machine Learning Track (with MLOps): Design

Date: 2026-10-26
Status: Approved in conversation; awaiting written-spec review
Builds on: the Harness and LLMs track designs, `docs/STYLE.md`, and the app's existing components

## 1. Intent

**Outcome.** The learner can train and evaluate a classic text classifier on real data, then run it the way teams do in production: track experiments, register versions, serve the model, watch it for drift, and retrain behind a gate. They understand that a trained model is a file of numbers, and when a small classic model beats an LLM on cost and speed.

**Audience.** The same learner, who has finished the Harness and LLMs tracks: comfortable with Python, the Claude API, evals, noise bands and gates. New to classic ML and MLOps.

**Success criteria.**
- The learner trains the classifier from the committed sample and reproduces the lesson's test metrics.
- The Python and Go servers return identical predictions on the shared test inputs.
- The learner sees real drift in the later-year stream and can explain what moved.
- The capstone retrains on newer data, the gate decides, and promotion and rollback work in the registry.
- All eleven ML lessons are live and pass the style-guide tests; `ml.yaml` is `live`.

**Stated by the user:** ML basics first, then MLOps (subsection "MLOps"); real public data, the CFPB consumer-complaint dataset; for each MLOps piece, build a small version first, then the standard tool; Go only for serving and monitoring; code organized as one small file per stage (approach A).

**Assumptions (accepted):** everything runs free on a laptop CPU; a fixed sample is committed so lessons are reproducible; splits are by time so drift is real; the only paid step is an optional, approved Claude comparison (~$0.10); MLflow is optional.

## 2. Scope

**In scope:** the data sample and fetch script; `train.py`, `mlops.py`, `with_mlflow.py`, `monitor.py`, `compare_llm.py`; the FastAPI server; the Go server with a drift command; tests in Python and Go; eight new aids, two diagrams, the `CodeTabs` source registry; eleven lessons; `ml.yaml` live.

**Out of scope:** deep learning and GPUs; cloud deployment; a real CI system; the LLMs-track framework lesson (queued next, separately); any track other than ML.

## 3. Architecture

```
masterclass/ml/
├── data/
│   ├── fetch_sample.py         one-time download from the CFPB API (network)
│   ├── complaints.jsonl        committed sample
│   ├── category_map.json       old → current product names
│   ├── fixtures/               small fixed files for tests
│   └── README.md               query, date fetched, counts, licence
├── python/
│   ├── train.py                data → features → models → metrics → export
│   ├── mlops.py                run tracker, registry, promotion gate
│   ├── with_mlflow.py          the same run and registration with MLflow (optional)
│   ├── monitor.py              PSI drift by month
│   ├── compare_llm.py          optional Claude comparison, saved-response replay
│   ├── requirements.txt
│   └── tests/
├── serve/server.py             FastAPI: /predict, /health
├── go/                         go.mod, main.go (serve + drift), main_test.go
├── models/                     exported model JSON per version (git-ignored except the committed production version)
├── runs/                       tracked runs (JSON per run)
└── registry.json
```

The app reads small derived summaries at build time (vocabulary slice, top words, run table, registry, drift series, metrics). It never ships the full model file.

## 4. Data

- **Source:** CFPB Consumer Complaint Database public API; US-government public-domain data. Narratives are published only with consumer consent and are already scrubbed (personal details replaced with `XXXX`).
- **Sample:** complaints with a narrative, about 600 per year for 2019–2024 (about 3,600 total), stratified by month, fixed random seed. Restricted to about six product categories with enough volume (for example credit reporting, debt collection, credit card, checking or savings account, mortgage, money transfer); the exact list is fixed in Task 1 from real counts.
- **Fields kept:** `id` (complaint ID), `date` (date received), `product` (mapped to the current name), `product_raw` (as published), `narrative`.
- **Category renames:** CFPB has renamed product categories over time; `category_map.json` records the mapping, and lessons 2 and 10 use it as a real example of label drift.
- **Splits (by time):** train 2019–2021, test 2022, production stream 2023–2024 by month.
- `fetch_sample.py` records the exact query, fetch date, row counts per year and category, and any rows dropped, in `ml/data/README.md`. If the API has changed, it fails clearly; the committed sample keeps working.

## 5. Code

Each file uses numbered `# ── N. Title` sections (Go `// ── N. Title`), kept short enough to quote.

**`train.py`**
1. Configuration (seed, paths, splits, vocabulary size cap, regularization).
2. Data and time-based splits.
3. Features: our own tokenizer (lowercase, `[a-z0-9']+`, drop `xxxx` placeholders, optional stop words) and TF-IDF (smooth IDF, sublinear TF off, L2 norm). A test proves it matches scikit-learn's `TfidfVectorizer` configured the same way.
4. Models: majority-class baseline, multinomial Naive Bayes, logistic regression (scikit-learn), fixed seed.
5. Metrics: per-category precision, recall, F1; macro and weighted averages; confusion matrix; train vs. test; a noise band for accuracy (1/√n, as in the LLMs track).
6. Export: model JSON with `vocabulary`, `idf`, `classes`, `coef`, `intercept`, `tokenizer` settings, `data_version` (sample hash), and `trained_on` split; plus a training log (loss by iteration) for the aid.
7. Entry point: `python train.py [--model nb|logreg] [--C ...]`.

**`mlops.py`**
1. Run tracker: one JSON per run in `ml/runs/` (params, metrics, data version, model file hash, git commit, time).
2. Registry: `registry.json` with versions, stages (`staging`, `production`, `archived`), promotion and rollback; atomic writes.
3. Promotion gate: candidate must beat production macro-F1 by more than the noise band and must not drop any category's F1 below a floor.
4. CLI: `runs`, `compare`, `register`, `promote`, `rollback`, `gate`.

**`with_mlflow.py`:** the same training run logged to MLflow, then registered with aliases; runs only if `mlflow` is installed.

**`serve/server.py` (FastAPI):** loads the production model JSON from the registry at startup; `POST /predict {"text"}` → `{label, scores, low_confidence, model_version}`; `GET /health`; validation errors as 400 with fixed messages.

**`ml/go/main.go`:** 1 Configuration, 2 Model loading, 3 Tokenizer and TF-IDF, 4 Prediction, 5 HTTP server (`net/http`), 6 Drift (PSI), 7 Entry point (`serve`, `predict`, `drift`). Go reimplements tokenization and scoring from the JSON alone.

**`monitor.py`:** PSI per month against the training distribution for (a) predicted-category mix and (b) the input word distribution over the top vocabulary terms; buckets with a small epsilon; months under 30 complaints shown but not alerted; alert threshold 0.2, with lessons explaining it is a convention, not a law.

**`compare_llm.py`:** Claude Haiku 5.5 classifies the same 200 test complaints with structured output; saved-response replay, estimate, confirmation and $10 cap, reusing the LLMs track's pattern. Replay is free; recording needs the user's approval.

## 6. Lessons

| # | Slug | Title | Half | Code | Aid |
|---|---|---|---|---|---|
| 1 | what | What a trained model is | ML | train §1, §6 | `FlowDiagram` `ml-pipeline` |
| 2 | data | The data | ML | train §2, fetch script | `ComplaintExplorer` |
| 3 | features | Turning text into numbers | ML | train §3 | `FeatureExplorer` |
| 4 | training | Training a classifier | ML | train §4 | `TrainingStepper` |
| 5 | evaluating | Evaluating fairly | ML | train §5 | `MetricsView` |
| 6 | vs-llm | Classic ML vs. an LLM | ML | compare_llm | `ModelVsLlm` |
| 7 | tracking | Tracking experiments | MLOps | mlops §1, with_mlflow | `RunTable` |
| 8 | registry | A model registry | MLOps | mlops §2, with_mlflow | `RegistryTimeline` |
| 9 | serving | Serving a model | MLOps | server, Go §2–5 | `FlowDiagram` + recorded latency table |
| 10 | drift | Monitoring for drift | MLOps | monitor, Go §6 | `DriftChart` |
| 11 | capstone | Capstone: retrain, gate, promote | Both | mlops §3–4 | `FlowDiagram` `ml-loop` replay |

Each lesson follows `docs/STYLE.md`. Every number comes from the committed data and the scripts' output. Lesson 6 shows "No recorded run yet" for the Claude side until the optional recording exists.

## 7. App changes

- **`CodeTabs` source registry:** named sources (`harness`, `llms`, `lora`, `ml-train`, `ml-mlops`, `ml-serve`, `ml-monitor`, `ml-go`, `ml-fetch`, `ml-llm`) each declaring a Python file and an optional Go file; a source without Go hides the Go tab. Existing lessons render unchanged; the lessons test checks sections exist in the named source.
- **Diagrams:** `ml-pipeline` and `ml-loop`, using existing roles.
- **Aids:** `ComplaintExplorer`, `FeatureExplorer` (top-2,000 vocabulary slice with IDF; tokenization in the browser must match Python on pinned examples), `TrainingStepper`, `MetricsView`, `ModelVsLlm`, `RunTable`, `RegistryTimeline`, `DriftChart`. Logic in `src/lib/`, thin components, `.astro` wrappers passing small derived props, accessibility as in the LLMs aids (keyboard, live regions, colour never the only signal), real numbers pinned from Python output.
- **Page budget:** the full model file never ships; new props total under ~300 KB.

## 8. Error handling

- Fetch: API change or network failure stops with a clear message; the committed sample is unaffected.
- Prediction: unknown words ignored; empty or all-unknown input returns the majority class with `low_confidence: true`.
- Servers: empty text or text over 20,000 characters → 400 with the same message in Python and Go; missing or corrupt model fails at startup.
- Registry: unknown version or rollback with no previous production version → clear error; atomic writes.
- Drift: months under 30 complaints shown without alerts.

## 9. Testing

- **Python:** tokenizer/TF-IDF vs. scikit-learn; metrics vs. hand-computed fixtures; export round trip; tracker, registry, gate, rollback; PSI vs. hand-computed values; FastAPI via its test client; fetch parsing and category mapping against a saved API response. No network.
- **Go:** model loading; tokenizer and predictions identical to Python on a shared file of test inputs and expected outputs; PSI identical to Python; HTTP handlers with `httptest`.
- **App:** each aid's logic; style-guide lesson rules; browser tokenization matches Python on pinned examples; real numbers pinned with literals from the Python output.

## 10. Cost and setup

- Free on a laptop CPU; training takes seconds.
- One network step while building: the CFPB sample download (public data, a few MB).
- Optional paid step: the lesson 6 Claude recording, about $0.10, with estimate, confirmation and cap, only with the user's approval.
- MLflow optional; lessons 7–8 work without it.

## 11. Open items

- Task 1 verifies the CFPB API's current endpoint and fields before the download and fixes the category list from real counts.
- The lesson 6 recording needs the user's approval of its printed estimate.
- After this track: the queued LLMs lesson 11 on an eval framework.
