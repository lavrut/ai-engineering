# AI Engineering

An interactive masterclass on AI engineering, with Python and Go side by side. Four tracks are live: **Agent Harness** (build the loop, tools, context handling and guardrails around a model), **LLMs** (evaluate, judge and tune an LLM feature), **Machine Learning** (train a classic text classifier on real complaints, then track, register, serve, monitor and retrain it) and **MCP** (build, connect, secure and ship Model Context Protocol servers and clients).

Hosted page: https://claude.ai/artifact/MH9ZBPk8GC1duTWc4Ke5cw (private until shared from the page's Share menu)

## Layout

- `harness/python/harness.py` and `harness/go/main.go` — the reference harness, one file per language with matching numbered sections.
- `llms/python/triage_lab.py` and `llms/go/main.go` — the LLMs track's eval lab (support-ticket triage), one file per language with matching numbered sections 1–10.
- `llms/finetune/lora.py` — the LoRA fine-tuning script (Python only; runs on a Colab GPU).
- `llms/data/` — tickets, prompts, reply labels and saved model responses; see [llms/data/README.md](llms/data/README.md).
- `ml/` — the Machine Learning track: `ml/python/` (`train.py`, `mlops.py`, `with_mlflow.py`, `monitor.py`, `compare_llm.py`, `bench.py`), `ml/serve/server.py` (FastAPI), `ml/go/` (the Go server and drift command), and the committed data sample, models, runs, registry and reports; see [ml/data/README.md](ml/data/README.md).
- `mcp/` — the MCP track: `mcp/core/` (the shared ticket, classifier and label code, Python and Go), `mcp/python/` (`read_server.py`, `ops_server.py`, `client.py`, `auth_server.py`, `agent.py`, `simulate.py`, `derive.py`, the App in `app/`), `mcp/go/` (`readserver`, `opsserver`, `client`, `auth`), the shared contract in `mcp/fixtures/`, and the recordings, simulated transcripts and derived lesson data in `mcp/data/`; see [mcp/fixtures/README.md](mcp/fixtures/README.md) and [mcp/data/recordings/README.md](mcp/data/recordings/README.md).
- `app/` — the Astro site that teaches all four tracks (lessons, diagrams, simulators, quizzes).

## Run a harness

Both harnesses call the Claude API, so running one costs money. They work inside `./workspace` (created if missing) and append events to `./trace.jsonl`.

```sh
export ANTHROPIC_API_KEY=sk-ant-...

# Python
cd harness/python
pip install -r requirements.txt
python harness.py

# Go
cd harness/go
go run .
```

Type a task at the prompt, or `exit`.

## Test the harnesses

The tests use a fake model and never call the API.

```sh
cd harness/python && pytest
cd harness/go && go test ./...
```

## Run the LLMs lab

Replay mode is the default: every model answer is read from `llms/data/responses/`, so these commands are free and need no API key.

```sh
pip install -r llms/python/requirements.txt

python3 llms/python/triage_lab.py run --prompt v2_rules --split dev
python3 llms/python/triage_lab.py compare --prompt v1_basic --vs v2_rules
python3 llms/python/triage_lab.py judge --prompt v2_rules --split test
python3 llms/python/triage_lab.py agreement
python3 llms/python/triage_lab.py gate --prompt v1_basic --baseline v2_rules --split dev   # FAIL, exit 1

cd llms/go && go run . run --prompt v2_rules     # same scores in Go
```

A request with no saved answer (a new prompt version, ticket, model or effort) stops with a replay-miss error; it never falls back to a paid call.

The data files (tickets, prompts, reply labels, saved responses), how the labels were made and what the recordings cost are described in [llms/data/README.md](llms/data/README.md).

### Test the lab

The tests use fixtures and fake models and never call the API.

```sh
cd llms/python && python3 -m pytest -q
cd llms/go && go test ./...
python3 -m pytest llms/finetune -q     # from the repo root; LoRA data formatting only, no GPU
```

### Record new answers (costs money)

`--mode record` (on `run` or `judge`) and the `record` command call the API and append to `llms/data/responses/`. Every paid run first prints the estimated cost, the worst case if every reply uses `max_tokens`, and the `--max-cost` cap (default $10), then asks for approval; nothing is called without a `y` or `--yes`. A run stops at the cap and keeps what finished; requests already recorded are skipped.

```sh
export ANTHROPIC_API_KEY=sk-ant-...
python3 llms/python/triage_lab.py run --prompt v2_rules --model claude-sonnet-5-5 --effort medium --mode record
python3 llms/python/triage_lab.py record --plan all --max-cost 10    # the full recording plan
```

### The same eval with Inspect (optional)

Lesson 11 re-runs the triage eval through [Inspect AI](https://inspect.aisi.org.uk/). It replays the same saved responses, so it is free and needs no API key. Install Inspect in its own venv, never in your base Python (`.venv-inspect/` is git-ignored):

```sh
python3 -m venv .venv-inspect
.venv-inspect/bin/pip install -r llms/inspect/requirements.txt    # inspect-ai==0.3.285
.venv-inspect/bin/pip install -e llms/inspect                     # registers the replay provider, which the CLI needs

.venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v1_basic --log-dir llms/inspect/logs
.venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v2_rules --epochs 3 --log-dir llms/inspect/logs
.venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v3_examples --log-dir llms/inspect/logs
.venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage_judged --model replay/claude-haiku-5-5 --model-role grader=replay/claude-opus-5-5 --log-dir llms/inspect/logs

.venv-inspect/bin/inspect view --log-dir llms/inspect/logs        # browse the logs
.venv-inspect/bin/python llms/inspect/summarize.py                # run all four, write llms/data/inspect_summary.json
.venv-inspect/bin/python -m pytest llms/inspect/tests -q          # the parity tests
```

`summarize.py` exits non-zero if an Inspect score stops matching the lab's. Logs go to `llms/inspect/logs/`; only the summary is committed. The live path (`--model anthropic/claude-haiku-5-5`) costs money: get the estimate first from `python3 llms/python/triage_lab.py run --prompt v2_rules --split all --reps 3 --mode live` and answer `N` to the prompt (it prints about $0.08, worst case $0.20; the judged run's Opus grader adds about $0.47, worst case $0.91).

### LoRA on Colab

`llms/finetune/lora.py` fine-tunes `Qwen/Qwen2.5-0.5B-Instruct` with LoRA on the dev tickets and grades it on test. It needs a GPU but no API key. In Google Colab (Runtime > Change runtime type > T4 GPU), about 10 minutes:

1. Zip the repo's `llms/` folder and upload `llms.zip` in Colab's file panel.
2. Run `!unzip -q llms.zip`, `!pip install -q -r llms/finetune/requirements.txt`, `!python llms/finetune/lora.py`. The first run downloads about 1 GB of model weights.
3. Download `predictions.jsonl` before the session ends, then re-score it anywhere with `python3 llms/finetune/lora.py --score predictions.jsonl`.

## Run the ML track

Everything in the ML track runs on a laptop CPU, offline and free. The only paid step is the optional Claude recording below, which is already done and committed.

### Setup

```sh
pip install -r ml/python/requirements.txt
```

The ML track needs Python 3.12 or later: from 3.12, `sum()` adds floats with compensation (Neumaier), and the Python/Go parity checks and the byte-identical rebuilds rely on it.

The scientific stack is pinned to exact versions (`scikit-learn==1.8.0`, `numpy==2.5.4`, `scipy==1.17.1`). The track checks that retraining reproduces `ml/models/v1.json`, the runs and the registry byte for byte, and the drift thresholds come from NumPy's seeded random generator. Another version's solver or generator can change the last digits, and with them the model's SHA-256. The server packages (`fastapi`, `uvicorn`, `httpx`), `anthropic` (used by `compare_llm.py`, even in free replay mode), `brotli` (older versions make every API call fail under the SDK's HTTP client) and `pytest` are lower bounds. The Go code needs only the standard library.

### Train and evaluate

```sh
cd ml/python
python3 train.py                   # rewrites models/v1.json byte for byte and prints the metrics table
python3 train.py --model logreg --version v1 --write-reports   # also rewrites dataset.json, metrics.json, training_log.json in ml/reports/, byte for byte
```

### Runs, registry and gate

`mlops.py` keeps one JSON file per run in `ml/runs/` and the versions, stages and history in `ml/registry.json`. Read-only commands:

```sh
cd ml/python
python3 mlops.py runs
python3 mlops.py compare logreg-C1-2019-2021 logreg-C3-2019-2021
```

Commands that write (`experiments`, `register`, `promote`, `rollback`, `gate`, `retrain`) take `--root DIR`. Try them on a copy so the committed files stay as they are:

```sh
ML="$(mktemp -d)/ml" && cp -R .. "$ML"
python3 mlops.py --root "$ML" gate v2          # FAILED vs v1, exit 1: gain +0.005 inside the 0.058 band
python3 mlops.py --root "$ML" promote v2 production
python3 mlops.py --root "$ML" rollback         # production is now v1
```

### Serve the model

Both servers load the registry's production version, check its SHA-256, and follow the same contract (`ml/serve/server.py`'s docstring). From the repo root:

```sh
uvicorn server:app --app-dir ml/serve --port 8000
go -C ml/go run . serve -port 8001
curl -s localhost:8000/predict -d '{"text": "my mortgage payment was misapplied"}'
curl -s localhost:8000/health
go -C ml/go run . predict "my mortgage payment was misapplied"   # score one text, no server
```

`ML_ROOT=/path/to/ml` points either server at another copy of `ml/`. `cd ml/python && python3 bench.py` starts both servers and measures their latency; `--write-reports` would replace the committed `ml/reports/latency.json` with your machine's numbers.

### Monitor for drift

```sh
cd ml/python
python3 monitor.py                # PSI per month and quarter against the 2022 reference; writes nothing
go -C ../go run . drift           # the same table, recomputed in Go (thresholds read from drift.json)
```

`python3 monitor.py --write-reports` rewrites `ml/reports/drift.json`.

### MLflow (optional)

`with_mlflow.py` logs v1's run to MLflow, registers it with a `production` alias, and checks both tools recorded the same numbers and model hash. Install MLflow in a throwaway virtual environment; it was tested on 3.17.2:

```sh
cd ml/python
python3 -m venv /tmp/mlflow-venv
/tmp/mlflow-venv/bin/pip install -r requirements.txt "mlflow==3.17.2"
/tmp/mlflow-venv/bin/python with_mlflow.py                       # temp folder, deleted at the end
/tmp/mlflow-venv/bin/python with_mlflow.py --workdir mlflow-demo # keep it; then run the printed `mlflow ui` command
```

### The Claude comparison

Lesson 6 compares v1 with `claude-haiku-5-5` on 200 test complaints. The answers were recorded once, with approval, for $0.0204, and are saved in `ml/data/llm_responses/`, so the comparison replays for free:

```sh
cd ml/python
python3 compare_llm.py compare     # v1 0.810 vs claude-haiku-5-5 0.865 (noise band ±0.071); rewrites llm_compare.json byte for byte
```

`record` and `live` call the API with your `ANTHROPIC_API_KEY` and cost money (about $0.02 for all 200). Each prints an estimate, a worst case and the $10 cap, and calls nothing without a `y` or `--yes`.

### Test the ML code

The tests never call the API or the network.

```sh
cd ml/python && python3 -m pytest -q
cd ml/go && go vet ./... && go test ./...
```

The data sample (where it comes from, the archive files and their checksums, how it was drawn and what it mutes) is described in [ml/data/README.md](ml/data/README.md).

## Run the MCP track

Two Lumen MCP servers over one shared core, to MCP spec 2026-07-28: `lumen-read` (read-only: search, get, classify) and `lumen-ops` (writes a label, only after the user confirms). Both exist in Python and in Go and answer the same contract. Everything runs on `127.0.0.1`, offline and free. The only paid steps are new agent recordings, which ask first; the approved ones are committed.

### Setup

```sh
python3 -m venv .venv-mcp && .venv-mcp/bin/pip install -r mcp/requirements.txt
```

- Python 3.12 or later, in the `.venv-mcp/` virtual environment at the repo root (git-ignored). `mcp==2.3.0` is pinned exactly; the ML stack is pinned as in the ML track, because `classify_complaint` loads the ML track's model.
- Go 1.27.2 (`mcp/go/go.mod`), with the official Go SDK pinned in `go.mod`.
- Node 22.19 or later only for the optional MCP Inspector smoke test.

### The servers

```sh
.venv-mcp/bin/python mcp/python/client.py --stdio discover                  # lumen-read over stdio (no auth)
.venv-mcp/bin/python mcp/python/auth_server.py                             # TEST-ONLY authorization server on :9000
.venv-mcp/bin/python mcp/python/read_server.py --http                      # lumen-read on 127.0.0.1:8765, needs tickets:read
cd mcp/go && go run ./opsserver -labels /tmp/lumen-live-labels.jsonl       # lumen-ops (Go) on 127.0.0.1:8766, needs tickets:write
```

The authorization server is for tests only: it approves every request at once for one fixed user. On its first start it writes a random signing key to `mcp/data/dev-signing.key` (mode 0600, git-ignored, never commit it). Both HTTP servers read that key and refuse to start without it; `LUMEN_SIGNING_KEY_FILE` moves it. Swap any server for its twin: `go run ./readserver -http`, or `.venv-mcp/bin/python mcp/python/ops_server.py --labels PATH`.

### Clients and auth

The Python and Go clients print the same bytes. Over HTTP the token goes in an `Authorization: Bearer` header, taken from `--token-file`, then `--token` (which warns: the command line is visible in the process list), then `LUMEN_TOKEN`. To get a token, run the real OAuth flow (authorization code with PKCE, audience-bound) with the steps in [mcp/python/AUTH_LIVE_CHECK.md](mcp/python/AUTH_LIVE_CHECK.md):

```sh
.venv-mcp/bin/python mcp/python/auth_live_check.py --url http://127.0.0.1:8765/mcp \
    --save-token /tmp/lumen-read.tok get_ticket '{"id": "t001"}'
.venv-mcp/bin/python mcp/python/client.py --url http://127.0.0.1:8765/mcp --token-file /tmp/lumen-read.tok call search_tickets '{"query": "charged twice", "limit": 1}'
(cd mcp/go && go run ./client -url http://127.0.0.1:8765/mcp -token-file /tmp/lumen-read.tok call search_tickets '{"query": "charged twice", "limit": 1}')
```

Stop the servers and `rm -f /tmp/lumen-read.tok /tmp/lumen-live-labels.jsonl` when done.

### The agent

`mcp/python/agent.py` runs the Track 1 harness on the MCP tools, with both servers in memory. **Replay is free**: it answers from `mcp/data/recordings/`, calls no API and needs no key.

```sh
.venv-mcp/bin/python mcp/python/agent.py --scenario lesson5
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --guard --approve decline --elicit accept
.venv-mcp/bin/python mcp/python/simulate.py      # the SIMULATED compromised model (a script, not a model): free
```

**Recording costs money.** Always look at the estimate first; it creates no API client:

```sh
.venv-mcp/bin/python mcp/python/agent.py --scenario lesson5 --mode record --dry-run
```

Then `--mode record --max-cost 10` (needs `ANTHROPIC_API_KEY`) prints the same estimate and worst case, asks `y/N`, and stops before any call that could pass the cap. See [mcp/data/recordings/README.md](mcp/data/recordings/README.md). After a new recording, `.venv-mcp/bin/python mcp/python/derive.py` rebuilds the lesson data.

### Test the MCP code

No test calls the Claude API. The model is a fake or a recording.

```sh
.venv-mcp/bin/python -m pytest mcp/python/tests -q
cd mcp/go && go test -race ./...
.venv-mcp/bin/python mcp/fixtures/gen_contract.py --check   # the shared contract is up to date
.venv-mcp/bin/python mcp/python/derive.py --check           # the lesson data matches the real servers and recordings
.venv-mcp/bin/python mcp/python/simulate.py --check         # the simulated transcripts match the real agent
bash mcp/scripts/inspector_smoke.sh                         # optional: the pinned MCP Inspector, both protocol eras (Node 22.19+)
```

## The site

```sh
cd app
npm install
npm run dev        # dev server at http://localhost:4321
npm test           # vitest
npm run build      # astro check + test type-check + static build into dist/
npm run package    # dist/ -> publish/ in the page-host format
```

`npm run package` writes `publish/index.html` without a document skeleton (the host supplies one), makes every `_astro/` reference relative, and exits 1 listing any external URL outside the allowed hosts (`fonts.googleapis.com`, `fonts.gstatic.com`, `cdnjs.cloudflare.com`, `cdn.jsdelivr.net`, `unpkg.com`).

## Adding content

Every track and lesson follows [docs/STYLE.md](docs/STYLE.md): the lesson anatomy, writing rules, components, design tokens and code conventions. Start new work from a template:

```sh
cd app
npm run new:lesson -- <track> <slug> "<Title>"   # from templates/lesson.mdx
cp templates/track.yaml src/content/tracks/<id>.yaml
```

`npm test` enforces the checkable rules: section order, no leftover `TODO`, glossary terms, colors only from `src/styles/tokens.css`, and valid UTF-8 with no garbled characters in any source file.
