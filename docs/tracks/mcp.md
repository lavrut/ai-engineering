# MCP: running and testing the track

Moved from the repository README on 2026-10-29; the commands are unchanged. Run everything from the repository root.

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
