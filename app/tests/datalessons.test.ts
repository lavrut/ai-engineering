import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';

// The Data Fabric & Data Contracts track, lessons 1-6: every number a lesson states in prose or in its quiz is
// pinned here against what produced it: data/data/derived/*.json (catalog.py, quality.py, gate.py, never written
// by hand), the contracts' JSON exports, the shared fixtures, the requirements, or the source text. Each claim is
// a phrase the lesson must contain plus the check behind it. A last test strips every claimed phrase, the lesson
// cross-references and the numbered lists from each lesson, and fails on any digit left over, so a new number
// cannot ship unpinned. The Try-it commands and their printed output are run and compared by
// data/python/tests/test_lesson_tryits.py; code blocks are left to it.
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '../..');
const lessonsDir = join(here, '../src/content/lessons/data');
const text = (rel: string) => readFileSync(join(repo, rel), 'utf8');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const json = (rel: string): any => JSON.parse(text(rel));

const catalog = json('data/data/derived/catalog.json');
const lineage = json('data/data/derived/lineage.json');
const verdicts = json('data/data/derived/verdicts.json');
const gate = json('data/data/derived/gate.json');
const contract = (id: string) => json(`data/contracts/${id}.contract.json`);
const cases = text('data/fixtures/cases.jsonl').trim().split('\n').map((l) => JSON.parse(l));
const evalIds = text('agents/evals/tickets.jsonl').trim().split('\n').map((l) => JSON.parse(l).id as string);
const py = (name: string) => text(`data/python/${name}`);
const goSrc = (name: string) => text(`data/go/validate/${name}`);
const ds = verdicts.datasets;
const traceSets = Object.keys(ds).filter((k) => k.startsWith('agent-traces/'));
type V = { rule: string; field: string | null; ok: boolean; detail: string };
const verdict = (key: string, rule: string, field: string | null) => (ds[key].verdicts as V[]).find((v) => v.rule === rule && v.field === field)!;
const hasSection = (src: string, n: number) => new RegExp(`^(#|//) ── ${n}\\. `, 'm').test(src);

const LESSONS = ['01-fabric', '02-catalog', '03-quality', '04-contract', '05-enforce', '06-capstone'];
const read = (name: string) => readFileSync(join(lessonsDir, `${name}.mdx`), 'utf8');

/** The lesson's prose and quiz: no fenced blocks (test_lesson_tryits.py checks those), imports or component tags. */
function prose(name: string): string {
  const { content, data } = matter(read(name));
  const quiz = (data.quiz as { question: string; options: string[]; explanation: string }[]).flatMap((q) => [q.question, ...q.options, q.explanation]);
  const body = content
    .replace(/```[\s\S]*?```/g, '')
    .replace(/^import .*$/gm, '')
    .replace(/<[A-Z][^>]*\/>/g, '');
  return [data.title, data.goal, body, ...quiz].join('\n');
}

type Claim = [lesson: string, phrase: string, check: () => void];

const CLAIMS: Claim[] = [
  // ── lesson 1 ──
  ['01-fabric', 'The LLMs track wrote 60 support tickets', () => expect(catalog.tickets).toMatchObject({ rows: 60, producer: 'llms' })],
  ['01-fabric', 'The ML track wrote 3,600 complaints', () => expect(catalog.complaints).toMatchObject({ rows: 3600, producer: 'ml' })],
  ['01-fabric', 'A catalogue of the 12 datasets the tracks share', () => expect(Object.keys(catalog)).toHaveLength(12)],
  ['01-fabric', 'Contracts for 3 datasets that cross a track boundary', () => {
    const v1 = ['tickets.v1', 'complaints.v1', 'agent-traces.v1'].map(contract);
    expect(v1.map((c) => c.info.version)).toEqual(['1', '1', '1']);
    expect(v1.map((c) => c.info.owner)).toEqual(['llms', 'ml', 'agents']);
    // the three the lesson names: the tickets, the complaints and the agent traces, each with a declared reader
    expect(v1.map((c) => c.consumers.map((x: { name: string }) => x.name))).toEqual([['mcp-read-server'], ['mcp-read-server'], ['agents-eval']]);
  }],
  ['01-fabric', 'in a small subset of five sections', () => expect(Object.keys(contract('tickets.v1')).sort()).toEqual(['consumers', 'info', 'quality', 'schema', 'servicing'])],
  ['01-fabric', 'The 12 datasets are written by 5 tracks, and the lines between them are the 31 edges in `lineage.json`', () => {
    expect(new Set(Object.values(catalog).map((e) => (e as { producer: string }).producer))).toEqual(new Set(['llms', 'data', 'ml', 'mcp', 'agents']));
    expect(lineage.edges).toHaveLength(31);
  }],
  ['01-fabric', '`sunset: 2027-01-31`', () => expect(contract('tickets.v1').info).toMatchObject({ status: 'deprecated', sunset: '2027-01-31' })],
  ['01-fabric', '`version: 1`', () => expect(contract('tickets.v1').consumers[0]).toMatchObject({ name: 'mcp-read-server', version: '1' })],
  ['01-fabric', 'Four things are left out on purpose', () => {
    const list = prose('01-fabric').split('### What a production fabric adds')[1].split('\n## ')[0];
    expect(list.match(/^- \*\*/gm)).toHaveLength(4);
  }],
  ['01-fabric', 'It holds two packages, `pyyaml` and `pytest`', () => expect(text('data/requirements.txt').trim().split('\n')).toEqual(['pyyaml==6.0.3', 'pytest>=8'])],
  // ── lesson 2 ──
  ['02-catalog', '`catalog.py` catalogues 12 datasets from a list written in the code', () => expect(py('catalog.py').match(/^ {4}\{"id": "/gm)).toHaveLength(12)],
  ['02-catalog', 'the agents traces are 134 files', () => expect(catalog.agents_traces).toMatchObject({ files: 134, rows: 134 })],
  ['02-catalog', 'read from the first 50 rows of the first file', () => expect(py('catalog.py')).toContain('SAMPLE_ROWS = 50')],
  ['02-catalog', '`SAMPLE_ROWS = 50`', () => expect(py('catalog.py')).toContain('SAMPLE_ROWS = 50  # the schema sketch reads this many rows of the first file')],
  ['02-catalog', 'It reads the first 50 rows', () => expect(py('catalog.py')).toContain('for row in rows[:SAMPLE_ROWS]:')],
  ['02-catalog', 'The tickets are 60 rows in 16,705 bytes', () => expect(catalog.tickets).toMatchObject({ rows: 60, bytes: 16705 })],
  ['02-catalog', 'The complaints are 3,600 rows in one file of 4,609,144 bytes', () => expect(catalog.complaints).toMatchObject({ rows: 3600, files: 1, bytes: 4609144 })],
  ['02-catalog', '`agents_traces` is 134 files of one row each', () => expect(catalog.agents_traces).toMatchObject({ files: 134, rows: 134 })],
  ['02-catalog', 'Four datasets are under a contract: `tickets`, `tickets_v2`, `complaints` and `agents_traces`', () => {
    expect(Object.keys(catalog).filter((k) => catalog[k].contract)).toEqual(['agents_traces', 'complaints', 'tickets', 'tickets_v2']);
  }],
  ['02-catalog', 'The other 8, such as `model_responses` with its 743 rows in 14 files', () => {
    expect(Object.keys(catalog).filter((k) => !catalog[k].contract)).toHaveLength(8);
    expect(catalog.model_responses).toMatchObject({ rows: 743, files: 14, contract: null });
  }],
  ['02-catalog', 'The agents eval scores 13 tickets. Twelve come from the Lumen tickets', () => {
    expect(evalIds).toHaveLength(13);
    expect(evalIds.filter((t) => t !== 't061')).toHaveLength(12);
    expect(Object.values(catalog).some((e) => (e as { path: string }).path.includes('injection'))).toBe(false);
  }],
  ['02-catalog', 'Section 3 builds the catalogue', () => expect(py('catalog.py')).toContain('# ── 3. The catalogue')],
  ['02-catalog', 'listed beside it in section 1', () => expect(py('catalog.py')).toContain('# ── 1. The datasets')],
  ['02-catalog', 'Section 4 turns the catalogue into lineage', () => expect(py('catalog.py')).toContain('# ── 4. Lineage')],
  // ── lesson 3 ──
  ['03-quality', 'All 21 verdicts pass', () => expect([ds.tickets.verdicts.length, ds.tickets.ok]).toEqual([21, true])],
  ['03-quality', 'the file was 2 days old as of 2026-10-28, the pinned date', () => {
    expect([ds.tickets.age_days, verdicts.as_of]).toEqual([2.0, '2026-10-28']);
  }],
  ['03-quality', 'verdicts.json says the tickets are 2 days old as of 2026-10-28', () => expect(verdict('tickets', 'freshness', null).detail).toBe('2 days old (max 365)')],
  ['03-quality', 'There are 6 trace sets and 134 files', () => {
    expect(traceSets).toHaveLength(6);
    expect(traceSets.reduce((a, k) => a + ds[k].files, 0)).toBe(134);
  }],
  ['03-quality', 'Each set has 26 verdicts, and every one passes in every file', () => {
    for (const k of traceSets) expect([k, ds[k].verdicts.length, ds[k].ok]).toEqual([k, 26, true]);
  }],
  ['03-quality', 'Every contract here allows `max_age_days: 365`', () => {
    for (const id of ['tickets.v1', 'tickets.v2', 'complaints.v1', 'agent-traces.v1']) expect(contract(id).servicing.freshness.max_age_days, id).toBe(365);
  }],
  ['03-quality', 'the same command fails on freshness and exits with 1', () => expect(py('quality.py')).toContain('return 0 if ok else 1')],
  ['03-quality', 'A dataset is checked with --age-days 400 against a contract whose max_age_days is 365', () => {
    expect(contract('tickets.v1').servicing.freshness.max_age_days).toBe(365);
  }],
  ['03-quality', 'the producer promised data no older than 365 days', () => expect(contract('tickets.v1').servicing.freshness.max_age_days).toBe(365)],
  ['03-quality', 'The other 20 verdicts can pass', () => expect(ds.tickets.verdicts.length - 1).toBe(20)],
  ['03-quality', '`return round(max(0.0, ref - stamp) / 86400, 1)`', () => expect(py('quality.py')).toContain('return round(max(0.0, ref - stamp) / 86400, 1)')],
  ['03-quality', '`row 4: duplicate of row 1`', () => {
    const c = cases.find((x) => x.name === 'composite-key-duplicate');
    expect(c.contract.quality.unique).toEqual(['desk', 'ticket']);
    expect(c.expect.find((v: V) => v.rule === 'unique')).toMatchObject({ field: 'desk,ticket', ok: false, detail: 'row 4: duplicate of row 1' });
  }],
  ['03-quality', 'Section 2 measures freshness from git', () => expect(py('quality.py')).toContain('# ── 2. Freshness from git, as of a date')],
  ['03-quality', 'Section 4 writes `verdicts.json`', () => expect(py('quality.py')).toContain('# ── 4. verdicts.json')],
  // ── lesson 4 ──
  ['04-contract', 'The schema has 6 fields, all required', () => {
    const f = contract('tickets.v1').schema.fields;
    expect(f).toHaveLength(6);
    expect(f.every((x: { required: boolean }) => x.required)).toBe(true);
  }],
  ['04-contract', 'Three have an enum: 6 categories, 4 priorities and 2 splits. One, `id`, has a pattern', () => {
    const f = contract('tickets.v1').schema.fields as { name: string; enum?: string[]; pattern?: string }[];
    expect(f.filter((x) => x.enum).map((x) => [x.name, x.enum!.length])).toEqual([['category', 6], ['priority', 4], ['split', 2]]);
    expect(f.filter((x) => x.pattern).map((x) => x.name)).toEqual(['id']);
  }],
  ['04-contract', 'The quality section asks for exactly 60 rows', () => expect(contract('tickets.v1').quality.row_count).toEqual({ min: 60, max: 60 })],
  ['04-contract', 'There is one consumer, the MCP read server, on version 1', () => expect(contract('tickets.v1').consumers).toEqual([expect.objectContaining({ name: 'mcp-read-server', version: '1' })])],
  ['04-contract', "`pattern: '^t\\d{3}$'`", () => expect(text('data/contracts/tickets.v1.yaml')).toContain("pattern: '^t\\d{3}$'")],
  ['04-contract', '`row_count: {min: 60, max: 60}`', () => expect(text('data/contracts/tickets.v1.yaml')).toContain('row_count: {min: 60, max: 60}')],
  ['04-contract', '`max_age_days: 365`', () => expect(text('data/contracts/tickets.v1.yaml')).toContain('max_age_days: 365')],
  ['04-contract', 'The command exits with status 1', () => expect(py('contract.py')).toContain('return 1 if any(not x["ok"] for x in verdicts) else 0')],
  ['04-contract', 'This also exits with status 1', () => expect(py('contract.py')).toContain('raise ContractError("info.sunset is required when info.status is deprecated")')],
  ['04-contract', 'The types are the six JSON types', () => {
    expect(py('contract.py')).toContain('FIELD_TYPES = ("string", "integer", "number", "boolean", "object", "array")');
    expect(goSrc('contract.go')).toContain('"string", "integer", "number", "boolean", "object", "array"');
  }],
  // ── lesson 5 ──
  ['05-enforce', '`data/fixtures/cases.jsonl` holds 25 cases', () => expect(cases).toHaveLength(25)],
  ['05-enforce', '`validate_test.go` runs the 25 fixture cases in Go as well', () => expect(goSrc('validate_test.go')).toContain('func TestCasesMatchFixtures(')],
  ['05-enforce', 'with counts capped at 1000', () => {
    expect(py('contract.py')).toContain('_MAX_REPEAT = 1000');
    expect(goSrc('contract.go')).toMatch(/maxRepeat\s+= 1000/);
  }],
  ['05-enforce', 'The cap of 1000 exists because RE2 refuses a nested repeat', () => expect(py('contract.py')).toContain('Ruling D9: counted repeats stop at 1000')],
  ['05-enforce', '`((a{1000}){1000}){1000}`', () => expect(py('contract.py')).toContain('((a{1000}){1000})')],
  ['05-enforce', 'every one of the 3,600 rows matches both', () => {
    expect(catalog.complaints.rows).toBe(3600);
    expect(['id', 'date'].map((f) => verdict('complaints', 'pattern', f))).toEqual(['id', 'date'].map((field) => ({ rule: 'pattern', field, ok: true, detail: 'all values match' })));
    expect(contract('complaints.v1').schema.fields.filter((f: { pattern?: string }) => f.pattern).map((f: { pattern: string }) => f.pattern)).toEqual(['^\\d+$', '^\\d{4}-\\d{2}-\\d{2}$']);
  }],
  ['05-enforce', 'The product enum has 6 values', () => expect(contract('complaints.v1').schema.fields.find((f: { name: string }) => f.name === 'product').enum).toHaveLength(6)],
  ['05-enforce', 'All 17 verdicts pass. The Go twin prints the same 17 lines', () => expect([ds.complaints.verdicts.length, ds.complaints.ok]).toEqual([17, true])],
  ['05-enforce', 'so a broken column is one line, not 3,600', () => expect(catalog.complaints.rows).toBe(3600)],
  ['05-enforce', 'collects every failing row, numbered from 1', () => expect(py('contract.py')).toContain('for i, r in enumerate(rows, 1) if not _present(r, name)]')],
  ['05-enforce', 'both exit with 1 when any verdict failed', () => {
    expect(py('contract.py')).toContain('return 1 if any(not x["ok"] for x in verdicts) else 0');
    expect(goSrc('main.go')).toContain('return 1');
  }],
  ['05-enforce', 'and the same `400 days old` rather than `400.0`', () => expect(py('contract.py')).toContain('return f"{x:g}"')],
  ['05-enforce', 'an age prints as `1.8` and a limit as `365` in both', () => {
    expect(ds.complaints.age_days).toBe(1.8);
    expect(verdict('complaints', 'freshness', null).detail).toBe('1.8 days old (max 365)');
  }],
  ['05-enforce', 'Section 3 holds the rules. The Go tab shows the same rules in `rules.go`, section 7', () => {
    expect(hasSection(py('contract.py'), 3) && py('contract.py').includes('# ── 3. Rules')).toBe(true);
    expect(goSrc('rules.go')).toContain('// ── 7. Rules');
  }],
  ['05-enforce', 'Section 4 puts the verdicts in order', () => {
    expect(py('contract.py')).toContain('# ── 4. Verdicts');
    expect(goSrc('rules.go')).toContain('// ── 8. Verdicts');
  }],
  ['05-enforce', '`contract.go` (sections 1 to 5), `rules.go` (6 to 8) and `main.go` (9)', () => {
    for (const [file, ns] of [['contract.go', [1, 2, 3, 4, 5]], ['rules.go', [6, 7, 8]], ['main.go', [9]]] as const) {
      expect((goSrc(file).match(/^\/\/ ── (\d)\./gm) ?? []).map((m) => Number(m.slice(6, 7))), file).toEqual(ns);
    }
  }],
  ['05-enforce', "Section 7 above is Python's section 3, rule for rule", () => {
    for (const fn of ['fieldRules', 'extraFields', 'qualityVerdicts', 'freshness']) expect(goSrc('rules.go')).toContain(`func ${fn}(`);
  }],
  ['05-enforce', "So `contract.go` section 1 parses JSON the way Python's `json` does", () => expect(goSrc('contract.go')).toContain('// ── 1. JSON values')],
  ['05-enforce', '`patternTokens` in section 4', () => expect(goSrc('contract.go')).toContain('// ── 4. Patterns (ruling D7)')],
  ['05-enforce', '`"\\x00"`', () => expect(goSrc('rules.go')).toContain('strings.Join(parts, "\\x00")')],
  ['05-enforce', '`f"{x:g}"`', () => expect(goSrc('contract.go')).toContain('num is Python\'s f"{x:g}"')],
  // ── lesson 6 ──
  ['06-capstone', '`urgency` is missing from all 60 rows, and none of its values appear', () => {
    expect(verdict('tickets-v2-before', 'required', 'urgency')).toMatchObject({ ok: false, detail: 'row 1: missing (+59 more)' });
    expect(verdict('tickets-v2-before', 'enum_coverage', 'urgency')).toMatchObject({ ok: false, detail: 'missing values: "low", "normal", "high", "urgent"' });
    expect(verdict('tickets-v2-before', 'extra_field', 'priority')).toMatchObject({ ok: true });
    expect(ds['tickets-v2-before'].expect).toBe('fail');
  }],
  ['06-capstone', 'The eval scores 13 tickets: 12 come from the v2 file, and the thirteenth, `t061`', () => {
    expect(evalIds).toHaveLength(13);
    expect(evalIds.at(-1)).toBe('t061');
    expect(text('mcp/data/injection.jsonl')).toContain('"id": "t061"');
  }],
  ['06-capstone', 'with its sunset still ahead as of 2026-10-28', () => {
    expect(verdicts.as_of).toBe('2026-10-28');
    expect(verdicts.as_of < contract('tickets.v1').info.sunset).toBe(true);
    expect(gate.consumers.find((c: { declared: string }) => c.declared === 'tickets.v1')).toMatchObject({ name: 'mcp-read-server', sunset: '2027-01-31', warning: false, verdicts_ok: true });
  }],
  ['06-capstone', 'The gate counts every consumer of every contract, 4 of 4, and is green', () => {
    expect(gate.consumers).toHaveLength(4);
    expect(gate.ok).toBe(true);
    expect(gate.reason).toBe('every consumer is green on the version it declares (4 of 4)');
  }],
  ['06-capstone', '`sunset: 2027-01-31`', () => expect(text('data/contracts/tickets.v1.yaml')).toContain('sunset: 2027-01-31')],
  ['06-capstone', 'The agents eval has moved, the MCP read server has until 2027-01-31', () => {
    expect(contract('tickets.v2').consumers.map((c: { name: string }) => c.name)).toEqual(['agents-eval']);
    expect(contract('tickets.v1').info.sunset).toBe('2027-01-31');
  }],
  ['06-capstone', 'This asks the gate as of 2027-02-01', () => expect('2027-02-01' > contract('tickets.v1').info.sunset).toBe(true)],
  ['06-capstone', '`version: 2`', () => expect(text('data/contracts/tickets.v2.yaml')).toContain('  version: 2\n  status: active')],
  ['06-capstone', 'The gate is section 2 of `gate.py`', () => expect(py('gate.py')).toContain('# ── 2. The gate')],
];

const REAL = CLAIMS;

describe('data lessons: files, aids and anatomy', () => {
  it('has the six lessons, each with the aid spec §5 names', () => {
    for (const l of LESSONS) expect(existsSync(join(lessonsDir, `${l}.mdx`)), l).toBe(true);
    expect(readdirSync(lessonsDir).filter((f) => f.endsWith('.mdx')).sort()).toEqual(LESSONS.map((l) => `${l}.mdx`));
    expect(read('01-fabric')).toMatch(/<FlowDiagram diagram="data-fabric" \/>/);
    expect(read('02-catalog')).toMatch(/<CatalogExplorer \/>/);
    expect(read('03-quality')).toMatch(/<QualityReport dataset="tickets" \/>/);
    expect(read('03-quality')).toMatch(/<QualityReport dataset="agent-traces" \/>/);
    expect(read('04-contract')).toMatch(/<ContractViewer contract="tickets.v1" \/>/);
    expect(read('05-enforce')).toMatch(/<ContractViewer contract="complaints.v1" verdicts=\{true\} \/>/);
    expect(read('06-capstone')).toMatch(/<LineageDiff from="tickets.v1" to="tickets.v2" \/>/);
  });

  it('gives lesson 5, and only lesson 5, a "How Go does it" section, quoting Go section 7', () => {
    for (const l of LESSONS) expect(read(l).includes('\n## How Go does it\n'), l).toBe(l === '05-enforce');
    expect(read('05-enforce')).toContain('<CodeTabs source="data-contract" section={3} goSection={7} />');
    expect(read('05-enforce')).toContain('test_twin.py');
  });

  it('gives each lesson 2 or 3 quiz questions of 4 options', () => {
    for (const l of LESSONS) {
      const quiz = matter(read(l)).data.quiz as { options: string[] }[];
      expect(quiz.length, l).toBeGreaterThanOrEqual(2);
      expect(quiz.length, l).toBeLessThanOrEqual(3);
      for (const q of quiz) expect(q.options, l).toHaveLength(4);
    }
  });

  it('lesson 1 carries the concept tour: fabric, mesh, warehouse, ODCS, the tools in one callout, and what a production fabric adds', () => {
    const l1 = read('01-fabric');
    for (const t of ['data warehouse', 'data mesh', 'data fabric', 'Open Data Contract Standard']) expect(l1).toContain(`<Term t="${t}" />`);
    const callout = l1.split('<Callout kind="note" title="The tools you would use at work">')[1].split('</Callout>')[0];
    for (const tool of ['Data contract CLIs', 'Great Expectations', 'dbt tests']) expect(callout).toContain(tool);
    // spec §2: the out-of-scope list, named as what a production fabric adds
    const adds = l1.split('### What a production fabric adds')[1].split('\n## ')[0];
    for (const item of ['Access control and governance', 'Streaming and event-time freshness', 'A live metadata store', 'Contract tooling']) expect(adds).toContain(item);
  });

  it('states the rulings the build made: the pattern grammar (D7/D9), the pinned as_of (D10), the composite key (D8), the gate (D12), tickets_v2 (D13), t061', () => {
    const l5 = read('05-enforce');
    for (const s of ['`\\d \\D \\w \\W \\s \\S`', '`* + ? {n} {n,} {n,m}`', 'capped at 1000', 'no counted repeat applied to a group that already holds one', '`^t(?=\\d{3}$)\\d+$`', 'RE2 has none']) expect(l5).toContain(s);
    const l3 = read('03-quality');
    for (const s of ['`quality.py --live`', 'as `as_of`', 'That is the point.', '<Term t="composite key" />']) expect(l3).toContain(s);
    const l6 = read('06-capstone');
    for (const s of ['It never reads a verdict from `verdicts.json`', 'a warning in the gate\'s reason, not a failure', 'The v2 file is a dataset of its own, `tickets_v2`', 'outside every contract']) expect(l6).toContain(s);
    expect(read('02-catalog')).toContain('One ticket is outside every contract');
  });

  it('points the diagram steps at code sections, with the catalogue ids as node ids', async () => {
    const { dataFabric } = await import('../src/data/diagrams/data-fabric');
    const ids = new Set(dataFabric.nodes.map((n) => n.id));
    for (const k of Object.keys(catalog)) expect(ids.has(k), k).toBe(true);
    expect(dataFabric.steps.filter((s) => s.code).map((s) => s.code!.source)).toEqual(['data-catalog', 'data-contract-tickets', 'data-quality', 'data-contract', 'data-gate']);
  });
});

describe('data lessons: every number is pinned', () => {
  it.each(REAL.map((c) => [`${c[0]}: ${c[1].slice(0, 70)}`, c] as const))('%s', (_n, [lesson, phrase, check]) => {
    expect(prose(lesson), `${lesson} no longer says: ${phrase}`).toContain(phrase);
    check();
  });

  it('the six JSON types and the five sections are the validator\'s', () => {
    expect(py('contract.py')).toContain('FIELD_TYPES = ("string", "integer", "number", "boolean", "object", "array")');
    expect(py('contract.py')).toContain('SECTIONS = ("info", "schema", "quality", "servicing", "consumers")');
    const list = read('04-contract').split('### Five sections')[1].split('### ')[0];
    expect([...list.matchAll(/^(\d)\. `(\w+)`/gm)].map((m) => [Number(m[1]), m[2]])).toEqual([[1, 'info'], [2, 'schema'], [3, 'quality'], [4, 'servicing'], [5, 'consumers']]);
    expect([...read('06-capstone').matchAll(/^### (\d)\. /gm)].map((m) => Number(m[1]))).toEqual([1, 2, 3, 4, 5]);
  });

  it.each(LESSONS)('%s: no digit is left once the claims, the lesson references and the numbered lists are taken out', (lesson) => {
    let t = prose(lesson);
    for (const [l, phrase] of REAL) if (l === lesson) t = t.split(phrase).join(' ');
    const refs = [...t.matchAll(/\b[Ll]esson (\d)\b/g)].map((m) => Number(m[1]));
    expect(refs.every((n) => n >= 1 && n <= 6), `${lesson} refers to ${refs}`).toBe(true);
    t = t
      .replace(/\b[Ll]esson \d\b/g, ' ')
      .replace(/^\d\. /gm, ' ')
      .replace(/^### \d\. /gm, ' ')
      .replace(/`[^`]*`/g, ' ') // inline code: identifiers, paths and commands (tickets.v1, t061, --age-days)
      .replace(/\b(tickets|complaints|agent-traces)\.v\d\b/g, ' ')
      .replace(/\bv[12]\b/g, ' ')
      .replace(/\btickets_v2\b/g, ' ')
      .replace(/\bRE2\b/g, ' '); // the engine's name
    expect(t.match(/.{0,40}\d.{0,20}/g) ?? [], `${lesson}: an unpinned number`).toEqual([]);
  });
});
