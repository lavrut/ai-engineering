// Logic for the ML track's basics aids: ComplaintExplorer (lesson 2), FeatureExplorer (3),
// TrainingStepper (4) and MetricsView (5). Pure and browser-safe: it imports values only
// from mlExplore.ts, and only types from mlData.ts (whose globs must never reach an island).
// The *Props builders run in the .astro wrappers at build time and keep only the small
// derived data each island needs.
import { forExplorer, roundSig, scoreText, tfidf, type ExplorerModel, type Prediction, type TermWeight } from './mlExplore';
import type { ClassMetrics, Complaint, DatasetReport, DriftReport, MetricsReport, SplitMetrics, TrainingLog } from './mlData';

/** Short category names for tight spaces (matrix headers, chips); the full name is always nearby. */
export const SHORT_NAMES: Record<string, string> = {
  'Checking or savings account': 'Checking/savings',
  'Credit card': 'Credit card',
  'Credit reporting or other personal consumer reports': 'Credit reporting',
  'Debt collection': 'Debt collection',
  'Money transfer, virtual currency, or money service': 'Money transfer',
  Mortgage: 'Mortgage',
};
export const shortName = (category: string): string => SHORT_NAMES[category] ?? category;

/** 0.4086 -> "40.9%". */
export const pct = (x: number, digits = 1): string => `${(x * 100).toFixed(digits)}%`;

// ── ComplaintExplorer ───────────────────────────────────────────────────────
/** dataset.json cuts snippets to 200 characters (train.dataset_report). */
export const SNIPPET_MAX = 200;

export type SplitName = 'train' | 'test' | 'stream';
export type SplitBadge = { name: SplitName; label: string; from: string; to: string; n: number; years: string[] };
export type SnippetRow = { id: string; date: string; year: string; category: string; product_raw: string; text: string };
export type ComplaintExplorerProps = {
  n: number;
  classes: string[];
  years: string[];
  splits: SplitBadge[];
  /** Sample counts, counts[class][year]. */
  counts: number[][];
  /** Each category's real share of that year's CFPB narratives (six categories), real[class][year], 4 s.f. */
  real: number[][];
  snippets: SnippetRow[];
};

const SPLIT_ORDER: SplitName[] = ['train', 'test', 'stream'];

/** "train 2019–21", "test 2022". */
function splitLabel(name: string, from: string, to: string): string {
  const a = from.slice(0, 4);
  const b = to.slice(0, 4);
  return a === b ? `${name} ${a}` : `${name} ${a}–${b.slice(2)}`;
}

export function complaintExplorerProps(data: { dataset: DatasetReport; drift: DriftReport }): ComplaintExplorerProps {
  const { dataset, drift } = data;
  const { classes, years } = dataset;
  const splits = SPLIT_ORDER.map((name) => {
    const s = dataset.splits[name];
    return {
      name,
      label: splitLabel(name, s.from, s.to),
      from: s.from,
      to: s.to,
      n: s.n,
      years: years.filter((y) => y >= s.from.slice(0, 4) && y <= s.to.slice(0, 4)),
    };
  });
  const real = classes.map((c) =>
    years.map((y) => {
      const share = drift.real_class_shares[y]?.[c];
      if (share === undefined) throw new Error(`drift.json: no real share for ${c} in ${y}`);
      return roundSig(share, 4);
    }),
  );
  const snippets = classes.flatMap((category) =>
    (dataset.snippets[category] ?? []).map((s) => {
      if (s.text.length > SNIPPET_MAX) throw new Error(`dataset.json: snippet ${s.id} is longer than ${SNIPPET_MAX} characters`);
      return { id: s.id, date: s.date, year: s.date.slice(0, 4), category, product_raw: s.product_raw, text: s.text };
    }),
  );
  return {
    n: dataset.n,
    classes,
    years,
    splits,
    counts: classes.map((c) => years.map((y) => dataset.counts[c]?.[y] ?? 0)),
    real,
    snippets,
  };
}

export type BalanceRow = { category: string; count: number; share: number; real: number | null };

/** Each category's sample count and share, over all years (year null) or one year, with the real
 *  share for that year (null over all years: the report has real shares per year only). */
export function balanceRows(p: ComplaintExplorerProps, year: string | null): BalanceRow[] {
  const y = year === null ? -1 : p.years.indexOf(year);
  if (year !== null && y < 0) throw new Error(`no year ${year}`);
  const counts = p.counts.map((row) => (y < 0 ? row.reduce((a, b) => a + b, 0) : row[y]));
  const total = counts.reduce((a, b) => a + b, 0);
  return p.classes.map((category, i) => ({
    category,
    count: counts[i],
    share: total ? counts[i] / total : 0,
    real: y < 0 ? null : p.real[i][y],
  }));
}

/** The split a year belongs to. */
export function splitOfYear(splits: SplitBadge[], year: string): SplitBadge {
  const s = splits.find((x) => x.years.includes(year));
  if (!s) throw new Error(`no split holds ${year}`);
  return s;
}

export type SnippetFilter = { category?: string | null; year?: string | null };
export function filterSnippets(rows: SnippetRow[], f: SnippetFilter): SnippetRow[] {
  return rows.filter((r) => (!f.category || r.category === f.category) && (!f.year || r.year === f.year));
}

// ── FeatureExplorer ─────────────────────────────────────────────────────────
/** Typing pauses this long before the text is re-tokenized. */
export const DEBOUNCE_MS = 250;
/** Presets: complaints of this many characters (long enough to show TF-IDF, short enough to read). */
export const PRESET_LENGTH = { min: 200, max: 600 };

export type FeaturePreset = { id: string; date: string; category: string; text: string };
export type FeatureExplorerProps = { model: PackedExplorer; presets: FeaturePreset[] };

/** An ExplorerModel with its big arrays as strings. Astro serializes island props with a type tag
 *  per array element, which nearly doubles 6,000 words and 13,000 numbers in the page; one string
 *  per array is written once. Tokens never contain a space ([a-z0-9']), so words join on spaces. */
export type PackedExplorer = Omit<ExplorerModel, 'vocabulary' | 'idf' | 'coef_terms' | 'coef'> & {
  vocabulary: string;
  idf: string;
  coef_terms: string;
  coef: string[];
};

export function packExplorer(m: ExplorerModel): PackedExplorer {
  if (m.vocabulary.some((w) => /\s/.test(w) || !w)) throw new Error('packExplorer: a vocabulary word is empty or has a space');
  return { ...m, vocabulary: m.vocabulary.join(' '), idf: m.idf.join(','), coef_terms: m.coef_terms.join(','), coef: m.coef.map((row) => row.join(',')) };
}

export function unpackExplorer(p: PackedExplorer): ExplorerModel {
  const nums = (s: string) => (s ? s.split(',').map(Number) : []);
  return { ...p, vocabulary: p.vocabulary ? p.vocabulary.split(' ') : [], idf: nums(p.idf), coef_terms: nums(p.coef_terms), coef: p.coef.map(nums) };
}

/** One complaint per category from the window (the test split): the lowest id of PRESET_LENGTH characters. */
export function featurePresets(complaints: Complaint[], classes: string[], window: { from: string; to: string }): FeaturePreset[] {
  return classes.flatMap((category) => {
    const pick = complaints
      .filter(
        (c) =>
          c.product === category &&
          c.date >= window.from &&
          c.date <= window.to &&
          c.narrative.length >= PRESET_LENGTH.min &&
          c.narrative.length <= PRESET_LENGTH.max,
      )
      .sort((a, b) => Number(a.id) - Number(b.id))[0];
    return pick ? [{ id: pick.id, date: pick.date, category, text: pick.narrative }] : [];
  });
}

export function featureExplorerProps(data: { explorer: ExplorerModel; complaints: Complaint[]; dataset: DatasetReport }): FeatureExplorerProps {
  return {
    model: packExplorer(forExplorer(data.explorer)),
    presets: featurePresets(data.complaints, data.explorer.classes, data.dataset.splits.test),
  };
}

export type TermRow = TermWeight & { /** false: the explorer kept no class weights for this word. */ hasWeights: boolean };
export type Contribution = { term: string; value: number };
export type ClassView = { label: string; p: number; /** Words pushing towards this class most (weight x coef > 0). */ top: Contribution[] };
export type Explanation = {
  tokens: string[];
  unknown: string[];
  /** Known words, heaviest TF-IDF weight first (ties in vocabulary order). */
  terms: TermRow[];
  prediction: Prediction;
  /** Every class, most likely first (ties in class order). */
  classes: ClassView[];
};

/** Tokens, TF-IDF weights, the label, and the words that push each class up most. */
export function explain(model: ExplorerModel, text: string, topN = 3): Explanation {
  const f = tfidf(model, text);
  const prediction = scoreText(model, text);
  const col = new Map(model.coef_terms.map((j, k) => [j, k]));
  const terms = f.terms
    .map((t) => ({ ...t, hasWeights: col.has(t.index) }))
    .sort((a, b) => b.weight - a.weight || a.index - b.index);
  const classes = model.classes
    .map((label, c) => {
      const top = f.terms
        .flatMap((t) => {
          const k = col.get(t.index);
          const value = k === undefined ? 0 : model.coef[c][k] * t.weight;
          return value > 0 ? [{ term: t.term, value }] : [];
        })
        .sort((a, b) => b.value - a.value)
        .slice(0, topN);
      return { label, p: prediction.scores[label], top, order: c };
    })
    .sort((a, b) => b.p - a.p || a.order - b.order)
    .map(({ order: _o, ...rest }) => rest);
  return { tokens: f.tokens, unknown: f.unknown, terms, prediction, classes };
}

// ── TrainingStepper ─────────────────────────────────────────────────────────
export type StepperCheckpoint = {
  iteration: number;
  loss: number;
  penalty: number;
  /** loss + penalty: what lbfgs minimises. */
  objective: number;
  /** The 8 heaviest [word, weight] pairs per class (in class order), where the log recorded them. */
  top: [string, number][][] | null;
};
export type TrainingStepperProps = { classes: string[]; C: number; convergedAt: number; note: string; checkpoints: StepperCheckpoint[] };

export function trainingStepperProps(log: TrainingLog): TrainingStepperProps {
  return {
    classes: log.classes,
    C: log.C,
    convergedAt: log.converged_at,
    note: log.note,
    checkpoints: log.checkpoints.map((c) => ({
      iteration: c.iteration,
      loss: c.loss,
      penalty: c.penalty,
      objective: c.loss + c.penalty,
      top: c.top_words ? log.classes.map((k) => c.top_words![k] ?? []) : null,
    })),
  };
}

/** The top words of the latest checkpoint at or before index `at` that recorded them. */
export function recordedTopWords(cps: StepperCheckpoint[], at: number): { iteration: number; words: [string, number][][] } | null {
  for (let i = Math.min(at, cps.length - 1); i >= 0; i--) {
    const top = cps[i].top;
    if (top) return { iteration: cps[i].iteration, words: top };
  }
  return null;
}

export type ChartLine = { key: 'objective' | 'loss' | 'penalty'; points: { x: number; y: number }[] };
/** SVG coordinates for the three series, checkpoints evenly spaced along x (iteration numbers label them). */
export function lossChart(cps: StepperCheckpoint[], box: { width: number; height: number; left: number; right: number; top: number; bottom: number }) {
  const max = Math.max(...cps.map((c) => c.objective));
  const yMax = Math.ceil(max * 4) / 4; // 1.7918 -> 2
  const w = box.width - box.left - box.right;
  const h = box.height - box.top - box.bottom;
  const x = (i: number) => box.left + (cps.length > 1 ? (i / (cps.length - 1)) * w : 0);
  const y = (v: number) => box.top + h - (v / yMax) * h;
  const line = (key: ChartLine['key']): ChartLine => ({ key, points: cps.map((c, i) => ({ x: x(i), y: y(c[key]) })) });
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => ({ value: t * yMax, y: y(t * yMax) }));
  return { lines: [line('objective'), line('loss'), line('penalty')], ticks, x };
}

/** Label positions at least `gap` apart, kept in their order and as close to `ys` as a top-down pass allows. */
export function spreadLabels(ys: number[], gap: number): number[] {
  const order = ys.map((y, i) => [y, i] as const).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const out = [...ys];
  let prev = -Infinity;
  for (const [y, i] of order) {
    out[i] = Math.max(y, prev + gap);
    prev = out[i];
  }
  return out;
}

// ── MetricsView ─────────────────────────────────────────────────────────────
export type ModelId = 'majority' | 'nb' | 'logreg';
export type MetricsModel = { id: ModelId; label: string; train: SplitMetrics; test: SplitMetrics };
export type MetricsViewProps = { classes: string[]; models: MetricsModel[]; note: string };

/** The model version a report's command wrote ("--version v1" -> "v1"). */
export function versionOf(generatedBy: string): string {
  const m = /--version\s+(\S+)/.exec(generatedBy);
  if (!m) throw new Error(`no --version in "${generatedBy}"`);
  return m[1];
}

// 6 significant figures: small, and shown to 3 decimals without double rounding.
const sig = (x: number) => roundSig(x, 6);
const prf = (m: { precision: number; recall: number; f1: number }) => ({ precision: sig(m.precision), recall: sig(m.recall), f1: sig(m.f1) });
function roundSplit(s: SplitMetrics): SplitMetrics {
  return {
    classes: s.classes,
    n: s.n,
    accuracy: sig(s.accuracy),
    band: sig(s.band),
    per_class: Object.fromEntries(Object.entries(s.per_class).map(([k, v]): [string, ClassMetrics] => [k, { ...prf(v), support: v.support }])),
    macro: prf(s.macro),
    weighted: prf(s.weighted),
    confusion: s.confusion,
  };
}

/** The three models on train and test, rounded to 6 significant figures; logistic regression is
 *  named with the version the report's command exported. */
export function metricsViewProps(metrics: MetricsReport): MetricsViewProps {
  const version = versionOf(metrics.generated_by);
  const labels: Record<ModelId, string> = {
    majority: 'Majority baseline',
    nb: 'Naive Bayes',
    logreg: `Logistic regression (${version})`,
  };
  const ids: ModelId[] = ['majority', 'nb', 'logreg'];
  for (const id of ids) {
    for (const split of ['train', 'test'] as const) {
      if (metrics.models[id][split].classes.join('\n') !== metrics.classes.join('\n')) {
        throw new Error(`metrics.json: ${id} ${split} classes differ from the report's`);
      }
    }
  }
  return {
    classes: metrics.classes,
    models: ids.map((id) => ({ id, label: labels[id], train: roundSplit(metrics.models[id].train), test: roundSplit(metrics.models[id].test) })),
    note: metrics.note,
  };
}

/** How much accuracy falls from train to test, against the test split's noise band. */
export function overfit(m: MetricsModel): { train: number; test: number; drop: number; band: number; beyond: boolean } {
  const drop = m.train.accuracy - m.test.accuracy;
  return { train: m.train.accuracy, test: m.test.accuracy, drop, band: m.test.band, beyond: drop > m.test.band };
}

/** A confusion cell with its row (true class) and column (predicted class) totals. */
export function cellDetail(s: SplitMetrics, r: number, c: number) {
  const n = s.confusion[r][c];
  const rowTotal = s.confusion[r].reduce((a, b) => a + b, 0);
  const colTotal = s.confusion.reduce((a, row) => a + row[c], 0);
  return { n, rowTotal, colTotal, correct: r === c };
}
