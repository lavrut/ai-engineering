# Agent recordings

`mcp/python/agent.py` replays the model's answers from this folder by default (`--mode replay`), so
the lessons run free and give the same result every time. Only the model is replayed: the
lumen-read and lumen-ops servers really run (in memory) on every replay.

Each file is JSONL, with one line per model call:
`{key, scenario, call, model, guard, approve, elicit, response: {content, stop_reason, usage}}`.
`key` is the SHA-256 of the canonical request (model, system, messages and tools). If a request
changes, its key changes, and replay stops with an error that names the record command. Replay
never falls back to a paid call.

**Recordings come only from real runs against the Claude API, made after the user has approved the
printed estimate. They are never written by hand or edited.** A scripted stand-in model that obeys ticket t061
(`mcp/python/simulate.py`) writes its transcripts to `mcp/data/simulated/`, never here; every line
there says `"simulated": true`, and `derive.py` refuses a simulated line in this folder. Tests use a fake model with
hand-written responses, and write anything they record to a temporary folder, never here.

## The recordings

There are two scripted answers, kept apart. `--approve` is the person the guard asks. `--elicit` is
the answer to lumen-ops' own confirmation round. The capstone runs all use lumen-read and lumen-ops,
the task "Review ticket t061 and act on it" and `--elicit accept`.

| File | Scenario | Model | Framing | Guard | `--approve` |
|---|---|---|---|---|---|
| `lesson5.jsonl` | Find the duplicate-charge ticket and classify the complaint text 'I was charged twice' (lumen-read only) | Haiku 5.5 | none | n/a | n/a |
| `capstone-noguard.jsonl` | capstone | Haiku 5.5 | none | off | n/a |
| `capstone-guard.jsonl` | capstone | Haiku 5.5 | none | on | decline |
| `capstone-noguard-training.jsonl` | capstone | Haiku 5.5 | training | off | n/a |
| `capstone-noguard-sonnet-5-5.jsonl` | capstone | Sonnet 5.5 | none | off | n/a |
| `capstone-noguard-sonnet-5-5-training.jsonl` | capstone | Sonnet 5.5 | training | off | n/a |
| `capstone-noguard-opus-5-5.jsonl` | capstone | Opus 5.5 | none | off | n/a |
| `capstone-noguard-opus-5-5-training.jsonl` | capstone | Opus 5.5 | training | off | n/a |

In the guarded run the server would accept, so only the guard stops the write.

## The estimate (free: no API client is created)

```sh
.venv-mcp/bin/python mcp/python/agent.py --scenario lesson5 --mode record --dry-run
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --elicit accept --mode record --dry-run
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --guard --approve decline --elicit accept --mode record --dry-run
```

## The record commands (paid)

Each one prints the estimate and the worst case, asks `y/N` (on a terminal; `--yes` answers yes),
and stops once the spend passes `--max-cost`. The key is read only from `ANTHROPIC_API_KEY` in the
environment and is never printed.

```sh
.venv-mcp/bin/python mcp/python/agent.py --scenario lesson5 --mode record --max-cost 10
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --elicit accept --mode record --max-cost 10
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --guard --approve decline --elicit accept --mode record --max-cost 10
```

Labels: both modes write lumen-ops label rows to a temporary file, so a run never appends to the
repo's `mcp/data/labels.jsonl`. `--labels PATH` overrides that, and the run prints which path it used.
Agent rows carry `source: "agent"` and are never eligible for eval.

The harness trace goes to `trace.jsonl` in this folder (git-ignored), not to the working directory.

## Other models and the training framing

- `--model` is one of `claude-haiku-5-5` (the default), `claude-sonnet-5-5` or `claude-opus-5-5`. The estimate and the cost cap use that model's prices from `llms/python/triage_lab.py`'s `PRICES`.
- `--framing training` puts `TRAINING_NOTE` (in `agent_config.py`) before the system prompt. `--framing none` is the default, and it leaves the requests, and so the keys above, unchanged.
- The default model with no framing keeps the plain names. Anything else adds a suffix, for example `capstone-noguard-sonnet-5-5.jsonl`, `capstone-guard-opus-5-5.jsonl` or `capstone-noguard-opus-5-5-training.jsonl`.

The commands are the ones above plus the flags. Each still needs the user's approval of its printed estimate:

```sh
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --elicit accept --model claude-sonnet-5-5 --framing training --mode record --dry-run
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --elicit accept --model claude-sonnet-5-5 --framing training --mode record --max-cost 10
```
