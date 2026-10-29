# AI Infrastructure track: decisions and rulings made during the build

Date: 2026-10-29. Branch `feat/infra-track`. Spec `docs/superpowers/specs/2026-10-29-ai-infrastructure-design.md`, plan `docs/superpowers/plans/2026-10-29-ai-infrastructure.md`. The plan's decisions P1-P7 come first, then the controller's rulings I1-I12 from the build ledger in the order made, then the implementers' rulings that matter most.

## Plan decisions

- P1: All 951 recordings carry `upstream` timing and usage, so no latency figure excludes anything; the capstone replays the traces' call order, 957 calls (the pattern traces repeat 6 recordings), not 951, so the cache has repeats to hit.
- P2: No count_tokens recordings exist; a request without `max_tokens` has a worst-case output of 0 and is never cached or priced.
- P3: The LLMs lab's saved responses do not join the replay (they are responses, not requests). Lesson 4's contrast is the SDK set's recorded prompt-cache reads and writes, with the lab's identical system prompt as the explanation of what a prefix cache helps.
- P4: A third team, `judge` (101 recordings, no traces), joins with its own budget.
- P5: The config is YAML read with pyyaml (as the Data and Ontology tracks do); the gateway's request path stays standard library.
- P6: In replay the gateway measures its own latency (about a millisecond); the capstone's p50 and p95 are over the recordings' wall clocks joined by the driver, which lesson 6 states (R62's rule, kept).
- P7: The gate's ledger condition compares the four desk sets only; the pattern traces repeat recordings, so their trace ledgers exceed their recordings' sums by design, and lesson 5 shows that difference as a cache lesson.

## Rulings from the build ledger

- Ruling I1: `ModelCfg.price: tuple[float, float] | str`; `ledger.resolve_prices(cfg) -> dict[str, tuple[float, float]]` turns `lab` into the lab's table and a `[in, out]` pair into itself; stages and the server use the resolved table — keeps the YAML readable and the Go twin's JSON export literal — cost if wrong: one helper
- Ruling I2: the request path (stages, server, ledger, traces, replay) is standard library; the config loader uses pyyaml (plan P5) — the spec's "standard library only for the gateway" is read as the request path — cost if wrong: a hand-written YAML subset reader later
- Ruling I3: `infra/requirements.txt` gains httpx pinned as agents/requirements.txt pins it, so the agents proxy imports read-only in `.venv-infra` for tests and `replay.py --start`; the gateway's own request path stays standard library — the alternative (a subprocess with .venv-agents) would make the suite depend on another venv — cost if wrong: one requirement line. Applied from Task 4 on (Task 4's dispatch carries it); Task 2's stand-in test is a deferred minor unless the reviewer flags it.
- Ruling I4: split rather than waive — the Python side is split the same way (responses.py, ledger.py) and lessons quote stages.go sections 3–7, which a split leaves in place; json.go, responses.go, ledger.go in the same package — cost if wrong: file moves only.
- Ruling I5 (amends P7): gate condition 1 reads "the gateway ledger plus the recorded cost of that set's cache hits equals the agents traces' cost within 1e-9", and the gate line names the hits — a hit is money the gateway saved and the agents ledger still counts; the lesson says so — cost if wrong: one gate line.
- Ruling I6: the replay driver runs the gateway on replay time (the clock advances by each call's recorded ms), so the limiter judges the recorded cadence, not the replay's speed; lesson 6 states it and the `about` key carries it — cost if wrong: limiter numbers in lessons 3 and 6.
- Ruling I7 (amends P1/P7): the cache's hits are the cross-set repeats, not the within-set ones; lessons 4 and 6 are written from that fact — the data settles it — cost if wrong: lesson text.
- Ruling I8: the Task 8 brief's TTL control is replaced by that toggle — the derived data has no timestamps per hit; a TTL recompute would be invented — cost if wrong: one aid interaction
- Ruling I9: Task 9 writes the INFRA budget block asserting the exact six-slug list but marks it it.skip until Task 10 adds lessons 4–6 (the ONTOLOGY block asserts an exact list; three of six files would fail it) — Task 10 un-skips it — cost if wrong: one it.skip left behind (the final review checks).
- Ruling I10: the "desk unchanged" Try-it is the agents chain pattern called through the gateway (run.py owns port 8790 for its proxy); lesson 6 and the README promise no run.py-through-gateway run — the agents code is read-only — cost if wrong: one Try-it block.
- Ruling I11 (for Task 10's Try-it test): an output block whose preceding prose says the counts depend on the wall clock is compared by shape (same line count, the gate lines' names and ok/red words) rather than byte for byte; every other block is compared line for line — the limiter on a by-hand replay is wall-clock by design (I6) — cost if wrong: a looser test for one block.
- Ruling I12: the finding is addressed — the review asked for prose saying the figures are wall-clock and unpinned; the paragraph directly before the block says it, contains "depend on the wall clock", and names the four gate lines and ok/FAILED; the sentence-level placement was the controller's over-specific instruction, not the finding. Task 10's Try-it test keys on the PARAGRAPH before a block containing that phrase — cost if wrong: one test keying rule.

## Implementer rulings worth knowing

- `ledger.open_for_append` truncates an unparseable half-written last row instead of writing a newline before the next row (a newline would turn it into a mid-file bad line the loader rejects); a tail that parses as JSON is kept and a newline is added.
- The gateway server holds one lock across the whole chain, upstream call included, so requests are served one at a time; the desk's parallel calls queue but stay correct.
- A refused request's trace stops at the refusing stage (parse, tag, budget, route or limit): only answered requests (ok, cached) carry the `ledger` and `trace` decisions, though a refusal still gets a ledger row and a trace line.
- The demo refusal in the capstone uses `budgets.patterns.limit_usd: 0.01` (`DEMO_LIMIT_USD`), not 0.001: at 0.001 the first call is refused on its worst case alone and the budget detail prints a limit of $0.00.
- Gate condition 1 adds back the recorded cost of a set's cache hits (desk-hand-go's one cross-set hit, $0.0001919) and names it in the gate line; without the add-back the bare equality is short by that amount (the same decision as I5).
