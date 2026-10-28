# Agent recordings

`agents/run.py` replays every engine's model calls from this folder by default (`--mode replay`), so the
lessons and the evals run free and give the same result every time. Only the model is replayed: the
lumen-read and lumen-ops servers really run on every replay (in memory for the Python engines, over stdio
for the Go engine).

Recordings are made by the record/replay proxy (`agents/proxy/proxy.py`), which every engine talks to at
`http://127.0.0.1:8790`. One file per request: `<set>/<key>.json` holds `{request_summary, response}`.
`key` is the SHA-256 of canonical JSON over `{method, path, body}`, with the volatile parts of the body
removed or masked (below). No header is in the key or stored, so no key, session id or account detail is
either. If a request changes, its key changes, and replay stops with a 404 that names the command to record
it. Replay never falls back to a paid call.

**Recordings come only from real runs against the Claude API, made after the user has approved the printed
estimate. They are never written by hand or edited.** Tests record against a fake upstream into a temporary
folder (`agents/tests/test_run.py`, `agents/proxy/tests/`), never here.

## The sets

Each set is one `--set NAME` subdirectory of this folder. Every run is one engine, one desk and one ticket,
with its own proxy run (ruling R25), so the call limit (`--max-calls`) is per ticket. The scripted person the
guard asks says no (`approve decline`); lumen-ops' own confirmation would say yes (`elicit accept`), so only
the guard stops a write.

| Set | Engine | Desks | Tickets | Runs |
|---|---|---|---|---|
| `patterns` | `hand` (Python) | the five patterns: chain, route, parallel, orchestrate, evaluate_optimize | t001, t007, t061 | 15 |
| `patterns-go` | `hand-go` (Go) | the same five patterns | t001, t007, t061 | 15 |
| `desk-hand` | `hand` (Python) | single, supervisor | all 13 eval tickets | 26 |
| `desk-langgraph` | `langgraph` | single, supervisor | all 13 | 26 |
| `desk-sdk` | `sdk` (Claude Agent SDK) | single, supervisor | all 13 | 26 |
| `desk-hand-go` | `hand-go` (Go, its own MCP Tools) | single, supervisor | all 13 | 26 |
| `judge` | the eval's judge (`agents/evals/run_evals.py --record-judge`) | | one request per distinct ticket and reply | 101 recordings |

Go records its own sets (ruling R23). Go writes its prompts' JSON the way Python does, and a Go desk's first
request has the same key as Python's, but nothing holds every later request to the same bytes (a tool
result's number formatting, for one: Python writes 1.0 where Go writes 1), so Go is not promised Python's
recordings.

Notes on what a set holds:
- `parallel` runs with one worker in `run.py` (the same requests as three workers, in a fixed trace order).
  Its two `search_tickets` lookups send the same request, so they are recorded once and answered alike.
- `--max-calls` is 12 per ticket, and 20 for the supervisor, whose typical run is 11 or 12 calls (R25, raised by R61).

## The store is pruned

Each set holds exactly the recordings its traces use: the keys in the runs' `proxy_calls` ledgers, and for
`judge` the requests the current traces ask for (one per distinct ticket and reply). A re-record leaves the
superseded files behind; replay never reads them, but they ship with the repo, so they are deleted (ruling R73).
One earlier round's superseded Agent SDK replies repeated the per-user temp directory the CLI gives the model.

```sh
.venv-agents/bin/python agents/prune.py            # dry run: lists what it would delete, per set
.venv-agents/bin/python agents/prune.py --delete   # deletes those files
.venv-agents/bin/python agents/derive.py           # sets.json then reports orphans 0 (judge included)
```

A set with a missing trace is never pruned (its unfinished runs may still need its files), and the judge set
waits until every desk set is complete. `agents/tests/test_data_hygiene.py` scans every recording, trace and
derived file for absolute local paths, user ids, email addresses and key prefixes, joining each streamed reply's
deltas first, so a new recording that repeats a path fails the suite before it is committed.

## What a recording holds

One JSON file per key: `{request_summary, response, upstream}`. `response` is the status, the allow-listed
headers and the body (JSON, or the SSE events byte for byte). `upstream` (ruling R62) is the recorded call
itself: `ms` (wall clock from send to last byte), `model`, and the API's four usage fields kept apart:
`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`. It sits beside the
response and is never part of the key. The eval's cost and latency come from it, replayed or live, with cache
tokens priced at the published multipliers (write 1.25x, read 0.10x the input price; ruling R64), so engines are
compared on one basis and the Agent SDK's prompt caching shows up as a lower cost, not a missing one.

`--mode record --refresh` ignores what the store already has and pays for every call again. It is atomic per
ticket (ruling R65): the new recordings replace the old ones only when the ticket's run finishes; a run that
stops early leaves the old recordings and the old trace as they were. Refresh only the tickets you need with
`--tickets`.

## What the key masks

- `metadata.user_id` (the Agent SDK puts a fresh session id in it per run).
- In the Agent SDK's `# Environment` block: the working directory, git flag, platform, shell, OS version and
  today's date (they differ per machine and per day).
- In an Agent SDK sub-agent's hand-back (the result of an `Agent` tool call, in the supervisor's later
  requests): the trailing footer's `agentId: <hex>` and `duration_ms: <n>`, a fresh id and a wall-clock time on
  every run (rulings R47, R53). Only that footer, at the very end of the result: the report above it, even a
  `duration_ms:` line in it, stays in the key, as do `subagent_tokens` and `tool_uses`.

Not masked, because replay reproduces it: the CLI's trailing `<total_tokens>N tokens left</total_tokens>`
system message. N is a fixed budget minus the tokens the recorded replies reported.

## The estimate (free: nothing is called)

```sh
.venv-agents/bin/python agents/run.py --set patterns --dry-run
.venv-agents/bin/python agents/run.py --set patterns-go --dry-run
.venv-agents/bin/python agents/run.py --set desk-hand --dry-run
.venv-agents/bin/python agents/run.py --set desk-langgraph --dry-run
.venv-agents/bin/python agents/run.py --set desk-sdk --dry-run
.venv-agents/bin/python agents/run.py --set desk-hand-go --dry-run
```

Each prints the runs, the calls (typical and max), the typical cost, the worst case, the cap and the
per-ticket `--max-calls`, and the exact record command.

## The record commands (paid)

Only after the user's explicit approve of that set's printed estimate. The key is read only from
`ANTHROPIC_API_KEY` in the environment, by the proxy, and is never printed or stored; the engines always send
the dummy key. Without the key, record mode refuses before anything listens. It asks `y/N` on a terminal
before the first paid call (anything else is no). `--yes` skips the question for a scripted run after the
printed estimate was approved; no printed command carries it (ruling R71).

```sh
.venv-agents/bin/python agents/run.py --set patterns --mode record --max-cost 10
.venv-agents/bin/python agents/run.py --set patterns-go --mode record --max-cost 10
.venv-agents/bin/python agents/run.py --set desk-hand --mode record --max-cost 10
.venv-agents/bin/python agents/run.py --set desk-langgraph --mode record --max-cost 10
.venv-agents/bin/python agents/run.py --set desk-sdk --mode record --max-cost 10
.venv-agents/bin/python agents/run.py --set desk-hand-go --mode record --max-cost 10
```

`--max-cost` is for the whole set: each ticket's proxy run gets what is left, and checks it before every
call. When the cap stops a run, recordings so far are kept, that run writes no trace, and the set stops
incomplete (the eval reads it so). A run that reaches its own `--max-calls` also writes no trace and is
incomplete, but the set goes on to the next ticket.

Labels: record mode writes lumen-ops label rows to `mcp/data/labels.jsonl` (git-ignored); replay writes them
to a temporary file.

## Traces

Every finished run writes `agents/data/traces/<set>/<engine>-<desk>-<ticket>.json`: the trace records
`{agent, kind, name, tokens_in, tokens_out, ms}`, the run's output, its `errors`, the engine's own `cost_usd`
(the Agent SDK's client-side estimate; `null` elsewhere) and the proxy's `spent` and `paid_calls` for that
run. A replay sets every `ms` to `null` (a replayed call's latency is the local proxy's) and never replaces a
recorded trace: it checks the run still matches it.
