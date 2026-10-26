import { readdirSync, readFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  accuracy,
  agreement,
  buildLlmsData,
  callCost,
  canonicalJson,
  cohenKappa,
  compare,
  confusionMatrix,
  gate,
  grade,
  gradeRun,
  judgeSystem,
  loadLlmsData,
  noiseBand,
  positionFlips,
  report,
  RUBRIC_IDS,
  runName,
  runReport,
  verdicts,
  type Grade,
  type LlmsData,
  type Run,
} from '../src/lib/llmsData';
import { cacheKey, judgeKey, triageKey } from '../src/lib/llmsKey';

// Fixtures are read with node:fs (ruling R6): the app loader never sees them.
const here = dirname(fileURLToPath(import.meta.url));
const fixtures = join(here, '../../llms/data/fixtures');
const read = (p: string) => readFileSync(join(fixtures, p), 'utf8');
const EXPECTED = JSON.parse(read('expected_scores.json'));

function fixtureResponses(): Record<string, string> {
  const dir = join(fixtures, 'responses');
  return Object.fromEntries(
    readdirSync(dir)
      .filter((f) => f.endsWith('.jsonl'))
      .map((f) => [`responses/${f}`, readFileSync(join(dir, f), 'utf8')]),
  );
}

const data: LlmsData = buildLlmsData({
  tickets: read('tickets.jsonl'),
  labels: read('reply_labels.jsonl'),
  prompts: read('prompts.json'),
  responses: fixtureResponses(),
});
const byId = new Map(data.tickets.map((t) => [t.id, t]));
const V1 = 'triage__claude-haiku-5-5__v1_basic__default';
const V2 = 'triage__claude-haiku-5-5__v2_rules__default';
const run = (name: string): Run => {
  const r = data.runs.find((x) => runName(x.config) === name);
  if (!r) throw new Error(`no run ${name}`);
  return r;
};
const fake = (id: string, ok: boolean): Grade => ({
  case_id: id,
  category_ok: ok,
  priority_ok: ok,
  both_ok: ok,
  outcome: 'ok',
});

describe('cache key', () => {
  it('reproduces the shared key vector (canonical JSON and sha256)', () => {
    const v = EXPECTED.key_vector;
    const req = {
      kind: v.kind,
      model: v.model,
      effort: v.effort,
      prompt_version: v.prompt_version,
      prompt_text: v.prompt_text,
      input: v.input,
      rep: v.rep,
    };
    expect(canonicalJson(req)).toBe(v.canonical);
    expect(cacheKey(req)).toBe(v.key);
  });

  it('matches every fixture triage line key', () => {
    for (const r of data.runs.filter((x) => x.config.kind === 'triage')) {
      for (const l of r.lines) {
        const t = byId.get(l.case_id)!;
        const { model, effort, prompt_version, rep } = l.config;
        expect(triageKey(t.text, prompt_version, model, effort, rep, data.prompts)).toBe(l.key);
      }
    }
  });

  it('canonical JSON sorts nested keys and escapes like Python', () => {
    expect(canonicalJson({ b: 1, a: { d: 'x', c: null } })).toBe('{"a":{"c":null,"d":"x"},"b":1}');
    expect(canonicalJson({ s: 'é <a> "q" \\ \n /' })).toBe('{"s":"é <a> \\"q\\" \\\\ \\n /"}');
  });
});

describe('loading', () => {
  it('builds one run per response file, recorded when any exist', () => {
    expect(data.recorded).toBe(true);
    expect(data.runs.map((r) => runName(r.config)).sort()).toEqual(
      Object.keys(fixtureResponses()).map((p) => basename(p, '.jsonl')).sort(),
    );
    expect(data.tickets).toHaveLength(8);
    expect(data.labels).toHaveLength(4);
    expect(Object.keys(data.prompts.triage)).toEqual(['v1_basic', 'v2_rules', 'v3_examples']);
  });

  it('is not recorded with an empty response set', () => {
    const empty = buildLlmsData({
      tickets: read('tickets.jsonl'),
      labels: read('reply_labels.jsonl'),
      prompts: read('prompts.json'),
      responses: {},
    });
    expect(empty.recorded).toBe(false);
    expect(empty.runs).toEqual([]);
    expect(empty.tickets).toHaveLength(8);
  });

  it('is not recorded when the response files are empty', () => {
    const blank = buildLlmsData({
      tickets: read('tickets.jsonl'),
      labels: read('reply_labels.jsonl'),
      prompts: read('prompts.json'),
      responses: { 'responses/triage__claude-haiku-5-5__v1_basic__default.jsonl': '\n' },
    });
    expect(blank.recorded).toBe(false);
    expect(blank.runs).toEqual([]);
  });

  it('keeps the latest line for a key (replay semantics)', () => {
    const l1 = { key: 'k1', config: { kind: 'triage', model: 'm', effort: null, prompt_version: 'p', rep: 0 }, case_id: 'a', output: null, stop_reason: null, input_tokens: 0, output_tokens: 0, latency_ms: 0, error: 'boom' };
    const l2 = { ...l1, key: 'k2', case_id: 'b', error: null, stop_reason: 'end_turn' };
    const l1b = { ...l1, error: null, stop_reason: 'end_turn' };
    const d = buildLlmsData({
      tickets: '',
      labels: '',
      prompts: read('prompts.json'),
      responses: { 'responses/triage__m__p__default.jsonl': [l1, l2, l1b].map((l) => JSON.stringify(l)).join('\n') },
    });
    expect(d.runs).toHaveLength(1);
    expect(d.runs[0].lines.map((l) => [l.key, l.error])).toEqual([
      ['k1', null],
      ['k2', null],
    ]);
  });

  it('names a bad line by file and line number', () => {
    expect(() =>
      buildLlmsData({ tickets: '{"id":1}\nnot json', labels: '', prompts: read('prompts.json'), responses: {} }),
    ).toThrow(/tickets\.jsonl:2/);
  });

  it('loads the real data, never the fixtures', () => {
    const real = loadLlmsData();
    expect(real.recorded).toBe(true);
    expect(real.tickets).toHaveLength(60);
    expect(real.tickets.every((t) => !t.id.startsWith('f'))).toBe(true);
    expect(real.labels).toHaveLength(30);
    const cases = real.runs.flatMap((r) => r.lines.map((l) => l.case_id));
    expect(cases.some((c) => /^f\d/.test(c))).toBe(false);
    expect(real.runs.map((r) => runName(r.config))).toContain(V2);
    // Each run's lines all share the run's config (one file per configuration).
    for (const r of real.runs) {
      for (const l of r.lines) {
        expect(runName(l.config)).toBe(runName(r.config));
      }
    }
  });
});

describe('scores match expected_scores.json', () => {
  it.each([V1, V2])('%s report', (name) => {
    const exp = EXPECTED[name];
    const r = runReport(run(name), data.tickets);
    expect(r.n).toBe(exp.n);
    expect(r.errors).toBe(exp.errors);
    expect(r.scored).toBe(exp.scored);
    expect(r.reps).toBe(exp.reps);
    for (const f of ['category', 'priority', 'both', 'band'] as const) expect(r[f]).toBeCloseTo(exp[f], 12);
  });

  it('v1_basic category confusion matrix', () => {
    const pairs = run(V1)
      .lines.filter((l) => !l.error)
      .map((l) => [byId.get(l.case_id)!.category, (l.output as { category?: string } | null)?.category ?? null] as [string, string | null]);
    expect(confusionMatrix(pairs)).toEqual(EXPECTED.v1_basic_confusion_category);
  });

  it('compare v1 -> v2 on rep-0 grades', () => {
    const exp = EXPECTED.compare_v1_v2;
    const c = compare(gradeRun(run(V1), data.tickets), gradeRun(run(V2), data.tickets, 0));
    expect(c.a).toBeCloseTo(exp.a, 12);
    expect(c.b).toBeCloseTo(exp.b, 12);
    expect(c.diff).toBeCloseTo(exp.diff, 12);
    expect(c.band).toBeCloseTo(exp.band, 12);
    expect(c.fixed).toEqual(exp.fixed);
    expect(c.broken).toEqual(exp.broken);
    expect(c.significant).toBe(exp.significant);
  });

  it('agreement, kappa and position flips (judge lines selected by key)', () => {
    const judgeRun = (order: string) => data.runs.find((r) => r.config.kind === 'judge' && r.config.prompt_version === order)!;
    const pick = (order: string) => {
      const lines = new Map(judgeRun(order).lines.map((l) => [l.key, l]));
      return data.labels.map((lab) => {
        const key = judgeKey(byId.get(lab.ticket_id)!.text, lab.reply, order, 'claude-opus-5-5', data.prompts);
        const line = lines.get(key);
        if (!line) throw new Error(`no judge line for ${lab.id} (${order})`);
        return verdicts(line);
      });
    };
    const before = pick('before');
    const after = pick('after');
    for (const c of RUBRIC_IDS) {
      const judgeV = after.map((v) => v[c]);
      const humanV = data.labels.map((l) => l.labels[c]);
      expect(agreement(judgeV, humanV, c)).toBeCloseTo(EXPECTED.agreement[c], 12);
      expect(cohenKappa(judgeV.map(Boolean), humanV)).toBeCloseTo(EXPECTED.kappa[c], 12);
      expect(positionFlips(before.map((v) => v[c]), judgeV)).toBe(EXPECTED.position_flips[c]);
    }
  });
});

describe('definitions (mirror triage_lab.py)', () => {
  it('grade: non-ok outcomes are never correct', () => {
    const t = { id: 'x', text: '', category: 'bug', priority: 'high', tags: [], split: 'dev' };
    expect(grade(t, { output: { category: 'bug', priority: 'low', reply: 'r' }, stop_reason: 'end_turn', error: null })).toEqual({
      case_id: 'x',
      category_ok: true,
      priority_ok: false,
      both_ok: false,
      outcome: 'ok',
    });
    for (const [line, outcome] of [
      [{ output: null, stop_reason: 'refusal', error: null }, 'refusal'],
      [{ output: null, stop_reason: 'max_tokens', error: null }, 'max_tokens'],
      [{ output: { category: 'bug', priority: 'high' }, stop_reason: null, error: 'boom' }, 'error'],
    ] as const) {
      const g = grade(t, line);
      expect(g.outcome).toBe(outcome);
      expect(g.category_ok || g.priority_ok || g.both_ok).toBe(false);
    }
  });

  it('accuracy leaves errors out and counts refusals as wrong', () => {
    const grades = gradeRun(run(V1), data.tickets);
    expect(grades).toHaveLength(8);
    expect(accuracy(grades, 'both_ok')).toBeCloseTo(3 / 7, 12);
    expect(accuracy(grades.filter((g) => g.outcome !== 'refusal'), 'both_ok')).toBeCloseTo(3 / 6, 12);
    expect(accuracy([], 'both_ok')).toBe(0);
  });

  it('noise band is 1/sqrt(scored) and needs scored lines (R9)', () => {
    expect(noiseBand(16)).toBe(0.25);
    expect(() => noiseBand(0)).toThrow(/no scored cases/);
    expect(() => report([{ ...fake('a', false), outcome: 'error' }])).toThrow(/no scored cases/);
  });

  it('compare rejects duplicate case ids and uses the smaller scored count (R10)', () => {
    expect(() => compare(gradeRun(run(V2), data.tickets), gradeRun(run(V2), data.tickets, 0))).toThrow(/one grade per case/);
    const a = [fake('a', false), fake('b', true), { ...fake('c', false), outcome: 'error' as const }, fake('z', false)];
    const b = [fake('a', true), fake('b', false), fake('c', true), fake('d', true)];
    const c = compare(a, b);
    expect(c.band).toBeCloseTo(1 / Math.sqrt(3), 12);
    expect(c.fixed).toEqual(['a', 'c']); // the error side counts as not correct; d and z are one-sided
    expect(c.broken).toEqual(['b']);
    // strict: a difference equal to the band is not significant
    const four = (k: number) => Array.from({ length: 4 }, (_, i) => fake(`c${i}`, i < k));
    expect(compare(four(1), four(3)).significant).toBe(false); // diff 0.5 == band 0.5
    expect(compare(four(0), four(3)).significant).toBe(true);
  });

  it('gate fails below the minimum or more than the band below the baseline (R11)', () => {
    const hundred = (k: number) => Array.from({ length: 100 }, (_, i) => fake(`c${i}`, i < k));
    expect(gate(hundred(75), hundred(80))).toEqual({ passed: true, reasons: [] });
    const drop = gate(hundred(75), hundred(90));
    expect(drop.passed).toBe(false);
    expect(drop.reasons).toHaveLength(1);
    expect(drop.reasons[0]).toContain('baseline');
    expect(gate(hundred(50), hundred(50)).reasons[0]).toContain('minimum');
  });

  it('cohen kappa, including the degenerate pe == 1 case', () => {
    const [t, f] = [true, false];
    expect(cohenKappa([t, t, f, f], [t, t, f, f])).toBe(1);
    expect(cohenKappa([t, t, f, f], [t, f, t, f])).toBe(0);
    expect(cohenKappa([t, t, t, f], [t, t, f, f])).toBeCloseTo(0.5, 12);
    expect(cohenKappa([t, t, t, t], [t, t, t, t])).toBe(1);
    expect(cohenKappa([t, t, t, t], [f, f, f, f])).toBe(0);
    expect(() => cohenKappa([], [])).toThrow();
  });

  it('agreement counts an ungraded verdict as a disagreement', () => {
    expect(agreement([true, null, false], [true, true, false])).toBeCloseTo(2 / 3, 12);
    expect(() => agreement([true], [true, false], 'tone')).toThrow(/tone/);
    expect(positionFlips([true, null, false], [true, true, true])).toBe(2);
  });

  it('verdicts are null for a line with no output', () => {
    expect(verdicts({ output: null })).toEqual({ addresses_issue: null, tone: null, no_false_promises: null });
  });

  it('call cost uses the price table', () => {
    expect(callCost('claude-haiku-5-5', 1_000_000, 1_000_000)).toBeCloseTo(0.6, 12);
    expect(callCost('claude-opus-5-5', 1000, 500)).toBeCloseTo(0.014, 12);
    expect(() => callCost('nope', 1, 1)).toThrow(/nope/);
  });

  it('judge system prompt fills in the rubric', () => {
    const s = judgeSystem(data.prompts);
    expect(s).not.toContain('{rubric}');
    expect(s).toContain("addresses_issue: The reply addresses the customer's actual issue.");
  });

  it('run names follow the response file names', () => {
    expect(runName({ kind: 'triage', model: 'claude-opus-5-5', prompt_version: 'v2_rules', effort: 'high' })).toBe(
      'triage__claude-opus-5-5__v2_rules__high',
    );
    expect(runName({ kind: 'judge', model: 'claude-opus-5-5', prompt_version: 'after', effort: null })).toBe(
      'judge__claude-opus-5-5__after__default',
    );
  });
});
