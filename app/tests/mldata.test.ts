import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  loadMlData,
  productionVersion,
  scoreText,
  tfidf,
  tokenize,
  VOCAB_SLICE_SIZE,
  vocabSlice,
  type ModelJson,
  type Registry,
} from '../src/lib/mlData';

// Fixtures and the full model are read with node:fs here; the app loader only ever builds the slice.
const here = dirname(fileURLToPath(import.meta.url));
const ml = join(here, '../../ml');
const readJson = (p: string) => JSON.parse(readFileSync(join(ml, p), 'utf8'));
const V1: ModelJson = readJson('models/v1.json');
const TOKENIZE: { name: string; input: string; tokens: string[] }[] = readJson('data/fixtures/tokenize.json');
const PREDICTIONS: {
  cases: { name: string; input: string; expected: { label: string; scores: Record<string, number>; low_confidence: boolean } }[];
} = readJson('data/fixtures/predictions.json');

describe('tokenize (rules R12, the same as train.tokenize and Go)', () => {
  it.each(TOKENIZE.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    expect(tokenize(c.input, V1.tokenizer)).toEqual(c.tokens);
  });

  it('uses the model tokenizer settings: stop words, and the x-run length', () => {
    expect(tokenize('The bank and THE card', { ...V1.tokenizer, stop_words: ['the'] })).toEqual(['bank', 'and', 'card']);
    expect(tokenize('xx xxx', { ...V1.tokenizer, drop_x_runs_from: 3 })).toEqual(['xx']);
  });

  it('refuses a lowercase rule it does not implement', () => {
    expect(() => tokenize('a', { ...V1.tokenizer, lowercase: 'unicode' as 'ascii' })).toThrow(/ascii/);
  });
});

describe('scoreText (the same as train.score and Go)', () => {
  it.each(PREDICTIONS.cases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const got = scoreText(V1, c.input);
    expect(got.label).toBe(c.expected.label);
    expect(got.low_confidence).toBe(c.expected.low_confidence);
    expect(Object.keys(got.scores)).toEqual(Object.keys(c.expected.scores));
    for (const [k, v] of Object.entries(c.expected.scores)) expect(Math.abs(got.scores[k] - v), k).toBeLessThan(1e-9);
  });

  it('has enough pinned cases to mean something', () => {
    expect(PREDICTIONS.cases.length).toBeGreaterThanOrEqual(10);
  });
});

describe('tfidf', () => {
  it('counts known words, weighs them by idf and scales the row to length 1', () => {
    const f = tfidf(V1, 'The bank closed my bank account. Zzqx!');
    const bank = f.terms.find((t) => t.term === 'bank')!;
    expect(bank.count).toBe(2);
    expect(bank.idf).toBe(V1.idf[V1.vocabulary.indexOf('bank')]);
    expect(bank.raw).toBe(2 * bank.idf);
    expect(bank.weight).toBeCloseTo(bank.raw / f.norm, 15);
    expect(f.terms.reduce((s, t) => s + t.weight * t.weight, 0)).toBeCloseTo(1, 12);
    expect(f.terms.map((t) => t.index)).toEqual([...f.terms.map((t) => t.index)].sort((a, b) => a - b));
    expect(f.tokens).toEqual(['the', 'bank', 'closed', 'my', 'bank', 'account', 'zzqx']);
    expect(f.unknown).toEqual(['zzqx']);
  });

  it('gives an all-zero row (norm 1) when no word is known', () => {
    const f = tfidf(V1, 'zzqx blorp');
    expect(f.terms).toEqual([]);
    expect(f.norm).toBe(1);
    expect(f.unknown).toEqual(['zzqx', 'blorp']);
  });
});

describe('vocabSlice', () => {
  it('keeps the most common words (lowest idf), in vocabulary order, with their idf and weights', () => {
    const s = vocabSlice(V1, 3);
    const byIdf = V1.vocabulary.map((t, j) => [V1.idf[j], j] as const).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const keep = byIdf.slice(0, 3).map(([, j]) => j).sort((a, b) => a - b);
    expect(s.vocabulary).toEqual(keep.map((j) => V1.vocabulary[j]));
    expect(s.idf).toEqual(keep.map((j) => V1.idf[j]));
    expect(s.coef).toEqual(V1.coef.map((row) => keep.map((j) => row[j])));
    expect(s).toMatchObject({ version: 'v1', size: 3, full_size: V1.vocabulary.length, classes: V1.classes });
    expect(s.intercept).toEqual(V1.intercept);
  });

  it('breaks idf ties by vocabulary order', () => {
    const m: ModelJson = { ...V1, vocabulary: ['a', 'b', 'c'], idf: [2, 1, 1], coef: V1.coef.map(() => [0, 0, 0]) };
    expect(vocabSlice(m, 1).vocabulary).toEqual(['b']);
  });

  it('keeps the whole vocabulary when it is smaller than the slice', () => {
    expect(vocabSlice(V1, 1e6).vocabulary).toEqual(V1.vocabulary);
  });
});

describe('productionVersion', () => {
  const reg = (stages: string[]): Registry =>
    ({ versions: Object.fromEntries(stages.map((stage, i) => [`v${i + 1}`, { stage }])), history: [] }) as unknown as Registry;
  it('names the one version in production', () => {
    expect(productionVersion(reg(['archived', 'production', 'staging']))).toBe('v2');
  });
  it('fails clearly with none or two', () => {
    expect(() => productionVersion(reg(['staging']))).toThrow(/no version in production/);
    expect(() => productionVersion(reg(['production', 'production']))).toThrow(/2 versions in production/);
  });
});

describe('loadMlData (the real ml/ folder, at build time)', () => {
  const data = loadMlData();
  const registry: Registry = readJson('registry.json');

  it('loads every report, each naming the command that wrote it', () => {
    for (const r of [data.dataset, data.metrics, data.trainingLog, data.drift, data.latency]) {
      expect(r.generated_by).toMatch(/^python /);
    }
    expect(data.dataset.n).toBe(3600);
    expect(data.dataset.splits.test.n).toBe(600);
    expect(data.metrics.models.logreg.test.accuracy).toBe(registry.versions.v1.metrics.accuracy);
    expect(data.trainingLog.checkpoints[0].iteration).toBe(0);
    expect(data.drift.months).toHaveLength(24);
    expect(data.drift.quarters).toHaveLength(8);
    expect(data.latency.servers.go.p50_ms).toBeGreaterThan(0);
  });

  it('reads llm_compare.json only when the optional recording wrote it', () => {
    const path = join(ml, 'reports/llm_compare.json');
    expect(data.llmCompare).toEqual(existsSync(path) ? readJson('reports/llm_compare.json') : null);
  });

  it('states the expected chance alerts as an upper bound (union of the two signals)', () => {
    const chance = data.drift.null.expected_chance_alerts;
    expect(chance.month.per_signal).toBeCloseTo(1.2, 12);
    expect(chance.month.either_signal_max).toBeCloseTo(2.4, 12);
    expect(chance.quarter.either_signal_max).toBeCloseTo(0.8, 12);
  });

  it('loads every run log, oldest first, and the registry', () => {
    const files = readdirSync(join(ml, 'runs')).filter((f) => f.endsWith('.json'));
    expect(data.runs.map((r) => r.run_id).sort()).toEqual(files.map((f) => f.replace(/\.json$/, '')).sort());
    const order = data.runs.map((r) => `${r.time} ${r.run_id}`);
    expect(order).toEqual([...order].sort());
    expect(data.registry).toEqual(registry);
    expect(data.production).toBe(productionVersion(registry));
  });

  it(`slices the top ${VOCAB_SLICE_SIZE} words from the production model, never the whole model`, () => {
    expect(VOCAB_SLICE_SIZE).toBe(2000);
    const prod: ModelJson = readJson(registry.versions[data.production].path);
    expect(data.vocab).toEqual(vocabSlice(prod, 2000));
    expect(data.vocab.vocabulary).toHaveLength(2000);
    expect(data.vocab.full_size).toBe(prod.vocabulary.length);
    expect(data.vocab.full_size).toBeGreaterThan(2000);
    // Nothing else in MlData carries the model's vocabulary or weights.
    const { vocab: _vocab, ...rest } = data;
    expect(JSON.stringify(rest)).not.toContain('"coef"');
  });

  it('scores with the slice like the full model on most pinned cases', () => {
    const agree = PREDICTIONS.cases.filter((c) => scoreText(data.vocab, c.input).label === c.expected.label).length;
    expect(agree / PREDICTIONS.cases.length).toBeGreaterThanOrEqual(0.8);
  });
});
