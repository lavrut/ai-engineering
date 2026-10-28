# Data Fabric & Data Contracts Track Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a six-lesson track that catalogues the course's shared Lumen datasets, writes contracts for three of them, enforces the contracts identically in Python and Go, and carries a breaking change through versioning and consumer migration.

**Architecture:** hand-built, in the shape of the Open Data Contract Standard: YAML contracts under `data/contracts/`, a Python validator with a Go twin proven equal on shared fixtures, a catalogue/lineage script over a fixed list of datasets, derived JSON for four app aids, and consumer-contract tests on the MCP read server and the agents eval. Datasets are read in place, never copied; the capstone's v2 dataset is the only derived data file.

**Tech Stack:** Python 3.12 in `.venv-data` (pyyaml 6, pytest), Go 1.27.2 standard library, Astro 7 + Preact + Vitest in `app/`.

**Spec:** `docs/superpowers/specs/2026-10-28-data-fabric-contracts-design.md`

## Global Constraints

- Free and offline: no API calls anywhere; tests make no network calls.
- Python runs only in `.venv-data` (`python3 -m venv .venv-data && .venv-data/bin/pip install -r data/requirements.txt`); never base Python. Go: `cd data/go && go test ./...`.
- Standard library only in both languages, plus `pyyaml` and `pytest` in Python.
- Datasets of other tracks are read in place and never modified; `harness/`, `llms/`, `ml/`, `mcp/`, `agents/` are imported or read, never edited, except the two consumer-contract tests this plan adds under `data/python/tests/` (they import the consumers; they do not change them) and the capstone shim, which lives under `data/`.
- Contract sections, exactly: `info`, `schema`, `quality`, `servicing`, `consumers`. Field types, exactly: `string`, `integer`, `number`, `boolean`, `object`, `array`.
- Verdict shape, exactly: `{"rule": str, "field": str | null, "ok": bool, "detail": str}`, sorted by `(rule, field)`; both validators print one line per verdict as `OK  rule field: detail` or `FAIL rule field: detail`, then `verdicts: N ok, M failed`; exit 1 when any failed.
- Versioning: same major for additive changes; new major for renames, removals and type changes; a deprecated version carries `sunset: YYYY-MM-DD`.
- Every number a lesson states is pinned by a test (prose and quiz); the shipped agents-style props budget (200 KB per track) and the quiz-spread test apply.
- Lessons follow `docs/STYLE.md`; the aids follow the MCP and agents aids' patterns (tokens only, keyboard, live regions, tables in their own scroll box, 375 px without page scroll).
- Stage explicit paths; never amend; no attribution lines; never commit `.vscode/`, `*.ipynb`, `.venv-*`, `app/dist`, `app/publish`.

## Review Focus

- A dataset row with a field the contract does not list: a reasonable person expects a warning-level verdict (`extra_field`, ok) not a failure, since additive data is allowed; pinned in Task 1.
- A contract field the dataset lacks in every row when `required: true`: a failed `required` verdict naming the field, not an exception; pinned in Task 1.
- An empty dataset file: `row_count` fails against a minimum of 1, every other rule reports `ok` with detail "no rows", exit 1; pinned in Task 1 and Task 2.
- A consumer declaring a contract version that has no file: the consumer-contract test fails naming the version; pinned in Task 4.
- A repo without git history (ZIP download): freshness reports `unknown` (ok, detail "no git date") rather than failing; pinned in Task 3.

---

### Task 1: Contract format and the Python validator

**Files:**
- Create: `data/requirements.txt` (`pyyaml==6.0.3`, `pytest>=8`), `data/python/contract.py`, `data/fixtures/gen_cases.py`, `data/fixtures/cases.jsonl`, `data/fixtures/README.md`
- Test: `data/python/tests/test_contract.py`

**Interfaces:**
- Produces: `load_contract(path: Path) -> dict` (validated shape: the five sections; raises `ContractError` naming the section or field on a malformed contract); `validate(contract: dict, rows: list[dict], *, file_age_days: float | None = None) -> list[dict]` (verdicts per the Global Constraints: rules `required`, `type`, `enum`, `pattern`, `extra_field`, `row_count`, `unique`, `enum_coverage`, `freshness`); `read_rows(path: Path) -> list[dict]` (JSONL or a JSON array; a bad line is one failed `parse` verdict, not an exception); `main(argv) -> int` for `python data/python/contract.py <contract.yaml> <dataset> [--age-days N]`.
- Fixture cases: `data/fixtures/cases.jsonl`, one JSON object per line: `{"name", "contract": {...}, "rows": [...], "age_days": null|number, "expect": [verdicts]}`; `gen_cases.py --check` regenerates and compares.

- [ ] **Step 1: Write the failing tests** in `test_contract.py`: `test_required_field_missing_fails`, `test_type_mismatch_fails_with_row_index`, `test_enum_and_pattern`, `test_extra_field_is_ok_warning`, `test_row_count_bounds_and_empty_file`, `test_unique_key`, `test_enum_coverage`, `test_freshness_unknown_when_no_age`, `test_freshness_fails_past_max_age`, `test_verdicts_sorted_and_exact_shape`, `test_bad_contract_names_section`, `test_bad_jsonl_line_is_parse_verdict`, `test_cli_prints_lines_and_exit_code`. Assert the exact verdict dicts from the Global Constraints.
- [ ] **Step 2: Run** `.venv-data/bin/python -m pytest data/python/tests/test_contract.py -v`. Expected: FAIL, module not found.
- [ ] **Step 3: Implement `contract.py`** with the interfaces above. Numbered sections (`# ── 1. Contract files`, `2. Rows`, `3. Rules`, `4. Verdicts`, `5. Command line`). Freshness: `servicing.freshness.max_age_days`; `file_age_days=None` → `{"rule": "freshness", "field": null, "ok": true, "detail": "unknown: no git date"}`.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Write `gen_cases.py`** producing at least 12 cases (one per rule, the empty file, the extra field, a bad line, a passing dataset) from Python, and `--check`; run it, commit `cases.jsonl`; `test_cases_file_matches_generator` runs the check.
- [ ] **Step 6: Commit** `data/requirements.txt data/python/contract.py data/python/tests/test_contract.py data/fixtures/` as `feat(data): contract format and Python validator`.

### Task 2: The Go twin validator

**Files:**
- Create: `data/go/go.mod` (module `lumen-data`, go 1.27), `data/go/validate/main.go`, `data/go/validate/contract.go`, `data/go/validate/rules.go`
- Test: `data/go/validate/validate_test.go`, `data/python/tests/test_twin.py`

**Interfaces:**
- Consumes: `data/fixtures/cases.jsonl` (Task 1).
- Produces: `go run ./validate <contract.yaml> <dataset> [-age-days N]` printing the same lines and exit code as Python; `Validate(c Contract, rows []map[string]any, ageDays *float64) []Verdict`.

- [ ] **Step 1: Write `validate_test.go`**: `TestCasesMatchFixtures` reads every case, runs `Validate`, and compares the verdict slice to `expect` exactly (JSON-encoded, sorted); `TestCLIOutputAndExit` runs the binary on one passing and one failing case.
- [ ] **Step 2: Run** `cd data/go && go test ./...`. Expected: FAIL to compile.
- [ ] **Step 3: Implement** a minimal YAML reader for the contract subset (standard library only: parse the contract from JSON instead by having Task 1's `contract.py` write `*.contract.json` beside each YAML via `python data/python/contract.py --export`, and have Go read the JSON). Ruling for the implementer: Go reads the exported JSON; the lesson states that the YAML is the source and the JSON its machine form, regenerated by `--export` and checked by `--check`.
- [ ] **Step 4: Run** Go tests. Expected: PASS. Then `test_twin.py::test_go_and_python_print_identical_lines` runs both CLIs over every fixture case written to a temp dir and compares stdout and exit codes.
- [ ] **Step 5: Commit** as `feat(data): Go twin validator, equal on the shared fixtures`.

### Task 3: Three contracts, quality and freshness

**Files:**
- Create: `data/contracts/tickets.v1.yaml`, `data/contracts/complaints.v1.yaml`, `data/contracts/agent-traces.v1.yaml` (+ their `.contract.json` exports), `data/python/quality.py`, `data/data/derived/verdicts.json`
- Test: `data/python/tests/test_quality.py`, `data/python/tests/test_contracts_hold.py`

**Interfaces:**
- Consumes: Task 1's `load_contract`, `validate`, `read_rows`.
- Produces: `git_age_days(path: Path) -> float | None` (None outside a git checkout or for an uncommitted file); `run(contract_path: Path, dataset_path: Path) -> list[dict]`; `write_verdicts(out: Path) -> dict` writing `verdicts.json` = `{"generated": "<git HEAD short sha>", "datasets": {id: {"contract": "tickets.v1", "path": ..., "age_days": ..., "verdicts": [...], "ok": bool}}}`; `--check`.
- Contracts: `tickets.v1` over `llms/data/tickets.jsonl` (fields id `^t\d{3}$`, text, category enum of the six, priority enum of the four, tags array, split enum dev|test; row_count 60..60; unique id; enum_coverage category and priority; freshness max_age_days 365; owner `llms`; consumers: `mcp-read-server` v1, `agents-eval` v1). `complaints.v1` over `ml/data/complaints.jsonl` (id, date pattern `^\d{4}-\d{2}-\d{2}$`, product, product_raw, narrative; row_count 3600..3600; unique id; owner `ml`; consumer `mcp-read-server` v1 via the classify tool). `agent-traces.v1` over `agents/data/traces/desk-sdk/sdk-single-t001.json` as the representative file and, in `quality.py`, over every file under `agents/data/traces/*/` (fields engine, desk, ticket, trace array, proxy_calls array, errors array; owner `agents`; consumer `agents-eval` v1).

- [ ] **Step 1: Write the tests**: `test_git_age_days_none_without_git` (temp dir), `test_three_contracts_hold` (every verdict ok on the live files), `test_verdicts_json_check`, and the Review Focus pin `test_freshness_unknown_in_zip_download` (copy a dataset to a temp dir with no `.git`; expect the `freshness` verdict ok with detail "unknown: no git date").
- [ ] **Step 2: Run.** Expected: FAIL (no contracts, no quality module).
- [ ] **Step 3: Write the three contracts and `quality.py`**; export the JSON forms; generate `verdicts.json`.
- [ ] **Step 4: Run the tests and `data/python/quality.py --check`.** Expected: PASS, "verdicts.json matches".
- [ ] **Step 5: Commit** as `feat(data): contracts for tickets, complaints and agent traces; quality and freshness`.

### Task 4: Catalogue, lineage and the consumer-contract tests

**Files:**
- Create: `data/python/catalog.py`, `data/data/derived/catalog.json`, `data/data/derived/lineage.json`
- Test: `data/python/tests/test_catalog.py`, `data/python/tests/test_consumers.py`

**Interfaces:**
- Produces: `DATASETS: list[dict]` (fixed list: id, path or glob, producer track, description; the nine datasets from the spec §4: tickets, complaints, category_map, reply_labels, model_responses (`llms/data/responses/*.jsonl`), ml_reports (`ml/reports/*.json`), ml_registry, mcp_recordings (`mcp/data/recordings/*`), mcp_derived, agents_traces, agents_derived); `catalog(root: Path) -> dict` = `{id: {"path", "files", "bytes", "rows", "schema": {field: type}, "producer", "consumers": [...], "contract": "tickets.v1" | null, "last_commit": sha|null, "missing": bool}}`; `lineage(catalog: dict, contracts: list[dict]) -> dict` = `{"nodes": [...], "edges": [{"from": producer-or-dataset, "to": consumer, "contract": id|null}]}`; `--check`.
- Consumer tests: `test_mcp_read_server_reads_tickets_v1` imports `lumen_core.tickets` (`mcp/core/python/lumen_core/tickets.py`, its `SOURCES` list) and asserts its first source is the path `tickets.v1` governs and that the rows it loads pass `tickets.v1`; `test_agents_eval_reads_tickets_v1` imports `agents/desk/python/desk_core.py`'s `LUMEN_TICKETS` and `load_eval_tickets()` and asserts the same; `test_consumer_declared_version_exists` (Review Focus): every `consumers[].version` in every contract has a file `data/contracts/<id>.v<version>.yaml`, failing with the version named.

- [ ] **Step 1: Write the tests** against a tiny fixture repo in a temp dir (two datasets, one missing) plus the live-repo consumer tests.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement `catalog.py`**; schema sketch from the first 50 rows; `last_commit` via `git log -1 --format=%h -- <path>`, None when git is absent.
- [ ] **Step 4: Run the tests and `--check`.** Expected: PASS; `catalog.json` + `lineage.json` written; their total under 60 KB.
- [ ] **Step 5: Commit** as `feat(data): catalogue and lineage; consumer contract tests`.

### Task 5: Capstone: tickets v2, migration and the gate

**Files:**
- Create: `data/contracts/tickets.v2.yaml` (+ export), `data/python/migrate_tickets.py`, `data/data/tickets.v2.jsonl`, `data/python/consumers/agents_eval_v2.py`, `data/python/gate.py`
- Modify: `data/contracts/tickets.v1.yaml` (status `deprecated`, `sunset: 2027-01-31`, consumers now `mcp-read-server` v1 only), `data/data/derived/verdicts.json` (adds the v2 entries), `data/data/derived/lineage.json` (edges carry the version)
- Test: `data/python/tests/test_capstone.py`

**Interfaces:**
- `migrate_tickets.py` → `migrate(row: dict) -> dict` renames `priority` → `urgency`, keeps everything else; `main` writes `data/data/tickets.v2.jsonl` deterministically and `--check` compares. v2 contract: `urgency` enum of the four, `category` enum of the six plus `security`, `row_count` 60..60, consumers: `agents-eval` v2.
- `consumers/agents_eval_v2.py` → `load_eval_tickets_v2() -> list[dict]`: reads `data/data/tickets.v2.jsonl`, keeps the 13 eval ids from `agents/evals/tickets.jsonl`, maps `urgency` back to `priority` for the scorer (the shim), and asserts equality with `desk_core.load_eval_tickets()` on every field but the renamed one.
- `gate.py` → `gate() -> dict` = `{"consumers": [{"name", "declared", "contract_exists", "verdicts_ok"}], "ok": bool, "reason": str}`; ok only when every consumer's declared version exists and validates green. Writes `data/data/derived/gate.json`; `--check`.

- [ ] **Step 1: Write the tests**: `test_v1_rows_fail_v2_contract` (the `required urgency` verdict fails), `test_migrated_rows_pass_v2`, `test_v1_still_passes_v1`, `test_shim_matches_v1_loader`, `test_gate_green_with_both_versions`, `test_gate_red_when_a_consumer_declares_a_missing_version` (temp copy with a consumer on v3).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the four files and the contract edits; regenerate verdicts, lineage, gate.
- [ ] **Step 4: Run** the data suite and every `--check` (`contract.py --export --check`, `quality.py --check`, `catalog.py --check`, `migrate_tickets.py --check`, `gate.py --check`). Expected: PASS; `git status --short llms ml mcp agents` empty.
- [ ] **Step 5: Commit** as `feat(data): capstone: tickets v2, migration shim and the gate`.

### Task 6: App: code sources, diagram and aids

**Files:**
- Modify: `app/src/lib/codeSources.ts`, `app/src/lib/codeSourceText.ts`, `app/src/components/FlowDiagram.astro`, `app/astro.config.mjs` and `app/vitest.config.ts` (allow `../data`), `app/tests/codesources.test.ts`, `app/tests/diagrams.test.ts`
- Create: `app/src/data/diagrams/data-fabric.ts`, `app/src/lib/dataData.ts`, `app/src/lib/dataAidsCatalog.ts`, `app/src/lib/dataAidsContract.ts`, `app/src/lib/dataAidsLineage.ts`, `app/src/components/data/{CatalogExplorer,QualityReport,ContractViewer,LineageDiff}.{astro,tsx}`, `app/src/components/data/dataStyle.ts`
- Test: `app/tests/dataaids-*.test.tsx`, `app/tests/dataaids-roundtrip.test.ts`

**Interfaces:**
- Code sources: `data-contract` (python `data/python/contract.py`, go `data/go/validate/rules.go`, goMore `main.go`, `contract.go`), `data-catalog` (`data/python/catalog.py`), `data-quality` (`data/python/quality.py`), `data-gate` (`data/python/gate.py`, `migrate_tickets.py` as pythonMore), and the three v1 contracts quoted as text sources (`data-contract-tickets`, `-complaints`, `-traces`) by `# ── N.` comment sections inside the YAML.
- Aids read `data/data/derived/{catalog,lineage,verdicts,gate}.json` at build time via `dataData.ts`; props narrowed per lesson (`CatalogExplorer datasets={[...]}`, `QualityReport dataset="tickets"`, `ContractViewer contract="tickets.v1" verdicts={true|false}`, `LineageDiff from="tickets.v1" to="tickets.v2"`).

- [ ] **Step 1: Write the failing tests**: registry entries exist and sections match the source files; diagram shape test like `agent-patterns`; each aid renders its data, keyboard and live-region tests as the MCP aids have; the round-trip test for any compact encoding; every displayed number pinned against the derived JSON; the props-budget test includes the data lessons (< 200 KB).
- [ ] **Step 2: Run** `cd app && npm test`. Expected: FAIL.
- [ ] **Step 3: Implement**; tokens only; tables in their own scroll box; pass and fail marked by text.
- [ ] **Step 4: Run** `npm test && npm run build`. Expected: PASS, 0 errors.
- [ ] **Step 5: Commit** as `feat(app): data track aids, diagram and code sources`.

### Task 7: Lessons 1–6, track live, docs

**Files:**
- Create: `app/src/content/lessons/data/01-fabric.mdx` … `06-capstone.mdx`
- Modify: `app/src/content/tracks/data.yaml` (`status: live`), `README.md` (section "Run the Data Fabric & Data Contracts track": `.venv-data`, the validators in both languages, every `--check`), `docs/STYLE.md`, `app/src/data/glossary.ts`
- Test: `app/tests/datalessons.test.ts` (every prose and quiz number pinned to the derived JSON, the fixtures, source text or command output run in this task), the quiz-spread test covering `data`, `data/python/tests/test_lesson_tryits.py` (every Try-it command run once; none can pay)

- [ ] **Step 1: Write the lessons** per spec §5: lesson 1's concept page (fabric vs mesh vs warehouse; the Open Data Contract Standard as the reference form; tools in one callout; what a production fabric adds); lessons 2–6 Concept → See it → The code → (How Go does it, lesson 5) → Try it + quiz. Every printed command is run once from the repo root with `.venv-data`.
- [ ] **Step 2: Write the pins and the try-it test.** Run `cd app && npm test && npm run build && npm run package` and `.venv-data/bin/python -m pytest data -q` and `cd data/go && go test ./...`. Expected: all PASS.
- [ ] **Step 3: Run every other suite** (harness, llms + inspect, ml, mcp, agents; their Go packages) to prove nothing else moved. Expected: counts as on main.
- [ ] **Step 4: Commit** as `feat(data): lessons 1-6, track live`.
