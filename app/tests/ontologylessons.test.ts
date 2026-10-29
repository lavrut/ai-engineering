import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';
import { ontologyLayers } from '../src/data/diagrams/ontology-layers';

// The Ontology track, lessons 1-4: every number a lesson states in prose or in its quiz is pinned here against what
// produced it: ontology/data/derived/*.json (ontology.py export, generate.py, grounding.py, audit.py, never written
// by hand), the ontology file, the other tracks' files it reads, or the source text. Each claim is a phrase the
// lesson must contain plus the check behind it. A last test strips every claimed phrase, the lesson references, the
// numbered lists and the names that carry digits (rule ids, prompt versions, ticket ids, contract ids) from each
// lesson, and fails on any digit left over, inline code included, so a new number cannot ship unpinned. The Try-it
// commands and their printed output are run and compared by ontology/python/tests/test_lesson_tryits.py; code
// blocks are left to it.
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '../..');
const lessonsDir = join(here, '../src/content/lessons/ontology');
const text = (rel: string) => readFileSync(join(repo, rel), 'utf8');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const json = (rel: string): any => JSON.parse(text(rel));
const jsonl = (rel: string) => text(rel).trim().split('\n').map((l) => JSON.parse(l));

const onto = json('ontology/data/derived/ontology.json');
const generated = json('ontology/data/derived/generated.json');
const validation = json('ontology/data/derived/validation.json');
const audit = json('ontology/data/derived/audit.json');
const lumen = text('ontology/lumen.yaml');
const py = (name: string) => text(`ontology/python/${name}`);
const tickets = jsonl('llms/data/tickets.jsonl');
const ticketsV2 = jsonl('data/data/tickets.v2.jsonl');

type Entry = { id: string; class?: string; kind?: string; source?: string; order?: number; text?: string };
const individuals = (cls: string) => (onto.individuals as Entry[]).filter((i) => i.class === cls).sort((a, b) => a.order! - b.order!);
const constraints = onto.constraints as Entry[];
const manual = constraints.filter((c) => c.kind === 'manual');
const checkable = constraints.filter((c) => c.kind === 'checkable');
type Source = { records: number; violations: Record<string, number>; violating: string[]; manual: Record<string, number> };
const sources = validation.sources as Record<string, Source>;
const triage = Object.keys(sources).filter((k) => !k.startsWith('agents/'));
const desks = Object.keys(sources).filter((k) => k.startsWith('agents/'));
type Row = { artefact: string; fact: string; status: string; detail: string };
const rows = audit.rows as Row[];
const rowsOf = (art: string) => rows.filter((r) => r.artefact === art);
const hasSection = (src: string, n: number, title: string) => src.includes(`# ── ${n}. ${title}`);

// Inline code whose digits are names, not numbers, and so need no pin: every other digit in inline code is checked
// like prose. Listed one by one on purpose, so a new span with a digit has to be added here or pinned.
const CODE_OK: RegExp[] = [
  /^v1_basic$/, /^v2_rules'?$/, /^v3_examples$/, // prompt versions
  /^(tickets|complaints)\.v[12](\.yaml|\.jsonl)?$/, // contract ids and the v2 file
  /^t0\d\d$/, // ticket ids
  /^[CUHL]\d$/, // guide rule ids
];

// A code quote that appears verbatim in the track's sources is pinned by that source text (the walkthrough spans).
const SOURCES = [...['ontology.py', 'generate.py', 'grounding.py', 'audit.py'].map((f) => `ontology/python/${f}`), 'ontology/lumen.yaml']
  .map(text)
  .join('\n');
const codeOk = (span: string) => CODE_OK.some((re) => re.test(span)) || SOURCES.includes(span);

const LESSONS = ['01-what', '02-lumen', '03-grounding', '04-capstone'];
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
  ['01-what', 'It has 7 classes, 5 relations and 16 individuals', () => {
    expect([onto.classes.length, onto.relations.length, onto.individuals.length]).toEqual([7, 5, 16]);
  }],
  ['01-what', 'Watch step 4: that is where the rules come in', () => {
    const s = ontologyLayers.steps[3];
    expect(s.n).toBe(4);
    expect(s.caption).toContain('It also adds rules');
  }],
  ['01-what', 'Step 6 is the rest of the track: generate from the ontology, then audit everything against it', () => {
    const s = ontologyLayers.steps[5];
    expect([s.n, ontologyLayers.steps.length]).toEqual([6, 6]);
    expect(s.caption).toContain('generate.py builds');
    expect(s.caption).toContain('audit.py checks every artefact');
  }],
  ['01-what', 'are individuals in section 4', () => expect(hasSection(lumen, 4, 'Individuals')).toBe(true)],
  ['01-what', 'Four things are left out on purpose', () => {
    const list = prose('01-what').split('### What a production ontology stack adds')[1].split('\n## ')[0];
    expect(list.match(/^- \*\*/gm)).toHaveLength(4);
  }],
  ['01-what', 'It holds two packages, `pyyaml` and `pytest`', () => expect(text('ontology/requirements.txt').trim().split('\n')).toEqual(['pyyaml==6.0.3', 'pytest>=8'])],
  // ── lesson 2 ──
  ['02-lumen', 'talks about: 7 of them', () => expect(onto.classes).toHaveLength(7)],
  ['02-lumen', 'exactly the 6 fields of `llms/data/tickets.jsonl`: `id`, `text`, `category`, `priority`, `tags` and `split`', () => {
    for (const t of tickets) expect(Object.keys(t)).toEqual(['id', 'text', 'category', 'priority', 'tags', 'split']);
    expect((onto.properties as { id: string; domain: string }[]).map((p) => [p.id, p.domain]).sort()).toEqual(
      ['category', 'id', 'priority', 'split', 'tags', 'text'].map((p) => [p, 'Ticket']),
    );
  }],
  ['02-lumen', 'Named edges between classes, 5 of them', () => expect(onto.relations).toHaveLength(5)],
  ['02-lumen', 'The named members of a class, 16 of them: the 6 categories, the 4 priorities and the 6 complaint products', () => {
    expect(onto.individuals).toHaveLength(16);
    expect(['Category', 'Priority', 'Product'].map((c) => individuals(c).length)).toEqual([6, 4, 6]);
  }],
  ["02-lumen", "The guide's rules, 25 of them", () => expect(constraints).toHaveLength(25)],
  ['02-lumen', 'from C1 to C6', () => expect(individuals('Category').map((i) => i.source)).toEqual(['C1', 'C2', 'C3', 'C4', 'C5', 'C6'])],
  ['02-lumen', 'is `billing` because C2 comes first', () => {
    const [billing, howTo] = ['billing', 'how_to'].map((id) => individuals('Category').find((i) => i.id === id)!);
    expect([billing.source, billing.order, howTo.source, howTo.order]).toEqual(['C2', 2, 'C6', 6]);
  }],
  ['02-lumen', 'and why the other 24 are manual', () => expect([checkable.length, manual.length]).toEqual([1, 24])],
  ['02-lumen', 'The other 24 are a', () => expect(manual).toHaveLength(24)],
  ['02-lumen', 'a wrongful charge over $100 is urgent', () => {
    const c = constraints.find((x) => x.id === 'large_wrongful_charge')!;
    expect([c.kind, c.source]).toEqual(['manual', 'U4']);
    expect(c.text).toContain('A wrongly taken charge over $100');
  }],
  ["02-lumen", "claimed to check U4's $100 threshold without an amount field", () => {
    expect(constraints.find((x) => x.id === 'large_wrongful_charge')!.text).toContain('over $100');
    expect(onto.properties.map((p: { id: string }) => p.id)).not.toContain('amount');
  }],
  ['02-lumen', 'one gold ticket is an outage at normal', () => {
    expect(tickets.filter((t) => t.category === 'outage' && t.priority === 'normal').map((t) => t.id)).toEqual(['t057']);
  }],
  ['02-lumen', '`Category` has 6 individuals, from `outage` (C1) to `how_to` (C6), and `Priority` has 4, from `urgent` to `normal`', () => {
    const cats = individuals('Category');
    expect([cats.length, cats[0].id, cats[0].source, cats[5].id, cats[5].source]).toEqual([6, 'outage', 'C1', 'how_to', 'C6']);
    expect(individuals('Priority').map((i) => i.id)).toEqual(['urgent', 'high', 'low', 'normal']);
  }],
  ['02-lumen', 'Section 4 holds the individuals', () => expect(hasSection(lumen, 4, 'Individuals')).toBe(true)],
  ['02-lumen', 'are manual constraints in section 5', () => expect(hasSection(lumen, 5, 'Constraints')).toBe(true)],
  ['02-lumen', 'Section 5 holds the constraints. The checkable one comes first', () => {
    const sec = lumen.split('# ── 5. Constraints')[1];
    expect(sec.indexOf('kind: checkable')).toBeLessThan(sec.indexOf('kind: manual'));
  }],
  ['02-lumen', 'in section 3 of `ontology.py`', () => expect(hasSection(py('ontology.py'), 3, 'Constraint expressions')).toBe(true)],
  ['02-lumen', 'Validate the 60 tickets', () => expect(tickets).toHaveLength(60)],
  ['02-lumen', 'The 1440 manual findings are the 24 manual constraints listed on each of the 60 tickets', () => {
    expect([manual.length * tickets.length, manual.length, tickets.length]).toEqual([1440, 24, 60]);
  }],
  ['02-lumen', 'The command exits with status 1', () => expect(py('ontology.py')).toContain('return 1 if counts["error"] else 0')],
  ['02-lumen', 'Both exit with status 1', () => expect(py('ontology.py')).toContain('return 1 if counts["error"] else 0')],
  // ── lesson 3 ──
  ['03-grounding', 'the prompt paraphrases the guide in 14 lines', () => {
    expect(generated.compare.definitions.diff).toContain('@@ -1,14 +1,10 @@');
    expect(generated.compare.definitions.existing.trimEnd().split('\n')).toHaveLength(14);
  }],
  ["03-grounding", "the guide's own wording in 10 lines, one per individual", () => {
    expect(generated.definitions_text.trimEnd().split('\n')).toHaveLength(10);
    expect(individuals('Category').length + individuals('Priority').length).toBe(10);
  }],
  ["03-grounding", "The `v3_examples` prompt's rule lines are the same as `v2_rules'`", () => expect(generated.compare.definitions.v3_rule_lines_equal_v2).toBe(true)],
  ['03-grounding', 'That is 700 records from 16 sources: 640 saved triage answers in 12 response files from the LLMs track, and 60 `label_ticket` calls in the 4 recorded agent desks. They hold 7 violations', () => {
    expect(validation.totals).toEqual({ records: 700, violations: { feature_request_never_above_normal: 7 } });
    expect([Object.keys(sources).length, triage.length, desks.length]).toEqual([16, 12, 4]);
    expect(triage.reduce((a, k) => a + sources[k].records, 0)).toBe(640);
    expect(desks.reduce((a, k) => a + sources[k].records, 0)).toBe(60);
  }],
  ['03-grounding', '- 2 from Haiku on the `v1_basic` prompt, on `t029` and `t055`;', () => {
    expect(sources['triage__claude-haiku-5-5__v1_basic__default']).toMatchObject({ violations: { feature_request_never_above_normal: 2 }, violating: ['t029', 't055'] });
  }],
  ['03-grounding', '- 4 from Opus and Sonnet on `v1_basic`, one per model and effort, all on `t029`;', () => {
    const four = ['opus', 'sonnet'].flatMap((m) => ['high', 'low'].map((e) => `triage__claude-${m}-5-5__v1_basic__${e}`));
    for (const k of four) expect(sources[k], k).toMatchObject({ violations: { feature_request_never_above_normal: 1 }, violating: ['t029'] });
  }],
  ['03-grounding', '- 1 from the fine-tuned Qwen model with LoRA, on `t055`.', () => {
    expect(sources['Qwen2.5-0.5B-Instruct+lora__lora__default']).toMatchObject({ violations: { feature_request_never_above_normal: 1 }, violating: ['t055'] });
  }],
  ['03-grounding', 'Zero violations came from `v2_rules` or `v3_examples`, which state the rule, and zero from the agents desks', () => {
    for (const k of Object.keys(sources).filter((k) => !k.includes('v1_basic') && !k.includes('lora'))) expect(sources[k].violations, k).toEqual({});
    const failing = Object.keys(sources).filter((k) => Object.keys(sources[k].violations).length > 0);
    expect(failing.every((k) => k.includes('v1_basic') || k.includes('lora'))).toBe(true);
  }],
  ['03-grounding', "Each desk's records list 25 constraints left to a person, the 24 manual ones and the checkable one, where a triage file lists 24", () => {
    for (const k of desks) {
      expect(Object.keys(sources[k].manual), k).toHaveLength(25);
      expect(Object.keys(sources[k].manual), k).toContain('feature_request_never_above_normal');
    }
    for (const k of triage) expect(Object.keys(sources[k].manual), k).toHaveLength(24);
  }],
  ['03-grounding', 'The validation is section 2 of `grounding.py`', () => expect(hasSection(py('grounding.py'), 2, 'Validation')).toBe(true)],
  // ── lesson 4 ──
  ['04-capstone', 'The audit writes 71 rows across 7 artefacts: 38 for the guide, 11 for each prompt, 6 for the category map and 5 for the contracts. 70 agree, 1 disagrees, and none is missing.', () => {
    expect(rows).toHaveLength(71);
    expect(new Set(rows.map((r) => r.artefact)).size).toBe(7);
    expect(rowsOf('llms/data/labeling_guide.md')).toHaveLength(38);
    expect(['v2_rules', 'v3_examples'].map((p) => rowsOf(`llms/data/prompts.json#triage.${p}`).length)).toEqual([11, 11]);
    expect(rowsOf('ml/data/category_map.json')).toHaveLength(6);
    expect(rows.filter((r) => r.artefact.startsWith('data/contracts/'))).toHaveLength(5);
    expect(['agree', 'disagree', 'missing'].map((s) => rows.filter((r) => r.status === s).length)).toEqual([70, 1, 0]);
  }],
  ['04-capstone', '`red: 1 disagree (data/contracts/tickets.v2.yaml enum:category); 0 missing`', () => {
    expect(audit.gate).toEqual({ ok: false, disagree: 1, missing: 0, reason: 'red: 1 disagree (data/contracts/tickets.v2.yaml enum:category); 0 missing' });
  }],
  ['04-capstone', 'someone checks the 60 gold labels against it', () => expect(tickets).toHaveLength(60)],
  ['04-capstone', 'That holds even though none of the 60 rows in `tickets.v2.jsonl` uses `security` today', () => {
    expect(ticketsV2).toHaveLength(60);
    expect(ticketsV2.filter((t) => t.category === 'security')).toEqual([]);
  }],
  ['04-capstone', 'So the fix is a new major, `tickets.v3`, as a new file without `security`', () => {
    // ruling O11: a removal is a new major; v2 is the current one, and no v3 exists yet
    expect(text('data/contracts/tickets.v2.yaml')).toContain('  version: 2\n  status: active');
    expect(existsSync(join(repo, 'data/contracts/tickets.v3.yaml'))).toBe(false);
  }],
  ['04-capstone', '`audit.py` itself exits with 0 after printing `RED`', () => expect(py('audit.py')).toContain('    _report(data)\n    return 0\n')],
  ['04-capstone', 'and exits with status 1 because it is red', () => {
    expect(read('04-capstone')).toContain('sys.exit(0 if gate["ok"] else 1)');
    expect(audit.gate.ok).toBe(false);
  }],
  ['04-capstone', 'The contracts\' rows come from section 5 of `audit.py`', () => expect(hasSection(py('audit.py'), 5, 'The data contracts')).toBe(true)],
  ['04-capstone', 'The gate is section 6.', () => expect(hasSection(py('audit.py'), 6, 'The audit and the gate')).toBe(true)],
];

// The audit detail strings and the hand-written artefacts' text the lessons quote, pinned to the files they come from.
const QUOTES: Claim[] = [
  ['04-capstone', "The row's detail is `extra value the ontology lacks: security`", () => {
    expect(rows.filter((r) => r.status !== 'agree')).toEqual([
      { artefact: 'data/contracts/tickets.v2.yaml', fact: 'enum:category', status: 'disagree', detail: 'extra value the ontology lacks: security' },
    ]);
  }],
  ['04-capstone', 'Of the contracts, `tickets.v1` and `complaints.v1` agree, and `tickets.v2` agrees on `urgency` and disagrees on `category`', () => {
    expect(rows.filter((r) => r.artefact.startsWith('data/contracts/')).map((r) => [r.artefact, r.fact, r.status, r.detail])).toEqual([
      ['data/contracts/complaints.v1.yaml', 'enum:product', 'agree', 'the 6 products'],
      ['data/contracts/tickets.v1.yaml', 'enum:category', 'agree', 'the 6 categories'],
      ['data/contracts/tickets.v1.yaml', 'enum:priority', 'agree', 'the 4 priorities'],
      ['data/contracts/tickets.v2.yaml', 'enum:category', 'disagree', 'extra value the ontology lacks: security'],
      ['data/contracts/tickets.v2.yaml', 'enum:urgency', 'agree', 'the 4 priorities'],
    ]);
  }],
  ['04-capstone', 'Both prompts name every value and state the rule', () => {
    for (const p of ['v2_rules', 'v3_examples']) {
      const r = rowsOf(`llms/data/prompts.json#triage.${p}`);
      expect(r.every((x) => x.status === 'agree')).toBe(true);
      expect(r.find((x) => x.fact === 'rule:feature_request_never_above_normal')!.detail).toBe('stated ("never above normal")');
    }
  }],
  ['04-capstone', 'rule U2, which already treats signs of unauthorized access as a priority', () => {
    const c = constraints.find((x) => x.source === 'U2')!;
    expect([c.id, c.kind, c.text]).toEqual(['security', 'manual', 'Signs of unauthorized access or exposed data.']);
  }],
  ['03-grounding', 'answering `label must be one of: billing, bug, account, how_to, feature_request, outage` when it is wrong', () => {
    expect(generated.compare.tool.existing.error_label).toBe('label must be one of: billing, bug, account, how_to, feature_request, outage');
    expect(text('mcp/python/ops_server.py')).toContain('ERR_LABEL = "label must be one of: " + ", ".join(CATEGORIES)');
  }],
  ['03-grounding', 'The server declares `id` and `label` as plain strings and checks the label at runtime', () => {
    expect(generated.compare.tool.existing.input_schema.properties).toEqual({ id: { type: 'string' }, label: { type: 'string' } });
    expect(generated.compare.tool.generated.input_schema.properties.label.enum).toEqual(individuals('Category').map((i) => i.id));
    expect(generated.compare.tool.generated.input_schema.properties.id.pattern).toBe('^t\\d{3}$');
  }],
  ['01-what', 'read by four small Python files', () => expect(readdirSync(join(repo, 'ontology/python')).filter((f) => f.endsWith('.py')).sort()).toEqual(['audit.py', 'generate.py', 'grounding.py', 'ontology.py'])],
  ['01-what', 'the loader, the generator, the validation and the audit are four small Python files', () => expect(readdirSync(join(repo, 'ontology/python')).filter((f) => f.endsWith('.py'))).toHaveLength(4)],
  ['03-grounding', 'They differ only in enum order', () => {
    const { existing, generated: gen } = generated.compare.schema;
    const sortEnums = (s: { properties: Record<string, { enum?: string[] }> }) =>
      JSON.stringify({ ...s, properties: Object.fromEntries(Object.entries(s.properties).map(([k, v]) => [k, v.enum ? { ...v, enum: [...v.enum].sort() } : v])) });
    expect(sortEnums(existing)).toBe(sortEnums(gen));
    expect(existing).not.toEqual(gen);
    expect(gen.properties.priority.enum).toEqual(['urgent', 'high', 'low', 'normal']);
    expect(existing.properties.category.enum[0]).toBe('billing');
    expect(existing.properties.priority.enum).toEqual(['low', 'normal', 'high', 'urgent']);
  }],
  ['03-grounding', 'Both name every category and every priority', () => {
    const existing = generated.compare.definitions.existing as string;
    for (const i of [...individuals('Category'), ...individuals('Priority')]) expect(existing, i.id).toContain(i.id);
  }],
  ['03-grounding', '`v1_basic` states no rule at all: it is two sentences: who the model is, and a request for a category, a priority and a reply', () => {
    expect(json('llms/data/prompts.json').triage.v1_basic).toBe("You triage support tickets for Lumen, a cloud notes app. Return the ticket's category, its priority and a short reply draft.");
  }],
  ['03-grounding', 'Each is a feature request answered `high`. Both tickets name a business impact, a large evaluation and a renewal', () => {
    const byId = Object.fromEntries(tickets.map((t) => [t.id, t]));
    for (const id of ['t029', 't055']) expect([byId[id].category, byId[id].priority]).toEqual(['feature_request', 'normal']);
    expect(byId.t029.text).toContain('We are evaluating Lumen');
    expect(byId.t055.text).toContain('we cannot renew');
  }],
];

const REAL = [...CLAIMS, ...QUOTES];

// Every source's record count, pinned whole (the GroundingReport table shows each one).
const RECORDS: Record<string, number> = {
  'Qwen2.5-0.5B-Instruct+lora__lora__default': 20,
  'agents/desk-hand': 17,
  'agents/desk-hand-go': 16,
  'agents/desk-langgraph': 13,
  'agents/desk-sdk': 14,
  'triage__claude-haiku-5-5__v1_basic__default': 60,
  'triage__claude-haiku-5-5__v2_rules__default': 180,
  'triage__claude-haiku-5-5__v3_examples__default': 60,
  'triage__claude-opus-5-5__v1_basic__high': 40,
  'triage__claude-opus-5-5__v1_basic__low': 40,
  'triage__claude-opus-5-5__v2_rules__high': 40,
  'triage__claude-opus-5-5__v2_rules__low': 40,
  'triage__claude-sonnet-5-5__v1_basic__high': 40,
  'triage__claude-sonnet-5-5__v1_basic__low': 40,
  'triage__claude-sonnet-5-5__v2_rules__high': 40,
  'triage__claude-sonnet-5-5__v2_rules__low': 40,
};

describe('ontology lessons: files, aids and anatomy', () => {
  it('has the four lessons, each with the aid spec §5 names', () => {
    expect(readdirSync(lessonsDir).filter((f) => f.endsWith('.mdx')).sort()).toEqual(LESSONS.map((l) => `${l}.mdx`));
    for (const l of LESSONS) expect(existsSync(join(lessonsDir, `${l}.mdx`)), l).toBe(true);
    expect(read('01-what')).toMatch(/<FlowDiagram diagram="ontology-layers" \/>/);
    expect(read('02-lumen')).toMatch(/<OntologyBrowser cls="Ticket" \/>/);
    expect(read('03-grounding')).toMatch(/<GroundingReport show="generated" \/>/);
    expect(read('03-grounding')).toMatch(/<GroundingReport show="validation" \/>/);
    expect(read('04-capstone')).toMatch(/<AuditTable \/>/);
  });

  it('gives lesson 2, and only lesson 2, the Go callout, and no lesson a "How Go does it" section (Python only)', () => {
    for (const l of LESSONS) {
      expect(read(l).includes('<Callout kind="go"'), l).toBe(l === '02-lumen');
      expect(read(l), l).not.toMatch(/\n## How \w+ does it\n/);
    }
    const go = read('02-lumen').split('<Callout kind="go" title="Python only">')[1].split('</Callout>')[0];
    expect(go).toContain('This track is Python only, and nothing in it is a twin.');
  });

  it('gives each lesson 2 or 3 quiz questions of 4 options', () => {
    for (const l of LESSONS) {
      const quiz = matter(read(l)).data.quiz as { options: string[] }[];
      expect(quiz.length, l).toBeGreaterThanOrEqual(2);
      expect(quiz.length, l).toBeLessThanOrEqual(3);
      for (const q of quiz) expect(q.options, l).toHaveLength(4);
    }
  });

  it('lesson 1 maps the file onto OWL, RDFS, SKOS and SHACL, and says the names are the course\'s own', () => {
    const l1 = read('01-what');
    for (const t of ['taxonomy', 'schema', 'ontology', 'knowledge graph', 'RDFS', 'OWL', 'SKOS', 'SHACL']) expect(l1).toContain(`<Term t="${t}" />`);
    for (const line of [
      '- `classes`, with an optional `subclass_of`, corresponds to classes in',
      '- `properties` corresponds to OWL datatype properties',
      '- `relations` corresponds to OWL object properties',
      '- `individuals` corresponds to <Term t="SKOS" /> concepts',
      '- `constraints` corresponds to <Term t="SHACL" /> shapes',
      "The section names are the course's own, not the standards'.",
    ]) expect(l1).toContain(line);
    // the five sections the mapping names are the file's, in order
    expect([...lumen.matchAll(/^# ── \d\. (\w+)/gm)].map((m) => m[1])).toEqual(['Classes', 'Properties', 'Relations', 'Individuals', 'Constraints']);
  });

  it('lesson 1 carries the concept tour: the LLM page, the tools in one callout, and the out-of-scope list as what a production stack adds', () => {
    const l1 = read('01-what');
    const llm = l1.split('### Where ontologies sit beside LLMs today')[1].split('\n### ')[0].split('<Callout')[0];
    for (const s of ['**Before the call**', '**At the call**', '**After the call**', '**Around the call**']) expect(llm).toContain(s);
    const callout = l1.split('<Callout kind="note" title="The tools you would use at work">')[1].split('</Callout>')[0];
    for (const tool of ['Protégé', '`rdflib`', '`pySHACL`', 'HermiT', 'Apache Jena', 'SPARQL']) expect(callout).toContain(tool);
    // spec §2: the out-of-scope list, named as what a production stack adds
    const adds = l1.split('### What a production ontology stack adds')[1].split('\n## ')[0];
    for (const item of ['Reasoning beyond subclass inheritance', 'A triple store and SPARQL queries', 'Live entity linking', 'Changes in other tracks']) expect(adds).toContain(item);
  });

  it('lesson 2 draws the honest line: checkable rules over the record\'s fields, manual ones over its text', () => {
    const l2 = read('02-lumen');
    expect(l2).toContain('a rule over the fields is checkable, a rule over the text is manual. That is the honest line.');
    for (const s of ['<Term t="individual" />', '<Term t="checkable constraint" />', '<Term t="manual constraint" />']) expect(l2).toContain(s);
    expect(checkable.map((c) => [c.id, c.source])).toEqual([['feature_request_never_above_normal', 'N-F']]);
  });

  it('never calls a --check a gate: lesson 4 says it catches drift only', () => {
    for (const l of LESSONS) expect(read(l), l).not.toMatch(/--check`?\s+(is|acts as|works as|serves as)\s+(a|the)\s+(CI\s+)?gate/i);
    expect(read('04-capstone')).toContain('### A drift check is not a gate');
    expect(read('04-capstone')).toContain('so it catches drift: a stale file, or one edited by hand. It says nothing about what the file holds.');
  });

  it('argues lesson 4\'s disagreement through with the data track\'s own versioning rule (ruling O11)', () => {
    const l4 = read('04-capstone').split('### Who must change')[1].split('\n### ')[0];
    for (const s of [
      '**A new contract version without `security`.**',
      '**A defined `security` category.**',
      'Taking a value out of an enum is a removal too.',
      'Today v2 lets a producer write `category: security`.',
      'v2 is deprecated with a sunset date, a migration note, and every consumer of v2 moving to v3 by that date',
      'it has not made that labelling decision',
      'Both need a decision nobody has taken yet.',
      'So the course leaves the gate red on purpose. A red gate with a named reason is the audit doing its job',
    ]) expect(l4).toContain(s);
    expect(l4).not.toMatch(/stays on version 2|change belongs in the contract/);
    // the data track's rule, quoted as its lesson states it, and v2 really allows security today
    expect(l4).toContain('"Renaming a field, removing one or changing its type breaks every reader that looks it up."');
    expect(text('app/src/content/lessons/data/04-contract.mdx')).toContain('Renaming a field, removing one or changing its type breaks every reader that looks it up.');
    expect(text('data/contracts/tickets.v2.yaml')).toContain('enum: [billing, bug, account, feature_request, how_to, outage, security]');
    // quiz 1 grades exactly that reasoning; editing v2 in place is the distractor
    const q1 = (matter(read('04-capstone')).data.quiz as { options: string[]; answer: number }[])[0];
    expect(q1.options[q1.answer]).toContain('Write tickets.v3 without security');
    expect(q1.options.findIndex((o) => o.startsWith('Edit tickets.v2 in place'))).not.toBe(q1.answer);
  });

  it('pins the record count of every one of the 16 validation sources', () => {
    expect(Object.fromEntries(Object.entries(sources).map(([k, s]) => [k, s.records]))).toEqual(RECORDS);
  });
});

describe('ontology lessons: every number is pinned', () => {
  it.each(REAL.map((c) => [`${c[0]}: ${c[1].slice(0, 70)}`, c] as const))('%s', (_n, [lesson, phrase, check]) => {
    expect(prose(lesson), `${lesson} no longer says: ${phrase}`).toContain(phrase);
    check();
  });

  it.each(LESSONS)('%s: no digit is left once the claims, the lesson references, the numbered lists and the names are taken out', (lesson) => {
    let t = prose(lesson);
    for (const [l, phrase] of REAL) if (l === lesson) t = t.split(phrase).join(' ');
    const refs = [...t.matchAll(/\b[Ll]esson (\d)\b/g)].map((m) => Number(m[1]));
    expect(refs.every((n) => n >= 1 && n <= 4), `${lesson} refers to ${refs}`).toBe(true);
    t = t
      .replace(/\b[Ll]esson \d\b/g, ' ')
      .replace(/^\d\. /gm, ' ')
      .replace(/`([^`]*)`/g, (_m, span: string) => (codeOk(span) ? ' ' : ` ${span} `))
      .replace(/\b(tickets|complaints)\.v\d\b/g, ' ')
      .replace(/\bv1_basic\b|\bv[123]\b/g, ' ') // prompt and contract versions
      .replace(/\b[CUHL]\d\b/g, ' ') // guide rule ids
      .replace(/\bW3C\b/g, ' '); // the standards body's name
    expect(t.match(/.{0,40}\d.{0,20}/g) ?? [], `${lesson}: an unpinned number`).toEqual([]);
  });
});
