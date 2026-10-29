# AI Engineering: Data Fabric & Data Contracts track (design)

Date: 2026-10-28
Status: design approved in conversation. This written spec is awaiting review.
Builds on: the six existing tracks, `docs/STYLE.md`, `~/ai-engineering-kit/APP-DESIGN-TEMPLATE.md`, and the datasets those tracks already share.

## 1. Intent

**Outcome.** The learner can:
- catalogue the datasets a company's AI systems share and trace lineage between producers and consumers;
- write a data contract that states a dataset's schema, the meaning of each field, its quality and freshness guarantees, its owner and its consumers;
- enforce the contract in two languages with identical verdicts;
- carry a breaking change through versioning, a deprecation window and consumer migration without surprising anyone.

**Audience.** The same learner, after the Harness, LLMs, ML, MCP and AI Agents tracks.

**Success criteria.**
- A script catalogues every shared Lumen dataset from the repo, with lineage.
- Three contracts validate green against their datasets in Python and Go, and both validators reproduce a shared fixture file of verdicts exactly.
- The capstone's breaking change is caught by the validators, versioned, and migrated; every consumer under contract is green against the version it declares.
- Six lessons pass the style, pin, budget and quiz-spread tests; `data.yaml` is live; the page is republished.

**Stated by the user:**
- Build on the course's own data; cover the field's concepts and standards briefly; keep the track shorter than the others.
- Six lessons.
- Python, with a Go twin for the contract validator only.
- Catalogue every shared dataset by script; write contracts for three that cross track boundaries.
- The capstone is a breaking change under contract, end to end.
- Hand-built, in the shape of the Open Data Contract Standard; no contract tooling dependencies.

**Assumptions (accepted):**
- Free and offline: no API calls anywhere; consumers replay from existing recordings.
- Standard library only in both languages, plus `pyyaml` and `pytest` in Python.
- Ontology and AI infrastructure are the next two roadmap items, each a short track with its own spec.

## 2. Scope

**In scope:**
- `data/`: three contracts (plus the capstone's v2), the Python validator, catalogue, quality and freshness code, the Go twin validator, shared fixtures, derived JSON.
- 4 aids and 1 diagram.
- 6 lessons, `data.yaml` set to live, README and STYLE updates.

**Out of scope (named in lesson 1 as what a production fabric adds):**
- Access control and governance.
- Streaming or event-time freshness.
- A live metadata store or any hosted catalogue product.
- Contract tooling (contract CLIs, Great Expectations, dbt tests): named, not installed.
- Any other track's code; datasets are read in place, never copied or changed (the capstone's v2 dataset is a derived copy under `data/`).

## 3. Architecture

```
data/
├── contracts/            tickets.v1.yaml, complaints.v1.yaml, agent-traces.v1.yaml,
│                         tickets.v2.yaml (the capstone's new version)
├── python/
│   ├── contract.py       load a contract, validate a dataset against it, report verdicts
│   ├── catalog.py        walk a fixed list of datasets, build the catalogue and lineage, write derived JSON
│   ├── quality.py        quality and freshness rules (row counts, uniqueness, enums, age)
│   └── tests/
├── go/validate/          the twin validator: same verdicts on data/fixtures
├── fixtures/             shared validator cases: dataset snippets + expected verdicts, generated with --check
├── data/derived/         catalog.json, lineage.json, verdicts.json for the app
└── requirements.txt      pyyaml, pytest
```

- Contracts follow the Open Data Contract Standard's section names in a documented subset, so a learner who meets the real standard recognises it.
- Datasets are never copied: the catalogue points at where each track keeps its files, and validators read them in place.
- Consumers under contract: the MCP read server (tickets, the complaints model) and the agents eval (tickets, agent traces). Each contract's `consumers` section names them with the version each declares, and a test per consumer fails when its declared version no longer matches what it reads.

## 4. Components

**Contract format.** One YAML file per dataset version:
- `info`: id, version, status (`active`, `deprecated`), owner (a track, not a person), description.
- `schema`: fields with name, type (`string`, `integer`, `number`, `boolean`, `object`, `array`), required, `enum` or `pattern` where it applies, and a one-line meaning.
- `quality`: row count bounds, uniqueness keys, enum coverage (every enum value appears at least once when the rule says so).
- `servicing`: freshness as a maximum age, measured from a `generated_at` field the producer writes or, failing that, the file's last git commit date.
- `consumers`: name, declared version, how it reads the dataset (a path or an import).
- Versioning rule: same major for additive changes; new major for renames, removals and type changes; a deprecated version keeps a `sunset` date.

**Validator.** `contract.py` and `go/validate` take a contract path and a dataset path and return verdicts, each `{rule, field, ok, detail}`, deterministic and sorted by rule then field. Exit code is non-zero when any verdict fails. Both print the same lines. `data/fixtures/cases.jsonl` holds small dataset snippets with expected verdicts; both languages reproduce it exactly, with a `--check` mode like the other tracks.

**Catalogue and lineage.** `catalog.py` reads a fixed list of dataset paths (never a filesystem crawl): the 60 Lumen tickets, the complaints set and its category map, the reply labels, the saved model responses, the ML reports and registry, the MCP recordings and derived data, the agents traces and derived files. For each it records size, row count, a schema sketch (field names and inferred types from a sample), producer track, consumer tracks, and the git commit that last changed it. Lineage edges come from the contracts' consumer lists plus a declared producer per dataset. Output: `catalog.json`, `lineage.json`.

**Quality and freshness.** `quality.py` runs a contract's rules against the live file and writes `verdicts.json`. In a static repo, freshness reads the git date; lesson 3 says what changes in a live pipeline.

**Capstone change.** `tickets.v2.yaml` renames `priority` to `urgency` and adds the category value `security`. A derived v2 dataset under `data/data/` is produced by a documented migration script; the v1 file is untouched. The contract carries a migration note and a `sunset` date for v1. One consumer (the agents eval) is migrated through a shim that reads v2; the other (the MCP read server) stays on v1 until its sunset. `verdicts.json` records before and after, and the gate is: every consumer green against the version it declares.

## 5. Lessons

| # | Slug | Title | Subsection | Aid |
|---|---|---|---|---|
| 1 | fabric | What a data fabric is | Data Fabric | `FlowDiagram` `data-fabric` |
| 2 | catalog | Cataloguing and lineage | Data Fabric | `CatalogExplorer` |
| 3 | quality | Quality and freshness checks | Data Fabric | `QualityReport` |
| 4 | contract | Writing a data contract | Data Contracts | `ContractViewer` |
| 5 | enforce | Enforcing it in two languages | Data Contracts | `ContractViewer` (verdicts) |
| 6 | capstone | Capstone: a breaking change under contract | Data Contracts | `LineageDiff` + gate |

- Each lesson follows `docs/STYLE.md`.
- Lesson 1 carries the concept tour: fabric versus mesh versus warehouse in a page; the Open Data Contract Standard as the reference form; the tools named in one callout.
- Lesson 5 has "How Go does it"; the others carry Go callouts only where something differs.
- Every number comes from the derive output or the validators, pinned (prose and quiz).

## 6. App

- **Code sources:** `data-contract` (Python + Go), `data-catalog`, `data-quality`, and the three v1 contract YAML files quoted by section.
- **Diagram:** `data-fabric`: datasets as nodes, tracks as producers and consumers, contracts on the edges.
- **Aids:** `CatalogExplorer` (browse the catalogue, pick a dataset, see its schema sketch, producer and consumers), `QualityReport` (the rules and verdicts for one dataset, pass and fail marked by text as well as colour), `ContractViewer` (a contract rendered section by section, with the verdict list beside it in lesson 5), `LineageDiff` (v1 against v2: which consumers are green on which version, and the gate verdict). All read `data/data/derived/*.json` at build time; logic in `src/lib/dataAids*.ts`, each under 400 lines; the shipped props under 200 KB; 375 px without page scroll.
- **Track file:** `data.yaml` goes live with its two subsections.

## 7. Error handling

- **Validator:** a missing file, an unreadable row or an unknown rule type is a failed verdict with a reason, never an exception; exit code non-zero when any verdict fails; Python and Go print the same lines.
- **Catalogue:** a listed dataset that is missing is recorded as `missing`, never skipped silently; `--check` fails when the derived JSON drifts.
- **Capstone:** a consumer whose declared version has no contract file fails its contract test with the version named.

## 8. Testing

- Validator fixtures in both languages, with the twin `--check` test.
- Catalogue and quality against a tiny fixture repo in a temp dir.
- A consumer-contract test per consumer (MCP read server, agents eval).
- App: Vitest, with every lesson number pinned, quizzes included, and the budget and quiz-spread tests.
- No API calls in tests.

## 9. Cost and setup

- A venv at `.venv-data` (pyyaml, pytest) plus Go 1.27.2.
- Cost: zero.

## 10. Open items

- Whether `generated_at` exists in any current dataset; if none, lesson 3 teaches freshness from git dates only, and the contract format keeps the field optional.
- The exact migration shim in the agents eval: a reader that maps v2's `urgency` back to `priority` for the scorer, chosen in the plan.
