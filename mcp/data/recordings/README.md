# Agent recordings

`mcp/python/agent.py` replays the model's answers from this folder by default (`--mode replay`), so
the lessons run free and give the same result every time. Only the model is replayed: the
lumen-read and lumen-ops servers really run (in memory) on every replay.

Each file is JSONL, with one line per model call:
`{key, scenario, call, model, guard, elicit, response: {content, stop_reason, usage}}`.
`key` is the SHA-256 of the canonical request (model, system, messages and tools). If a request
changes, its key changes, and replay stops with an error that names the record command. Replay
never falls back to a paid call.

**Recordings come only from real runs against the Claude API, made after the user has approved the
printed estimate. They are never written by hand or edited.** Tests use a fake model with
hand-written responses, and write anything they record to a temporary folder, never here.

## The three recordings

| File | Scenario | Servers | Guard | Scripted answer |
|---|---|---|---|---|
| `lesson5.jsonl` | Find the duplicate-charge ticket and classify the complaint text 'I was charged twice' | lumen-read | n/a | n/a |
| `capstone-noguard.jsonl` | Review ticket t061 and act on it | lumen-read, lumen-ops | off | accept |
| `capstone-guard.jsonl` | Review ticket t061 and act on it | lumen-read, lumen-ops | on | decline |

## The estimate (free: no API client is created)

```sh
.venv-mcp/bin/python mcp/python/agent.py --scenario lesson5 --mode record --dry-run
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --elicit accept --mode record --dry-run
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --guard --elicit decline --mode record --dry-run
```

## The record commands (paid)

Each one prints the estimate and the worst case, asks `y/N` (on a terminal; `--yes` answers yes),
and stops once the spend passes `--max-cost`. The key is read only from `ANTHROPIC_API_KEY` in the
environment and is never printed.

```sh
.venv-mcp/bin/python mcp/python/agent.py --scenario lesson5 --mode record --max-cost 10
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --elicit accept --mode record --max-cost 10
.venv-mcp/bin/python mcp/python/agent.py --scenario capstone --guard --elicit decline --mode record --max-cost 10
```

The two capstone runs write label rows to `mcp/data/labels.jsonl` (git-ignored) unless `--labels PATH`
says otherwise. Agent rows carry `source: "agent"` and are never eligible for eval.
