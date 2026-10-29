# AI Engineering: AI Infrastructure track (design)

Date: 2026-10-29
Status: design approved in conversation. This written spec is awaiting review.
Builds on: the eight existing tracks, `docs/STYLE.md`, `~/ai-engineering-kit/APP-DESIGN-TEMPLATE.md`, and in particular the AI Agents track's record/replay proxy (`agents/proxy/`), its recordings (`agents/data/recordings/`), its cost ledger (`agents/proxy/proxy_cost.py`) and the LLMs lab's price table (`llms/python/triage_lab.py`).

## 1. Intent

**Outcome.** The learner can:
- say what sits between a program and a model API in production, and what a gateway gives every caller at once: routing with fallback, retries and rate limits, a response cache, budgets with a spend ledger, and queryable traces;
- build that gateway as a drop-in HTTP server in the Messages API shape, as a chain of pure stages over one config file, in Python with a Go twin of the request path;
- run the course's own recorded traffic through it, free and deterministic, and read the ledger and the traces;
- state honestly what the drop-in trade costs (headers are the only channel), what breaks replay and audit (editing bodies), and what a production gateway adds.

**Audience.** The same learner, after the Harness, LLMs, ML, MCP, AI Agents, Data and Ontology tracks.

**Success criteria.**
- The gateway starts from `infra/gateway.yaml`, refuses an invalid config with a named error, and forwards the course's recorded requests byte for byte to the agents replay proxy.
- Every stage's decision is traced; the fixture injector exercises retries, backoff, fallback, rate limiting, budget refusal and deny.
- The Go twin returns the same verdict and status as the Python stages on every shared fixture case.
- The capstone replay of all 951 recordings passes its gate: the gateway's ledger equals the agents track's ledger to the cent for the desk sets, no budget exceeded, every trace complete, and the hit rate and latency figures pinned.
- Six lessons pass the style, pin, budget and quiz-spread tests; `infra.yaml` is live; the page is republished.

**Stated by the user:**
- The platform layer around model calls (not serving or compute, not containers).
- Six lessons.
- The upstream is the agents track's replay proxy: zero spend; a live mode behind the usual estimate and y/N that no lesson number depends on.
- Python gateway with a Go twin of the request path.
- A drop-in HTTP server in the Messages API shape.
- The capstone runs the course's recorded traffic through it under one config, with a gate.

**Assumptions (accepted):**
- Standard library only for the gateway; `pytest` for tests; Go 1.27.2 for the twin.
- Other tracks' files are read in place and never changed; the agents proxy is started on another port (`--port 8791`) and its module imported read-only by tests, as the agents tests do.
- A new venv `.venv-infra` at the repo root.

## 2. Scope

**In scope:**
- `infra/`: the config, the gateway and its stages, the replay driver, the ledger, the trace store and query, fixtures and the injector, the Go twin, derived JSON.
- 5 aids and 1 diagram.
- 6 lessons, `infra.yaml` set to live, README and STYLE updates.

**Out of scope (named in lesson 1 as what a production gateway adds):**
- Multi-tenant auth and key issuance; the course's gateway trusts loopback callers and reads team and tag from headers.
- Streaming beyond byte forwarding of what the proxy replays.
- Distributed state for the cache, the rate limiter and the ledger; here they are files.
- A metrics backend and dashboards; here traces are JSONL and a query command.
- Horizontal deployment, containers, service discovery.
- Serving models (the LoRA model stays on Colab) and GPU or capacity planning.

## 3. Architecture

```
infra/
├── gateway.yaml          one config: upstream, models, routes, cache, retry, budgets, traces
├── python/
│   ├── gateway.py        the server: parse, run the chain, answer (thin)
│   ├── stages.py         the chain: tag → budget → cache → route → limit → retry → upstream → ledger → trace
│   ├── replay.py         the traffic driver: the course's recorded requests through the gateway; the gate
│   ├── ledger.py         spend ledger and budget maths, per team and per request tag
│   ├── traces.py         trace store (JSONL) and the query command
│   └── tests/            unit tests per stage, server tests, twin equality, the gate test, the Try-it test
├── go/gateway/           the twin of stages 2–6 over the same config and fixtures
├── data/
│   ├── fixtures/         request cases, expected decisions, injector scripts (both languages read them)
│   ├── traces/           per-run JSONL (git-ignored) and the committed sample
│   └── derived/          capstone.json, ledger.json, traces.sample.json
└── requirements.txt      pytest
```

- **Where it sits.** The gateway listens on the loopback and speaks the Messages API shape: the same paths and bodies the agents proxy accepts. Its upstream is configurable; in the course it is the agents replay proxy on port 8791.
- **Two ways to put traffic through it, both free.** The replay driver reads the 951 recorded requests under `agents/data/recordings/<set>/*.json` (`request_summary`: method, path, body) and sends each through the gateway, by set and in file order, with `x-team` per set (desk sets → `desk`, pattern sets → `patterns`) and `x-request-tag` set to the ticket id read from the body's first user message. This is the capstone's traffic and every lesson's numbers. The desk unchanged: the desk engines hard-code the proxy's port 8790, so lesson 1's last Try-it starts the proxy on 8791 and the gateway on 8790 in front of it and runs the desk as the agents track left it; shown once, nothing pinned on it.
- **Bodies are never edited.** The proxy replays by a hash of method, path and body; a gateway that rewrites a body (a rerouted `model`, an injected system line) gets a 404 from replay. The course's config routes every recorded model to itself with fallbacks reserved for injector cases; the gateway forwards bodies byte for byte; routing decisions, retries and budget verdicts live in headers and traces. Lesson 2 states the general lesson: a gateway that edits requests breaks replay, caching and audit alike.
- **Latency and cost are real.** Each recording stores the original call's wall clock and usage (`upstream.ms`, tokens), so traces carry the recorded latency and the ledger prices the recorded tokens with the LLMs lab's price table, as the agents track's cost lesson does. count_tokens recordings carry no usage: forwarded and traced, never cached or priced.

## 4. The stages and the config

Each stage is a pure function `stage(request, state, config) -> decision`, run in order by the server; every decision is appended to the request's trace as `{stage, verdict, detail}`.

1. **tag**: reads `x-team` and `x-request-tag`; a missing team is `untagged`, which the budget config may refuse.
2. **budget**: the team's spend so far plus this request's worst case (`max_tokens` at the target model's output price plus the estimated input, as the proxy's cap does) against the team's limit: `allow`, `warn` (over `warn_usd`) or `refuse` (402 with a body naming the team and the limit). A refused request costs nothing and is still traced.
3. **cache**: key = hash of the model and the body; a hit answers from the cache with `x-cache: hit` and no upstream call; only responses with `stop_reason` `end_turn` are stored, with a TTL; `skip_tools: true` marks requests carrying `tools` uncacheable (the desk's calls, shown by hit rate in lesson 4).
4. **route**: maps the body's model through `routes` to a target and an ordered fallback list; `"*": deny` refuses an unknown model (404) instead of forwarding it.
5. **limit**: a token bucket per target model (`rpm`, `tpm`); over the limit → wait up to `max_wait_ms`, else 429 with `retry-after`.
6. **retry**: forwards to the upstream; on 429, 5xx or a connection error, retries with exponential backoff and jitter up to `attempts`, then the next fallback; the final failure returns 502 with the attempt list in the trace.
7. **ledger**: prices the response's usage (the lab's price table; cache-read tokens priced apart) and appends `{team, tag, model, tokens, cost, replayed, cached}` to the ledger.
8. **trace**: writes one line per request: id, parent id (from `x-parent-id` when a caller sends one; the desk's supervisor calls carry none, which lesson 6 shows as the gap only the caller can close), stage decisions, latency from the recording's wall clock, outcome.

The config, `infra/gateway.yaml`, with `# ── N.` sections so lessons can quote it:

```yaml
upstream: http://127.0.0.1:8791            # the agents replay proxy; live mode needs the usual y/N
models:
  claude-haiku-5-5: {rpm: 60, tpm: 200000, price: lab}   # price from llms/python/triage_lab.py's table
routes:
  claude-haiku-5-5: {to: claude-haiku-5-5, fallback: []}
  "*": deny
cache: {ttl_s: 3600, skip_tools: true}
retry: {attempts: 3, base_ms: 200, max_ms: 2000}
limit: {max_wait_ms: 1000}
budgets:
  desk: {limit_usd: 1.00, warn_usd: 0.80}
  lab:  {limit_usd: 0.50}
  patterns: {limit_usd: 0.50}
  untagged: refuse
traces: {dir: infra/data/traces}
```

The Go twin (`infra/go/gateway`) implements stages 2 to 6 over the same config and fixture cases; the shared test asserts equal verdict and status case for case. tag, ledger and trace are Python only, with a callout that a production team would twin those too.

## 5. Data, fixtures and the gate

**Fixtures (`infra/data/fixtures/`).** Request cases as JSON read by both languages: a cacheable call and its repeat, a tool call (uncacheable), an unknown model (deny), a team over budget, a request with no team, and three injector scripts. The injector is a fixture upstream the tests start in-process: a small HTTP server scripted per request to answer 200, 429, 500 or drop the connection in a given sequence, so retries, backoff, fallback and the limiter are exercised without inventing lesson traffic. Each case carries its expected decision list and status.

**Derived data (`infra/data/derived/`), written by the scripts, never by hand, each with `--check`:**
- `capstone.json`: per set and per team: requests, cache hits and hit rate, retries, refusals, cost, p50 and p95 latency from the recorded wall clocks, and the gate.
- `ledger.json`: the ledger after the capstone replay, per team and per tag; the top tickets by cost.
- `traces.sample.json`: a handful of traces the lessons quote (one cached, one retried, one refused), picked by stable ids.

**The capstone gate** (`replay.py --gate`), green when all hold, each printed on its own line with its figures:
1. the gateway's ledger equals the agents track's ledger to the cent for the desk sets;
2. no team exceeded its limit, and every refusal is traced;
3. every trace has every stage's decision, and parent ids are consistent where present;
4. the cache hit rate and the p95 latency equal the figures `capstone.json` holds (drift fails `--check`, not the gate).

The lesson states the hit rate honestly: the desk's calls are mostly tool calls and distinct prompts, so the rate is low, and lesson 4 shows what traffic a cache helps with the lab's repeated system prompt as the contrast.

**Trace store.** JSONL, one file per run under `infra/data/traces/` (git-ignored except the sample). `traces.py` queries it: by team, tag or outcome; the slowest; the costliest; a tree for a parent id.

## 6. Lessons

| # | Slug | Title | Subsection | Aid |
|---|---|---|---|---|
| 1 | gateway | What an AI gateway is | Gateway | `FlowDiagram` `infra-gateway` |
| 2 | routing | Routing and fallback | Gateway | `RouteTable` |
| 3 | resilience | Retries and rate limits | Gateway | `RetryTimeline` |
| 4 | cache | The response cache | Operations | `CacheReport` |
| 5 | budgets | Budgets and the ledger | Operations | `LedgerView` |
| 6 | capstone | Capstone: the course through the gateway | Operations | `TraceExplorer` + gate |

- Each lesson follows `docs/STYLE.md`: Concept → See it → The code → How Go does it (lessons 2–5 quote the twin stage; 1 and 6 carry the callout) → Try it, with a quiz in plain text.
- Lesson 1 carries the concept tour: what sits between a program and a model API in production, the drop-in trade, what the course's gateway is and what a production one adds (the out-of-scope list), and the first run (proxy on 8791, gateway on 8790, one set replayed; then the desk unchanged).
- Try-its edit `gateway.yaml` and rerun: 2 adds a route and a deny; 3 turns an injector script on; 4 changes the TTL and the tools rule and watches the hit rate; 5 lowers a budget until a refusal appears and reads the 402; 6 runs the full replay and the gate, then the trace queries.
- Every number comes from `capstone.json`, `ledger.json` or the fixtures' expected decisions, pinned (prose and quiz).

## 7. App

- **Code sources:** `infra-gateway` (gateway.py), `infra-stages` (stages.py; the Go twin numbered to match), `infra-replay`, `infra-ledger`, `infra-traces`, `infra-config` (the YAML by its `# ── N.` sections).
- **Diagram:** `infra-gateway`: programs → the gateway's stages → the upstream, with what each stage reads and writes, and where the ledger and traces land.
- **Aids:** `RouteTable` (pick a model in a request; see the route, the fallback order or the deny, and the replay key unchanged), `RetryTimeline` (an injector script replayed as a timeline: attempts, backoff waits, fallback, the outcome, with text labels), `CacheReport` (hit rate per set, what was skipped and why, the lab's prompt as the contrast, a TTL control that recomputes from the data), `LedgerView` (spend per team and tag, the warn and refuse lines, the top tickets by cost, the agents ledger beside it to the cent), `TraceExplorer` (filter traces by team, tag, outcome; open one to see the stage decisions and latency; the gate verdict verbatim). All read `infra/data/derived/*.json` at build time; logic in `src/lib/infraAids*.ts` under 400 lines each; keyboard use, a live region, text never colour alone; the track's props under 200 KB; 375 px without page scroll.
- **Track file:** `infra.yaml` live with subsections "Gateway" and "Operations".

## 8. Error handling

- A malformed body, an unknown path or a body over the size limit: 400 with a JSON error naming the problem; nothing forwarded. An unknown model: 404 `deny`, never a pass-through.
- The upstream host must be a loopback address unless `--live` is on; live mode prints the estimate for the set, asks y/N, needs the key inline as every track does, and enforces a cap per run; replay is the default and no printed command carries `--yes`. A replay 404 from the proxy is returned as 502 with the proxy's message, naming the missing recording.
- Every stage returns a verdict; the server maps verdicts to statuses (402 budget, 404 deny, 429 limit with `retry-after`, 502 upstream after the last attempt). A stage that raises is a bug: 500, a trace line with `stage: error` and the exception's name, and a test case for it.
- The ledger and traces are append-only JSONL; a half-written last line is skipped with a warning, never counted. The cache file is rewritten atomically; a corrupt cache is discarded with a warning and the run goes on cold.
- The config is validated at start: a missing section, an unknown key, a negative limit, a route to an undefined model, a warn line above its limit: each a named error and the server refuses to start; `--max-cost`, `limit_usd` and the rates refuse nan, inf, zero and negatives.
- The gate prints each condition on its own line, red or green, with the figures; every `--check` names the file that drifted.
- The Go twin fails the same way: the fixtures include the error cases and the equality test compares verdict and status.

## 9. Testing

- Unit tests per stage over the fixture cases; budget maths; cache key and TTL; the token bucket with a fake clock; backoff with a fixed seed; the ledger's prices against the lab's table; the trace queries; config validation, one case per rule.
- Server tests: the gateway in-process against the injector (in-process) for retries, 429s, dropped connections, 402s and 404s; against the agents proxy started in-process in replay mode for the pass-through cases.
- Twin equality: `go test ./...` in `infra/go/gateway` over the same fixtures; a Python test runs the Go binary over the fixtures and asserts equal verdicts and statuses case for case.
- Capstone: `replay.py --gate` in the suite against the proxy in replay mode; the gate test asserts the four conditions and that the derived files match; a Try-it test runs every lesson's commands as printed and compares the output.
- App: Vitest for the aids (render, one interaction each, keyboard, text not colour alone), round trips of the derived JSON into props, every lesson number pinned, the quiz-spread and no-backtick guards, the props budget.
- No API calls in tests; live mode is tested with a fake upstream only.

## 10. Cost and setup

- A venv at `.venv-infra` (pytest); Go 1.27.2 for the twin; the agents recordings already in the repo; `.venv-agents` for lesson 1's desk Try-it.
- Cost: zero.
- The capstone replay sends 951 requests over loopback; budget under a minute in the suite; only the gate test runs it.

## 11. Open items

- Which of the 951 recordings carry `upstream` timing (recordings that predate it have none): the plan confirms the count and the gate's latency figures exclude those without, stated in lesson 6.
- The exact worst-case formula the budget stage uses for a count_tokens request (no `max_tokens`): the plan decides (zero, since it is never priced).
- Whether the LLMs lab's saved responses join the replay as a `lab` team: the plan decides from their shape (they are responses, not recorded requests, so they likely serve only lesson 4's contrast figure, computed offline from the prompt files).
