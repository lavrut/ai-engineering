// Logic for the ML track's aids: the basics, ComplaintExplorer (lesson 2), FeatureExplorer (3),
// TrainingStepper (4) and MetricsView (5); and MLOps, ModelVsLlm (6), RunTable (7),
// RegistryTimeline (8) and DriftChart (10). Pure and browser-safe: it imports values only
// from mlExplore.ts, and only types from mlData.ts (whose globs must never reach an island).
// The *Props builders run in the .astro wrappers at build time and keep only the small
// derived data each island needs.
import { forExplorer, roundSig, scoreText, tfidf, type ExplorerModel, type Prediction, type TermWeight } from './mlExplore';
import type {
  ClassMetrics,
  Complaint,
  DatasetReport,
  DriftPeriod,
  DriftReport,
  GateResult,
  LatencyReport,
  LlmCompareReport,
  MetricsReport,
  Registry,
  RegistryEvent,
  RunMetrics,
  RunRecord,
  ShiftedWord,
  SplitMetrics,
  Stage,
  TrainingLog,
} from './mlData';

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

// ── ModelVsLlm ──────────────────────────────────────────────────────────────
/** What to run when reports/llm_compare.json is missing (compare_llm.py's own advice): record
 *  the paid answers once (prints the cost and asks first), then replay them into the report. */
export const LLM_COMMANDS = ['python compare_llm.py record', 'python compare_llm.py compare'];
const MODEL_NAMES: Record<string, string> = { logreg: 'logistic regression', nb: 'naive Bayes' };

export type LlmSide = {
  name: string;
  accuracy: number;
  macro_f1: number;
  /** In `classes` order. */
  per_class_f1: number[];
  /** US dollars per 1,000 complaints. */
  cost_per_1000: number;
  latency_p50_ms: number;
  latency_note: string;
};
export type LlmCompareView = {
  generated_by: string;
  n: number;
  no_answer: number;
  /** 1/√n, unrounded: the gap sentence compares against it. */
  band: number;
  split: string;
  scoring: string;
  answered_only: LlmCompareReport['answered_only'];
  classes: string[];
  v1: LlmSide;
  claude: LlmSide;
  latency_caveat: string;
};
export type ServerLatency = { p50_ms: number; p95_ms: number; command: string };
export type ModelVsLlmProps = {
  /** null until the optional recording: the aid then shows "No recorded run yet" and `commands`. */
  compare: LlmCompareView | null;
  /** reports/latency.json: both local servers, same machine, same requests. */
  servers: { python: ServerLatency; go: ServerLatency; ratio_p50: number; machine: string; requests: number; note: string };
  commands: string[];
};

export function modelVsLlmProps(data: { llmCompare: LlmCompareReport | null; latency: LatencyReport; registry: Registry }): ModelVsLlmProps {
  const { llmCompare: c, latency } = data;
  const server = (s: LatencyReport['servers']['python']): ServerLatency => ({ p50_ms: sig(s.p50_ms), p95_ms: sig(s.p95_ms), command: s.command });
  let compare: LlmCompareView | null = null;
  if (c) {
    const side = (name: string, x: LlmCompareReport['v1'] | LlmCompareReport['claude']): LlmSide => ({
      name,
      accuracy: sig(x.accuracy),
      macro_f1: sig(x.macro_f1),
      per_class_f1: c.classes.map((k) => {
        const f = x.per_class_f1[k];
        if (f === undefined) throw new Error(`llm_compare.json: no F1 for ${k} (${name})`);
        return sig(f);
      }),
      cost_per_1000: x.cost_per_1000,
      latency_p50_ms: sig(x.latency_p50_ms),
      latency_note: x.latency_note,
    });
    const kind = data.registry.versions[c.v1.version]?.model;
    compare = {
      generated_by: c.generated_by,
      n: c.n,
      no_answer: c.no_answer,
      band: c.band,
      split: c.split,
      scoring: c.scoring,
      answered_only: c.answered_only,
      classes: c.classes,
      v1: side(kind ? `${c.v1.version} (${MODEL_NAMES[kind] ?? kind})` : c.v1.version, c.v1),
      claude: side(c.claude.model, c.claude),
      latency_caveat: c.latency_caveat,
    };
  }
  return {
    compare,
    servers: {
      python: server(latency.servers.python),
      go: server(latency.servers.go),
      ratio_p50: sig(latency.ratio_python_over_go.p50),
      machine: String(latency.machine.cpu),
      requests: latency.request_set.requests_per_server,
      note: latency.note,
    },
    commands: LLM_COMMANDS,
  };
}

/** Claude's accuracy minus v1's, against the 1/√n band of the comparison. */
export function llmGap(c: LlmCompareView): { gap: number; band: number; inside: boolean; leader: 'claude' | 'v1' | 'tie' } {
  const gap = c.claude.accuracy - c.v1.accuracy;
  return { gap, band: c.band, inside: Math.abs(gap) <= c.band, leader: gap > 0 ? 'claude' : gap < 0 ? 'v1' : 'tie' };
}

// ── RunTable ────────────────────────────────────────────────────────────────
export type FlatValue = number | string | boolean | null;
export type RunRow = {
  id: string;
  /** YYYY-MM-DD (runs are dated by their data, not the clock). */
  date: string;
  time: string;
  params: Record<string, number | string>;
  train: { from: string; to: string };
  eval: { name: string; from: string; to: string };
  /** The eval split's headline numbers (6 s.f.). */
  metrics: { n: number; accuracy: number; band: number; macro_f1: number; weighted_f1: number };
  /** Every metric, flattened as mlops._flatten does ("test.per_class_f1.Mortgage"); lists joined with "; ". */
  flat: Record<string, FlatValue>;
  dataVersion: string;
  modelHash: string;
  gitCommit: string | null;
  gitDirty: boolean | null;
  /** The registry version logged from this run, if any. */
  version: string | null;
  stage: Stage | null;
  /** The run's model hash equals the registry's (null: not registered). */
  hashMatches: boolean | null;
};
export type RunTableProps = { runs: RunRow[] };

function flatten(d: Record<string, unknown>, prefix = '', out: Record<string, FlatValue> = {}): Record<string, FlatValue> {
  for (const [k, v] of Object.entries(d)) {
    if (v && typeof v === 'object' && !Array.isArray(v)) flatten(v as Record<string, unknown>, `${prefix}${k}.`, out);
    else if (Array.isArray(v)) out[`${prefix}${k}`] = v.join('; ');
    else out[`${prefix}${k}`] = typeof v === 'number' && !Number.isInteger(v) ? sig(v) : (v as FlatValue);
  }
  return out;
}

export function runTableProps(data: { runs: RunRecord[]; registry: Registry }): RunTableProps {
  const byRun = new Map(Object.entries(data.registry.versions).map(([v, e]) => [e.run, { version: v, entry: e }]));
  return {
    runs: data.runs.map((r) => {
      const m = r.metrics[r.windows.eval.name] as RunMetrics | undefined;
      if (!m || !('macro_f1' in m)) throw new Error(`runs/${r.run_id}.json: no metrics for its eval split "${r.windows.eval.name}"`);
      const reg = byRun.get(r.run_id);
      return {
        id: r.run_id,
        date: r.time.slice(0, 10),
        time: r.time,
        params: r.params,
        train: r.windows.train,
        eval: r.windows.eval,
        metrics: { n: m.n, accuracy: sig(m.accuracy), band: sig(m.band), macro_f1: sig(m.macro_f1), weighted_f1: sig(m.weighted_f1) },
        flat: flatten(r.metrics),
        dataVersion: r.data_version,
        modelHash: r.model.sha256,
        gitCommit: r.git_commit,
        gitDirty: r.git_dirty,
        version: reg?.version ?? null,
        stage: reg?.entry.stage ?? null,
        hashMatches: reg ? reg.entry.sha256 === r.model.sha256 : null,
      };
    }),
  };
}

export type SortKey = 'time' | 'accuracy' | 'macro_f1' | 'weighted_f1';
/** Newest first for time; highest first for a metric (ties by run id). */
export function sortRuns(rows: RunRow[], key: SortKey): RunRow[] {
  const byId = (a: RunRow, b: RunRow) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  return [...rows].sort((a, b) =>
    key === 'time' ? (a.time < b.time ? 1 : a.time > b.time ? -1 : -byId(a, b)) : b.metrics[key] - a.metrics[key] || byId(a, b),
  );
}

export type RunDiff = {
  params: { key: string; a: FlatValue; b: FlatValue }[];
  windows: { key: 'train' | 'eval'; a: string; b: string }[];
  /** Changed metrics; a or b is null when only one run has the key; delta when both are numbers. */
  metrics: { key: string; a: FlatValue; b: FlatValue; delta: number | null }[];
  sameEval: boolean;
  sameData: boolean;
};

/** Only what differs, as `python mlops.py compare A B` prints it, plus the windows and data version. */
export function diffRuns(a: RunRow, b: RunRow): RunDiff {
  const keys = (x: object, y: object) => [...new Set([...Object.keys(x), ...Object.keys(y)])].sort();
  const params = keys(a.params, b.params)
    .map((key) => ({ key, a: a.params[key] ?? null, b: b.params[key] ?? null }))
    .filter((d) => d.a !== d.b);
  const win = (w: { from: string; to: string }) => `${w.from} to ${w.to}`;
  const windows = [
    { key: 'train' as const, a: win(a.train), b: win(b.train) },
    { key: 'eval' as const, a: `${a.eval.name} ${win(a.eval)}`, b: `${b.eval.name} ${win(b.eval)}` },
  ].filter((d) => d.a !== d.b);
  const metrics = keys(a.flat, b.flat)
    .map((key) => {
      const x = a.flat[key] ?? null;
      const y = b.flat[key] ?? null;
      const numeric = typeof x === 'number' && typeof y === 'number';
      return { key, a: x, b: y, delta: numeric ? (y as number) - (x as number) : null };
    })
    .filter((d) => d.a !== d.b);
  const sameEval = a.eval.name === b.eval.name && a.eval.from === b.eval.from && a.eval.to === b.eval.to;
  return { params, windows, metrics, sameEval, sameData: a.dataVersion === b.dataVersion };
}

// ── RegistryTimeline ────────────────────────────────────────────────────────
export type VersionView = {
  version: string;
  stage: Stage;
  model: string;
  trained_on: { from: string; to: string };
  registered: string;
  run: string;
  sha256: string;
  metrics: { split: string; n: number; accuracy: number; macro_f1: number };
};
export type GateView = {
  version: string;
  production_version: string;
  passed: boolean;
  gain: number;
  band: number;
  floor: number;
  reasons: string[];
  candidate_macro_f1: number;
  production_macro_f1: number;
  n: number;
  split: string;
};
export type RegistryTimelineProps = { versions: VersionView[]; history: RegistryEvent[]; gate: GateView | null };

export function registryTimelineProps(data: { registry: Registry; runs: RunRecord[] }): RegistryTimelineProps {
  const { registry } = data;
  const versions = Object.keys(registry.versions)
    .sort()
    .map((version) => {
      const v = registry.versions[version];
      return {
        version,
        stage: v.stage,
        model: v.model,
        trained_on: v.trained_on,
        registered: v.registered,
        run: v.run,
        sha256: v.sha256,
        metrics: { split: v.metrics.split, n: v.metrics.n, accuracy: sig(v.metrics.accuracy), macro_f1: sig(v.metrics.macro_f1) },
      };
    });
  // The latest run that ran the gate, and the version registered from it.
  const gated = data.runs.filter((r) => r.metrics.gate).at(-1);
  let gate: GateView | null = null;
  if (gated) {
    const g = gated.metrics.gate as GateResult;
    const name = gated.windows.eval.name;
    const cand = gated.metrics[name] as RunMetrics;
    const prod = gated.metrics[`production_${name}`] as RunMetrics | undefined;
    const version = versions.find((v) => v.run === gated.run_id)?.version;
    if (!version || !prod) throw new Error(`runs/${gated.run_id}.json: the gate needs a registered version and production_${name} metrics`);
    gate = {
      version,
      production_version: g.production_version,
      passed: g.passed,
      gain: sig(g.gain),
      band: sig(g.band),
      floor: g.floor,
      reasons: g.reasons,
      candidate_macro_f1: sig(cand.macro_f1),
      production_macro_f1: sig(prod.macro_f1),
      n: cand.n,
      split: cand.split,
    };
  }
  return { versions, history: registry.history, gate };
}

/** A copy of the registry for the in-browser simulation; never written anywhere. */
export type SimEvent = RegistryEvent & { simulated: boolean };
export type SimState = { stages: Record<string, Stage>; history: SimEvent[] };
export type SimResult = { state: SimState; error?: string; restored?: string };
export const SIM_TIME = 'simulated';

export function simStart(p: RegistryTimelineProps): SimState {
  return {
    stages: Object.fromEntries(p.versions.map((v) => [v.version, v.stage])),
    history: p.history.map((h) => ({ ...h, simulated: false })),
  };
}

/** mlops._move on a copy: change one stage, append the event. */
function simMove(s: SimState, version: string, stage: Stage, action: string, note: string): SimState {
  return {
    stages: { ...s.stages, [version]: stage },
    history: [...s.history, { time: SIM_TIME, action, version, from_stage: s.stages[version], to_stage: stage, note, simulated: true }],
  };
}
const simProduction = (s: SimState) => Object.keys(s.stages).find((v) => s.stages[v] === 'production') ?? null;

/** mlops.promote(version, "production"): the current production version is archived first. */
export function simPromote(s: SimState, version: string): SimResult {
  if (!(version in s.stages)) return { state: s, error: `unknown version ${version}` };
  if (s.stages[version] === 'production') return { state: s, error: `${version} is already in production` };
  const current = simProduction(s);
  let next = current ? simMove(s, current, 'archived', 'archive', `replaced by ${version}`) : s;
  // mlops.py promote without --note writes an empty note; the simulation invents none.
  next = simMove(next, version, 'production', 'promote', '');
  return { state: next };
}

/** mlops.rollback: the last version other than the current one ever promoted to production comes back. */
export function simRollback(s: SimState): SimResult {
  const current = simProduction(s);
  if (!current) return { state: s, error: 'no production version to roll back from' };
  const earlier = s.history.filter((h) => h.to_stage === 'production' && h.version !== current).map((h) => h.version);
  if (!earlier.length) return { state: s, error: `no previous production version before ${current}` };
  const previous = earlier[earlier.length - 1];
  const next = simMove(simMove(s, current, 'archived', 'rollback', `rolled back to ${previous}`), previous, 'production', 'rollback', `replaces ${current}`);
  return { state: next, restored: previous };
}

// ── DriftChart ──────────────────────────────────────────────────────────────
export type Signal = 'predictions' | 'words';
export type DriftUnit = 'month' | 'quarter';
export type DriftPoint = {
  label: string;
  n: number;
  predictions: number;
  words: number;
  /** Per signal: PSI >= alert_at (the convention) / above that signal's calibrated threshold; n >= min_n. */
  predictions_convention: boolean;
  predictions_calibrated: boolean;
  words_convention: boolean;
  words_calibrated: boolean;
  top: ShiftedWord[];
};
export type InSamplePoint = { label: string; n: number; predictions: number; words: number; predictions_convention: boolean; words_convention: boolean };
export type NullView = { p50: number; p95: number; within_2022_max: number; threshold: number };
export type DriftUnitView = {
  periods: DriftPoint[];
  null: Record<Signal, NullView>;
  /** Null sample size (complaints per draw). */
  nullN: number;
  expected: { per_signal: number; either_signal_max: number };
  /** One fixed axis per signal, from 0, holding every value that can be shown (in-sample too) and 0.2. */
  scale: Record<Signal, { max: number; ticks: number[] }>;
};
export type DriftChartProps = {
  alertAt: number;
  minN: number;
  reference: string;
  nullReference: string;
  expectedNote: string;
  inSampleNote: string;
  units: Record<DriftUnit, DriftUnitView>;
  /** The training-reference variant (months only): shown to explain why it misleads. */
  inSample: InSamplePoint[];
  classes: string[];
  years: string[];
  /** Shares per class per year (4 s.f.): real CFPB narratives and the committed sample. */
  real: number[][];
  sample: number[][];
};

const SIGNALS: Signal[] = ['predictions', 'words'];
const TICK_STEPS = [0.01, 0.02, 0.025, 0.05, 0.1, 0.2, 0.25, 0.5, 1];
const r6 = (x: number) => Number(x.toFixed(6));

/** An axis from 0 with at most 5 steps that holds `max`. */
export function driftScale(max: number): { max: number; ticks: number[] } {
  const step = TICK_STEPS.find((s) => Math.ceil(max / s - 1e-9) <= 5) ?? 1;
  const k = Math.max(1, Math.ceil(max / step - 1e-9));
  return { max: r6(k * step), ticks: Array.from({ length: k + 1 }, (_, i) => r6(i * step)) };
}

export function driftChartProps(drift: DriftReport): DriftChartProps {
  const { alert_at: alertAt, min_n: minN } = drift.settings;
  const inSample: InSamplePoint[] = drift.reference_training_in_sample.months.map((m) => {
    const big = m.n >= minN;
    const p = { label: m.month, n: m.n, predictions: sig(m.psi_predictions), words: sig(m.psi_words), predictions_convention: big && m.psi_predictions >= alertAt, words_convention: big && m.psi_words >= alertAt };
    if ((p.predictions_convention || p.words_convention) !== m.alert_convention) throw new Error(`drift.json: in-sample ${m.month} alert disagrees with its PSI`);
    return p;
  });
  const unit = (u: DriftUnit, rows: (DriftPeriod & { label: string })[]): DriftUnitView => {
    const nb = drift.null[u];
    const nullView = (s: Signal): NullView => ({ p50: sig(nb[s].p50), p95: sig(nb[s].p95), within_2022_max: sig(nb[s].within_2022_max), threshold: sig(nb[s].threshold) });
    const periods = rows.map((r) => {
      const big = r.n >= minN;
      const p: DriftPoint = {
        label: r.label,
        n: r.n,
        predictions: sig(r.psi_predictions),
        words: sig(r.psi_words),
        predictions_convention: big && r.psi_predictions >= alertAt,
        predictions_calibrated: big && r.psi_predictions > nb.predictions.threshold,
        words_convention: big && r.psi_words >= alertAt,
        words_calibrated: big && r.psi_words > nb.words.threshold,
        top: r.top_shifted_words.map((w) => ({ word: w.word, contribution: roundSig(w.contribution, 4), direction: w.direction })),
      };
      if ((p.predictions_convention || p.words_convention) !== r.alert_convention || (p.predictions_calibrated || p.words_calibrated) !== r.alert_calibrated) {
        throw new Error(`drift.json: ${r.label} alerts disagree with its PSI and thresholds`);
      }
      return p;
    });
    const scale = (s: Signal) =>
      driftScale(Math.max(alertAt, nb[s].p95, nb[s].threshold, ...periods.map((p) => p[s]), ...(u === 'month' ? inSample.map((p) => p[s]) : [])));
    return {
      periods,
      null: { predictions: nullView('predictions'), words: nullView('words') },
      nullN: nb.n,
      expected: drift.null.expected_chance_alerts[u],
      scale: { predictions: scale('predictions'), words: scale('words') },
    };
  };
  const years = Object.keys(drift.real_class_shares).sort();
  const classes = Object.keys(drift.real_class_shares[years[0]] ?? {}).sort();
  const shares = (t: Record<string, Record<string, number>>, what: string) =>
    classes.map((c) =>
      years.map((y) => {
        const v = t[y]?.[c];
        if (v === undefined) throw new Error(`drift.json: no ${what} share for ${c} in ${y}`);
        return roundSig(v, 4);
      }),
    );
  return {
    alertAt,
    minN,
    reference: drift.settings.reference,
    nullReference: drift.null.reference,
    expectedNote: drift.null.expected_chance_alerts.note,
    inSampleNote: drift.reference_training_in_sample.note,
    units: {
      month: unit('month', drift.months.map((m) => ({ ...m, label: m.month }))),
      quarter: unit('quarter', drift.quarters.map((q) => ({ ...q, label: q.quarter }))),
    },
    inSample,
    classes,
    years,
    real: shares(drift.real_class_shares, 'real'),
    sample: shares(drift.sample_class_shares, 'sample'),
  };
}

export function alertCounts(periods: DriftPoint[]): Record<Signal, { calibrated: number; convention: number }> {
  const count = (s: Signal) => ({
    calibrated: periods.filter((p) => p[`${s}_calibrated`]).length,
    convention: periods.filter((p) => p[`${s}_convention`]).length,
  });
  return { predictions: count('predictions'), words: count('words') };
}

/** P(X >= k) for X ~ Binomial(n, p). */
export function binomialTail(n: number, p: number, k: number): number {
  let total = 0;
  let c = 1; // C(n, i)
  for (let i = 0; i <= n; i++) {
    if (i >= k) total += c * p ** i * (1 - p) ** (n - i);
    c = (c * (n - i)) / (i + 1);
  }
  return Math.min(1, total);
}

/** The months ("2024-10") a quarter ("2024-Q4") covers. */
const quarterMonths = (q: string) => {
  const [y, n] = q.split('-Q');
  return [1, 2, 3].map((k) => `${y}-${String((Number(n) - 1) * 3 + k).padStart(2, '0')}`);
};

/** The calibrated alerts seen against what chance alone gives: the report's expected counts are
 *  averages under the null at an upper-bound rate (p95 is exceeded 5% of the time; the within-2022
 *  maximum makes it rarer), so the binomial tail at that rate is an upper bound on how often a
 *  count this high happens with no drift. Above the expectation, the reading names why the
 *  evidence stays weak: alerts that barely clear a threshold set by a single 2022 period, and
 *  quarterly alerts that overlap a month alert (not independent evidence). */
export function chanceReading(p: DriftChartProps, u: DriftUnit): string {
  const v = p.units[u];
  const c = alertCounts(v.periods);
  const s = SIGNALS.reduce((a, b) => (c[b].calibrated > c[a].calibrated ? b : a));
  const seen = c[s].calibrated;
  const n = v.periods.length;
  const rate = v.expected.per_signal / n;
  const head =
    `By chance alone, expect on average at most about ${v.expected.per_signal} calibrated alerts per signal over ${n} ${u}s ` +
    `(${v.expected.either_signal_max} on either). Seen: predictions ${c.predictions.calibrated}, words ${c.words.calibrated}.`;
  if (seen === 0) return `${head} No calibrated alert: nothing to investigate.`;
  const tail =
    ` With no drift, ${seen} or more on a given signal happens up to ${pct(binomialTail(n, rate, seen))} of the time ` +
    `(binomial, at the null's upper-bound rate of ${pct(rate, 0)} per ${u})`;
  if (seen <= v.expected.per_signal) return `${head}${tail}: within what chance gives.`;
  const nul = v.null[s];
  const alerts = v.periods.filter((q) => q[`${s}_calibrated`]);
  const reasons: string[] = [];
  const oneSet = nul.threshold === nul.within_2022_max && nul.within_2022_max > nul.p95;
  for (const q of alerts) {
    if (oneSet && q[s] < nul.threshold * 1.1) {
      reasons.push(`${q.label} (${q[s].toFixed(3)}) barely clears a threshold (${nul.threshold.toFixed(3)}) set by a single 2022 ${u}`);
    }
  }
  if (u === 'quarter') {
    for (const q of alerts) {
      const m = p.units.month.periods.find((x) => quarterMonths(q.label).includes(x.label) && x[`${s}_calibrated`]);
      if (m) reasons.push(`${q.label} overlaps the ${m.label} month alert, so it is not independent evidence`);
    }
  }
  return `${head}${tail}: weak evidence at most${reasons.length ? `, because ${reasons.join(', and ')}` : ''}.`;
}

/** x and y in SVG units for one drift chart (periods evenly spaced, 0 at the bottom). */
export function driftGeometry(count: number, max: number, box: { width: number; height: number; left: number; right: number; top: number; bottom: number }) {
  const w = box.width - box.left - box.right;
  const h = box.height - box.top - box.bottom;
  const step = count > 1 ? w / (count - 1) : 0;
  return {
    x: (i: number) => box.left + i * step,
    y: (v: number) => box.top + h - (Math.min(v, max) / max) * h,
    step,
  };
}
