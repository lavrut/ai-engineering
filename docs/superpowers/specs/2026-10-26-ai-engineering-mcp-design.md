# AI Engineering: MCP Track (design)

Date: 2026-10-26
Status: design approved in conversation; this written spec awaits review.
Builds on:
- the Harness, LLMs and ML track designs;
- `docs/STYLE.md`;
- `~/ai-engineering-kit/APP-DESIGN-TEMPLATE.md`;
- verified facts in `docs/superpowers/research/2026-10-26-mcp-facts.md`.

## 1. Intent

**Outcome.** The learner can build, connect, secure and ship Model Context Protocol servers and clients to the current spec (2026-07-28). Concretely, they can:
- expose tools, resources and prompts;
- write a client;
- give an agent MCP tools;
- run a server over stdio and remotely over Streamable HTTP;
- protect write actions with OAuth and human confirmation;
- ship an MCP App;
- defend against prompt injection that arrives through tool results.

**Audience.** The same learner, who has finished the Harness, LLMs and ML tracks.

**Success criteria.**
- The learner runs both Lumen servers in Python and Go, and the shared contract tests pass identically in both languages.
- The learner connects the SDK client, the Track 1 harness (replay), and the MCP Inspector in modern mode. Claude Desktop and Claude Code are optional.
- A write via `lumen-ops` happens only after confirmation, with a valid `tickets:write` token whose audience is the server, and it records provenance.
- The capstone shows injection ticket #61 succeeding without defences and failing with them, from recorded runs.
- All 11 lessons pass the style tests, `mcp.yaml` is `live`, and the page is republished.

**Stated by the user:**
- a running example built on the course's own artefacts;
- Go as the second language;
- scope: core, plus remote HTTP, OAuth, MCP Apps, and security and safety;
- approach A (one growing codebase) with a trust split into two servers over one shared core, which the user chose after reviews by an ML engineer, a systems architect and a security analyst.

**Assumptions (accepted):**
- free and offline by default;
- pinned SDK versions;
- Claude hosts are optional demos;
- two small paid recordings, each run only with explicit approval of its printed estimate.

## 2. Scope

**In scope:**
- `mcp/` code (core, two servers, client and local auth server in Python and Go, App in Python), tests and fixtures;
- the injection ticket;
- the recordings;
- 5 new aids and 4 diagrams;
- 11 lessons;
- `mcp.yaml` set to live;
- README and STYLE updates.

**Out of scope:**
- the Tasks extension (neither SDK implements it);
- sampling and roots (deprecated);
- the TypeScript SDK;
- a Go App;
- deploying to a cloud;
- a production authorization server;
- other tracks.

## 3. Architecture

```
mcp/
├── core/python/lumen_core/   ticket store, classifier loader, label store (no MCP code)
├── core/go/                  the same, Go package
├── python/                   read_server.py, ops_server.py, client.py, auth_server.py,
│                             app/ (labelling App HTML), tests/
├── go/                       readserver/, opsserver/, client/, contract tests
├── fixtures/contract.jsonl   requests → expected results, shared by both languages
├── data/                     tickets come from llms/data/tickets.jsonl plus mcp/data/injection.jsonl (#61);
│                             labels.jsonl (git-ignored, runtime); recordings/ (saved Claude runs)
└── requirements.txt          mcp==2.3.0 (exact), anthropic, pytest; go.mod pins go-sdk v1.8.0
```

There are two servers, split by trust:
- **`lumen-read`**: read-only, no side effects. It runs over stdio, and over HTTP with scope `tickets:read`.
- **`lumen-ops`**: writes. It runs over HTTP only, with scope `tickets:write`.

Hosts grant consent per server, so the read server can be approved once while the write server stays gated. Both servers are thin layers over `lumen_core`.

## 4. Servers and core

**`lumen_core`** (Python and Go, same behaviour):
- `tickets()` reads the 60 Lumen tickets plus #61, from the existing files; nothing is copied.
- `search(query, limit)` searches tickets.
- `get(id)` returns one ticket.
- `classify(text)` uses Track 3's production model JSON, found through `ml/registry.json`. It returns the label, the scores, `low_confidence` and `model_version`, exactly as `ml/serve` does.
- `write_label(id, label, source, model_version, confirmed)` appends to `labels.jsonl` with a timestamp. Rows with `source="agent"` or `confirmed=false` are rejected by any eval or training reader. A guard function enforces this, and a test pins it.

**`lumen-read`** tools all carry `readOnlyHint: true`:
- `search_tickets(query, limit≤20)` returns structured output. Ticket text appears only in an `untrusted_body` field.
- `get_ticket(id)`.
- `classify_complaint(text ≤ 20,000 chars)`.

Its resources are `lumen://tickets/{id}` and `lumen://reports/eval` (from `llms/data` reports). Its prompt is `review_ticket(id)`.

**`lumen-ops`**:
- `label_ticket(id, label)` confirms on the server through multi-round-trip elicitation: it returns `InputRequiredResult` asking the user to confirm, and writes only when the retried request carries an acceptance. It then writes with `source` taken from the token's client and `confirmed=true`.
- It hosts the labelling App, a `ui://` resource with mime type `text/html;profile=mcp-app` linked from the tool's `_meta.ui.resourceUri`. The App is Python only.

**Errors:**
- Validation failures return tool errors (`isError`) with fixed messages, identical in Python and Go.
- An unknown resource returns the spec's error.
- Text over the limit gets a fixed message.

**HTTP:**
- Streamable HTTP binds to 127.0.0.1.
- The `Origin` header is checked.
- Bodies are capped.
- Configuration lives in a numbered section, so lessons can quote it.

## 5. Authorization

- **The local test authorization server** (`auth_server.py`, Python only, test use only) issues tokens for the scopes `tickets:read` and `tickets:write`, with the audience set to the MCP server's canonical URI (RFC 8707). It supports the client registration method the spec prefers (Client ID Metadata Documents), or the best one the SDK supports.
- **Both servers act as resource servers.** They serve protected-resource metadata, validate the bearer token's audience and scope (Python sets `validate_token_resource=True` explicitly; Go uses `auth.RequireBearerToken`), and return 401 or 403 as the spec says.
- **Precondition (plan Task 1, a spike):** prove the local AS works end to end in Python, plus the Go resource-server checks, including whether localhost resource URIs are acceptable. If a piece fails, the plan records a ruling and a fallback, such as a pre-registered client. The fallback must never be "skip auth".

## 6. Clients and recordings

- **`client.py` and the Go client:** discovery (`server/discover` or the listing methods), calls, errors, and handling `InputRequiredResult` by prompting the user.
- **The Track 1 harness as an MCP client.** It is extended without changing its existing behaviour: MCP tools are added to its tool list. It also gets a rule that once a turn has read untrusted content, any write needs a human yes. That rule is off by default, and the capstone turns it on.
- **Recordings** (paid; each needs the user's approval of the printed estimate and worst case; replayed for free afterwards):
  - (a) lesson 5: the harness on Haiku 5.5 using lumen-read tools on a few tickets;
  - (b) the capstone: the harness reading #61 with defences off, then on.

  Each recording has a $10 cap, and the expected total is under $1. Recordings come only from real runs and are never invented. Until they exist, the aids show "No recorded run yet" plus the command.

## 7. Lessons

| # | Slug | Title | Subsection | Aid |
|---|---|---|---|---|
| 1 | what | What MCP is | — | `FlowDiagram` `mcp-architecture` |
| 2 | first-server | Your first server | Servers | `McpMessageView` |
| 3 | resources-prompts | Resources and prompts | Servers | `McpMessageView` |
| 4 | client | Writing a client | Clients | `FlowDiagram` `mcp-client` |
| 5 | agent | An agent that uses MCP | Clients | `LoopSimulator` (recorded) |
| 6 | hosts | Hosts and many servers | Clients | `ToolBudget` |
| 7 | http | Going remote over HTTP | Tools | `FlowDiagram` `mcp-http` |
| 8 | write-tools | Write tools and confirmation | Tools | `ElicitationStepper` |
| 9 | auth | Authorization | Tools | `FlowDiagram` `mcp-oauth` |
| 10 | apps | MCP Apps | Apps | `AppPreview` |
| 11 | capstone | Capstone: attack and defend | All | `InjectionLab` |

What each lesson covers:
1. Hosts, clients and servers; JSON-RPC; the 2026-07-28 changes (no handshake, no sessions, per-request capabilities); stdio and HTTP.
2. Tools: schemas, structured output, the Inspector in `--protocol-era modern`.
3. Resources and prompts, and when to use a tool, a resource or a prompt.
4. Discovery, calls, errors and `InputRequiredResult`.
5. The harness with MCP tools.
6. Claude Desktop and Claude Code config (optional), several servers, name clashes, and the context cost of each tool list.
7. Streamable HTTP, localhost binding, Origin checks and body caps.
8. Elicitation in several round trips, provenance, and why annotations aren't enforcement.
9. Protected-resource metadata, the local AS, scopes, audience, and no token passthrough.
10. The labelling App in a reference host.
11. Ticket #61, the layered defences, and the injection success rate.

Every lesson follows `docs/STYLE.md`. "How Go does it" appears where Go differs; the App lesson is Python only, with a callout explaining why.

## 8. App changes

- **CodeTabs sources:** `mcp-read`, `mcp-ops`, `mcp-client`, `mcp-auth` (Python + Go), `mcp-app` (Python only).
- **Diagrams:** `mcp-architecture`, `mcp-client`, `mcp-http`, `mcp-oauth`, using existing role tokens. Add one new role token only if a test shows it's needed, with light and dark values and 3:1 contrast.
- **Aids:** logic in `src/lib/mcpAids.ts` (split if it nears 400 lines), thin components, built-time data only.
  - `McpMessageView`: steps through recorded JSON-RPC message pairs captured from the real servers by a script.
  - `ToolBudget`: tool count and estimated tokens of each server's `tools/list`, measured by a script.
  - `ElicitationStepper`: the round trips of a confirmed write.
  - `AppPreview`: the App's HTML in a sandboxed iframe, with a static snapshot.
  - `InjectionLab`: toggles each defence and shows the recorded outcome.
- **Data:** small derived JSON files under `mcp/data/derived/`, written by scripts, never typed in.
- **Page budget:** new props under about 200 KB.

## 9. Testing

- **Contract** (`mcp/fixtures/contract.jsonl`): every tool, resource and prompt, including errors and limits, gives identical results from the Python and Go servers. It runs in-memory and over HTTP.
- **Python (pytest):**
  - core: search, classify parity with `ml/serve` on shared inputs, and the provenance guard;
  - each server through the in-memory `Client(server)`;
  - the elicitation round trip;
  - auth: 401 without a token, 403 with the wrong scope or audience, success with the right token;
  - Origin rejection;
  - the App resource's mime type and link.
- **Go (`go test`):** the same, minus the App.
- **The Inspector CLI**, pinned, in modern mode, as a smoke test script.
- **App (Vitest):** aid logic, style rules, pinned numbers, diagram code links.
- **No test calls the Claude API.**

## 10. Error handling

- Tool input errors give fixed messages and `isError`.
- A missing model or registry fails at server start.
- Auth failures give 401 (with `WWW-Authenticate` pointing to the metadata) or 403 for scope.
- A declined elicitation writes nothing and returns a clear result.
- A replay miss in recorded runs gives a clear error naming the record command.
- Ticket text is always data, never instructions; it is returned only in `untrusted_body`.

## 11. Cost and setup

- **Free**, apart from the two optional, approved recordings (expected under $1 in total, $10 cap).
- **Setup:** a venv at `.venv-mcp` with the pinned packages; Go 1.22+; Node 22.19+ only for the optional Inspector and the App reference host.
- Claude Desktop and Claude Code are optional and use the learner's own accounts.

## 12. Open items

- **Task 1 spike:** the local AS (CIMD or the fallback, audience, localhost URIs), Go's resource-server auth, and Python App serving in the reference host. Each gets a ruling before the lessons that depend on it.
- **Recordings** need the user's approval of their printed estimates.
- **Whether Claude Desktop and Claude Code speak 2026-07-28** or fall back to the old handshake. Both SDKs handle both, so the lessons state what was observed, if the user tries it.
