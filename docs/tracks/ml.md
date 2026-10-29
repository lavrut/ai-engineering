# Machine Learning: running and testing the track

Moved from the repository README on 2026-10-29; the commands are unchanged. Run everything from the repository root.

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
