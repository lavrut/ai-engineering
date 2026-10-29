# Data Fabric & Data Contracts: running and testing the track

Moved from the repository README on 2026-10-29; the commands are unchanged. Run everything from the repository root.

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
