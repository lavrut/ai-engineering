# AI Engineering

An interactive masterclass on AI engineering, with Python and Go side by side. Eight tracks are live: **Agent Harness** (build the loop, tools, context handling and guardrails around a model), **LLMs** (evaluate, judge and tune an LLM feature), **Machine Learning** (train a classic text classifier on real complaints, then track, register, serve, monitor and retrain it), **MCP** (build, connect, secure and ship Model Context Protocol servers and clients), **AI Agents** (design agent workflows and a multi-agent desk, rebuild it in LangGraph and the Claude Agent SDK, record and replay every engine, and evaluate them fairly), **Data Fabric & Data Contracts** (catalogue the datasets the tracks share, write contracts for them, enforce them in Python and Go, and ship a breaking change under contract), **Ontology** (write Lumen's facts once as an ontology, generate the prompt definitions, schema and tool from it, validate the saved model outputs against it, audit every other artefact for drift, and check every recorded agent action against the permissions it states) and **AI Infrastructure** (build the gateway between an app and the model in Python and Go: routing, retries and rate limits, a response cache, budgets with a ledger and traces, then replay the recorded traffic through it and gate the result).

Hosted page: https://claude.ai/artifact/MH9ZBPk8GC1duTWc4Ke5cw (private until shared from the page's Share menu)

## Layout

- `harness/python/harness.py` and `harness/go/main.go` — the reference harness, one file per language with matching numbered sections.
- `llms/python/triage_lab.py` and `llms/go/main.go` — the LLMs track's eval lab (support-ticket triage), one file per language with matching numbered sections 1–10.
- `llms/finetune/lora.py` — the LoRA fine-tuning script (Python only; runs on a Colab GPU).
- `llms/data/` — tickets, prompts, reply labels and saved model responses; see [llms/data/README.md](llms/data/README.md).
- `ml/` — the Machine Learning track: `ml/python/` (`train.py`, `mlops.py`, `with_mlflow.py`, `monitor.py`, `compare_llm.py`, `bench.py`), `ml/serve/server.py` (FastAPI), `ml/go/` (the Go server and drift command), and the committed data sample, models, runs, registry and reports; see [ml/data/README.md](ml/data/README.md).
- `mcp/` — the MCP track: `mcp/core/` (the shared ticket, classifier and label code, Python and Go), `mcp/python/` (`read_server.py`, `ops_server.py`, `client.py` with `client_connect.py` and `client_calls.py`, `auth_server.py`, `agent.py` with `agent_config.py`, `agent_host.py` and `agent_model.py`, `simulate.py`, `derive.py`, the App in `app/`), `mcp/go/` (`readserver`, `opsserver`, `client`, `auth`), the shared contract in `mcp/fixtures/`, and the recordings, simulated transcripts and derived lesson data in `mcp/data/`; see [mcp/fixtures/README.md](mcp/fixtures/README.md) and [mcp/data/recordings/README.md](mcp/data/recordings/README.md).
- `agents/` — the AI Agents track: `agents/proxy/` (the record/replay proxy: `proxy.py` with `proxy_cost.py`, `proxy_handler.py` and `proxy_server.py`), `agents/desk/python/` and `agents/desk/go/` (the five patterns and the supervisor desk, matching numbered sections), `agents/frameworks/` (`langgraph_desk.py`, `sdk_desk.py`), `agents/evals/` (`scorers.py`, `run_evals.py` with `judge_set.py`, the 13 eval tickets), `agents/run.py` (runs any engine through the proxy, with `run_sets.py`, `run_engines.py` and `run_traces.py`), `agents/derive.py` (the lesson data, with `derive_read.py`), the shared contract in `agents/fixtures/`, and the recordings, traces and derived data in `agents/data/`; see [agents/data/recordings/README.md](agents/data/recordings/README.md).
- `data/` — the Data Fabric & Data Contracts track: `data/contracts/` (four contracts in YAML and their JSON exports), `data/python/` (`contract.py` the validator, `catalog.py`, `quality.py`, `gate.py`, `migrate_tickets.py`, and in `consumers/` the shim that shows the agents eval's move to v2), `data/go/validate/` (the Go twin validator), the shared validator cases in `data/fixtures/`, and the migrated tickets and derived lesson data in `data/data/`; see [data/fixtures/README.md](data/fixtures/README.md).
- `ontology/` — the Ontology track (Python only): `ontology/lumen.yaml` (the ontology, in six numbered sections, the sixth the agents' permissions), `ontology/python/` (`ontology.py` the loader, checker and validator, `generate.py`, `grounding.py`, `audit.py`, `governance.py`) and the derived lesson data in `ontology/data/derived/`.
- `infra/` — the AI Infrastructure track: `infra/gateway.yaml` (the gateway's config, in six numbered sections), `infra/python/` (`gateway.py` the server, `stages.py` the chain, `ledger.py`, `traces.py`, `injector.py`, `config.py`, `replay.py` the replay driver and gate, with `responses.py`, `cachefile.py` and `capstone_extras.py`), `infra/go/gateway/` (the Go twin of the chain), and `infra/data/` (the shared fixtures in `fixtures/` and the derived lesson data in `derived/`).
- `app/` — the Astro site that teaches all eight tracks (lessons, diagrams, simulators, quizzes).

## Run a harness

Both harnesses call the Claude API, so every task you type costs money. Before the first call of each task the REPL prints an estimate (typical, and a worst case that assumes every call stays at the context budget; it is not a ceiling) and asks `Run it? [y/N]`; anything but `y` skips the task, and with no terminal to ask the answer is no. `--max-cost` (default $10) is the hard stop: it is checked before every model call against what the session has spent plus the most that call could cost (an estimate, so the session stops at about the cap). `--yes` approves every task without asking: use it only for a scripted run you have already approved. They work inside `./workspace` (created if missing) and append events to `./trace.jsonl`.

The key is read from `ANTHROPIC_API_KEY` only and is never printed or logged. Set it without putting it in your shell history:

```sh
read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY
```

Python (from the repo root; the venv once: `python3 -m venv .venv-harness && .venv-harness/bin/pip install -r harness/python/requirements.txt`):

```sh
cd harness/python && ../../.venv-harness/bin/python harness.py --max-cost 10
```

Go (from the repo root):

```sh
cd harness/go && go run . --max-cost 10
```

Type a task at the prompt, or `exit`.

## Test the harnesses

The tests use a fake model (and, for the model call itself, a fake API server on 127.0.0.1 with a dummy key) and never call the API.

Set up the venv once, from the repo root (`.venv-harness/` is git-ignored; never install into your base Python):

```sh
python3 -m venv .venv-harness && .venv-harness/bin/pip install -r harness/python/requirements.txt
.venv-harness/bin/python -m pytest harness/python -q
(cd harness/go && go test ./...)
```

## Run the LLMs lab

Replay mode is the default: every model answer is read from `llms/data/responses/`, so these commands are free and need no API key.

```sh
python3 -m venv .venv-llms && .venv-llms/bin/pip install -r llms/python/requirements.txt    # once, from the repo root; .venv-llms/ is git-ignored

.venv-llms/bin/python llms/python/triage_lab.py run --prompt v2_rules --split dev
.venv-llms/bin/python llms/python/triage_lab.py compare --prompt v1_basic --vs v2_rules
.venv-llms/bin/python llms/python/triage_lab.py judge --prompt v2_rules --split test
.venv-llms/bin/python llms/python/triage_lab.py agreement
.venv-llms/bin/python llms/python/triage_lab.py gate --prompt v1_basic --baseline v2_rules --split dev   # FAIL, exit 1

cd llms/go && go run . run --prompt v2_rules     # same scores in Go
```

A request with no saved answer (a new prompt version, ticket, model or effort) stops with a replay-miss error; it never falls back to a paid call.

The data files (tickets, prompts, reply labels, saved responses), how the labels were made and what the recordings cost are described in [llms/data/README.md](llms/data/README.md).

### Test the lab

The tests use fixtures and fake models and never call the API.

```sh
.venv-llms/bin/python -m pytest llms/python -q
cd llms/go && go test ./...
.venv-llms/bin/python -m pytest llms/finetune -q     # LoRA data formatting only, no GPU
```

### Record new answers (costs money)

`--mode record` (on `run` or `judge`) and the `record` command call the API and append to `llms/data/responses/`. Every paid run first prints the estimated cost, the worst case if every reply uses `max_tokens`, and the `--max-cost` cap (default $10), then asks for approval; nothing is called without a `y` or `--yes`. A run stops at the cap and keeps what finished; requests already recorded are skipped.

```sh
read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY    # paste the key; it is not echoed or kept in shell history
.venv-llms/bin/python llms/python/triage_lab.py run --prompt v2_rules --model claude-sonnet-5-5 --effort medium --mode record
.venv-llms/bin/python llms/python/triage_lab.py record --plan all --max-cost 10    # the full recording plan
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

`summarize.py` exits non-zero if an Inspect score stops matching the lab's. Logs go to `llms/inspect/logs/`; only the summary is committed.

**The live path costs money.** Any model that is not `replay/...`, as `--model` or as the grader role (`anthropic/claude-haiku-5-5`, say), calls the API and pays for it. Inspect has no estimate and no y/N prompt, so the task refuses a non-replay model unless `LUMEN_LIVE_OK=1` is set. Price the run first; this is free and calls nothing:

```sh
.venv-inspect/bin/python llms/inspect/summarize.py --estimate     # add --model / --grader anthropic/<model> to price another model
```

It prints calls × the lab's `PRICES` per run: the `v2_rules` run over 3 epochs is 180 Haiku calls, about $0.08 (worst case $0.20), and the judged run's Opus grader adds about $0.47 (worst case $0.91). If you accept the cost, run that one command with the approval in front (`LUMEN_LIVE_OK=1 .venv-inspect/bin/inspect eval ... --model anthropic/claude-haiku-5-5 ...`). There is no run-wide spend cap: Inspect's `--cost-limit` applies to each sample.

### LoRA on Colab

`llms/finetune/lora.py` fine-tunes `Qwen/Qwen2.5-0.5B-Instruct` with LoRA on the dev tickets and grades it on test. It needs a GPU but no API key. In Google Colab (Runtime > Change runtime type > T4 GPU), about 10 minutes:

1. Zip the repo's `llms/` folder and upload `llms.zip` in Colab's file panel.
2. Run `!unzip -q llms.zip`, `!pip install -q -r llms/finetune/requirements.txt`, `!python llms/finetune/lora.py`. The first run downloads about 1 GB of model weights.
3. Download `predictions.jsonl` before the session ends, then re-score it anywhere with `.venv-llms/bin/python llms/finetune/lora.py --score predictions.jsonl`.

## Run the ML track

Everything in the ML track runs on a laptop CPU, offline and free. The only paid step is the optional Claude recording below, which is already done and committed.

### Setup

Run every command below from the repo root, in the `.venv-ml/` virtual environment (git-ignored; never install into your base Python).

```sh
python3 -m venv .venv-ml && .venv-ml/bin/pip install -r ml/python/requirements.txt
```

The ML track needs Python 3.12 or later: from 3.12, `sum()` adds floats with compensation (Neumaier), and the Python/Go parity checks and the byte-identical rebuilds rely on it.

The scientific stack is pinned to exact versions (`scikit-learn==1.8.0`, `numpy==2.5.4`, `scipy==1.17.1`). The track checks that retraining reproduces `ml/models/v1.json`, the runs and the registry byte for byte, and the drift thresholds come from NumPy's seeded random generator. Another version's solver or generator can change the last digits, and with them the model's SHA-256. The server packages (`fastapi`, `uvicorn`, `httpx`), `anthropic` (used by `compare_llm.py`, even in free replay mode), `brotli` (older versions make every API call fail under the SDK's HTTP client) and `pytest` are lower bounds. The Go code needs only the standard library.

### Train and evaluate

```sh
.venv-ml/bin/python ml/python/train.py                   # rewrites models/v1.json byte for byte and prints the metrics table
.venv-ml/bin/python ml/python/train.py --model logreg --version v1 --write-reports   # also rewrites dataset.json, metrics.json, training_log.json in ml/reports/, byte for byte
```

### Runs, registry and gate

`mlops.py` keeps one JSON file per run in `ml/runs/` and the versions, stages and history in `ml/registry.json`. Read-only commands:

```sh
.venv-ml/bin/python ml/python/mlops.py runs
.venv-ml/bin/python ml/python/mlops.py compare logreg-C1-2019-2021 logreg-C3-2019-2021
```

Commands that write (`experiments`, `register`, `promote`, `rollback`, `gate`, `retrain`) take `--root DIR`. Try them on a copy so the committed files stay as they are. `experiments` refuses to overwrite a run that is already there, so delete the copy's `runs/` first (`rm -r "$ML/runs"`), as lesson 7 does:

```sh
ML="$(mktemp -d)/ml" && cp -R ml "$ML"
.venv-ml/bin/python ml/python/mlops.py --root "$ML" gate v2          # FAILED vs v1, exit 1: gain +0.005 inside the 0.058 band
.venv-ml/bin/python ml/python/mlops.py --root "$ML" promote v2 production
.venv-ml/bin/python ml/python/mlops.py --root "$ML" rollback         # production is now v1
```

### Serve the model

Both servers load the registry's production version, check its SHA-256, and follow the same contract (`ml/serve/server.py`'s docstring). From the repo root:

```sh
.venv-ml/bin/uvicorn server:app --app-dir ml/serve --port 8000
go -C ml/go run . serve -port 8001
curl -s localhost:8000/predict -d '{"text": "my mortgage payment was misapplied"}'
curl -s localhost:8000/health
go -C ml/go run . predict "my mortgage payment was misapplied"   # score one text, no server
```

`ML_ROOT=/path/to/ml` points either server at another copy of `ml/`. `.venv-ml/bin/python ml/python/bench.py` starts both servers and measures their latency; `--write-reports` would replace the committed `ml/reports/latency.json` with your machine's numbers.

### Monitor for drift

```sh
.venv-ml/bin/python ml/python/monitor.py    # PSI per month and quarter against the 2022 reference; writes nothing
go -C ml/go run . drift           # the same table, recomputed in Go (thresholds read from drift.json)
```

`.venv-ml/bin/python ml/python/monitor.py --write-reports` rewrites `ml/reports/drift.json`.

### MLflow (optional)

`with_mlflow.py` logs v1's run to MLflow, registers it with a `production` alias, and checks both tools recorded the same numbers and model hash. Install MLflow in a throwaway virtual environment; it was tested on 3.17.2:

```sh
python3 -m venv /tmp/mlflow-venv
/tmp/mlflow-venv/bin/pip install -r ml/python/requirements.txt "mlflow==3.17.2"
(cd ml/python && /tmp/mlflow-venv/bin/python with_mlflow.py)                       # temp folder, deleted at the end
(cd ml/python && /tmp/mlflow-venv/bin/python with_mlflow.py --workdir mlflow-demo)  # keep it; then run the printed `mlflow ui` command
```

### The Claude comparison

Lesson 6 compares v1 with `claude-haiku-5-5` on 200 test complaints. The answers were recorded once, with approval, for $0.0204, and are saved in `ml/data/llm_responses/`, so the comparison replays for free:

```sh
.venv-ml/bin/python ml/python/compare_llm.py compare     # v1 0.810 vs claude-haiku-5-5 0.865 (noise band ±0.071); rewrites llm_compare.json byte for byte
```

`record` and `live` call the API with your `ANTHROPIC_API_KEY` and cost money (about $0.02 for all 200). Each prints an estimate, a worst case and the $10 cap, and calls nothing without a `y` or `--yes`.

### Test the ML code

The tests never call the API or the network.

```sh
.venv-ml/bin/python -m pytest ml/python -q
cd ml/go && go vet ./... && go test ./...
```

The data sample (where it comes from, the archive files and their checksums, how it was drawn and what it mutes) is described in [ml/data/README.md](ml/data/README.md).

## Run the MCP track

Two Lumen MCP servers over one shared core, to MCP spec 2026-07-28: `lumen-read` (read-only: search, get, classify) and `lumen-ops` (writes a label, only after the user confirms). Both exist in Python and in Go and answer the same contract. Everything runs on `127.0.0.1`, offline and free. The only paid steps are new agent recordings, which ask first; the approved ones are committed.

### Setup

```sh
python3 -m venv .venv-mcp && .venv-mcp/bin/pip install -r mcp/requirements.txt
```

- Python 3.12 or later, in the `.venv-mcp/` virtual environment at the repo root (git-ignored). Every package in `mcp/requirements.txt` is pinned exactly (`mcp==2.3.0`, `anthropic==2.3.0`, `pytest==9.1.1` and the rest); the ML stack is pinned as in the ML track, because `classify_complaint` loads the ML track's model.
- Go 1.27.2 (`mcp/go/go.mod`), with the official Go SDK pinned in `go.mod`.
- Node 22.19 or later only for the optional MCP Inspector smoke test.

### The servers

The first command runs once and exits. The other three are long-running servers: start each in its own terminal, from the repo root, and stop it with Ctrl-C.

```sh
.venv-mcp/bin/python mcp/python/client.py --stdio discover                  # lumen-read over stdio (no auth)
.venv-mcp/bin/python mcp/python/auth_server.py                             # TEST-ONLY authorization server on :9000
.venv-mcp/bin/python mcp/python/read_server.py --http                      # lumen-read on 127.0.0.1:8765, needs tickets:read
cd mcp/go && go run ./opsserver -labels ~/.lumen/live-labels.jsonl       # lumen-ops (Go) on 127.0.0.1:8766, needs tickets:write
```

The authorization server is for tests only: it approves every request at once for one fixed user. So it registers only redirect URIs on `http://127.0.0.1`, `http://localhost` or `http://[::1]` (any port, no fragment) and refuses browser requests from any other origin, which keeps a web page from collecting a token from it; run it only on 127.0.0.1 and only while learning. Servers on other ports: start the AS with `--read-port` / `--ops-port` to match. On its first start it writes a random signing key to `mcp/data/dev-signing.key` (mode 0600, git-ignored, never commit it). Both HTTP servers read that key and refuse to start without it; `LUMEN_SIGNING_KEY_FILE` moves it. Swap any server for its twin: `(cd mcp/go && go run ./readserver -http)`, or `.venv-mcp/bin/python mcp/python/ops_server.py --labels PATH`.

### Clients and auth

The Python and Go clients print the same bytes. Over HTTP the token goes in an `Authorization: Bearer` header, taken from `--token-file`, then `--token` (which warns: the command line is visible in the process list), then `LUMEN_TOKEN`. To get a token, run the real OAuth flow (authorization code with PKCE, audience-bound) with the steps in [mcp/python/AUTH_LIVE_CHECK.md](mcp/python/AUTH_LIVE_CHECK.md):

```sh
.venv-mcp/bin/python mcp/python/auth_live_check.py --url http://127.0.0.1:8765/mcp \
    --save-token ~/.lumen/read.tok get_ticket '{"id": "t001"}'
.venv-mcp/bin/python mcp/python/client.py --url http://127.0.0.1:8765/mcp --token-file ~/.lumen/read.tok call search_tickets '{"query": "charged twice", "limit": 1}'
(cd mcp/go && go run ./client -url http://127.0.0.1:8765/mcp -token-file ~/.lumen/read.tok call search_tickets '{"query": "charged twice", "limit": 1}')
```

Stop the servers and `rm -f ~/.lumen/read.tok ~/.lumen/live-labels.jsonl` when done.

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
(cd mcp/go && go test -race ./...)
.venv-mcp/bin/python mcp/fixtures/gen_contract.py --check   # the shared contract is up to date
.venv-mcp/bin/python mcp/python/derive.py --check           # the lesson data matches the real servers and recordings
.venv-mcp/bin/python mcp/python/simulate.py --check         # the simulated transcripts match the real agent
bash mcp/scripts/inspector_smoke.sh                         # optional: the pinned MCP Inspector, both protocol eras (Node 22.19+)
```

## Run the AI Agents track

The Lumen support desk in four engines (hand-built Python, hand-built Go, LangGraph and the Claude Agent SDK), all talking to Claude through one record/replay proxy on `127.0.0.1:8790`. Everything below replays offline and free from the committed recordings. The only paid steps are new recordings, which print an estimate and ask first; the approved ones are committed.

### Setup

```sh
python3 -m venv .venv-agents && .venv-agents/bin/pip install -r agents/requirements.txt
```

- Python 3.12 or later, in the `.venv-agents/` virtual environment at the repo root (git-ignored). `agents/requirements.txt` pins `langgraph==1.2.18`, `langchain==1.4.7`, `langchain-anthropic==1.7.6`, `claude-agent-sdk==0.2.174` (it bundles its own Claude Code CLI) and `mcp==2.3.0`, plus the ML stack that `classify_complaint` loads. The MCP servers run from this venv too.
- Go 1.27.2 (`agents/desk/go/go.mod`) for the hand-built Go engine; `run.py` builds it on first use. That first build fetches the module's dependencies (`github.com/modelcontextprotocol/go-sdk` v1.8.0 and what it requires) once, unless your Go module cache already holds them, so it needs the network that one time; every replay after it is offline.

### Replay (free)

Each run gets its own proxy run in replay mode: every model call comes from `agents/data/recordings/<set>/`, the lumen tools really run, and a request with no recording stops with a 404 naming the command that records it. It never falls back to a paid call.

```sh
.venv-agents/bin/python agents/run.py --set patterns          # the five patterns in Python (patterns-go for Go)
.venv-agents/bin/python agents/run.py --set desk-hand         # single + supervisor on 13 tickets (desk-hand-go, desk-langgraph, desk-sdk)
.venv-agents/bin/python agents/run.py --engine sdk --desk supervisor --tickets t001
.venv-agents/bin/python agents/proxy/proxy.py --set desk-hand # the proxy on its own, replaying one set; stop it with Ctrl-C
```

Each run prints `ok, N model calls, spent $0.0000; matches the recorded trace (kept)`: a replay never replaces a recorded trace, it checks the run still matches it.

### Scores and lesson data

```sh
.venv-agents/bin/python agents/evals/run_evals.py             # score every engine x desk, apply the capstone gate, write engine_scores.json
.venv-agents/bin/python agents/evals/run_evals.py --check     # the committed scores match the traces
.venv-agents/bin/python agents/derive.py --check              # the lesson data in agents/data/derived/ matches the traces
```

Both replay the judge's recorded calls (set `judge`), so they are free. After a new recording, run them without `--check` to rebuild the files, then rebuild the app.

### Recording (costs money)

Look at the estimate first; it calls nothing:

```sh
.venv-agents/bin/python agents/run.py --set desk-hand --dry-run
.venv-agents/bin/python agents/evals/run_evals.py --judge-dry-run
```

Then the record commands the dry run prints (`--mode record --max-cost 10`, or `run_evals.py --record-judge`) call the Claude API with `ANTHROPIC_API_KEY` from your environment. The key is read by the proxy only, never by an engine, and never logged or stored. Each command prints the estimate and worst case again and asks `y/N` (input that is not a terminal counts as no), the proxy checks the cap before every call, and a set the cap stops is scored "incomplete", never partly. `--refresh` pays for every call again, atomically per ticket. Record only after you have read and approved the printed estimate. `--yes` answers the question in advance: it exists for a scripted run after you have approved the estimate, and the dry runs never print it, so a pasted command always asks. See [agents/data/recordings/README.md](agents/data/recordings/README.md).

### Test the AI Agents code

No test calls the Claude API. The models are fakes, the Agent SDK runs against a fake CLI or a local stub, and the proxy records against a fake upstream into a temporary folder.

```sh
.venv-agents/bin/python -m pytest agents -q
(cd agents/desk/go && go test -race ./...)
.venv-agents/bin/python agents/fixtures/gen_contract.py --check   # the shared Python/Go contract is up to date
```

## Run the Data Fabric & Data Contracts track

A catalogue of the datasets the other tracks share, contracts for three of them, a validator in Python and a twin in Go that print identical verdicts, and a breaking change (`tickets.v2`) carried through a migration and a gate. Everything reads the other tracks' files in place and never writes to them. Nothing in this track calls a model, so every command below is free.

### Setup

```sh
python3 -m venv .venv-data && .venv-data/bin/pip install -r data/requirements.txt
```

- Python 3.12 or later, in the `.venv-data/` virtual environment at the repo root (git-ignored). `data/requirements.txt` holds `pyyaml==6.0.3` and `pytest>=8`; the rest is the standard library.
- Go 1.27.2 (`data/go/go.mod`), standard library only, for the twin validator.

### Validate a dataset

```sh
.venv-data/bin/python data/python/contract.py data/contracts/tickets.v1.yaml llms/data/tickets.jsonl
(cd data/go && go run ./validate ../contracts/tickets.v1.contract.json ../../llms/data/tickets.jsonl)
```

Both print one line per verdict, `OK  rule field: detail` or `FAIL rule field: detail`, then `verdicts: N ok, M failed`, and exit 1 when any verdict failed. `--age-days N` (Go: `-age-days N`) gives the file's age for the freshness rule. Go reads the contract's JSON export: after editing a contract, run `.venv-data/bin/python data/python/contract.py --export data/contracts/<name>.yaml`.

### Catalogue, quality and the gate

```sh
.venv-data/bin/python data/python/catalog.py           # catalog.json and lineage.json
.venv-data/bin/python data/python/quality.py           # verdicts.json, as of its stored date
.venv-data/bin/python data/python/migrate_tickets.py   # data/data/tickets.v2.jsonl from the v1 tickets
.venv-data/bin/python data/python/gate.py              # gate.json: every consumer green on the version it declares
.venv-data/bin/python data/python/quality.py --live    # freshness as of today; writes nothing
```

Each writes its file under `data/data/` and prints what it found. `quality.py` measures each dataset's age from its last git commit as of the date stored in `verdicts.json`, so the committed file does not go stale; `--as-of YYYY-MM-DD` moves that date. `--live` asks about today instead, and starts failing once a dataset is older than its contract's `max_age_days` (365 days). That is the check doing its job. Ages come from each file's last commit, by author time, which a rebase or cherry-pick keeps; a squash merge makes one new commit and loses them, so merge this track with a regular merge.

The catalogue and the verdicts read the other tracks' data in place, so a commit to a catalogued file in another track (re-recorded traces, a new saved model response, new ML reports) makes `catalog.py --check` and `quality.py --check` fail until you rerun `catalog.py`, `quality.py` and `gate.py` and commit what they write.

### Test the data code

```sh
.venv-data/bin/python -m pytest data -q
(cd data/go && go test ./...)
.venv-data/bin/python data/python/catalog.py --check          # catalog.json and lineage.json match the datasets
.venv-data/bin/python data/python/quality.py --check          # verdicts.json matches, as of its stored date
.venv-data/bin/python data/python/gate.py --check             # gate.json matches
.venv-data/bin/python data/python/migrate_tickets.py --check  # tickets.v2.jsonl matches the v1 file
.venv-data/bin/python data/python/contract.py --export --check  # every contract's JSON export is current
.venv-data/bin/python data/fixtures/gen_cases.py --check      # the shared validator cases are current
```

The pytest suite includes the twin test (both command lines on every fixture case, byte for byte) and runs every lesson's Try-it command once (`data/python/tests/test_lesson_tryits.py`). It leaves every committed file as it found it.

Commit dates need the full git history. In a shallow clone (CI's default checkout) or a zip or `git archive` copy, `catalog.py --check` and `quality.py --check` compare everything except the commit-derived fields (`last_commit`, the ages and the freshness detail) and print `no git history: commit dates not compared`, and the tests that measure real commit dates skip with `needs full git history: run git fetch --unshallow`. In CI, check out with `fetch-depth: 0` to run them.

## Run the Ontology track

Lumen's ontology, `ontology/lumen.yaml`: the classes, properties, relations, individuals and constraints a support ticket's labels rely on, quoted from the labelling guide and the ML category map, and a sixth section, `actions`, that says which agent role may take which action and on what condition. The scripts load and check it, generate the prompt definitions, the triage JSON schema and the `label_ticket` tool from it, validate every saved model answer and agents tool call against it, audit the guide, the prompts, the category map and the data contracts for disagreement, and check every recorded agents action against the `actions` section. Everything reads the other tracks' files in place and never writes to them. Nothing in this track calls a model, so every command below is free. Python only: there is no Go twin.

### Setup

```sh
python3 -m venv .venv-ontology && .venv-ontology/bin/pip install -r ontology/requirements.txt
```

- Python 3.12 or later, in the `.venv-ontology/` virtual environment at the repo root (git-ignored). `ontology/requirements.txt` holds `pyyaml==6.0.3` and `pytest>=8`; the rest is the standard library.

### Check and validate

```sh
.venv-ontology/bin/python ontology/python/ontology.py check                                    # consistency findings; exit 1 on any error
.venv-ontology/bin/python ontology/python/ontology.py validate Ticket llms/data/tickets.jsonl   # errors and warnings, then a count
```

`validate` prints one line per error or warning, `level rule subject: detail`, then `N records, N errors, N warnings, N manual`, and exits 1 when any record has an error. `--partial` checks a model's answer, which carries only some fields; `--manual` also prints the rules left to a person.

### Generate, ground, audit and govern

```sh
.venv-ontology/bin/python ontology/python/ontology.py export   # ontology.json for the app
.venv-ontology/bin/python ontology/python/generate.py          # generated.json: definitions, schema, tool, and the diff with the hand-written ones
.venv-ontology/bin/python ontology/python/grounding.py         # validation.json: the saved model outputs, per source and per violation type
.venv-ontology/bin/python ontology/python/audit.py             # audit.json: one row per artefact per fact, and the gate
.venv-ontology/bin/python ontology/python/governance.py        # governance.json: every recorded agents action, roles x actions, and the gate
```

Each writes its file under `ontology/data/derived/` and prints what it found. `audit.py` prints the gate, `ok` or `RED`, with every disagreement, and exits 0 either way; a pipeline that should stop on a disagreement reads `gate.ok` from `audit.json`. `governance.py` does the same with its own gate, red when any recorded action is not permitted, and refuses to run (exit 1, nothing written) on an ontology whose `check` has errors; `--ontology PATH` checks the traces against a copy. The scripts read the other tracks' files in place, so a change to the labelling guide, the prompts, a saved response, a trace, the category map or a contract makes the matching `--check` fail until you rerun the script and commit what it writes.

### Test the ontology code

```sh
.venv-ontology/bin/python -m pytest ontology -q
.venv-ontology/bin/python ontology/python/ontology.py export --check   # ontology.json matches lumen.yaml
.venv-ontology/bin/python ontology/python/generate.py --check          # generated.json matches
.venv-ontology/bin/python ontology/python/grounding.py --check         # validation.json matches the saved outputs
.venv-ontology/bin/python ontology/python/audit.py --check             # audit.json matches the artefacts
.venv-ontology/bin/python ontology/python/governance.py --check        # governance.json matches section 6 and the agents traces
```

Each `--check` catches drift in its file and nothing else: `audit.py --check` passes while the gate inside `audit.json` is red. The pytest suite runs every lesson's Try-it command once (`ontology/python/tests/test_lesson_tryits.py`) and leaves every committed file as it found it.

## Run the AI Infrastructure track

The AI gateway for Lumen's desk: a loopback HTTP server in the Messages API shape that puts a chain of stages (tag, budget, cache, route, limit, retry) between an app and the model, books every answer in a ledger and writes one trace per request. `infra/gateway.yaml` configures it, the Go twin in `infra/go/gateway/` runs the same chain, and `replay.py` sends the recorded traffic of the AI Agents track through it and applies a gate. Nothing here calls a model unless you pass `--live`, which prints an estimate, asks `y/N` and stops at `--max-cost`; no printed command below uses it.

### Setup

```sh
python3 -m venv .venv-infra && .venv-infra/bin/pip install -r infra/requirements.txt
```

- Python 3.12 or later, in the `.venv-infra/` virtual environment at the repo root (git-ignored). `infra/requirements.txt` holds `pyyaml==6.0.3`, `pytest>=8` and `httpx==0.28.1` (the agents proxy, imported read-only by the tests and `replay.py --start`); the gateway's request path is the standard library.
- Go 1.27.2 for the twin (`infra/go/go.mod`).
- The recordings the replay reads are the AI Agents track's, already in the repo under `agents/data/recordings/`. The proxy you start by hand runs from the `.venv-agents/` venv (see the AI Agents setup).

### Start the proxy and the gateway

Each in a terminal of its own, from the repo root. The proxy replays one recorded set on 8791 (`--set` takes a set name such as `patterns` or `desk-hand`); the gateway listens on 8790 and forwards to it.

```sh
.venv-agents/bin/python agents/proxy/proxy.py --port 8791 --set patterns
.venv-infra/bin/python infra/python/gateway.py --port 8790 --run first
```

`--run` names the trace file, `infra/data/traces/<run>.jsonl` (the start time if you leave it out). Stop either with Ctrl-C. The ledger (`infra/data/ledger.jsonl`) and the cache (`infra/data/cache.json`) persist between starts, so a budget does not reset when the gateway restarts; delete both for a clean run. Both are git-ignored, as are the traces.

### Replay and the gate

```sh
.venv-infra/bin/python infra/python/replay.py --start --gate    # proxies and gateway in-process; rewrites infra/data/derived/
.venv-infra/bin/python infra/python/replay.py --start --check   # fail if a derived file drifted; writes nothing there
```

`--start` (the default) sends the 957 recorded calls of the AI Agents track through the gateway, in the order its runs made them, and prints the totals. `--gate` adds four lines, each `ok` or `FAILED`: `ledger` (for each desk set the gateway's ledger, plus the recorded cost of that set's cache hits, equals the agents traces' cost), `budgets` (spend against each team's limit, and the refusals), `stages` (every trace has its stages in order and a refusal stops at the refusing stage) and `figures` (the hit rate and p95 recomputed from the traces and the recordings agree). The gate is `gate.ok` in `capstone.json`. `--gateway URL` drives a gateway you started by hand instead (the proxy for the set must be running), `--sets a,b` replays only those sets and `--out DIR` moves the run's ledger, cache and traces.

Exit codes: 0 when the run finished (a failed gate line is printed, not an exit status, as with the Ontology audit), or for `--check` when the files are up to date; 1 when `--check` finds a derived file drifted; 2 when the config is invalid or the gateway or proxy could not be started or reached, or when `--check` is combined with `--gateway`, `--sets` or another config.

The replay runs on replay time: the gateway's clock advances by each call's recorded milliseconds, so the rate limiter judges the recorded cadence and not the speed of the replay. A by-hand replay through `--gateway` runs on the wall clock, so its limiter figures differ from the committed ones. There is no run of the agents track's `run.py` through the gateway: `run.py` owns port 8790 for its own proxy and the agents code is read-only, so lesson 1 calls the hand-built chain pattern through the gateway instead.

### Trace queries

```sh
.venv-infra/bin/python infra/python/traces.py infra/data/derived/run/capstone.jsonl --team desk --outcome cached
.venv-infra/bin/python infra/python/traces.py infra/data/derived/run/capstone.jsonl --tag t061 --costliest 3
.venv-infra/bin/python infra/python/traces.py infra/data/derived/run/capstone.jsonl --tree <TRACE-ID>
```

`traces.py RUN.jsonl` prints one line per trace: id, status, team, tag, milliseconds, cost and outcome. The filters are `--team`, `--tag` and `--outcome` (`ok`, `cached`, `refused`, `limited` and so on); `--slowest N` and `--costliest N` rank what is left; `--tree ID` prints the trace and the traces below it by parent id. `infra/data/derived/run/` holds the last `replay.py --start` run and is git-ignored, so run the replay first.

### Test the infrastructure code

No test calls the Claude API. The upstream is the agents replay proxy or a loopback fixture, and live mode is tested against a fake upstream.

```sh
.venv-infra/bin/python -m pytest infra -q
(cd infra/go && go test ./...)
.venv-infra/bin/python infra/python/config.py --check          # config.json matches gateway.yaml
.venv-infra/bin/python infra/python/replay.py --start --check  # capstone.json, ledger.json and traces.sample.json match the replay
```

The pytest suite includes the twin test (the Python and Go chains on every fixture case), and runs every lesson's Try-it command once (`infra/python/tests/test_lesson_tryits.py`), its `--check` among them. It leaves every committed file as it found it. As with the other tracks, `--check` catches drift in the derived files only: `replay.py --check` passes while a gate line inside `capstone.json` is `FAILED`.

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
