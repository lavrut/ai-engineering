# Ontology: running and testing the track

Moved from the repository README on 2026-10-29; the commands are unchanged. Run everything from the repository root.

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
