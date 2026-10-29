# AI Engineering: Ontology track (design)

Date: 2026-10-29
Status: design approved in conversation. This written spec is awaiting review.
Builds on: the seven existing tracks, `docs/STYLE.md`, `~/ai-engineering-kit/APP-DESIGN-TEMPLATE.md`, and the Lumen artefacts those tracks already hold (tickets, the labelling guide, the triage prompts, the saved model responses, the ML category map, the agents traces, the data contracts).

## 1. Intent

**Outcome.** The learner can:
- tell a taxonomy, a schema, an ontology and a knowledge graph apart, and say what each adds;
- write a small ontology for a domain: classes with definitions, properties, relations, individuals and constraints;
- generate from it the artefacts an AI system needs (prompt definitions, a JSON schema, a tool definition) and validate model outputs against it;
- audit every other artefact that implies the same facts, so the ontology becomes the single source of truth.

**Audience.** The same learner, after the Harness, LLMs, ML, MCP, AI Agents and Data Fabric & Data Contracts tracks.

**Success criteria.**
- Lumen's ontology loads, passes its own consistency checks, and validates the 60 tickets.
- The generator reproduces the course's existing definitions, schema and tool definition from the ontology; the lesson states where the hand-written artefacts drift, and a test pins the exact differences.
- The validator scores every replayed triage response from the LLMs track and every recorded `label_ticket` call from the agents traces, with counts per violation type.
- The capstone audit finds every disagreement between the ontology and the labelling guide, the two prompts, the ML category map and the data contracts, with a gate.
- Four lessons pass the style, pin, budget and quiz-spread tests; `ontology.yaml` is live; the page is republished.

**Stated by the user:**
- A short track of four lessons.
- Hand-built in the shape of the standards; Python only, with one Go callout saying why.
- Lessons 3 and 4 generate from the ontology and validate against it, then audit the other artefacts.
- Free and offline.

**Assumptions (accepted):**
- Standard library only plus `pyyaml` and `pytest`.
- Other tracks' files are read in place and never changed.
- AI infrastructure is the next roadmap item, a separate short track with its own spec.

## 2. Scope

**In scope:**
- `ontology/`: the Lumen ontology file, the loader/checker/validator, the generator, the audit, derived JSON.
- 3 aids and 1 diagram.
- 4 lessons, `ontology.yaml` set to live, README and STYLE updates.

**Out of scope (named in lesson 1 as what a production ontology stack adds):**
- Reasoning beyond subclass inheritance (no OWL reasoner).
- SPARQL-style queries and a triple store.
- Live entity linking of free text to individuals.
- Any contract or dataset change in other tracks: the audit reports disagreements; it does not fix them.

## 3. Architecture

```
ontology/
├── lumen.yaml            the ontology: classes, properties, relations, individuals, constraints
├── python/
│   ├── ontology.py       load, check consistency, validate records against it
│   ├── generate.py       prompt definitions, JSON schema, tool definition from the ontology
│   ├── audit.py          compare the ontology with every artefact that implies the same facts
│   └── tests/
├── data/derived/         ontology.json (for the app), validation.json, audit.json
└── requirements.txt      pyyaml, pytest
```

- The file's sections map onto the standards: classes and `subclass_of` ↔ OWL/RDFS, individuals ↔ SKOS concepts, constraints ↔ SHACL shapes. Lesson 1 states the mapping and says the names are the course's own.
- Inputs read in place: `llms/data/tickets.jsonl`, `llms/data/labeling_guide.md`, `llms/data/prompts.json`, `llms/data/responses/*.jsonl`, `ml/data/category_map.json`, `agents/data/traces/*/*.json`, `data/contracts/*.yaml`, and the MCP server's `label_ticket` schema (`mcp/python/ops_server.py`).
- The audit is read-only and deterministic; its findings are lesson 4's table and gate.

## 4. Components

**Ontology file (`ontology/lumen.yaml`).** Five sections, each under a `# ── N. Title` comment so lessons can quote them:
- `classes`: id, label, definition, optional `subclass_of`.
- `properties`: id, domain class, range (a class id, a literal type `string|integer|number|boolean|date`, or an enumeration of individual ids), cardinality `one|many`, required.
- `relations`: named edges between classes (e.g. `Ticket has_category Category`, `Complaint about Product`, `Reply answers Ticket`).
- `individuals`: the six categories and four priorities with their labelling-guide definitions and rule order; the complaint products from the ML category map.
- `constraints`: rules from the labelling guide, each with the guide rule id it encodes and a `kind`: `checkable` (an expression over a record's properties the validator evaluates: category decision order, the "one report is not an outage" tie-breaker as a constraint on the user-count property, the $100 billing threshold for priority) or `manual` (the rule's text, listed, never pretended to be checkable).

**`ontology.py`.** `load(path: Path) -> Ontology`; `check(onto: Ontology) -> list[Finding]` (unknown range, cycle in `subclass_of`, duplicate id, a constraint naming an unknown class or property, an individual whose class has no definition, a required property with no domain); `validate(onto: Ontology, record: dict, cls: str) -> list[Finding]` (required properties present, values in range, enumerations respected, checkable constraints satisfied, manual ones reported as `manual`). Finding shape: `{level: "error"|"warning"|"manual", rule: str, subject: str, detail: str}`, sorted by `(level, rule, subject)`. A malformed file or a dangling reference is a finding naming the id, never an exception.

**`generate.py`.** From the ontology only: `definitions_text(onto) -> str` (the category and priority definitions as prompt prose in rule order), `json_schema(onto) -> dict` (structured output for a triage result: category and priority enumerations, a reply string), `tool_definition(onto) -> dict` (the `label_ticket` tool's input schema). Each is compared with the course's existing artefact (the prompts' rule text, the lab's triage output shape, the MCP server's tool schema); the comparison is a stated lesson fact with the exact diff pinned by a test, never a test failure.

**Validation (`validation.json`).** Every replayed triage response from the LLMs track (per prompt version) and every recorded `label_ticket` call in the agents traces, validated as `TriageResult` records: counts per violation type and per source, plus the list of violating ids. Nothing is called; the responses are read from the saved files.

**`audit.py`.** Compares the ontology with: the labelling guide (every rule id present, category definitions present and worded alike, judged by a normalised string match with the diff shown), `v2_rules` and `v3_examples` (every category and priority named, every checkable rule stated), the ML category map (product individuals), the data contracts (enum values for category, priority and urgency). Output `audit.json`: one row per artefact per fact with `agree` / `disagree` / `missing` and a detail, and a gate: `ok` when no row is `disagree` (`missing` allowed, counted). `--check` fails when the file drifts.

## 5. Lessons

| # | Slug | Title | Subsection | Aid |
|---|---|---|---|---|
| 1 | what | What an ontology is (and what it isn't) | Ontology | `FlowDiagram` `ontology-layers` |
| 2 | lumen | Lumen's ontology | Ontology | `OntologyBrowser` |
| 3 | grounding | Grounding an AI system in it | Grounding | `GroundingReport` |
| 4 | capstone | Capstone: one source of truth | Grounding | `AuditTable` + gate |

- Each lesson follows `docs/STYLE.md`.
- Lesson 1 carries the concept tour: taxonomy vs schema vs ontology vs knowledge graph; OWL, RDFS, SKOS and SHACL named and mapped to the file's sections; where ontologies sit beside LLMs today, in one page; the out-of-scope list as what a production stack adds.
- Lesson 2 has the one Go callout (this track is Python only; nothing here is a twin).
- Every number comes from the derived JSON, pinned (prose and quiz).

## 6. App

- **Code sources:** `ontology-core` (`ontology/python/ontology.py`), `ontology-generate` (`generate.py`), `ontology-audit` (`audit.py`), `ontology-lumen` (the YAML quoted by its `# ── N.` sections).
- **Diagram:** `ontology-layers`: taxonomy → schema → ontology → knowledge graph, what each adds, and where the course's artefacts sit (the category map, the data contracts, Lumen's ontology, the lineage graph).
- **Aids:** `OntologyBrowser` (pick a class; see its definition, properties, relations, individuals and constraints; keyboard navigation and a live region), `GroundingReport` (the generated artefacts beside the hand-written ones with the diff, and the validation counts per violation type, pass and fail marked by text as well as colour), `AuditTable` (one row per artefact per fact, agree/disagree/missing, and the gate verdict verbatim). All read `ontology/data/derived/*.json` at build time; logic in `src/lib/ontologyAids*.ts` under 400 lines each; the track's shipped props under 200 KB; 375 px without page scroll.
- **Track file:** `ontology.yaml` live with subsections "Ontology" and "Grounding".

## 7. Error handling

- **Loader and checker:** a malformed file or a dangling reference is a finding naming the id, never an exception; a constraint that cannot be evaluated is reported `manual`, never silently passed.
- **Generator:** a mismatch with a hand-written artefact is a stated lesson fact with the diff pinned, not a test failure.
- **Audit:** an artefact that cannot be parsed is a row of `missing` with a reason; `--check` fails on drift; a `disagree` row turns the gate red and names the artefact and the fact.

## 8. Testing

- Loader and checker on a fixture ontology with each defect; validation on fixture records; the generator against the committed artefacts (exact diffs pinned); the audit against a fixture set with a planted disagreement.
- App: Vitest; every lesson number pinned, quiz included; the budget and quiz-spread tests.
- No API calls in tests.

## 9. Cost and setup

- A venv at `.venv-ontology` (pyyaml, pytest).
- Cost: zero.

## 10. Open items

- Which labelling-guide rules are `checkable` against the ticket record's fields as they exist today (the tickets carry text, category, priority, tags, split; user counts and amounts are in the text, not fields). The plan decides: constraints over fields are `checkable`; those needing the text are `manual`, and lesson 3 says that is the honest line.
- Whether the saved triage responses' output shape carries both category and priority for every prompt version; the plan confirms from the files.
