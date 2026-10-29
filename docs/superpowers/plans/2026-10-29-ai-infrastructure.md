# AI Infrastructure Track Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A six-lesson AI Infrastructure track: a drop-in HTTP gateway in the Messages API shape (routing, retries and rate limits, a response cache, budgets with a ledger, traces) in Python with a Go twin of the request path, run free over the course's recorded traffic with a capstone gate.

**Architecture:** One process, a chain of pure stages over one YAML config; the HTTP server is a thin wrapper; the agents track's replay proxy (on port 8791) is the upstream; a replay driver sends the traces' recorded calls through the gateway and writes the derived JSON the lessons pin. State is files (cache JSON, ledger and traces JSONL).

**Tech Stack:** Python 3.12 standard library (http.server, http.client, json, hashlib) + pytest in `.venv-infra`; Go 1.27.2 for the twin; Astro 7 + Preact + Vitest for the app.

**Spec:** `docs/superpowers/specs/2026-10-29-ai-infrastructure-design.md`

## Global Constraints

- Python only in `.venv-infra/bin/python` (`infra/requirements.txt`: `pytest>=8`; the gateway imports only the standard library). Go 1.27.2 for `infra/go/gateway`.
- Other tracks' files are read in place and never edited. The agents proxy is started on `--port 8791` (never 8790 when the gateway takes 8790) and imported read-only by tests only, as the agents tests do; `llms/python/triage_lab.py`'s `PRICES` is imported read-only by `ledger.py` (it has no third-party import at module level).
- Zero paid calls. `--live` requires the key inline (`ANTHROPIC_API_KEY="$(security find-generic-password -a "$USER" -s anthropic-api-key -w)"`), prints an estimate, asks y/N, enforces `--max-cost`; no printed command carries `--yes`; `--max-cost` and every money or rate value in the config refuse nan, inf, 0 and negatives.
- The upstream host must be a loopback address (`127.0.0.1`, `localhost`, `[::1]`) unless `--live`; bodies are forwarded byte for byte; the gateway never edits a request body.
- Derived files under `infra/data/derived/` are written by `replay.py`, never by hand; every script has `--check` that fails on drift naming the file.
- Every number a lesson states (prose, inline code, quiz) is pinned by `app/tests/infralessons.test.ts` against the derived JSON or the fixtures; quiz frontmatter has no backticks; the cross-track quiz-spread test stays green.
- Files under 400 lines except where this plan says otherwise; aids: keyboard use, a live region, text never colour alone, 375 px without page scroll; the track's shipped props under 200 KB.
- No absolute local paths, no personal data, generic User-Agent `ai-engineering-course-sample/1.0` on live calls. Commits with explicit paths, never `--amend`, no attribution lines; never commit `.venv-*`, `infra/data/traces/*.jsonl` (except the sample), `infra/data/cache.json`, `infra/data/ledger.jsonl`.
- Lessons follow `docs/STYLE.md` and `app/templates/`; the course's own words, no outside quotation.

## Review Focus

1. A request whose body is not JSON, or has no `model`, reaches the route stage: expected 400 before any stage runs, traced as `stage: parse`. Test in Task 4.
2. An upstream that answers 200 with a body the gateway cannot read usage from (an SSE stream without `message_start`): expected a ledger row with zero tokens and cost `null`, never a crash. Test in Task 3.
3. A budget refusal while the cache has a hit: expected the cache stage runs after budget, so the refusal wins and nothing is served from cache (the spec's stage order). Test in Task 2.
4. Two sets with the same request bodies (the pattern repeats) under a response cache: expected the second call is a hit only when the stored response was `end_turn` and carried no tools. Test in Task 6 (the three pattern repeats are tool-free `end_turn` calls or not: the test pins what the data says).
5. The gateway started with a config whose upstream is `https://api.anthropic.com` and no `--live`: expected refusal to start with the message naming the host. Test in Task 1 and Task 4.

---

### Task 1: Config, fixtures scaffold and validation

**Files:**
- Create: `infra/requirements.txt`, `infra/gateway.yaml`, `infra/python/config.py`, `infra/python/tests/conftest.py`, `infra/python/tests/test_config.py`, `infra/data/fixtures/configs/*.yaml` (one invalid config per rule), `infra/data/traces/.gitkeep`
- Modify: `.gitignore` (add `infra/data/traces/*.jsonl`, `infra/data/cache.json`, `infra/data/ledger.jsonl`, `.venv-infra/`)

**Interfaces:**
- Produces: `config.load(path: Path, live: bool = False) -> Config` (a frozen dataclass: `upstream: str`, `models: dict[str, ModelCfg]` with `rpm: int`, `tpm: int`, `price: tuple[float, float]`, `routes: dict[str, Route]` with `to: str`, `fallback: list[str]`, plus `deny_unknown: bool`, `cache: CacheCfg` (`ttl_s`, `skip_tools`), `retry: RetryCfg` (`attempts`, `base_ms`, `max_ms`), `limit: LimitCfg` (`max_wait_ms`), `budgets: dict[str, Budget]` (`limit_usd`, `warn_usd | None`), `untagged: "refuse" | str`, `traces_dir: Path`); `config.ConfigError(ValueError)` whose message names the key; `config.SECTIONS = ("upstream", "models", "routes", "cache", "retry", "limit", "budgets", "traces")`.
- `price: lab` resolves through `ledger.lab_prices()` (Task 2); Task 1 stores the literal `lab` and Task 2 resolves it.

- [ ] **Step 1: Write `infra/gateway.yaml` exactly as spec §4, with `# ── N.` section comments: 1 Upstream and models, 2 Routes, 3 Cache, 4 Retry and limits, 5 Budgets, 6 Traces.** Add a `judge: {limit_usd: 0.50}` budget (the judge recordings are a team; spec §3 names desk and patterns, the data has a third set).

- [ ] **Step 2: Write the failing tests** in `test_config.py`:

```python
def test_loads_the_course_config():
    cfg = config.load(ROOT / "infra/gateway.yaml")
    assert cfg.upstream == "http://127.0.0.1:8791"
    assert cfg.models["claude-haiku-5-5"].rpm == 60 and cfg.models["claude-haiku-5-5"].tpm == 200000
    assert cfg.routes["claude-haiku-5-5"].to == "claude-haiku-5-5" and cfg.deny_unknown is True
    assert cfg.budgets["desk"].limit_usd == 1.00 and cfg.budgets["desk"].warn_usd == 0.80
    assert cfg.untagged == "refuse"

@pytest.mark.parametrize("name,needle", [
    ("missing_section", "budgets"), ("unknown_key", "retries"), ("negative_limit", "limit_usd"),
    ("route_to_undefined_model", "claude-sonnet-5-5"), ("warn_above_limit", "warn_usd"),
    ("nan_rate", "rpm"), ("zero_ttl", "ttl_s"), ("non_loopback_upstream", "api.anthropic.com")])
def test_each_rule_names_its_key(name, needle):
    with pytest.raises(config.ConfigError, match=needle):
        config.load(FIXTURES / "configs" / f"{name}.yaml")

def test_live_allows_a_remote_upstream():
    cfg = config.load(FIXTURES / "configs" / "non_loopback_upstream.yaml", live=True)
    assert cfg.upstream.startswith("https://api.anthropic.com")
```

- [ ] **Step 3: Run: `.venv-infra/bin/python -m pytest infra/python/tests/test_config.py -q`** → FAIL (no module config).

- [ ] **Step 4: Implement `config.py`** (`# ── 1. Shape`, `2. Rules`, `3. Load`, `4. Export and command line`): the config is read with pyyaml (`pyyaml==6.0.3` in `infra/requirements.txt`, as the Data and Ontology tracks pin it); the gateway's request path stays standard library. Each rule raises `ConfigError` with the key in the message; `is_loopback(url) -> bool` covers `127.0.0.1`, `localhost`, `[::1]`. `config.py --export` writes the parsed config as JSON to `infra/data/fixtures/config.json` (the file the Go twin reads, the Data track's `contract.py --export` pattern) and `--check` fails when that file drifts; add `test_export_matches_the_yaml` to the tests.

- [ ] **Step 5: Run the tests** → PASS. Add `conftest.py` with `ROOT`, `FIXTURES` and a `tmp_traces` fixture.

- [ ] **Step 6: Commit** `infra/requirements.txt infra/gateway.yaml infra/python/config.py infra/python/tests/ infra/data/fixtures/configs/ infra/data/traces/.gitkeep .gitignore` as `feat(infra): gateway config with validation`.

### Task 2: The stages, the ledger maths and the shared cases

**Files:**
- Create: `infra/python/stages.py`, `infra/python/ledger.py` (prices and maths only in this task), `infra/data/fixtures/cases.json`, `infra/python/tests/test_stages.py`, `infra/python/tests/test_ledger_math.py`

**Interfaces:**
- Produces in `stages.py`: `Request` (dataclass: `method: str`, `path: str`, `headers: dict[str, str]` lower-cased, `body: bytes`, `json: dict | None`); `Decision = dict` with keys `stage, verdict, detail` (strings) plus stage-specific keys; `State` (dataclass: `spend: dict[str, float]` per team, `cache: dict[str, CacheEntry]`, `buckets: dict[str, Bucket]`, `clock: Callable[[], float]`, `rng: random.Random`); stage functions `tag(req, cfg) -> Decision` (`verdict` is the team: a name or `untagged`; `tag` key = ticket id `t\d{3}` found first in the messages text, else `none`), `budget(req, team, state, cfg) -> Decision` (`allow | warn | refuse`, `worst_usd`, `spent_usd`, `limit_usd`), `cache_lookup(req, state, cfg) -> Decision` (`hit | miss | skip`, `key`), `route(req, cfg) -> Decision` (`route | deny`, `to`, `fallback`), `limit(target, tokens, state, cfg) -> Decision` (`allow | wait | refuse`, `wait_ms`), `retry_plan(cfg, attempt) -> int` backoff ms (`min(max_ms, base_ms * 2**attempt)` plus jitter in `[0, base_ms)` from `state.rng`), `worst_case_tokens(req) -> tuple[int, int]` (input estimate = canonical JSON length // 4 as the proxy does; output = `max_tokens` or 0), `run_chain(req, state, cfg, send: Callable[[str, Request], Upstream]) -> Outcome` (`Outcome`: `status`, `headers`, `body: bytes`, `decisions: list[Decision]`, `team`, `tag`, `usage: dict | None`, `cost_usd: float | None`, `ms: float`, `cached: bool`, `attempts: list[dict]`); `Upstream` (dataclass: `status`, `headers`, `body: bytes`, or `error: str`); `cache_key(path, body) -> str` sha256 hex; `response_usage(headers, body) -> dict | None` and `stop_reason(headers, body) -> str | None` reading JSON bodies and SSE (`message_start` usage, `message_delta` usage and stop_reason); `run_cases(path) -> list[dict]` and a `--cases PATH` CLI printing one JSON line per case `{id, decisions, status}`.
- Produces in `ledger.py`: `lab_prices() -> dict[str, tuple[float, float]]` (imports `PRICES` from `llms/python/triage_lab.py` with `sys.path`, read-only), `CACHE_WRITE_MULTIPLIER = 1.25`, `CACHE_READ_MULTIPLIER = 0.10`, `USAGE_FIELDS`, `cost_of(model, usage, prices) -> float | None` (the proxy's `ledger_cost` formula, R64), `worst_case_usd(model, in_tokens, out_tokens, prices) -> float`.
- Fixture `cases.json`: a list of cases `{id, config: {overrides of gateway.yaml keys}, request: {method, path, headers, body}, state: {spend: {}, cache: [keys stored with stop_reason and tools flag], bucket: {model: {tokens, requests}}}, upstream: [statuses in order, "drop" for a dropped connection], expect: {decisions: [{stage, verdict}], status}}`. Cases (ids): `cacheable_miss`, `cacheable_hit`, `tool_call_skip`, `unknown_model_deny`, `team_over_budget`, `team_warn`, `no_team_refused`, `limit_wait`, `limit_refuse`, `retry_then_ok` (upstream `[429, 200]`), `fallback_after_attempts` (`[500, 500, 500, 200]` with a fallback model), `all_fail_502` (`[500, 500, 500]`), `dropped_connection_retry` (`["drop", 200]`), `budget_refusal_beats_cache_hit`.

- [ ] **Step 1: Write the failing ledger tests** in `test_ledger_math.py`:

```python
def test_prices_are_the_labs():
    assert ledger.lab_prices()["claude-haiku-5-5"] == (0.10, 0.50)

def test_cost_matches_the_proxys_formula():
    usage = {"input_tokens": 1000, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 300, "output_tokens": 100}
    assert ledger.cost_of("claude-haiku-5-5", usage, ledger.lab_prices()) == pytest.approx((1000*0.10 + 200*1.25*0.10 + 300*0.10*0.10 + 100*0.50) / 1e6)
    sys.path.insert(0, str(ROOT / "agents/proxy")); import proxy_cost          # read-only, as the agents tests do
    assert ledger.cost_of("claude-haiku-5-5", usage, ledger.lab_prices()) == proxy_cost.ledger_cost("claude-haiku-5-5", usage)

def test_unknown_model_costs_none():
    assert ledger.cost_of("claude-x", {k: 1 for k in ledger.USAGE_FIELDS}, ledger.lab_prices()) is None
```

- [ ] **Step 2: Write the failing stage tests** in `test_stages.py`: one test per stage function with the values the cases fix (e.g. `test_tag_reads_team_and_ticket` asserts team `desk`, tag `t008` for a body containing `<ticket id="t008">`; `test_budget_refuses_when_worst_case_crosses_limit` with spend 0.95, worst 0.06, limit 1.00 → `refuse`; `test_cache_skips_tool_calls`; `test_route_denies_unknown_model`; `test_limit_waits_then_refuses` with a fake clock; `test_backoff_is_capped_and_jittered` with `random.Random(0)`; `test_usage_from_sse` over a two-event SSE string; `test_stop_reason_from_sse`), and `test_every_case_matches_its_expectation` parametrised over `cases.json` through `run_cases`.

- [ ] **Step 3: Run both files** → FAIL.

- [ ] **Step 4: Implement `ledger.py` (sections 1 Prices, 2 Cost) and `stages.py` (sections 1 Request and state, 2 Tag, 3 Budget, 4 Cache, 5 Route, 6 Limit, 7 Retry and upstream, 8 The chain, 9 Cases and command line).** `run_chain` order: parse (400 if `json` is None or no `model`) → tag → budget (refuse 402) → cache (hit: answer, `x-cache: hit`) → route (deny 404) → limit (wait up to `max_wait_ms` via `state.clock`/sleep injected; refuse 429 with `retry-after`) → retry over `[to] + fallback` × `attempts` with backoff (the sleep is injectable; `send(model, req)` is the upstream call; a fallback changes the target in the decision, never the body) → usage and cost from the response → `Outcome`. The chain never raises: an exception inside a stage yields status 500 and a decision `{stage: <name>, verdict: error, detail: <ExceptionName>}`.

- [ ] **Step 5: Run the tests** → PASS; `stages.py --cases infra/data/fixtures/cases.json` prints 14 lines.

- [ ] **Step 6: Commit** as `feat(infra): the gateway stages, ledger maths and shared cases`.

### Task 3: Ledger and trace stores, the trace query

**Files:**
- Create: `infra/python/traces.py`, `infra/python/tests/test_stores.py`
- Modify: `infra/python/ledger.py` (sections 3 Ledger store, 4 Budget state)

**Interfaces:**
- `ledger.Ledger(path: Path)`: `append(row: dict) -> None` (row keys `ts, team, tag, model, input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens, cost_usd, replayed, cached, status`), `spend_by_team() -> dict[str, float]`, `rows() -> list[dict]`, `load()` skips a half-written last line with a warning on stderr; `ledger.summary(rows) -> dict` (`teams: {team: {requests, cost_usd, cached, refused}}`, `tags: {tag: cost_usd}`, `top_tags: [[tag, cost_usd], ...]` top 10).
- `traces.TraceStore(dir: Path, run: str)`: `write(trace: dict) -> None` (one JSON line: `id, parent_id, ts, team, tag, model, path, status, decisions, ms, usage, cost_usd, cached, attempts, outcome`), `read_run(path) -> list[dict]`; query functions `by(traces, team=None, tag=None, outcome=None)`, `slowest(traces, n)`, `costliest(traces, n)`, `tree(traces, parent_id) -> list[dict]`; a CLI `traces.py RUN.jsonl [--team] [--tag] [--outcome] [--slowest N] [--costliest N] [--tree ID]` printing one line per trace `id status team tag ms cost outcome`.
- `cache` store in `stages.py` gains `load_cache(path) -> dict` and `save_cache(path, cache)` (atomic: write `.tmp` then `os.replace`); a corrupt file loads as `{}` with a warning.

- [ ] **Step 1: Write the failing tests**: `test_ledger_appends_and_sums`, `test_half_written_last_line_is_skipped_with_a_warning` (capsys), `test_summary_top_tags`, `test_trace_roundtrip`, `test_query_filters_and_orders`, `test_tree_follows_parent_ids`, `test_corrupt_cache_loads_empty`, `test_cache_save_is_atomic` (no `.tmp` left), and Review Focus 2: `test_response_without_usage_gives_null_cost` (an SSE body with only `message_stop` → usage None, `cost_usd` None in the ledger row).

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** as `feat(infra): ledger and trace stores with the trace query`.

### Task 4: The server and the injector

**Files:**
- Create: `infra/python/gateway.py`, `infra/python/injector.py`, `infra/data/fixtures/injector/{flaky.json,dead.json,ratelimited.json}`, `infra/python/tests/test_server.py`

**Interfaces:**
- `gateway.make_server(cfg: Config, port: int = 0, state: State | None = None, ledger_path: Path | None = None, traces_dir: Path | None = None, run: str = "run") -> ThreadingHTTPServer` (loopback only; `server.url`); the handler reads the body, builds a `Request`, calls `run_chain` with `send` = `http.client` to `cfg.upstream` (host and port from the URL; path and headers forwarded; `x-team`, `x-request-tag`, `x-parent-id` stripped before forwarding; response headers forwarded from the proxy's allow-list), writes the ledger row and the trace, answers with the outcome's status, headers plus `x-gateway-trace: <id>`, `x-cache`; `main(argv)` with `--config` (default `infra/gateway.yaml`), `--port` (default 8790), `--live`, `--max-cost` (`cost_arg` as the proxy's), `--run NAME`; `--live` prints the estimate line and asks y/N before serving.
- `injector.make_server(script: list[int | str], port: int = 0) -> HTTPServer` answering each request with the next scripted status (200 with a fixed JSON Messages body `end_turn` and usage `{input_tokens: 10, output_tokens: 5}`; 429 with `retry-after: 0`; 500; `"drop"` closes the socket) and cycling at the end; `injector.py --script PATH --port N` CLI; the three fixtures: `flaky` `[429, 500, 200]`, `dead` `[500]`, `ratelimited` `[429, 429, 200]`.

- [ ] **Step 1: Write the failing server tests** (gateway and injector in-process on port 0; `cfg` from `gateway.yaml` with `upstream` replaced by the injector's URL and `retry.base_ms: 1`):

```python
def test_passthrough_answers_and_traces(gw, injector_ok):
    r = post(gw.url, "/v1/messages", BODY, {"x-team": "desk"})
    assert r.status == 200 and r.headers["x-cache"] == "miss" and "x-gateway-trace" in r.headers
    assert last_trace(gw)["decisions"][0]["stage"] == "tag"

def test_flaky_upstream_is_retried(gw_flaky):               # [429, 500, 200]
    r = post(gw_flaky.url, "/v1/messages", BODY, {"x-team": "desk"})
    assert r.status == 200 and [a["status"] for a in last_trace(gw_flaky)["attempts"]] == [429, 500, 200]

def test_dead_upstream_is_502_after_attempts(gw_dead):
    r = post(...); assert r.status == 502 and len(last_trace(gw_dead)["attempts"]) == 3

def test_budget_refusal_is_402_and_traced(gw_small_budget): ...   # body names team and limit
def test_unknown_model_is_404(gw): ...
def test_no_team_is_refused(gw): ...                                # untagged: refuse → 402
def test_malformed_body_is_400_before_any_stage(gw): ...           # Review Focus 1: decisions == [{"stage": "parse", ...}]
def test_remote_upstream_refused_without_live(tmp_path): ...       # Review Focus 5: main() exits 2 naming the host
def test_live_asks_and_declines(monkeypatch, capsys): ...          # input "n" → nothing served, estimate printed
def test_replay_proxy_passthrough():                               # the agents proxy in-process (sys.path agents/proxy, make_server(mode="replay", port=0, store_dir=agents/data/recordings/desk-hand))
    # one recorded request (read from a recording file) through gateway → proxy: 200, body bytes equal playback_bytes(recording["response"]), usage priced == proxy_cost.ledger_cost(...)
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement `gateway.py`** (sections 1 Handler, 2 Forwarding, 3 Server, 4 Live mode, 5 Command line; under 400 lines) **and `injector.py`** (1 Script, 2 Server, 3 Command line). **Step 4: Run** → PASS. **Step 5: Commit** as `feat(infra): the gateway server and the injector fixture upstream`.

### Task 5: The Go twin

**Files:**
- Create: `infra/go/go.mod` (module `lumen-gateway`, go 1.27), `infra/go/gateway/config.go`, `stages.go`, `main.go`, `stages_test.go`, `infra/python/tests/test_twin.py`

**Interfaces:**
- Go: `LoadConfig(path string) (Config, error)` reads the JSON export (`infra/data/fixtures/config.json`; Go never parses YAML, so there is no YAML module: `go.mod` has no requires) and applies the same rules as Task 1 with the same error wording for the key; `Budget`, `CacheLookup`, `Route`, `Limit`, `Retry` with the same verdict strings and the same numbered sections as `stages.py` 3–7 (so a lesson's `goSection` equals the Python section), `RunCases(path string) ([]CaseResult, error)`; `main` accepts `--cases PATH` and prints the same JSON lines as `stages.py --cases` (same key order: `id, decisions, status`).
- Python `test_twin.py` (the Data track's pattern: build once with `go build`, skip if `go` is absent): runs both CLIs over `cases.json` and asserts the outputs are equal line for line; a second test runs both over `configs/*.yaml` expecting the same accept/reject per file.

- [ ] **Step 1: Write `stages_test.go`** with one test per stage over `cases.json` (table-driven) and **`test_twin.py`** with `test_python_and_go_agree_on_every_case` and `test_both_reject_the_same_configs`.
- [ ] **Step 2: Run** `cd infra/go && go test ./...` → FAIL (no code); `.venv-infra/bin/python -m pytest infra/python/tests/test_twin.py` → FAIL.
- [ ] **Step 3: Implement the Go files** over the JSON export and `cases.json` (a case's `config` overrides are applied on top of the export in both languages). The second twin test runs `config.py --export` on each `configs/*.yaml` that loads and the Go `--check-config PATH` on each export, expecting the same accept/reject per file.
- [ ] **Step 4: Run both** → PASS. **Step 5: Commit** as `feat(infra): Go twin of the request path, equal on the shared cases`.

### Task 6: The replay driver, the gate and the derived files

**Files:**
- Create: `infra/python/replay.py`, `infra/python/tests/test_replay.py`, `infra/data/derived/capstone.json`, `infra/data/derived/ledger.json`, `infra/data/derived/traces.sample.json`

**Interfaces:**
- `replay.calls(root: Path) -> list[Call]` (`Call`: `set, team, order, key, request (method, path, body bytes), recorded_ms, recorded_usage, cost_recorded`): for each set with traces (`agents/data/traces/{desk-hand,desk-hand-go,desk-langgraph,desk-sdk,patterns,patterns-go}`), each trace file in sorted order, each `proxy_calls` entry's `key` → the recording `agents/data/recordings/<set>/<key>.json`; then the `judge` set from its recordings in sorted order (no traces). Teams: `desk-*` → `desk`, `patterns*` → `patterns`, `judge` → `judge`. Expected count 957 (183 + 188 + 179 + 190 + 60 + 56 + 101).
- `replay.run(cfg_path, gateway_url, out_dir, calls) -> Capstone`: sends each call with `x-team`, `x-request-tag` (the ticket id from the body or `none`), collects status, `x-cache`, the trace id, and joins `recorded_ms`.
- `replay.gate(capstone, agents_root) -> list[tuple[str, bool, str]]` the four conditions: (1) per desk set, the gateway ledger's cost sum equals the sum of that set's traces' `proxy_calls` `cost_usd` within 1e-9; (2) no team's spend exceeds its limit and every 402 has a trace; (3) every trace has the eight stage names in order (or stops at the refusing stage) and every `parent_id` named exists; (4) the hit rate and p95 equal `capstone.json`'s.
- `capstone.json`: `{config: <the parsed config as JSON>, calls: 957, sets: {set: {calls, cache_hits, hit_rate, retries, refusals, cost_usd, p50_ms, p95_ms, prompt_cache_read_tokens, prompt_cache_write_tokens}}, teams: {...same...}, cases: [{id, decisions, status}] from cases.json, injector: {flaky: <timeline from one in-process run>, dead: ..., ratelimited: ...}, gate: {ok, lines: [[name, ok, detail], ...]}}`. `ledger.json` = `ledger.summary(rows)` plus `agents: {set: cost_usd}` beside it. `traces.sample.json`: the first cached trace (a pattern repeat), the `flaky` injector trace, the first 402 (from a second short run with `budgets.desk.limit_usd: 0.01`, labelled `demo`).
- CLI: `replay.py [--config] [--gateway URL | --start] [--sets a,b] [--gate] [--check] [--out DIR]`; `--start` starts the proxy (`agents/proxy/proxy.py --port 8791` as a subprocess with `.venv-agents/bin/python` if present, else the in-process import) and the gateway on a free port, runs, stops both. Exit 0 after writing even when the gate is red; 1 on `--check` drift.

- [ ] **Step 1: Write the failing tests**: `test_calls_are_957_in_trace_order` (first desk-hand call's key equals the first `proxy_calls` key of the first desk-hand trace), `test_team_and_tag_mapping`, `test_run_over_two_sets_writes_capstone` (proxy + gateway in-process; sets `patterns,judge`; `hit_rate` of patterns ≥ 0; sums match the recordings), `test_gate_ledger_condition_matches_desk_traces` (one desk set), `test_gate_goes_red_on_a_planted_overspend` (a ledger row injected over the limit → condition 2 false), `test_check_detects_drift` (edit a figure in a temp copy), Review Focus 4: `test_pattern_repeats_hit_only_when_end_turn_without_tools`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement `replay.py`** (sections 1 Calls, 2 Run, 3 Gate, 4 Derived files, 5 Command line; under 400 lines). **Step 4: Run** → PASS.
- [ ] **Step 5: Produce the derived files**: `.venv-infra/bin/python infra/python/replay.py --start --gate` from the repo root (the proxy needs `.venv-agents`); then `--check` → "up to date". Record the printed gate lines and the figures in the task report.
- [ ] **Step 6: Commit** as `feat(infra): the replay driver, the capstone gate and the derived lesson data`.

### Task 7: App data, types, diagram and code sources

**Files:**
- Create: `app/src/lib/infraAidsTypes.ts`, `app/src/lib/infraAids.ts`, `app/src/lib/infraData.ts`, `app/src/lib/infraAidsRoutes.ts`, `infraAidsRetry.ts`, `infraAidsCache.ts`, `infraAidsLedger.ts`, `infraAidsTraces.ts`, `app/src/data/diagrams/infra-gateway.ts`, `app/tests/infraaids-roundtrip.test.ts`
- Modify: `app/src/lib/codeSources.ts` (`infra-gateway`, `infra-stages` with `go: 'infra/go/gateway/stages.go'`, `infra-replay`, `infra-ledger`, `infra-traces`, `infra-injector`, `infra-config` YAML by sections), `app/src/lib/codeSourceText.ts`, `app/src/components/FlowDiagram.astro` (register), `app/tests/diagrams.test.ts` (describe block pinning the diagram's caption numbers against `capstone.json`), `app/tests/codesources.test.ts`

**Interfaces:**
- `infraData.ts` loads the three derived files at build time (the `ontologyData.ts` pattern, with the same missing-file error); each `infraAids*.ts` exports a `…Props(data)` builder: `routeTableProps(config)` → rows per model plus the deny row and the replay-key note; `retryTimelineProps(capstone.injector)` → per script a timeline `[{attempt, status, wait_ms, target}]` and the outcome; `cacheReportProps(capstone)` → per set hits, misses, skips, hit rate, prompt-cache read and write tokens, the TTL recompute over the stored timestamps; `ledgerViewProps(ledger)` → teams with limit and warn lines, top tags, the agents figures beside; `traceExplorerProps(traces.sample, capstone.gate)` → the sample traces and the gate lines. Every builder is pure and under 400 lines.

- [ ] **Step 1: Write `infraaids-roundtrip.test.ts`** (each builder over the committed JSON: counts equal the JSON's, props serialise under the per-aid budget, an unknown set or script throws naming the known ones) and the diagram describe block.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `cd app && npx vitest run tests/infraaids-roundtrip.test.ts tests/diagrams.test.ts tests/codesources.test.ts` → PASS. **Step 5: Commit** as `feat(app): infra data loading, aid props, gateway diagram and code sources`.

### Task 8: The five aids

**Files:**
- Create: `app/src/components/infra/{RouteTable,RetryTimeline,CacheReport,LedgerView,TraceExplorer}.{astro,tsx}`, `app/src/components/infra/infraStyle.ts`, `app/tests/infraaids-{routes,retry,cache,ledger,traces}.test.tsx`

**Interfaces:** each `.astro` wrapper reads `infraData` and passes the builder's props to its island (the `ontology/` pattern); each island has one interaction (RouteTable: pick a model; RetryTimeline: pick a script; CacheReport: a TTL control that recomputes; LedgerView: a team filter; TraceExplorer: team/tag/outcome filters and open-one), arrow-key navigation with a live region, state words beside symbols (`hit`, `miss`, `skip`; `allow`, `warn`, `refuse`), CSS tokens only (no hex), a scroll box for anything wider than 375 px.

- [ ] **Step 1: Write the five test files** (render from the committed JSON, the interaction changes the shown rows, keyboard moves focus and updates the live region, every state has a word, no inline hex).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run the five files** → PASS. **Step 5: Commit** as `feat(app): RouteTable, RetryTimeline, CacheReport, LedgerView and TraceExplorer`.

### Task 9: Lessons 1–3, the track file and the pins

**Files:**
- Create: `app/src/content/tracks/infra.yaml` (`id: infra`, `title: AI Infrastructure`, `order: 8`, `status: live`, subsections `["Gateway", "Operations"]`), `app/src/content/lessons/infra/01-gateway.mdx`, `02-routing.mdx`, `03-resilience.mdx`, `app/tests/infralessons.test.ts`
- Modify: `app/tests/agentsaids-evals.test.tsx` (an INFRA block as the ONTOLOGY one: slugs `['gateway','routing','resilience','cache','budgets','capstone']`, aids `{gateway: 'FlowDiagram', routing: 'RouteTable', resilience: 'RetryTimeline', cache: 'CacheReport', budgets: 'LedgerView', capstone: 'TraceExplorer'}`, 200 KB), `app/src/data/glossary.ts` (`gateway`, `token bucket`, `response cache`, `prompt cache`)

- [ ] **Step 1: Write the three lessons** per spec §6 and STYLE: lesson 1 concept tour, the out-of-scope list, the first run (proxy on 8791, gateway on 8790, `replay.py --sets patterns`), the desk unchanged once; lesson 2 routing with the deny and the "bodies are never edited" rule (the replay key); lesson 3 the injector scripts, backoff figures from `cases.json` and the `flaky` timeline. Each with `<CodeTabs source="infra-stages" section={N} goSection={N} />` where the twin exists, a quiz of 3 questions in plain text, Try-it blocks whose outputs are real.
- [ ] **Step 2: Write `infralessons.test.ts`** pinning every number in the three lessons against `capstone.json`, `cases.json` and `gateway.yaml` (the digit guard from the ontology test, lesson refs 1–6), and run `npx vitest run tests/infralessons.test.ts tests/agentsaids-evals.test.tsx tests/quizspread.test.ts tests/style.test.ts tests/lessons.test.ts` → PASS.
- [ ] **Step 3: Commit** as `feat(infra): lessons 1-3, the track file and the pins`.

### Task 10: Lessons 4–6 and the Try-it test

**Files:**
- Create: `app/src/content/lessons/infra/04-cache.mdx`, `05-budgets.mdx`, `06-capstone.mdx`, `infra/python/tests/test_lesson_tryits.py`
- Modify: `app/tests/infralessons.test.ts` (pins for 4–6)

- [ ] **Step 1: Write the three lessons**: lesson 4 the response cache (hits are the pattern repeats; the rate per set; the SDK set's prompt-cache reads and writes as the contrast between an exact-body cache and the API's prefix cache; the lab's identical system prompt as the explanation); lesson 5 budgets, the worst-case line, the 402 body, the ledger beside the agents' to the cent, the top tickets; lesson 6 the full replay, the gate lines, the trace queries, the parent-id gap the caller must close.
- [ ] **Step 2: Write `test_lesson_tryits.py`** (the Ontology track's pattern: run every bash block, compare stdout with the text block, exit 1 only where the prose says so, snapshot and restore every watched file, no paid command, the servers started and stopped by the test on free ports with the printed commands' ports rewritten through an env var the scripts honour: `INFRA_GATEWAY_PORT`, `INFRA_PROXY_PORT`).
- [ ] **Step 3: Run** `.venv-infra/bin/python -m pytest infra -q` and `cd app && npm test` → PASS. **Step 4: Commit** as `feat(infra): lessons 4-6 and the Try-it test`.

### Task 11: README, STYLE, rulings

**Files:**
- Modify: `README.md` (track blurb in the opening paragraph; `infra/` in Layout; a "Run the AI Infrastructure track" section with Setup, Start the proxy and the gateway, Replay and the gate, Trace queries, Test; the `.venv-infra` line in the venv list), `docs/STYLE.md` (the sources row, the aids row, the numbering bullet, the budget sentence), `docs/superpowers/research/2026-10-29-ai-infrastructure-rulings.md` (new: the plan's decisions below plus the build's rulings), `~/ai-engineering-kit` is NOT touched by the build (the controller updates it after merge).

- [ ] **Step 1: Write the docs.** **Step 2: Run** `cd app && npm test && npm run build && npm run package`, `.venv-infra/bin/python -m pytest infra -q`, `cd infra/go && go test ./...`, every `--check`. **Step 3: Commit** as `docs: AI infrastructure track in README, STYLE and the rulings`.

## Plan decisions (the spec's open items and what the data forced)

- P1: All 951 recordings carry `upstream` timing and usage, so no latency figure excludes anything; the capstone replays the traces' call order, 957 calls (the pattern traces repeat 6 recordings), not 951, so the cache has repeats to hit.
- P2: No count_tokens recordings exist; a request without `max_tokens` has a worst-case output of 0 and is never cached or priced.
- P3: The LLMs lab's saved responses do not join the replay (they are responses, not requests). Lesson 4's contrast is the SDK set's recorded prompt-cache reads and writes, with the lab's identical system prompt as the explanation of what a prefix cache helps.
- P4: A third team, `judge` (101 recordings, no traces), joins with its own budget.
- P5: The config is YAML read with pyyaml (as the Data and Ontology tracks do); the gateway's request path stays standard library.
- P6: In replay the gateway measures its own latency (about a millisecond); the capstone's p50 and p95 are over the recordings' wall clocks joined by the driver, which lesson 6 states (R62's rule, kept).
- P7: The gate's ledger condition compares the four desk sets only; the pattern traces repeat recordings, so their trace ledgers exceed their recordings' sums by design, and lesson 5 shows that difference as a cache lesson.
