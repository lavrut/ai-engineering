# AI Infrastructure: running and testing the track

Moved from the repository README on 2026-10-29; the commands are unchanged. Run everything from the repository root.

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
