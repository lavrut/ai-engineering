# HTTP material removed from lesson 4 (R39), for lessons 7 and 9

Plain notes for Task 14. Not a lesson. All of it was run and verified in Task 13.

## Concept: the --url flag and token flags

`--url` talks to a Streamable HTTP endpoint. The client has token flags (`--token-file`, then `--token`, then the `LUMEN_TOKEN` environment variable; `--token` warns because a command line is visible in the process list). The token goes in an `Authorization: Bearer` header on every request, never in the URL, and only with `--url`.

## Removed subsection

### HTTP needs a token

`lumen-read` over HTTP is an OAuth resource server: every request needs a bearer token issued for it. The client has flags for a token (`--token-file`, `--token` and the `LUMEN_TOKEN` environment variable), and sends it in an `Authorization: Bearer` header, never in the URL. Where tokens come from is lesson 9. Here you only need to see what happens without one.


## Section 2 walkthrough bullets (mcp-client section 2)

- `HttpTarget(url, headers)` is a URL plus the headers every request carries. `http_target(url, token)` is the one place the HTTP path is built. With a token it adds `Authorization: Bearer ...`, and the URL stays exactly as given.

- `if isinstance(target, str) and target.startswith(("http://", "https://"))` sends a URL down the HTTP path. Anything else, such as `StdioServerParameters` or an in-process server in a test, goes to `Client(target, ...)`.

- `http.event_hooks["response"].append(record)` remembers the status of the last non-2xx answer to a POST. A failed call can then say "HTTP 401 Unauthorized" instead of an SDK stack.

- The comment on `connect` notes that the SDK follows a redirect only within the endpoint's own origin, so the headers never go elsewhere.


## Try it step removed

### Try HTTP without a token

This needs three terminals, all from the repo root. The first command starts the test authorization server. It is **test only** and it auto-approves every request, so it must stay on `127.0.0.1`. On its first start it writes `mcp/data/dev-signing.key`, which is git-ignored. The HTTP server reads the same key, and refuses to start without it.

Terminal 1:

```bash
.venv-mcp/bin/python mcp/python/auth_server.py
```

Terminal 2 starts `lumen-read` over HTTP on `127.0.0.1:8765`:

```bash
.venv-mcp/bin/python mcp/python/read_server.py --http
```

Terminal 3 connects with no token. You should see one line, `client: the server refused the request: HTTP 401 Unauthorized`, and exit code 1. The Go client prints the same line:

```bash
.venv-mcp/bin/python mcp/python/client.py --url http://127.0.0.1:8765/mcp discover
```

```bash
cd mcp/go && go run ./client -url http://127.0.0.1:8765/mcp discover
```

Stop both servers with Ctrl-C when you are done. Lesson 9 shows how to get a token that this server accepts.


## Diagram captions (mcp-client steps 1 and 2) that mentioned HTTP

- Step 1: flags pick the server (--stdio or --url), where the token comes from (--token-file, then --token, then LUMEN_TOKEN).
- Step 2: Over HTTP the token goes in an Authorization: Bearer header on every request and never in the URL. A refused request is reported with its status in one line.


## Verified outputs
- With no token: `client: the server refused the request: HTTP 401 Unauthorized`, exit 1, from both the Python and the Go client (`go run ./client -url http://127.0.0.1:8765/mcp discover`).
- Needs the test AS running first (it creates `mcp/data/dev-signing.key`, git-ignored; the HTTP server refuses to start without it).
