import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';
import { LORA_D, loraParams } from '../src/lib/llmsAids';
import {
  agreement,
  buildLlmsData,
  callCost,
  cohenKappa,
  compare,
  gradeRun,
  noiseBand,
  positionFlips,
  report,
  runName,
  verdicts,
  RUBRIC_IDS,
  type Grade,
  type JudgeOutput,
  type Line,
  type LlmsData,
  type Run,
  type Ticket,
  type TriageOutput,
} from '../src/lib/llmsData';
import { judgeKey, triageKey } from '../src/lib/llmsKey';

// The LLMs track, lessons 1-11: every number a lesson states is pinned here against its source, so a
// re-record, a relabel or a code change fails this test and names the lesson to update. Sources:
//  - llms/data (tickets, prompts, reply labels, responses/, label_audits/, label_changes.md, README.md,
//    inspect_summary.json), recomputed with the app's mirror of the lab (llmsData.ts, llmsKey.ts);
//  - git history for the lesson 2 story (the old labels at 3610e80, the first recording at b9981a6) and
//    lesson 6's commit times; history never changes, so those pins only guard the prose;
//  - constants read from llms/python/triage_lab.py by regex;
//  - the Try-it output blocks: llms/python/tests/test_lesson_tryits.py runs every Try-it command in
//    replay and checks its block line for line, and checks every paid estimate a lesson quotes with the
//    call declined. A number the prose repeats from a block is pinned here against the data.
//
// RELABEL: the tests marked "RELABEL" pin lesson 6 numbers that come from the 30 Claude-drafted reply
// labels in llms/data/reply_labels.jsonl. When you review and change those labels, these fail; update
// lesson 6 (the lines named in each test) and the agreement and kappa_demo blocks, which
// test_lesson_tryits.py checks. Everything else in lesson 6 uses only the judge and does not move.
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '../..');
const lessonsDir = join(here, '../src/content/lessons/llms');
const src = (p: string) => readFileSync(join(repo, p), 'utf8');
const git = (...args: string[]) => execFileSync('git', args, { cwd: repo, encoding: 'utf8' });
const read = (n: string) => readFileSync(join(lessonsDir, `${n}.mdx`), 'utf8');
/** A lesson's body with runs of whitespace collapsed, so a pinned sentence may wrap. */
const flat = (n: string) => matter(read(n)).content.replace(/\s+/g, ' ');
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const usd = (x: number, d = 2) => `$${x.toFixed(d)}`;
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const mean = (xs: number[]) => sum(xs) / xs.length;
const commas = (n: number) => n.toLocaleString('en-US');
const jsonl = <T>(text: string): T[] => text.trim().split('\n').filter(Boolean).map((l) => JSON.parse(l));

const RESPONSES = [
  'judge__claude-opus-5-5__after__default', 'judge__claude-opus-5-5__before__default',
  'triage__claude-haiku-5-5__v1_basic__default', 'triage__claude-haiku-5-5__v2_rules__default',
  'triage__claude-haiku-5-5__v3_examples__default', 'triage__claude-opus-5-5__v1_basic__high',
  'triage__claude-opus-5-5__v1_basic__low', 'triage__claude-opus-5-5__v2_rules__high',
  'triage__claude-opus-5-5__v2_rules__low', 'triage__claude-sonnet-5-5__v1_basic__high',
  'triage__claude-sonnet-5-5__v1_basic__low', 'triage__claude-sonnet-5-5__v2_rules__high',
  'triage__claude-sonnet-5-5__v2_rules__low',
];
const D: LlmsData = buildLlmsData({
  tickets: src('llms/data/tickets.jsonl'),
  labels: src('llms/data/reply_labels.jsonl'),
  prompts: src('llms/data/prompts.json'),
  responses: Object.fromEntries(RESPONSES.map((r) => [`responses/${r}.jsonl`, src(`llms/data/responses/${r}.jsonl`)])),
});
const tickets = D.tickets;
const byId = new Map(tickets.map((t) => [t.id, t]));
const dev = tickets.filter((t) => t.split === 'dev');
const test = tickets.filter((t) => t.split === 'test');
const run = (name: string, data = D): Run => {
  const r = data.runs.find((x) => runName(x.config) === name);
  if (!r) throw new Error(`no run ${name}`);
  return r;
};
const haiku = (p: string) => `triage__claude-haiku-5-5__${p}__default`;
/** Grades of one run on one split, one rep. */
const gradesOn = (name: string, split: Ticket[], rep = 0, data = D, tks = tickets): Grade[] => {
  const ids = new Set(split.map((t) => t.id));
  return gradeRun(run(name, data), tks, rep).filter((g) => ids.has(g.case_id));
};
const both = (name: string, split: Ticket[], rep = 0) => report(gradesOn(name, split, rep)).both;
const linesOn = (name: string, split: Ticket[], rep = 0) => {
  const ids = new Set(split.map((t) => t.id));
  return run(name).lines.filter((l) => l.config.rep === rep && ids.has(l.case_id));
};
const wrong = (gs: Grade[]) => gs.filter((g) => !g.both_ok).map((g) => g.case_id).sort();
/** Constants from triage_lab.py. */
const lab = src('llms/python/triage_lab.py');
const EST = Object.fromEntries(
  [...lab.matchAll(/"(triage|judge)": \((\d+), (\d+)\)/g)].map((m) => [m[1], [Number(m[2]), Number(m[3])]]),
) as Record<'triage' | 'judge', [number, number]>;
const MAX_TOKENS = Number(/^MAX_TOKENS = (\d+)/m.exec(lab)![1]);
const estimate = (kind: 'triage' | 'judge', model: string, n: number) => n * callCost(model, EST[kind][0], EST[kind][1]);
const worst = (kind: 'triage' | 'judge', model: string, n: number) => n * callCost(model, EST[kind][0], MAX_TOKENS);
const HAIKU = 'claude-haiku-5-5';
const OPUS = 'claude-opus-5-5';
const SONNET = 'claude-sonnet-5-5';

/** The judge's latest verdict lines for the 30 labelled replies, in label order. */
const judged = (order: 'before' | 'after') => {
  const lines = new Map(run(`judge__${OPUS}__${order}__default`).lines.map((l) => [l.key, l]));
  return D.labels.map((r) => {
    const line = lines.get(judgeKey(byId.get(r.ticket_id)!.text, r.reply, order, OPUS, D.prompts));
    if (!line) throw new Error(`no ${order} verdict for ${r.id}`);
    return line;
  });
};
/** The judge's verdicts (rubric after) on one prompt's rep-0 test replies. */
const judgedTest = (prompt: string) => {
  const lines = new Map(run(`judge__${OPUS}__after__default`).lines.map((l) => [l.key, l]));
  return linesOn(haiku(prompt), test).map((l) => {
    const reply = (l.output as TriageOutput).reply;
    const j = lines.get(judgeKey(byId.get(l.case_id)!.text, reply, 'after', OPUS, D.prompts));
    if (!j) throw new Error(`no verdict for ${prompt} ${l.case_id}`);
    return { id: l.case_id, triage: l, judge: j, v: verdicts(j) };
  });
};

describe('the LLMs lessons as a set', () => {
  it('run every Python and pip command in .venv-llms (P1), like the README; Inspect in .venv-inspect', () => {
    for (const l of ['01-what', '02-dataset', '03-graders', '04-runner', '05-judge', '06-judge-check', '07-prompt',
      '08-model', '09-lora', '10-capstone', '11-framework']) {
      const blocks = [...matter(read(l)).content.matchAll(/^ *```(bash|sh)\n([\s\S]*?)^ *```/gm)].map((m) => m[2]);
      for (const line of blocks.join('\n').split('\n')) {
        if (/^\s*python3 -m venv \.venv-(llms|inspect)\b/.test(line)) continue; // creating the venv
        if (/^\s*!/.test(line)) continue; // a Colab cell: a throwaway machine (lesson 9)
        const rest = line.replace(/(\.\.\/)*\.venv-(llms|inspect)\/bin\/\w+( -m \w+)?/g, 'VENV');
        expect(/(^|[\s(;&|'<])(python3?|pip3?|pytest|inspect)\s/.exec(rest), `${l}: ${line}`).toBeNull();
      }
    }
    expect(src('README.md')).toContain('python3 -m venv .venv-llms && .venv-llms/bin/pip install -r llms/python/requirements.txt');
    expect(read('01-what')).toContain('python3 -m venv .venv-llms && .venv-llms/bin/pip install -r llms/python/requirements.txt\ncd llms/python\n');
  });

  it('the paid Inspect path is documented as gated by LUMEN_LIVE_OK and priced with summarize.py --estimate', () => {
    const l = flat('11-framework');
    const inspect = src('llms/inspect/triage_inspect.py');
    expect(inspect).toContain('LIVE_OK = "LUMEN_LIVE_OK"');
    expect(l).toContain('.venv-inspect/bin/python llms/inspect/summarize.py --estimate');
    expect(l).toContain('LUMEN_LIVE_OK=1 .venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model anthropic/claude-haiku-5-5');
    expect(src('README.md')).toContain('.venv-inspect/bin/python llms/inspect/summarize.py --estimate');
    expect(src('README.md')).toContain('unless `LUMEN_LIVE_OK=1` is set');
  });
});

describe('lesson 1 (what an eval is): the numbers it states', () => {
  const l = flat('01-what');
  it('40 dev tickets; v1_basic 70.0% and v2_rules 97.5% both right (the block is pinned by the Try-it test)', () => {
    expect(dev).toHaveLength(40);
    expect(l).toContain('Run the eval on the 40 `dev` tickets');
    expect(pct(both(haiku('v1_basic'), dev))).toBe('70.0%');
    expect(l).toContain(`\`both\` rises to \`${pct(both(haiku('v2_rules'), dev))}\``);
  });
  it('recorded on 2026-10-25, and the ten numbered sections in each file', () => {
    expect(l).toContain('recorded from the API on 2026-10-25');
    expect(src('llms/data/README.md')).toContain('## Recording run (2026-10-25)');
    expect([...lab.matchAll(/^# ── (\d+)\./gm)].map((m) => Number(m[1]))).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect([...src('llms/go/main.go').matchAll(/^\/\/ ── (\d+)\./gm)].map((m) => Number(m[1]))).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(l).toContain('with the same ten numbered sections');
  });
});

describe('lesson 2 (the dataset): the numbers it states', () => {
  const l = flat('02-dataset');
  const count = (xs: string[]) => xs.reduce<Record<string, number>>((m, x) => ({ ...m, [x]: (m[x] ?? 0) + 1 }), {});
  it('60 tickets: the categories, priorities and tags', () => {
    expect(tickets).toHaveLength(60);
    expect(count(tickets.map((t) => t.category))).toEqual({ bug: 15, billing: 10, account: 10, feature_request: 10, how_to: 9, outage: 6 });
    expect(l).toContain('there are 15 `bug`, 10 each of `billing`, `account` and `feature_request`, 9 `how_to` and 6 `outage`');
    expect(count(tickets.map((t) => t.priority))).toEqual({ low: 25, normal: 16, high: 10, urgent: 9 });
    expect(l).toContain('Priorities are 25 `low`, 16 `normal`, 10 `high` and 9 `urgent`');
    expect(tickets.filter((t) => t.tags.length)).toHaveLength(25);
    expect(count(tickets.flatMap((t) => t.tags))).toEqual({ multi_issue: 6, very_short: 4, sarcasm: 4, non_english: 4, angry_minor: 3, very_long: 2, injection: 2 });
    expect(l).toContain('25 tickets carry tags');
    expect(l).toContain('`multi_issue` (6), `very_short` (4), `sarcasm` (4), `non_english` (4), `angry_minor` (3), `very_long` (2) and `injection` (2). The two `injection` tickets');
  });
  it('the split: 40 dev and 20 test, priorities per split, the one test high ticket, the 40% baseline', () => {
    expect([dev.length, test.length]).toEqual([40, 20]);
    expect(l).toContain('The tickets are split 40 `dev` and 20 `test`');
    expect(count(test.map((t) => t.priority))).toEqual({ low: 9, normal: 7, high: 1, urgent: 3 });
    expect(l).toContain('The test split has 9 `low`, 7 `normal`, 3 `urgent` and only 1 `high` ticket');
    expect(count(dev.map((t) => t.priority))).toEqual({ low: 16, normal: 9, high: 9, urgent: 6 });
    expect(l).toContain('which has 16 `low`, 9 `normal`, 9 `high` and 6 `urgent`');
    expect(l).toContain('`low` is 16 of the 40 dev tickets, so a "model" that always answers `low` scores 40% on priority');
    expect(test.filter((t) => t.priority === 'high').map((t) => t.id)).toEqual(['t042']);
    expect(l).toContain('High and test together leave one ticket, `t042`');
    expect(l).toContain('pick `dev` with `high`: nine tickets');
  });

  // The story: the old labels (3610e80) graded against the first recording (b9981a6).
  const old = (() => {
    const data = buildLlmsData({
      tickets: git('show', '3610e80:llms/data/tickets.jsonl'),
      labels: '',
      prompts: git('show', 'b9981a6:llms/data/prompts.json'),
      responses: Object.fromEntries(['v1_basic', 'v2_rules', 'v3_examples'].map((p) => haiku(p))
        .concat(['sonnet-5-5__v1_basic__low', 'sonnet-5-5__v1_basic__high', 'opus-5-5__v1_basic__low', 'opus-5-5__v1_basic__high']
          .map((s) => `triage__claude-${s}`))
        .map((r) => [`responses/${r}.jsonl`, git('show', `b9981a6:llms/data/responses/${r}.jsonl`)])),
    });
    const oldDev = data.tickets.filter((t) => t.split === 'dev');
    const score = (name: string) => report(gradesOn(name, oldDev, 0, data, data.tickets));
    return { data, oldDev, score };
  })();
  it('history: 55.0% for all three prompts; Sonnet 50.0/52.5, Opus 52.5/50.0; 16 wrong in all five; 10 relabelled, 6 kept', () => {
    for (const p of ['v1_basic', 'v2_rules', 'v3_examples']) expect(pct(old.score(haiku(p)).both)).toBe('55.0%');
    expect(l).toContain('The first recorded run scored all three prompts at the same 55.0% on dev');
    const five = [haiku('v1_basic'), `triage__${SONNET}__v1_basic__low`, `triage__${SONNET}__v1_basic__high`,
      `triage__${OPUS}__v1_basic__low`, `triage__${OPUS}__v1_basic__high`];
    expect(five.map((n) => pct(old.score(n).both))).toEqual(['55.0%', '50.0%', '52.5%', '52.5%', '50.0%']);
    expect(l).toContain('Haiku scores 55.0%, Sonnet 50.0% and 52.5% at low and high effort, and Opus 52.5% and 50.0%');
    const wrongAll = old.oldDev.map((t) => t.id).filter((id) =>
      five.every((n) => !gradesOn(n, old.oldDev, 0, old.data, old.data.tickets).find((g) => g.case_id === id)!.both_ok));
    expect(wrongAll).toHaveLength(16);
    expect(l).toContain('16 dev tickets were wrong in all five');
    const oldById = new Map(old.data.tickets.map((t) => [t.id, t]));
    const kept = wrongAll.filter((id) => oldById.get(id)!.category === byId.get(id)!.category && oldById.get(id)!.priority === byId.get(id)!.priority);
    expect(kept).toEqual(['t007', 't012', 't015', 't029', 't034', 't039']);
    expect(wrongAll.length - kept.length).toBe(10);
    expect(l).toContain('changed the labels on 10 of those 16 tickets. The other 6 (`t007`, `t012`, `t015`, `t029`, `t034`, `t039`) kept their labels');
  });
  it('the audits: 57/60 and 33/60 (55%); 24 relabels (2 categories, 23 priorities); 25 in all; round 3 60/60 with 5 ambiguous', () => {
    const oldById = new Map(old.data.tickets.map((t) => [t.id, t]));
    const r1 = jsonl<{ id: string; category: string; priority: string }>(src('llms/data/label_audits/round1_blind.jsonl'));
    expect(r1.filter((r) => r.category === oldById.get(r.id)!.category)).toHaveLength(57);
    expect(r1.filter((r) => r.priority === oldById.get(r.id)!.priority)).toHaveLength(33);
    expect(l).toContain('It matched the category on 57 of 60 tickets, but the priority on only 33 of 60 (55%)');
    const changes = src('llms/data/label_changes.md');
    const round1 = changes.slice(0, changes.indexOf('\n## ', changes.indexOf('| id |')));
    const rows = round1.split('\n').filter((r) => /^\| t\d{3} \|/.test(r)).map((r) => r.split('|').map((c) => c.trim()));
    expect(rows).toHaveLength(24);
    expect(rows.filter((r) => r[3].includes('→'))).toHaveLength(2);
    expect(rows.filter((r) => r[4].includes('→'))).toHaveLength(23);
    expect(rows.filter((r) => r[3].includes('→') && r[4].includes('→')).map((r) => r[1])).toEqual(['t038']);
    expect(l).toContain('the guide changed 24 tickets\' labels: 2 categories and 23 priorities (`t038` changed both)');
    const differs = tickets.filter((t) => oldById.get(t.id)!.category !== t.category || oldById.get(t.id)!.priority !== t.priority);
    expect(differs).toHaveLength(25);
    expect(l).toContain('That makes 25 tickets in all whose labels differ from the original ones');
    expect(oldById.get('t047')!.priority).toBe('high');
    expect(byId.get('t047')!.priority).toBe('low');
    expect(oldById.get('t052')!.priority).toBe('high');
    expect(byId.get('t052')!.priority).toBe('urgent');
    const r3 = jsonl<{ id: string; category: string; priority: string; ambiguous: boolean }>(src('llms/data/label_audits/round3_blind.jsonl'));
    expect(r3.filter((r) => r.category === byId.get(r.id)!.category)).toHaveLength(60);
    expect(r3.filter((r) => r.priority === byId.get(r.id)!.priority)).toHaveLength(60);
    expect(r3.filter((r) => r.ambiguous).map((r) => r.id)).toEqual(['t018', 't034', 't037', 't047', 't057']);
    expect(l).toContain('A third blind labeller then matched 60 of 60 categories and 60 of 60 priorities');
    expect(l).toContain('The last labeller still marked 5 tickets ambiguous (`t018`, `t034`, `t037`, `t047`, `t057`)');
    const r2 = jsonl<{ ambiguous: boolean }>(src('llms/data/label_audits/round2_blind.jsonl'));
    expect(r2.filter((r) => r.ambiguous)).toHaveLength(5);
    expect(l).toContain('matched all 60 labels but marked 5 tickets ambiguous');
  });
  it('v1_basic 55.0% to 70.0% with unchanged responses; v2/v3 97.5% on dev', () => {
    expect(pct(old.score(haiku('v1_basic')).both)).toBe('55.0%');
    expect(pct(both(haiku('v1_basic'), dev))).toBe('70.0%');
    expect(git('diff', '--stat', 'b9981a6', 'HEAD', '--', `llms/data/responses/${haiku('v1_basic')}.jsonl`)).toBe('');
    expect(l).toContain('its dev score went from 55.0% to 70.0%, purely because the answer key changed');
    expect([pct(both(haiku('v2_rules'), dev)), pct(both(haiku('v3_examples'), dev))]).toEqual(['97.5%', '97.5%']);
    expect(l).toContain('so their 97.5% on dev partly measures');
  });
  it('one new ticket: 41 calls estimated $0.02 / $0.04 (the Try-it test) and about $0.0004 for x001 alone', () => {
    expect(usd(estimate('triage', HAIKU, 1), 4)).toBe('$0.0004');
    expect(l).toContain('so only `x001` is actually called, about `$0.0004`');
  });
});

describe('lesson 3 (code graders): the numbers it states', () => {
  const l = flat('03-graders');
  const v1 = linesOn(haiku('v1_basic'), dev);
  const out = (line: Line) => line.output as TriageOutput;
  it('every latest triage line ended end_turn; the longest reply used 761 of 2,000 output tokens (Haiku v2_rules)', () => {
    const triage = D.runs.filter((r) => r.config.kind === 'triage').flatMap((r) => r.lines);
    expect(new Set(triage.map((x) => x.stop_reason))).toEqual(new Set(['end_turn']));
    const longest = triage.reduce((a, b) => (b.output_tokens > a.output_tokens ? b : a));
    expect([longest.output_tokens, longest.config.model, longest.config.prompt_version]).toEqual([761, HAIKU, 'v2_rules']);
    expect(MAX_TOKENS).toBe(2000);
    expect(l).toContain('the longest reply (Haiku, `v2_rules`) used 761 of its 2,000 output tokens');
  });
  it('the v1_basic priority matrix: 31 of 40 on the diagonal, 8 one step off, one high to low, 5 too high, 4 too low', () => {
    const P = ['low', 'normal', 'high', 'urgent'];
    const pairs = v1.map((x) => [P.indexOf(byId.get(x.case_id)!.priority), P.indexOf(out(x).priority)]);
    const rows = P.map((_, e) => P.map((__, p) => pairs.filter(([a, b]) => a === e && b === p).length));
    const block = /```text\n(expected [\s\S]*?)```/.exec(read('03-graders'))![1].split('\n');
    expect(block.slice(1, 5).map((r) => r.trim().split(/\s+/).slice(1).map(Number))).toEqual(rows);
    expect(pairs.filter(([a, b]) => a === b)).toHaveLength(31);
    expect(pairs.filter(([a, b]) => Math.abs(a - b) === 1)).toHaveLength(8);
    expect(pairs.filter(([a, b]) => Math.abs(a - b) === 2)).toEqual([[2, 0]]);
    expect(pairs.filter(([a, b]) => b > a)).toHaveLength(5);
    expect(pairs.filter(([a, b]) => b < a)).toHaveLength(4);
    expect(rows[1][2]).toBe(3);
    expect(l).toContain('The diagonal adds up to 31 of 40, the 77.5% priority accuracy. The other nine are not random. Eight are one step off, and only one `high` ticket fell two steps, to `low`. Five answers are too high, such as three `normal` tickets raised to `high`, and four are too low.');
  });
  it('the category matrix: 37 of 40, the three mistakes are t015, t028, t039 answered how_to; Opus high misses t015 and t039 too', () => {
    const miss = v1.filter((x) => out(x).category !== byId.get(x.case_id)!.category);
    expect(miss.map((x) => [x.case_id, byId.get(x.case_id)!.category, out(x).category]).sort())
      .toEqual([['t015', 'account', 'how_to'], ['t028', 'bug', 'how_to'], ['t039', 'account', 'how_to']]);
    expect(l).toContain('37 of 40 are right, and all three mistakes land in the `how_to` column');
    const opus = linesOn(`triage__${OPUS}__v1_basic__high`, dev).filter((x) => out(x).category !== byId.get(x.case_id)!.category);
    expect(opus.filter((x) => out(x).category === 'how_to' && byId.get(x.case_id)!.category === 'account').map((x) => x.case_id).sort())
      .toEqual(['t015', 't039']);
    const v2 = linesOn(haiku('v2_rules'), dev);
    expect(v2.every((x) => out(x).category === byId.get(x.case_id)!.category)).toBe(true);
    expect(l).toContain('Switch the run to `Haiku 5.5 · v2_rules`, which states the rules: every ticket sits on the diagonal');
  });
  it('the Try-it arithmetic: 27 of 40 after a refusal, 27 of 39 after an error; v2_rules misses one high answered normal', () => {
    const right = v1.filter((x) => out(x).category === byId.get(x.case_id)!.category && out(x).priority === byId.get(x.case_id)!.priority);
    expect(right.length).toBe(28);
    expect([pct(27 / 40), pct(27 / 39)]).toEqual(['67.5%', '69.2%']);
    expect(l).toContain('The refusal turns a right answer into a wrong one: 27 of 40. The error removes the ticket from the count: 27 of 39.');
    const v2 = linesOn(haiku('v2_rules'), dev).filter((x) => out(x).priority !== byId.get(x.case_id)!.priority);
    expect(v2.map((x) => [byId.get(x.case_id)!.priority, out(x).priority])).toEqual([['high', 'normal']]);
    expect(l).toContain('the matrix is all diagonal except one `high` ticket answered `normal`');
  });
});

describe('lesson 4 (the runner): the numbers it states', () => {
  const l = flat('04-runner');
  it('the estimate: 900/700 tokens, $0.00044 a call, $0.0176 for 40, worst $0.0436; 120 calls $0.05 / $0.13', () => {
    expect(EST).toEqual({ triage: [900, 700], judge: [1400, 900] });
    expect(usd(estimate('triage', HAIKU, 1), 5)).toBe('$0.00044');
    expect(usd(estimate('triage', HAIKU, 40), 4)).toBe('$0.0176');
    expect(usd(worst('triage', HAIKU, 40), 4)).toBe('$0.0436');
    expect(l).toContain('900 input and 700 output for a triage call. For Haiku, at $0.10 and $0.50 per million tokens, that is $0.00044 a call, or $0.0176 for the 40 dev tickets.');
    expect(l).toContain('all of `MAX_TOKENS = 2000` output tokens: $0.0436 for the same 40');
    expect(l).toContain('`(900, 700)` for triage and `(1400, 900)` for the judge');
    expect(l).toContain('The cost lands well under the $0.0176 estimate');
    expect(/"--max-cost", type=float, default=10\.0/.test(lab)).toBe(true);
    expect(l).toContain('A cap, `--max-cost`, $10 by default');
  });
  it('the recorded v2_rules calls: 180 lines averaging about 1,546 input and 215 output tokens', () => {
    const lines = run(haiku('v2_rules')).lines;
    expect(lines).toHaveLength(180);
    expect(Math.round(mean(lines.map((x) => x.input_tokens)))).toBe(1546);
    expect(Math.round(mean(lines.map((x) => x.output_tokens)))).toBe(215);
    expect(l).toContain("Haiku's 180 recorded `v2_rules` calls (60 tickets, 3 repeats) averaged about 1,546 input and 215 output tokens");
  });
  it('the recording costs, from llms/data/README.md: $4.06 -> $1.59 and $2.69 -> $1.47', () => {
    const readme = src('llms/data/README.md');
    expect(readme).toContain('Full plan: 537 calls, $1.59, estimated $4.06 beforehand');
    expect(readme).toContain('Cost $1.47, estimated $2.69 beforehand');
    expect(l).toContain('The full recording plan was estimated at $4.06 and cost $1.59. The re-recording after the labelling guide was estimated at $2.69 and cost $1.47.');
  });
  it('the judge file kept 3 errored lines, each superseded by a later good one', () => {
    const raw = jsonl<Line>(src(`llms/data/responses/judge__${OPUS}__after__default.jsonl`));
    const errored = raw.filter((x) => x.error);
    expect(errored).toHaveLength(3);
    const latest = new Map(raw.map((x) => [x.key, x]));
    expect(errored.every((x) => !latest.get(x.key)!.error)).toBe(true);
    for (const r of RESPONSES.filter((n) => n.startsWith('triage'))) {
      expect(jsonl<Line>(src(`llms/data/responses/${r}.jsonl`)).filter((x) => x.error), r).toHaveLength(0);
    }
    expect(l).toContain('`judge__claude-opus-5-5__after__default.jsonl` still holds 3 errored lines, each superseded by a later good one');
  });
  it('noise: 97.5, 97.5, 95.0 on the three reps; t034 always wrong, t018 once; bands 15.8 and 9.1; +27.5 points; 96.7% pooled', () => {
    const reps = [0, 1, 2].map((r) => gradesOn(haiku('v2_rules'), dev, r));
    expect(reps.map((g) => pct(report(g).both))).toEqual(['97.5%', '97.5%', '95.0%']);
    expect(l).toContain('Haiku with `v2_rules` on the 40 dev tickets scored 97.5%, 97.5% and 95.0%. `t034` was wrong every time; `t018` was right twice and wrong once.');
    expect(reps.map(wrong)).toEqual([['t034'], ['t034'], ['t018', 't034']]);
    expect(pct(noiseBand(40))).toBe('15.8%');
    expect(l).toContain('For one run of 40 that is ±15.8%');
    const pooled = report(reps.flat());
    expect([pooled.scored, pct(pooled.band), pct(pooled.both)]).toEqual([120, '9.1%', '96.7%']);
    expect(l).toContain('the code reports 120 scored lines and ±9.1%');
    expect(l).toContain('prints the same 96.7% as Python');
    const c = compare(gradesOn(haiku('v1_basic'), dev), gradesOn(haiku('v2_rules'), dev));
    expect((c.diff * 100).toFixed(1)).toBe('27.5');
    expect(l).toContain('`v1_basic` to `v2_rules` on dev is +27.5 points, larger than 15.8');
    expect(l).toContain('The three repeats differ by 2.5 points');
  });
  it('the runner: 8 workers, 4 SDK retries, up to 7 more calls past the cap; the keys in the Try it', () => {
    expect(/"--workers", type=int, default=(\d+)/.exec(lab)![1]).toBe('8');
    expect(l).toContain('on a <Term t="thread pool" /> of 8 workers');
    expect(lab).toContain('anthropic.Anthropic(max_retries=4)');
    expect(l).toContain('up to 4 times');
    expect(l).toContain('the real spend can pass the cap by the cost of up to 7 more calls');
    const t = dev[0];
    const edited = { ...D.prompts, triage: { ...D.prompts.triage, v2_rules: `${D.prompts.triage.v2_rules}\nBe concise.` } };
    expect([triageKey(t.text, 'v2_rules', HAIKU, null, 0, D.prompts).slice(0, 12),
      triageKey(t.text, 'v2_rules', HAIKU, null, 0, edited).slice(0, 12)]).toEqual(['5d1983c4d25c', '053094d77cfe']);
  });
});

describe('lesson 5 (the judge): the numbers it states', () => {
  const l = flat('05-judge');
  const v2 = judgedTest('v2_rules');
  const cost = (xs: Line[]) => sum(xs.map((x) => callCost(x.config.model, x.input_tokens, x.output_tokens)));
  it('judging the 20 v2_rules test replies cost $0.2215 ($0.011 a call); triaging them $0.0051; about 43 times', () => {
    const judge = cost(v2.map((x) => x.judge));
    const triage = cost(v2.map((x) => x.triage));
    expect([usd(judge, 4), usd(judge / 20, 3), usd(triage, 4), Math.round(judge / triage)]).toEqual(['$0.2215', '$0.011', '$0.0051', 43]);
    expect(l).toContain('Opus judging the 20 `v2_rules` replies on the test split cost $0.2215, about $0.011 a call. Haiku triaging those same 20 tickets cost $0.0051. Judging cost about 43 times as much');
    expect(l).toContain('The recorded run cost $0.22.');
  });
  it('the 30 labelled replies, rubric after: $0.2809, 907 input and 287 output tokens a call; the estimate $0.0236 a call', () => {
    const after = judged('after');
    expect(usd(cost(after), 4)).toBe('$0.2809');
    expect([Math.round(mean(after.map((x) => x.input_tokens))), Math.round(mean(after.map((x) => x.output_tokens)))]).toEqual([907, 287]);
    expect(l).toContain('cost $0.2809, an average of 907 input and 287 output tokens a call');
    expect(usd(estimate('judge', OPUS, 1), 4)).toBe('$0.0236');
    expect(l).toContain('the lab estimates 1,400 input and 900 output tokens a call, $0.0236 for Opus');
  });
  it('the judged test passes: 14, 19, 19 of 20', () => {
    expect(RUBRIC_IDS.map((rid) => v2.filter((x) => x.v[rid]).length)).toEqual([14, 19, 19]);
    expect(flat('06-judge-check')).toContain('the judge passes 14 of 20 `v2_rules` replies on `addresses_issue`');
    expect(l).toContain('Six replies fail `addresses_issue`');
  });
  it('the reasons quoted for r001 and r029, and r029 the only flip of 90 verdicts per order', () => {
    const after = judged('after');
    const before = judged('before');
    const reason = (x: Line, rid: string) => (x.output as JudgeOutput)[rid as 'tone'].reason;
    const i001 = D.labels.findIndex((r) => r.id === 'r001');
    expect(l).toContain(`> fail: ${reason(after[i001], 'no_false_promises')}`);
    expect(verdicts(after[i001]).no_false_promises).toBe(false);
    const i029 = D.labels.findIndex((r) => r.id === 'r029');
    expect([verdicts(before[i029]).no_false_promises, verdicts(after[i029]).no_false_promises]).toEqual([false, true]);
    expect(reason(before[i029], 'no_false_promises')).toContain('implies a same-day fix that the company cannot guarantee');
    expect(reason(after[i029], 'no_false_promises')).toContain('a suggestion, not a commitment');
    expect(l).toContain('the phrase "implies a same-day fix that the company cannot guarantee". With the rubric after, it passes it: "a suggestion, not a commitment"');
    expect(sum(RUBRIC_IDS.map((rid) => positionFlips(before.map((x) => verdicts(x)[rid]), after.map((x) => verdicts(x)[rid]))))).toBe(1);
    expect(l).toContain('Across the 30 replies and 3 criteria, 90 verdicts in each order, this is the only one that changes');
  });
  it('30 judge calls estimated at $0.7080; 20 Opus calls $0.47 / $0.91', () => {
    expect(usd(estimate('judge', OPUS, 30), 4)).toBe('$0.7080');
    expect([usd(estimate('judge', OPUS, 20)), usd(worst('judge', OPUS, 20))]).toEqual(['$0.47', '$0.91']);
  });
});

describe('lesson 6 (checking the judge): the numbers it states', () => {
  const l = flat('06-judge-check');
  const human = (rid: string) => D.labels.map((r) => r.labels[rid as 'tone']);
  const judgeV = (order: 'before' | 'after', rid: string) => judged(order).map((x) => verdicts(x)[rid as 'tone']);
  const agree = (order: 'before' | 'after', rid: string) => agreement(judgeV(order, rid), human(rid));
  const kappa = (order: 'before' | 'after', rid: string) => cohenKappa(judgeV(order, rid).map(Boolean), human(rid));

  it('30 replies r001 to r030; the label fix at 19:31 and the first judge verdicts at 20:08 on 2026-10-25', () => {
    expect(D.labels.map((r) => r.id)).toEqual(Array.from({ length: 30 }, (_, i) => `r${String(i + 1).padStart(3, '0')}`));
    expect(git('log', '-1', '--format=%ad', '--date=format:%Y-%m-%d %H:%M', 'fbeb068').trim()).toBe('2026-10-25 19:31');
    expect(git('log', '-1', '--format=%ad', '--date=format:%Y-%m-%d %H:%M', 'b9981a6').trim()).toBe('2026-10-25 20:08');
    expect(git('show', '--stat', '--format=', 'b9981a6')).toContain('judge__claude-opus-5-5__after__default.jsonl');
    expect(l).toContain('the label fix committed at 19:31 on 2026-10-25 and the first judge verdicts at 20:08');
    expect(src('llms/data/README.md')).toContain('These labels are Claude-drafted and pending your review.');
  });
  it('RELABEL: agreement after the reply 28, 29, 30 of 30 (93.3%, 96.7%, 100%); lesson 6 "Agreement" and "See it" paragraphs', () => {
    expect(RUBRIC_IDS.map((rid) => Math.round(agree('after', rid) * 30))).toEqual([28, 29, 30]);
    expect(l).toContain('the judge agrees on 28 of 30 replies for `addresses_issue` (93.3%), 29 of 30 for `tone` (96.7%) and 30 of 30 for `no_false_promises` (100%)');
    expect(RUBRIC_IDS.map((rid) => pct(agree('before', rid)))).toEqual(['93.3%', '96.7%', '96.7%']);
    expect(l).toContain('`no_false_promises` is at 100.0% and kappa 1.00. Swap to before and it drops to 96.7% and 0.92');
    expect(l).toContain('`addresses_issue` stays at 93.3% (0.81) and `tone` at 96.7% (0.89) in both orders');
    expect(judged('after').every((x) => x.output)).toBe(true);
    expect(l).toContain('None of these 30 were ungraded');
  });
  it('RELABEL: kappa 0.81, 0.89, 1.00 after and 0.92 before; one disagreement takes 1.00 to 0.92; the worked pe 0.64', () => {
    expect(RUBRIC_IDS.map((rid) => kappa('after', rid).toFixed(2))).toEqual(['0.81', '0.89', '1.00']);
    expect(kappa('before', 'no_false_promises').toFixed(2)).toBe('0.92');
    expect(l).toContain('With the rubric after the reply, kappa is 0.81 for `addresses_issue`, 0.89 for `tone` and 1.00 for `no_false_promises`');
    expect(l).toContain('one disagreement on `no_false_promises` takes kappa from 1.00 to 0.92');
    expect(l).toContain('`no_false_promises` agreement is 29 of 30 and kappa 0.92. With it after, it is 30 of 30 and 1.00');
    const j = judgeV('after', 'addresses_issue').filter(Boolean).length;
    const h = human('addresses_issue').filter(Boolean).length;
    expect([j, h]).toEqual([22, 24]);
    const pe = (j / 30) * (h / 30) + (1 - j / 30) * (1 - h / 30);
    expect(pe.toFixed(2)).toBe('0.64');
    expect(l).toContain('The judge passes 22 of 30 replies and the labels pass 24. By chance they would both say pass `(22/30) × (24/30)` of the time and both say fail `(8/30) × (6/30)` of the time, so `pe` is 0.64. `po` is 28/30, or 0.933');
  });
  it('RELABEL: the labels pass 24, 25, 22 of 30, so always-pass agrees 80.0%, 83.3%, 73.3%', () => {
    expect(RUBRIC_IDS.map((rid) => human(rid).filter(Boolean).length)).toEqual([24, 25, 22]);
    expect(RUBRIC_IDS.map((rid) => pct(agreement(human(rid).map(() => true), human(rid))))).toEqual(['80.0%', '83.3%', '73.3%']);
    expect(l).toContain('The drafted labels pass 24 of 30 replies on `addresses_issue`. A "judge" that says pass to everything, without reading a word, would agree 80% of the time.');
  });
  it('RELABEL: the three disagreements after the reply are r022, r003 (addresses_issue) and r012 (tone), the judge stricter in each', () => {
    const dis = RUBRIC_IDS.flatMap((rid) => D.labels.map((r, i) => ({ id: r.id, rid, j: judgeV('after', rid)[i], h: r.labels[rid] }))
      .filter((x) => x.j !== x.h));
    expect(dis.map((x) => [x.id, x.rid]).sort()).toEqual([['r003', 'addresses_issue'], ['r012', 'tone'], ['r022', 'addresses_issue']]);
    expect(dis.every((x) => x.j === false && x.h === true)).toBe(true);
    expect(l).toContain('`r022`, `addresses_issue`');
    expect(l).toContain('`r003`, `addresses_issue`');
    expect(l).toContain('`r012`, `tone`');
    const i029 = D.labels.findIndex((r) => r.id === 'r029');
    expect(D.labels[i029].labels.no_false_promises).toBe(true);
    expect(l).toContain('the judge fails `r029`, disagreeing with the label');
  });
  it('the flip (judge only): one in 90, r029 on no_false_promises; re-judging all 60 about $1.42', () => {
    const flips = RUBRIC_IDS.map((rid) => positionFlips(judgeV('before', rid), judgeV('after', rid)));
    expect(flips).toEqual([0, 0, 1]);
    expect(l).toContain('Here there is one in 90: `r029` on `no_false_promises`');
    expect(l).toContain('the flip count, 1 of 30 for `no_false_promises`, is the same either way');
    expect(usd(estimate('judge', OPUS, 60))).toBe('$1.42');
    expect(l).toContain('would make 60 Opus calls, which the lab estimates at about $1.42');
  });
});

describe('lesson 7 (prompt versions): the numbers it states', () => {
  const l = flat('07-prompt');
  it('v1_basic is 124 characters, v2_rules 3,746', () => {
    expect([...D.prompts.triage.v1_basic].length).toBe(124);
    expect([...D.prompts.triage.v2_rules].length).toBe(3746);
    expect(l).toContain('`v1_basic` is one sentence, 124 characters');
    expect(l).toContain('`v2_rules` is 3,746 characters');
  });
  it('v1_basic to v2_rules on dev: 70.0% to 97.5%, +27.5 points, 11 fixed and none broken; five of the six kept-label tickets', () => {
    const c = compare(gradesOn(haiku('v1_basic'), dev), gradesOn(haiku('v2_rules'), dev));
    expect([pct(c.a), pct(c.b), (c.diff * 100).toFixed(1), pct(c.band)]).toEqual(['70.0%', '97.5%', '27.5', '15.8%']);
    expect(c.fixed).toEqual(['t001', 't003', 't007', 't012', 't015', 't028', 't029', 't030', 't033', 't038', 't039']);
    expect(c.broken).toEqual([]);
    expect(l).toContain('`v1_basic` to `v2_rules` on dev goes from 70.0% to 97.5%, a gain of 27.5 points');
    expect(l).toContain('`v2_rules` fixed 11 dev tickets and broke none: `t001`, `t003`, `t007`, `t012`, `t015`, `t028`, `t029`, `t030`, `t033`, `t038` and `t039`');
    const six = ['t007', 't012', 't015', 't029', 't034', 't039'];
    expect(six.filter((id) => c.fixed.includes(id))).toHaveLength(5);
    expect(l).toContain('`v2_rules` fixes five of them. The sixth, `t034`, is still wrong');
    expect(l).toContain('the fixed list holds the 11 tickets');
  });
  it('v2_rules to v3_examples: +0.0, nothing fixed or broken; 1,780 vs 1,543 input tokens on dev rep 0, about 13% (237)', () => {
    const c = compare(gradesOn(haiku('v2_rules'), dev), gradesOn(haiku('v3_examples'), dev));
    expect([(c.diff * 100).toFixed(1), c.fixed, c.broken]).toEqual(['0.0', [], []]);
    expect(l).toContain('`v2_rules` to `v3_examples` on dev is +0.0. Both score 97.5%, and no ticket is fixed or broken');
    const v3 = mean(linesOn(haiku('v3_examples'), dev).map((x) => x.input_tokens));
    const v2 = mean(linesOn(haiku('v2_rules'), dev).map((x) => x.input_tokens));
    expect([Math.round(v3), Math.round(v2), Math.round(v3 - v2)]).toEqual([1780, 1543, 237]);
    const dollars = (p: string) => sum(linesOn(haiku(p), dev).map((x) => callCost(HAIKU, x.input_tokens, x.output_tokens)));
    expect(Math.round((dollars('v3_examples') / dollars('v2_rules') - 1) * 100)).toBe(13);   // the cost per ticket, not the input
    expect(l).toContain('`v3_examples` sends about 1,780 input tokens a call where `v2_rules` sends about 1,543, so every ticket costs about 13% more');
  });
  it('test: v2_rules 95.0% both, 100.0% category, 95.0% priority, ±22.4%, the miss t047 (low answered normal); v1_basic 55.0%', () => {
    const r = report(gradesOn(haiku('v2_rules'), test));
    expect([pct(r.both), pct(r.category), pct(r.priority), pct(r.band)]).toEqual(['95.0%', '100.0%', '95.0%', '22.4%']);
    const miss = linesOn(haiku('v2_rules'), test).filter((x) => (x.output as TriageOutput).priority !== byId.get(x.case_id)!.priority);
    expect(miss.map((x) => [x.case_id, byId.get(x.case_id)!.priority, (x.output as TriageOutput).priority])).toEqual([['t047', 'low', 'normal']]);
    expect(l).toContain('On test, `v2_rules` scores 95.0% `both`: 100.0% category, 95.0% priority, noise band ±22.4%. Its one miss is `t047`, labelled `low` and answered `normal`');
    expect(pct(both(haiku('v1_basic'), test))).toBe('55.0%');
    expect(l).toContain('`v1_basic` scores 55.0% on the same 20 tickets');
  });
  it('the judge on test: v2_rules 14 of 20, v1_basic 19; 5 pass to fail, none back; t041 is v1_basic\'s only failure; 25 points', () => {
    const v1 = new Map(judgedTest('v1_basic').map((x) => [x.id, x.v.addresses_issue]));
    const v2 = new Map(judgedTest('v2_rules').map((x) => [x.id, x.v.addresses_issue]));
    expect([[...v2.values()].filter(Boolean).length, [...v1.values()].filter(Boolean).length]).toEqual([14, 19]);
    expect(l).toContain("Lesson 5's judge passes `v2_rules`'s test replies on `addresses_issue` 14 times out of 20. On the same 20 tickets, `v1_basic`'s replies pass 19 times.");
    const ids = [...v1.keys()];
    expect(ids.filter((id) => v1.get(id) && !v2.get(id))).toHaveLength(5);
    expect(ids.filter((id) => !v1.get(id) && v2.get(id))).toHaveLength(0);
    expect(ids.filter((id) => !v1.get(id))).toEqual(['t041']);
    expect(v2.get('t041')).toBe(false);
    expect(l).toContain("5 replies went from pass to fail and none the other way. `v1_basic`'s only failure, `t041`, is one of `v2_rules`'s six");
    expect(l).toContain('It is 25 points against a band of ±22.4% on 20 replies');
  });
});

describe('lesson 8 (choosing a model): the numbers it states', () => {
  const l = flat('08-model');
  const configs: [string, string, string | null][] = [['Haiku', HAIKU, null], ['Sonnet', SONNET, 'low'],
    ['Sonnet', SONNET, 'high'], ['Opus', OPUS, 'low'], ['Opus', OPUS, 'high']];
  const name = (model: string, effort: string | null, p = 'v2_rules') => `triage__${model}__${p}__${effort ?? 'default'}`;
  const row = (model: string, effort: string | null, p = 'v2_rules') => {
    const lines = linesOn(name(model, effort, p), dev);
    return {
      both: both(name(model, effort, p), dev),
      per1000: (sum(lines.map((x) => callCost(model, x.input_tokens, x.output_tokens))) / 40) * 1000,
      out: mean(lines.map((x) => x.output_tokens)),
      input: mean(lines.map((x) => x.input_tokens)),
      wrong: wrong(gradesOn(name(model, effort, p), dev)),
    };
  };
  const rows = configs.map(([, m, e]) => row(m, e));
  it('the prices table is PRICES, and Opus is 40 times Haiku per token', () => {
    for (const [m, i, o] of [[HAIKU, '0.10', '0.50'], [SONNET, '2.00', '10.00'], [OPUS, '4.00', '20.00']]) {
      expect(lab).toContain(`"${m}": (${Number(i).toFixed(2)}, ${Number(o).toFixed(2)})`);
      expect(l).toContain(`| \`${m}\` | $${i} | $${o} |`);
    }
    expect([callCost(OPUS, 1e6, 0) / callCost(HAIKU, 1e6, 0), callCost(OPUS, 0, 1e6) / callCost(HAIKU, 0, 1e6)]).toEqual([40, 40]);
    expect(l).toContain('Opus costs 40 times what Haiku costs per token');
  });
  it('the v2_rules table: both, cost per 1,000 and output tokens per call for the five configurations', () => {
    configs.forEach(([label, , effort], i) => {
      const r = rows[i];
      expect(l).toContain(`| ${label} | ${effort ?? 'default'} | ${pct(r.both)} | ${usd(r.per1000)} | ${Math.round(r.out)} |`);
    });
    expect(rows.map((r) => Math.round(r.out))).toEqual([199, 117, 229, 158, 228]);
    expect(rows.every((r) => Math.round(r.input) === 1543)).toBe(true);
    expect(l).toContain('Every call sent about 1,543 input tokens');
  });
  it('the ratios: about 42 times, 12.5 points, roughly double, 44% more, about 37 over Opus low', () => {
    expect(Math.floor(rows[4].per1000 / rows[0].per1000)).toBe(42);
    expect(l).toContain('Haiku gets 97.5% for $0.25 per 1,000 tickets. Opus at high effort gets 95.0% for $10.73, about 42 times as much');
    expect(((rows[0].both - rows[1].both) * 100).toFixed(1)).toBe('12.5');
    expect(l).toContain('Even Sonnet low, 12.5 points below Haiku');
    expect((rows[2].out / rows[1].out).toFixed(1)).toBe('2.0');
    expect(((rows[2].both - rows[1].both) * 100).toFixed(1)).toBe('2.5');
    expect(l).toContain("roughly doubled Sonnet's output tokens and bought 2.5 points");
    expect(Math.round((rows[4].out / rows[3].out - 1) * 100)).toBe(44);
    expect(l).toContain('Opus used about 44% more output tokens at `high` and scored the same');
    expect(Math.round(rows[3].per1000 / rows[0].per1000)).toBe(37);
    expect(l).toContain('by a factor of about 37 over the next one, Opus low');
  });
  it('the misses: Haiku t034, both Opus runs t019 and t033, Sonnet low six including t015 and t039', () => {
    expect(rows[0].wrong).toEqual(['t034']);
    expect(rows[3].wrong).toEqual(['t019', 't033']);
    expect(rows[4].wrong).toEqual(['t019', 't033']);
    expect(rows[1].wrong).toHaveLength(6);
    expect(rows[1].wrong).toEqual(expect.arrayContaining(['t015', 't039']));
    expect(l).toContain('Haiku misses `t034`. Both Opus runs miss `t019` and `t033`. Sonnet low misses six, among them `t015` and `t039`.');
  });
  it('v1_basic: Opus high 72.5%, Haiku 70.0% at about $0.13 per 1,000; Haiku test 95.0%', () => {
    expect(pct(row(OPUS, 'high', 'v1_basic').both)).toBe('72.5%');
    const h = row(HAIKU, null, 'v1_basic');
    expect([pct(h.both), usd(h.per1000)]).toEqual(['70.0%', '$0.13']);
    expect(l).toContain('With `v1_basic`, the order changes: Opus high scores 72.5% and Haiku 70.0%');
    expect(l).toContain('the most accurate is Opus high at 72.5%. At a 70% target, Haiku with `v1_basic` clears it at about $0.13 per 1,000 tickets');
    expect(pct(both(haiku('v2_rules'), test))).toBe('95.0%');
    expect(l).toContain("Haiku's one test number, 95.0% (lesson 7)");
  });
  it('medium effort estimates: 40 Sonnet calls $0.35 / $0.87, Opus $0.70 / $1.74', () => {
    expect([usd(estimate('triage', SONNET, 40)), usd(worst('triage', SONNET, 40))]).toEqual(['$0.35', '$0.87']);
    expect([usd(estimate('triage', OPUS, 40)), usd(worst('triage', OPUS, 40))]).toEqual(['$0.70', '$1.74']);
  });
});

describe('lesson 9 (LoRA): the numbers it states', () => {
  const l = flat('09-lora');
  const lora = src('llms/finetune/lora.py');
  it('d = 896, r = 8: 14,336 of 802,816 (1.79%); r = 1 1,792 (0.22%); r = 64 114,688 (14.29%)', () => {
    for (const [r, n, p] of [[8, '14,336', '1.79'], [1, '1,792', '0.22'], [64, '114,688', '14.29']] as const) {
      const c = loraParams(LORA_D, r);
      expect([commas(c.trainable), commas(c.full), ((c.trainable / c.full) * 100).toFixed(2)]).toEqual([n, '802,816', p]);
    }
    expect(l).toContain('With `r = 8`, that is 14,336 trained numbers against 802,816 in `W`: 1.79%');
    expect(l).toContain('At `r = 8`, `A` and `B` are thin strips beside `W`: 14,336 numbers, 1.79%. At `r = 1` they are 1,792 numbers, 0.22%. At `r = 64` they are 114,688, 14.29%');
    expect(lora).toContain('LORA_R = 8 ');
    expect(lora).toContain('LORA_ALPHA = 16 ');
  });
  it('15 steps: 40 tickets, batch 4, accumulate 2, 3 epochs; 32 new tokens; 512-token cut', () => {
    expect(lora).toContain('EPOCHS = 3\n');
    expect(lora).toContain('per_device_train_batch_size=4, gradient_accumulation_steps=2');
    expect((40 / (4 * 2)) * 3).toBe(15);
    expect(l).toContain('40 tickets, in batches of 4 with 2 batches added up per step, is 5 steps an epoch. Three epochs make 15 steps.');
    expect(lora).toContain('MAX_NEW_TOKENS = 32 ');
    expect(lora).toContain('[:512]');
  });
  it('Haiku: 97.5% dev and 95.0% test with v2_rules, 19 and 11 of 20 on test, ±22.4%, $0.25 per 1,000', () => {
    const r2 = gradesOn(haiku('v2_rules'), test);
    const r1 = gradesOn(haiku('v1_basic'), test);
    expect([r2.filter((g) => g.both_ok).length, r1.filter((g) => g.both_ok).length]).toEqual([19, 11]);
    expect(l).toContain('Haiku with `v2_rules` gets 19 of the 20 (95.0%) and `v1_basic` gets 11 (55.0%)');
    expect(l).toContain('`v2_rules` on Haiku gets 97.5% on dev and 95.0% on test (lesson 7), for about $0.25 per 1,000 tickets');
    expect(pct(noiseBand(20))).toBe('22.4%');
  });
  it('the format tests: 9 of them', () => {
    const n = [...src('llms/finetune/test_format.py').matchAll(/^def test_/gm)].length;
    expect(n).toBe(9);
    expect(read('09-lora')).toContain(`\`\`\`text\n${n} passed\n\`\`\``);
  });
});

describe('lesson 10 (the capstone): the numbers it states', () => {
  const l = flat('10-capstone');
  it('the gate arithmetic: 0.975 - 0.158 = 0.817; 90.0% against 97.5% is 7.5 points; ±15.6% for 41', () => {
    const b = both(haiku('v2_rules'), dev);
    expect((b - noiseBand(40)).toFixed(3)).toBe('0.817');
    expect(l).toContain('0.975 − 0.158 = 0.817, and 0.700 is below that');
    expect(l).toContain('a candidate at 90.0% against a baseline at 97.5% is 7.5 points worse and still passes');
    expect(pct(noiseBand(41))).toBe('15.6%');
    expect(l).toContain('The band is now `1/√41`, ±15.6%');
    expect(usd(estimate('triage', HAIKU, 1), 4)).toBe('$0.0004');
    expect(l).toContain('so only `t061` is paid for, about `$0.0004`');
    expect(l).toContain("passed the judge's `addresses_issue` 14 times out of 20 against 19");
  });
  it('the files: Python about 850 lines, Go about 1,400', () => {
    const py = lab.split('\n').length - 1;
    const go = src('llms/go/main.go').split('\n').length - 1;
    expect(Math.abs(py - 850)).toBeLessThanOrEqual(25);
    expect(Math.abs(go - 1400)).toBeLessThanOrEqual(50);
    expect(l).toContain('The Python file is about 850 lines');
    expect(l).toContain('The Go file is about 1,400 lines');
  });
});

describe('lesson 11 (Inspect): the numbers it states', () => {
  const l = flat('11-framework');
  type Row = { metric: string; lab: number; inspect: number; stderr: number; n: number; match: boolean };
  const summary = JSON.parse(src('llms/data/inspect_summary.json')) as { inspect_version: string; runs: { id: string; rows: Row[] }[] };
  const rowOf = (id: string, metric: string) => summary.runs.find((r) => r.id === id)!.rows.find((r) => r.metric === metric)!;
  it('the scores: 0.9 / 0.7333, 1.0 / 0.9611, 1.0 / 0.9667; the judge 0.7, 0.95, 0.95; stderr 0.0239; all nine match', () => {
    const v = (id: string, m: string) => String(rowOf(id, m).inspect);
    expect(l).toContain(`\`v1_basic\` ${v('v1_basic', 'category')} category and ${v('v1_basic', 'priority')} priority, \`v2_rules\` ${rowOf('v2_rules', 'category').inspect.toFixed(1)} and ${v('v2_rules', 'priority')}, \`v3_examples\` ${rowOf('v3_examples', 'category').inspect.toFixed(1)} and ${v('v3_examples', 'priority')}`);
    expect([v('v1_basic', 'category'), v('v1_basic', 'priority'), v('v2_rules', 'priority'), v('v3_examples', 'priority')]).toEqual(['0.9', '0.7333', '0.9611', '0.9667']);
    expect(RUBRIC_IDS.map((rid) => rowOf('judge', rid).inspect)).toEqual([0.7, 0.95, 0.95]);
    expect(l).toContain('`addresses_issue` 0.7, `tone` 0.95 and `no_false_promises` 0.95');
    const p = rowOf('v2_rules', 'priority');
    expect([p.lab, p.inspect, p.stderr]).toEqual([0.9611, 0.9611, 0.0239]);
    expect(l).toContain('scores 0.9611 on priority in both the lab and Inspect, with a standard error of 0.0239');
    expect(read('11-framework')).toContain('Inspect reports the same priority score as the lab, 0.9611 for v2_rules on all 60 tickets');
    const rows = summary.runs.flatMap((r) => r.rows);
    expect([rows.length, rows.every((r) => r.match)]).toEqual([9, true]);
    expect(l).toContain('All nine rows match');
    expect(Math.round(rowOf('judge', 'addresses_issue').inspect * 20)).toBe(14);
    expect(l).toContain('passes `addresses_issue` on 0.7 of 20 replies, 14 of 20, in both');
    expect(summary.inspect_version).toBe('0.3.285');
    expect(l).toContain('Inspect AI, version 0.3.285');
  });
  it('the live estimate: 180 Haiku calls at $0.00044 = $0.0792 (worst $0.20); the judged run about $0.47 (worst $0.91)', () => {
    expect([usd(estimate('triage', HAIKU, 180), 4), usd(worst('triage', HAIKU, 180))]).toEqual(['$0.0792', '$0.20']);
    expect(l).toContain('60 tickets × 3 = 180 Haiku calls. At 900 input and 700 output tokens a call and $0.10 / $0.50 per million, that is 180 × $0.00044 = $0.0792, about $0.08 (worst case $0.20');
    expect([usd(estimate('judge', OPUS, 20)), usd(worst('judge', OPUS, 20))]).toEqual(['$0.47', '$0.91']);
    expect(l).toContain('The judged run adds 20 Opus grader calls, about $0.47 (worst case $0.91)');
  });
});
