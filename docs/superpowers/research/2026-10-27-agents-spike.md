# Agents track spike: how each SDK talks to the API (2026-10-27)

Task 1, Step 1. Every finding below was observed locally with no API key (`sk-ant-dummy`) and no
network model call. Each SDK was pointed at a dummy HTTP server on 127.0.0.1 that logged the method,
path, header *names* (never values) and body, and answered a canned message or a canned SSE stream.
Versions: the exact pins in `agents/requirements.txt` (anthropic 1.17.0, langchain-anthropic 1.7.6,
claude-agent-sdk 0.2.174 with bundled CLI 2.1.310). The Agent SDK ran with `HOME` and
`CLAUDE_CONFIG_DIR` pointed at an empty scratch directory and a cleared environment (`env -i`), with
`tools=[]`, `setting_sources=[]`, `model="claude-haiku-5-5"`.

## 1. Summary

| SDK | Redirect | Path | Streams by default | Volatile body fields |
|---|---|---|---|---|
| `anthropic` | `base_url=` (or `ANTHROPIC_BASE_URL`) | `POST /v1/messages` | no (`messages.stream` sets `"stream": true`) | none |
| `langchain-anthropic` | `ChatAnthropic(base_url=...)` | `POST /v1/messages` | no for `invoke`, yes for `.stream()` | none |
| `claude-agent-sdk` | `ClaudeAgentOptions(env={"ANTHROPIC_BASE_URL": ...})`: **works** | `POST /v1/messages?beta=true` | **yes, always** | `metadata.user_id`; the `# Environment` block (cwd, git flag, platform, shell, OS version, date) |

**The Agent SDK can be proxied.** The bundled CLI honours `ANTHROPIC_BASE_URL` passed through
`ClaudeAgentOptions(env=...)`: every model request went to the dummy server, and none was attempted
elsewhere that the dummy could see. The message-stream-capture fallback is not needed.

End to end, through the real proxy (`agents/proxy/proxy.py`, record mode with the dummy as upstream,
then replay mode with the dummy stopped), all three SDKs produced identical results: `anthropic`
create + stream, `langchain-anthropic` invoke + stream, and the Agent SDK with an in-process MCP tool
(tool_use turn, tool result, final text) and with the title helper. The Agent SDK replay also hit
from a different working directory, which proves the environment masking below.

## 2. Per SDK

### anthropic 1.17.0
- `POST /v1/messages`, JSON body exactly as passed: `{max_tokens, messages, model}`; `+ stream: true` for `messages.stream`.
- Headers: `accept, accept-encoding, anthropic-version, connection, content-length, content-type, host, user-agent, x-api-key, x-stainless-arch, x-stainless-async, x-stainless-lang, x-stainless-os, x-stainless-package-version, x-stainless-read-timeout, x-stainless-retry-count, x-stainless-runtime, x-stainless-runtime-version, x-stainless-timeout`; streaming adds `x-stainless-helper-method, x-stainless-stream-helper`.
- Two identical calls: identical bodies. Volatile fields: none.

### langchain-anthropic 1.7.6
- `POST /v1/messages`, body `{max_tokens, messages, model}`; `.stream()` adds `stream: true`; `bind_tools` adds `tools` (`name, input_schema, description`).
- `invoke` does not stream. Headers are the anthropic set plus `x-stainless-raw-response`.
- Identical calls give identical bodies. Volatile fields: none. A LangGraph or `create_agent` run is
  a sequence of these requests; each is keyed on its full message history.

### claude-agent-sdk 0.2.174 (bundled CLI 2.1.310)
- Requests per `query("ping")`: **one `HEAD /api/hello`** (connectivity probe, no body) then
  **one `POST /v1/messages?beta=true`**. With a longer prompt there is a **second POST: a session
  title helper** (system text "You are naming a coding session ..."), fired alongside the main request,
  also streamed, same model. A tool turn adds one POST per model turn, as expected. No
  `/v1/messages/count_tokens` request was seen. Compaction was not triggered (tiny contexts).
- With `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` the HEAD probe **and** the title helper are gone:
  one POST per model turn.
- Every POST streams (`"stream": true`).
- Body keys: `context_management, max_tokens, messages, metadata, model, output_config, stream,
  system, thinking, tools` (the title helper has no `context_management`/`thinking`).
  - `max_tokens` is **128000** by default; `CLAUDE_CODE_MAX_OUTPUT_TOKENS=4000` makes it 4000.
  - `thinking: {"type": "adaptive", "display": "updates"}`, `output_config: {"effort": "medium"}`,
    `context_management: {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]}`.
  - `tools` is `[]` with `tools=[]`; with an SDK MCP server it is `["DeferredToolPlaceholder", "mcp__demo__echo"]`.
  - `system[0]` is `x-anthropic-billing-header: cc_version=2.1.310.<3 hex>; cc_entrypoint=sdk-py;`.
    The 3-hex suffix changed with the prompt text (`077` for "ping", `31a`/`b09` for another prompt)
    but was identical across repeated identical runs: deterministic per request, not volatile.
    `CLAUDE_CODE_ATTRIBUTION_HEADER=0` removes this block.
  - `messages` carries a `role: "system"` message after the user turn: the `# Environment` block
    (primary working directory, git flag, platform, shell, OS version, model line, a
    `<total_tokens>` budget line, and `Today's date is YYYY-MM-DD.`). Later turns add a short
    `role: "system"` token-budget message, which is derived from usage and so is deterministic in replay.
- Headers: `accept, accept-encoding, anthropic-beta, anthropic-dangerous-direct-browser-access, anthropic-version, connection, content-length, content-type, host, user-agent, x-api-key, x-app, x-claude-code-session-id, x-stainless-arch, x-stainless-lang, x-stainless-os, x-stainless-package-version, x-stainless-retry-count, x-stainless-runtime, x-stainless-runtime-version, x-stainless-timeout`. `x-claude-code-session-id` changes every query (headers are not in the key anyway).
- Volatile across two identical `query()` calls: **only `metadata.user_id`**, a JSON string
  `{"device_id": ..., "account_uuid": "", "session_id": <new uuid per query>}`.
  After removing it, both the main request and the title helper hashed identically across runs,
  including the second turn of a tool run.
- Volatile across machines and days (not across two runs on one machine): the `# Environment`
  lines. `CLAUDE_CODE_HIDE_CWD`, `CLAUDE_CODE_OVERRIDE_DATE` and `CLAUDE_CODE_SIMPLE_SYSTEM_PROMPT`
  (names found in the CLI binary, undocumented) did **not** remove them, and a custom
  `system_prompt` keeps the block too. So the proxy masks them in the key.
- Side note: in the first no-tool runs `ResultMessage.total_cost_usd` read 2.0 for 10 input and
  1 output tokens; in the tool run it read 4e-06. That is the CLI's client-side estimate against a
  canned reply, not billing; Task 7 should take cost from the proxy or from token usage instead.

## 3. Proposed rulings (the controller confirms)

- **S1. Agent SDK uses the proxy.** No fallback. The SDK desk passes
  `env={"ANTHROPIC_BASE_URL": PROXY_URL, "ANTHROPIC_API_KEY": "sk-ant-dummy" in replay, ...}`.
- **S2. Volatile key fields.** The key drops `metadata.user_id` and masks these lines wherever they
  appear in top-level `system` text or in `role: "system"` messages: `- Primary working directory:`,
  `- Is a git repository:`, `- Platform:`, `- Shell:`, `- OS Version:`, `Today's date is`.
  Masking (not dropping) keeps the rest of the block in the key. Stored `request_summary` holds the
  masked body, so recordings carry no local paths. Everything else stays in the key, including
  `?beta=true` in the path and the billing-header fingerprint: an SDK upgrade then misses loudly
  instead of matching a stale recording.
- **S3. Helper requests.** The title helper is a normal, deterministic `POST /v1/messages` and is
  recorded and replayed like any other request (no special case). The SDK desk should still set
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, which removes it and saves one paid call per run.
  `HEAD` requests (the `/api/hello` probe) are answered `200` with no body locally in both modes,
  never forwarded or stored. Any other path (e.g. `count_tokens`, if a later version sends it) is
  keyed and recorded the same way, so it replays or misses loudly; nothing is canned.
- **S4. SDK settings for determinism and cost.** The SDK desk sets
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS=4000` (= `MAX_TOKENS`), `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`,
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, an isolated `CLAUDE_CONFIG_DIR`, and a fixed `cwd`.
  Thinking and effort stay at the CLI defaults (they are in the body, so in the key).
- **S5. Worst case per call uses the request's own `max_tokens`** (falling back to `MAX_TOKENS` when
  absent). Without S4 the SDK asks for 128000 output tokens, and pricing that at 4000 would under-count
  the cap. With S4 the two agree.
- **S6. Replay of streams.** Stored as `sse_events`: the raw stream split after each blank line,
  each event keeping its separator, so `"".join(sse_events)` is the exact upstream bytes. Upstream is
  asked for `accept-encoding: identity`. In record mode the proxy buffers the whole reply before
  sending it on (fine for 4000-token replies; the clients' timeouts are minutes).
