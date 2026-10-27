# Live OAuth check (127.0.0.1 only)

This check runs the real OAuth 2.1 flow end to end, with real processes. Specifically:

- The SDK's OAuth client runs authorization code + PKCE (S256) against the local test AS.
- The token is bound to one resource server (RFC 8707).
- That server checks the token.

The pytest suites cover each piece separately. This check runs the whole chain. It needs ports 9000, 8765 and 8766 free, because the client compares the server's metadata with the canonical URIs.

The authorization server (AS) is **test-only**. It auto-approves every request as the fixed user `user-1`, and it must never listen beyond 127.0.0.1. No token or key is printed at any step.

Run every command from the repository root, each server in its own terminal.

## 1. Start the test AS (it creates the signing key)

```sh
.venv-mcp/bin/python mcp/python/auth_server.py
```

On its first start, the AS writes `mcp/data/dev-signing.key`. The file holds 32 random bytes as hex, has mode 0600, and is git-ignored. Later starts reuse it.

To keep the key elsewhere, set `LUMEN_SIGNING_KEY_FILE=/path/to/key` for the AS and for both servers.

## 2. Start the resource servers

They read the same key file. Without it, they refuse to start and say to start the AS first.

```sh
.venv-mcp/bin/python mcp/python/read_server.py --http          # lumen-read  :8765, scope tickets:read
cd mcp/go && go run ./opsserver -labels /tmp/lumen-live-labels.jsonl   # lumen-ops :8766 (Go), scope tickets:write
```

You can swap either server for its twin:
- Python lumen-ops: `.venv-mcp/bin/python mcp/python/ops_server.py --labels /tmp/lumen-live-labels.jsonl`.
- Go lumen-read: `cd mcp/go && go run ./readserver -http`.

## 3. Run the flow

The pre-registered public client `lumen-cli` (ruling R4, primary):

```sh
.venv-mcp/bin/python mcp/python/auth_live_check.py --url http://127.0.0.1:8765/mcp \
    --save-token /tmp/lumen-read.tok get_ticket '{"id": "t001"}'
```

Expected:
- `registration: pre-registered lumen-cli`
- `token claims: aud=http://127.0.0.1:8765/mcp scope=tickets:read client_id=lumen-cli sub=user-1`
- the ticket under `untrusted_body`

Dynamic Client Registration (R4, secondary):

```sh
.venv-mcp/bin/python mcp/python/auth_live_check.py --url http://127.0.0.1:8765/mcp --dcr \
    search_tickets '{"query": "refund", "limit": 1}'
```

Expected: `registration: DCR`, and a `client_id` that is a fresh UUID.

A confirmed write on lumen-ops:

```sh
.venv-mcp/bin/python mcp/python/auth_live_check.py --url http://127.0.0.1:8766/mcp --yes \
    label_ticket '{"id": "t001", "label": "billing"}'
cat /tmp/lumen-live-labels.jsonl
```

Expected:
- `aud=http://127.0.0.1:8766/mcp scope=tickets:write`
- `{"label": "billing", "ticket_id": "t001", "written": true}`
- a row with `"client_id":"lumen-cli"` and `"confirmed":true`

The `client_id` comes from the verified token, not from anything the client says.

## 4. Use the token from the clients

Pass the token as a file, not on the command line (ruling R26).

```sh
.venv-mcp/bin/python mcp/python/client.py --url http://127.0.0.1:8765/mcp --token-file /tmp/lumen-read.tok discover
(cd mcp/go && go run ./client -url http://127.0.0.1:8765/mcp -token-file /tmp/lumen-read.tok discover)
```

Both print the same bytes. `LUMEN_TOKEN` also works.

`--token` / `-token` works too, but it prints this warning, because a command line is visible in the process list and the shell history:

```
client: warning: a token on the command line is visible in the process list and shell history; use a token file or LUMEN_TOKEN
```

## 5. A token for one server is no key to the other

Send the lumen-read token to lumen-ops. Use `curl` here so that you can see the status code:

```sh
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8766/mcp \
    -H "Authorization: Bearer $(cat /tmp/lumen-read.tok)" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

The command prints `401`. The response's `WWW-Authenticate` header names `resource_metadata="http://127.0.0.1:8766/.well-known/oauth-protected-resource/mcp"`.

The audience is checked before the scope (ruling R24). The token was issued for `http://127.0.0.1:8765/mcp`, so at lumen-ops it is an invalid token, whatever its scope says.

Both clients fail too. They print nothing on stdout, and they print the same single line on stderr, then exit 1:

```sh
.venv-mcp/bin/python mcp/python/client.py --url http://127.0.0.1:8766/mcp --token-file /tmp/lumen-read.tok tools
(cd mcp/go && go run ./client -url http://127.0.0.1:8766/mcp -token-file /tmp/lumen-read.tok tools)
```

```
client: the server refused the request: HTTP 401 Unauthorized
```

## 6. Clean up

Stop the three processes, then:

```sh
rm -f /tmp/lumen-read.tok /tmp/lumen-live-labels.jsonl
```

Keep `mcp/data/dev-signing.key` for the next run, or delete it to rotate the key. Deleting it invalidates every token issued so far.
