import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  AGREEMENT_WINDOW,
  COEF_SLICE_SIZE,
  explorerModel,
  forExplorer,
  labelAgreement,
  loadMlData,
  roundSig,
  productionVersion,
  scoreText,
  tfidf,
  tokenize,
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

describe('explorerModel', () => {
  const influence = (j: number) => Math.max(...V1.coef.map((row) => Math.abs(row[j])));

  it('keeps the full vocabulary and idf, and the weights of the most influential words', () => {
    const e = explorerModel(V1, 3);
    const top = V1.vocabulary
      .map((_, j) => j)
      .sort((a, b) => influence(b) - influence(a) || a - b)
      .slice(0, 3)
      .sort((a, b) => a - b);
    expect(e.vocabulary).toBe(V1.vocabulary);
    expect(e.idf).toBe(V1.idf);
    expect(e.coef_terms).toEqual(top);
    expect(e.coef).toEqual(V1.coef.map((row) => top.map((j) => row[j])));
    expect(e).toMatchObject({ version: 'v1', slice_size: 3, full_size: V1.vocabulary.length, classes: V1.classes });
  });

  it('breaks ties by vocabulary order', () => {
    const m: ModelJson = { ...V1, vocabulary: ['a', 'b', 'c'], idf: [1, 1, 1], coef: V1.coef.map(() => [0.5, 1, -1]) };
    expect(explorerModel(m, 1).coef_terms).toEqual([1]);
  });

  it('scores exactly like the full model when it keeps every word', () => {
    const e = explorerModel(V1, V1.vocabulary.length);
    for (const c of PREDICTIONS.cases) expect(scoreText(e, c.input)).toEqual(scoreText(V1, c.input));
  });

  it('gives a word with no kept weight nothing, but keeps it in the vector', () => {
    const e = explorerModel(V1, 10);
    const f = tfidf(e, 'the bank');
    expect(f.terms.map((t) => t.term)).toEqual(['bank', 'the']);
    expect(f).toEqual(tfidf(V1, 'the bank'));
  });
});

describe('forExplorer', () => {
  it('rounds idf, weights and intercepts to 4 significant figures, and nothing else', () => {
    const e = explorerModel(V1, 50);
    const r = forExplorer(e);
    expect(r.vocabulary).toBe(e.vocabulary);
    expect(r.coef_terms).toBe(e.coef_terms);
    expect(r.idf).toEqual(e.idf.map((x) => roundSig(x, 4)));
    expect(r.coef).toEqual(e.coef.map((row) => row.map((x) => roundSig(x, 4))));
    expect(r.intercept).toEqual(e.intercept.map((x) => roundSig(x, 4)));
    expect(forExplorer(e, 2).idf[0]).toBe(roundSig(e.idf[0], 2));
  });

  it('roundSig keeps significant figures, not decimals', () => {
    expect(roundSig(3.14159, 4)).toBe(3.142);
    expect(roundSig(-0.000123456, 4)).toBe(-0.0001235);
    expect(roundSig(12345.6, 4)).toBe(12350);
    expect(roundSig(0, 4)).toBe(0);
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

  // The feature explorer's data, as an island receives it (forExplorer), against the full model.
  const complaints: { date: string; narrative: string }[] = readFileSync(join(ml, 'data/complaints.jsonl'), 'utf8')
    .split('\n')
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l));
  const unseen = complaints
    .filter((r) => r.date >= AGREEMENT_WINDOW.from && r.date <= AGREEMENT_WINDOW.to)
    .map((r) => r.narrative);
  const shipped = forExplorer(data.explorer);

  it('builds the explorer from the production model: every word and idf, weights for 1,200', () => {
    const prod: ModelJson = readJson(registry.versions[data.production].path);
    expect(COEF_SLICE_SIZE).toBe(1200);
    expect(data.explorer.vocabulary).toEqual(prod.vocabulary);
    expect(data.explorer.idf).toEqual(prod.idf);
    const { agreement, note, ...rest } = data.explorer;
    const { agreement: _a, note: _n, ...expected } = explorerModel(prod, 1200);
    expect(rest).toEqual(expected);
    expect(data.explorer.slice_size).toBe(1200);
    expect(data.explorer.full_size).toBe(5539);
    expect(agreement.n).toBe(1800);
    expect(note).toBe(
      `Weights for the 1,200 most influential of v1's 5,539 words; the label matches the full model on ${(agreement.share * 100).toFixed(1)}% of 1,800 complaints from 2022–2024.`,
    );
  });

  it('gives the shipped explorer the server\'s exact tokens, unknown words and TF-IDF weights', () => {
    expect(unseen).toHaveLength(1800);
    let worst = 0;
    for (const text of unseen) {
      const got = tfidf(shipped, text);
      const want = tfidf(V1, text); // exact: scoreText(V1) matches train.score to 1e-9 above
      expect(got.tokens).toEqual(want.tokens);
      expect(got.unknown).toEqual(want.unknown);
      expect(got.terms.map((t) => [t.index, t.count])).toEqual(want.terms.map((t) => [t.index, t.count]));
      got.terms.forEach((t, i) => (worst = Math.max(worst, Math.abs(t.weight - want.terms[i].weight))));
    }
    expect(worst).toBeLessThan(5e-4); // idf at 4 significant figures
  });

  it('labels at least 95% of unseen complaints like the full model, and records the share', () => {
    const share = labelAgreement(shipped, V1, unseen);
    expect(data.explorer.agreement).toEqual({ share, n: 1800, on: 'complaints sent 2022-01-01 to 2024-12-31' });
    expect(share).toBeGreaterThanOrEqual(0.95);
  });

  it('keeps the island prop small, and the model weights out of the rest of MlData', () => {
    expect(JSON.stringify(shipped).length).toBeLessThanOrEqual(155_000);
    const { explorer: _e, ...rest } = data;
    expect(JSON.stringify(rest)).not.toContain('"coef"');
  });

});

describe('mlExplore.ts', () => {
  it('imports nothing, so an island that imports it bundles no data', () => {
    const src = readFileSync(join(here, '../src/lib/mlExplore.ts'), 'utf8');
    expect(src).not.toMatch(/^\s*import\b|import\.meta|\brequire\(/m);
  });
});
