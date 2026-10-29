# AI Agents: running and testing the track

Moved from the repository README on 2026-10-29; the commands are unchanged. Run everything from the repository root.

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
