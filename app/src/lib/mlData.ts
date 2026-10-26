// The ML track's data, and the tokenizer and scorer the browser needs, mirroring
// ml/python/train.py (tokenize and score) and ml/go/main.go section 3-4. Pinned by
// ml/data/fixtures/tokenize.json and predictions.json in tests/mldata.test.ts.
//
// Everything here is pure and browser-safe except loadMlData, which reads ml/ at build
// time. Call it from an .astro wrapper and pass the island only what it needs. The full
// model never leaves the build: MlData carries a slice of the production model's
// vocabulary (VOCAB_SLICE_SIZE words), not the model file.

// ── Model and scoring types ─────────────────────────────────────────────────
/** train.FEATURES as exported in the model JSON. */
export type Tokenizer = {
  lowercase: 'ascii';
  pattern: string;
  drop_x_runs_from: number;
  stop_words: string[];
  min_df: number;
  max_features: number | null;
};

/** What scoring needs: a full model JSON and a VocabSlice both fit. */
export type ScoringModel = {
  vocabulary: string[];
  idf: number[];
  classes: string[];
  /** One row per class, one column per vocabulary term. */
  coef: number[][];
  intercept: number[];
  majority_class: string;
  tokenizer: Tokenizer;
};

/** ml/models/<version>.json (only tests and loadMlData read one). */
export type ModelJson = ScoringModel & {
  version: string;
  created: string;
  model: 'logreg' | 'nb';
  data_version: string;
  trained_on: { from: string; to: string };
  tokenizer_note: string;
  metrics: Record<string, unknown>;
};

/** The production model cut down to its most common words; scores like a model (scoreText). */
export type VocabSlice = ScoringModel & {
  version: string;
  /** Words kept (VOCAB_SLICE_SIZE, or fewer if the model has fewer). */
  size: number;
  /** Words in the full model. */
  full_size: number;
  note: string;
};

export type Prediction = { label: string; scores: Record<string, number>; low_confidence: boolean };

/** One known word of a text: its count, idf, count x idf, and that value after L2 scaling. */
export type TermWeight = { term: string; index: number; count: number; idf: number; raw: number; weight: number };

export type Features = {
  /** Every token, in text order (repeats kept). */
  tokens: string[];
  /** Known words, in vocabulary order. */
  terms: TermWeight[];
  /** The L2 length of the raw row; 1 when no word is known (the row stays all zeros). */
  norm: number;
  /** Distinct tokens the vocabulary lacks, in first-seen order. */
  unknown: string[];
};

// ── Report types (ml/reports/*.json, ml/runs/*.json, ml/registry.json) ─────
export type ClassMetrics = { precision: number; recall: number; f1: number; support: number };
export type SplitMetrics = {
  classes: string[];
  n: number;
  accuracy: number;
  band: number;
  per_class: Record<string, ClassMetrics>;
  macro: { precision: number; recall: number; f1: number };
  weighted: { precision: number; recall: number; f1: number };
  /** confusion[true][predicted], in `classes` order. */
  confusion: number[][];
};

export type DatasetReport = {
  generated_by: string;
  n: number;
  years: string[];
  classes: string[];
  splits: Record<'train' | 'test' | 'stream', { from: string; to: string; n: number }>;
  /** counts[category][year] (mapped to the current category names). */
  counts: Record<string, Record<string, number>>;
  /** counts_raw[product as published][year]. */
  counts_raw: Record<string, Record<string, number>>;
  narrative_chars: { min: number; median: number; max: number };
  snippets: Record<string, { id: string; date: string; product_raw: string; text: string }[]>;
};

export type MetricsReport = {
  generated_by: string;
  note: string;
  classes: string[];
  C: number;
  nb_alpha: number;
  models: Record<'majority' | 'nb' | 'logreg', { train: SplitMetrics; test: SplitMetrics }>;
};

export type TrainingCheckpoint = {
  iteration: number;
  loss: number;
  penalty: number;
  /** At some checkpoints: the 8 heaviest [word, weight] pairs per class. */
  top_words?: Record<string, [string, number][]>;
};
export type TrainingLog = {
  generated_by: string;
  classes: string[];
  C: number;
  converged_at: number;
  checkpoints: TrainingCheckpoint[];
  note: string;
};

export type ShiftedWord = { word: string; contribution: number; direction: 'up' | 'down' };
export type DriftPeriod = {
  n: number;
  psi_predictions: number;
  psi_words: number;
  alert_convention: boolean;
  alert_calibrated: boolean;
  top_shifted_words: ShiftedWord[];
};
export type NullSignal = {
  p50: number;
  p95: number;
  within_2022: Record<string, number>;
  within_2022_max: number;
  threshold: number;
};
export type NullBand = { n: number; draws: number; seed: number; predictions: NullSignal; words: NullSignal };
export type DriftReport = {
  generated_by: string;
  settings: {
    alert_at: number;
    epsilon: number;
    min_n: number;
    top_k: number;
    model_version: string;
    reference: string;
    stop_words: string[];
  };
  null: {
    reference: string;
    expected_chance_alerts: {
      note: string;
      /** per_signal: expected chance alerts on one signal; either_signal_max: the union bound for either. */
      month: { per_signal: number; either_signal_max: number };
      quarter: { per_signal: number; either_signal_max: number };
    };
    month: NullBand;
    quarter: NullBand;
  };
  months: (DriftPeriod & { month: string })[];
  quarters: (DriftPeriod & { quarter: string })[];
  reference_training_in_sample: {
    note: string;
    months: { month: string; n: number; psi_predictions: number; psi_words: number; alert_convention: boolean }[];
  };
  /** Shares of each category per year in the full CFPB data, and in the committed sample. */
  real_class_shares: Record<string, Record<string, number>>;
  sample_class_shares: Record<string, Record<string, number>>;
};

export type LatencyStats = {
  command: string;
  n: number;
  p50_ms: number;
  p95_ms: number;
  mean_ms: number;
  min_ms: number;
  max_ms: number;
};
export type LatencyReport = {
  generated_by: string;
  note: string;
  request_set: { source: string; distinct_texts: number; requests_per_server: number; warmup_per_server: number; order: string };
  parity: string;
  machine: Record<string, string | number>;
  servers: { python: LatencyStats; go: LatencyStats };
  ratio_python_over_go: { p50: number; p95: number; mean: number };
};

/** Written by `python compare_llm.py compare` after the optional paid recording. */
export type LlmCompareReport = {
  generated_by: string;
  n: number;
  sample_size: number;
  no_answer: number;
  band: number;
  split: string;
  classes: string[];
  scoring: string;
  answered_only: { n: number; v1_accuracy: number | null; claude_accuracy: number | null };
  v1: {
    version: string;
    accuracy: number;
    macro_f1: number;
    per_class_f1: Record<string, number>;
    latency_p50_ms: number;
    latency_p50_ms_go: number;
    latency_note: string;
    cost_per_1000: number;
  };
  claude: {
    model: string;
    accuracy: number;
    macro_f1: number;
    per_class_f1: Record<string, number>;
    latency_p50_ms: number;
    latency_note: string;
    cost_per_1000: number;
  };
  latency_caveat: string;
};

export type RunMetrics = {
  split: string;
  n: number;
  accuracy: number;
  band: number;
  macro_f1: number;
  weighted_f1: number;
  per_class_f1: Record<string, number>;
};
export type GateResult = {
  passed: boolean;
  gain: number;
  band: number;
  floor: number;
  reasons: string[];
  production_version: string;
};
/** One ml/runs/<run_id>.json. metrics holds the eval split's RunMetrics by name, and `gate` when one ran. */
export type RunRecord = {
  run_id: string;
  time: string;
  params: { model: string; C?: number; alpha?: number; max_iter?: number; min_df: number; max_features: number; seed: number };
  windows: { train: { from: string; to: string }; eval: { name: string; from: string; to: string } };
  data_version: string;
  model: { path: string; sha256: string };
  metrics: Record<string, RunMetrics | GateResult>;
  git_commit: string | null;
  git_dirty: boolean | null;
};

export type Stage = 'staging' | 'production' | 'archived';
export type RegistryVersion = {
  path: string;
  sha256: string;
  model: string;
  trained_on: { from: string; to: string };
  metrics: RunMetrics;
  stage: Stage;
  registered: string;
  run: string;
};
export type RegistryEvent = {
  time: string;
  /** register, promote, gate or rollback. */
  action: string;
  version: string;
  from_stage: Stage | null;
  to_stage: Stage;
  note: string;
};
export type Registry = { versions: Record<string, RegistryVersion>; history: RegistryEvent[] };

export type MlData = {
  dataset: DatasetReport;
  metrics: MetricsReport;
  trainingLog: TrainingLog;
  drift: DriftReport;
  latency: LatencyReport;
  /** null until the optional Claude recording writes reports/llm_compare.json: aids show "No recorded run yet". */
  llmCompare: LlmCompareReport | null;
  /** Every run log, oldest first (ties by run_id). */
  runs: RunRecord[];
  registry: Registry;
  /** The version the registry names production. */
  production: string;
  /** The production model's VOCAB_SLICE_SIZE most common words. */
  vocab: VocabSlice;
};

// ── Tokenizer (train.py section 3; rules R12) ─────────────────────────────────
/** Split text into tokens exactly as train.tokenize and Go's tokenize do:
 *  1. lowercase A-Z only (no Unicode case folding);
 *  2. a token is a maximal run of [a-z0-9'] (every other character, any non-ASCII one too, separates);
 *  3. strip leading and trailing apostrophes;
 *  4. drop empty tokens, stop words, and tokens made only of x with length >= drop_x_runs_from (XX, XXXX);
 *  5. no stemming. */
export function tokenize(text: string, cfg: Tokenizer): string[] {
  if (cfg.lowercase !== 'ascii') throw new Error(`tokenize implements lowercase='ascii' only, not ${cfg.lowercase}`);
  const lowered = text.replace(/[A-Z]+/g, (s) => s.toLowerCase()); // the matched run is ASCII, so this is exact
  const stop = new Set(cfg.stop_words);
  const out: string[] = [];
  // Without the u flag the class matches UTF-16 code units; it holds only ASCII, so every
  // other code unit (surrogate halves included) is a separator, as in Python and Go.
  for (const [match] of lowered.matchAll(new RegExp(cfg.pattern, 'g'))) {
    const tok = match.replace(/^'+|'+$/g, '');
    if (!tok || stop.has(tok)) continue;
    if (tok.length >= cfg.drop_x_runs_from && /^x+$/.test(tok)) continue;
    out.push(tok);
  }
  return out;
}

// ── TF-IDF and scoring (train.py section 6, score) ──────────────────────────
/** Sums the way Python 3.12+ sum() does (Neumaier compensation), as Go's pySum does. */
function pySum(xs: number[]): number {
  let s = 0;
  let c = 0;
  for (const x of xs) {
    const t = s + x;
    c += Math.abs(s) >= Math.abs(x) ? s - t + x : x - t + s;
    s = t;
  }
  return c !== 0 && Number.isFinite(c) ? s + c : s;
}

const indexes = new WeakMap<string[], Map<string, number>>();
function indexOf(vocabulary: string[]): Map<string, number> {
  let index = indexes.get(vocabulary);
  if (!index) {
    index = new Map(vocabulary.map((t, i) => [t, i]));
    indexes.set(vocabulary, index);
  }
  return index;
}

/** A text's TF-IDF row: counts of known words x idf, scaled to length 1 (L2). */
export function tfidf(model: Pick<ScoringModel, 'vocabulary' | 'idf' | 'tokenizer'>, text: string): Features {
  const index = indexOf(model.vocabulary);
  const tokens = tokenize(text, model.tokenizer);
  const counts = new Map<number, number>();
  const unknown: string[] = [];
  for (const t of tokens) {
    const j = index.get(t);
    if (j === undefined) {
      if (!unknown.includes(t)) unknown.push(t);
    } else counts.set(j, (counts.get(j) ?? 0) + 1);
  }
  const cols = [...counts.keys()].sort((a, b) => a - b);
  const raws = cols.map((j) => counts.get(j)! * model.idf[j]);
  const norm = Math.sqrt(pySum(raws.map((w) => w * w))) || 1;
  const terms = cols.map((j, i) => ({
    term: model.vocabulary[j],
    index: j,
    count: counts.get(j)!,
    idf: model.idf[j],
    raw: raws[i],
    weight: raws[i] / norm,
  }));
  return { tokens, terms, norm, unknown };
}

/** Score a text like train.score: softmax(coef . x + intercept), sums in vocabulary order.
 *  The label is the first class with the top probability. With no known word the scores are
 *  softmax(intercept) and the label is the majority class, flagged low_confidence; otherwise
 *  low_confidence means the top probability is below 0.5. */
export function scoreText(model: ScoringModel, text: string): Prediction {
  const { terms } = tfidf(model, text);
  const logits = model.coef.map((row, k) => model.intercept[k] + pySum(terms.map((t) => row[t.index] * t.weight)));
  const top = Math.max(...logits);
  const exps = logits.map((z) => Math.exp(z - top));
  const total = pySum(exps);
  const probs = exps.map((e) => e / total);
  const scores = Object.fromEntries(model.classes.map((c, k) => [c, probs[k]]));
  if (!terms.length) return { label: model.majority_class, scores, low_confidence: true };
  let best = 0;
  probs.forEach((p, k) => {
    if (p > probs[best]) best = k;
  });
  return { label: model.classes[best], scores, low_confidence: probs[best] < LOW_CONFIDENCE };
}

export const LOW_CONFIDENCE = 0.5;

// ── Vocabulary slice and registry ───────────────────────────────────────────
export const VOCAB_SLICE_SIZE = 2000;

/** The model's `size` most common words (lowest idf = in the most training complaints; ties by
 *  vocabulary order), kept in vocabulary order with their idf and weight columns. Scoring with
 *  the slice ignores the other words, so it approximates the full model. */
export function vocabSlice(model: ModelJson, size: number = VOCAB_SLICE_SIZE): VocabSlice {
  const keep = model.vocabulary
    .map((_, j) => j)
    .sort((a, b) => model.idf[a] - model.idf[b] || a - b)
    .slice(0, size)
    .sort((a, b) => a - b);
  return {
    version: model.version,
    size: keep.length,
    full_size: model.vocabulary.length,
    note:
      `The ${keep.length} most common of ${model.version}'s ${model.vocabulary.length} words (lowest idf). ` +
      'Scores from the slice ignore the other words, so they can differ a little from the full model.',
    vocabulary: keep.map((j) => model.vocabulary[j]),
    idf: keep.map((j) => model.idf[j]),
    classes: model.classes,
    coef: model.coef.map((row) => keep.map((j) => row[j])),
    intercept: model.intercept,
    majority_class: model.majority_class,
    tokenizer: model.tokenizer,
  };
}

/** The one version the registry names production. */
export function productionVersion(registry: Registry): string {
  const found = Object.entries(registry.versions)
    .filter(([, v]) => v.stage === 'production')
    .map(([k]) => k);
  if (found.length === 0) throw new Error('registry.json: no version in production');
  if (found.length > 1) throw new Error(`registry.json: ${found.length} versions in production (${found.join(', ')})`);
  return found[0];
}

// ── Loading ─────────────────────────────────────────────────────────────────
/** Raw file text by repo-relative path below ml/ ("reports/drift.json", "runs/x.json", "models/v1.json"). */
export type MlFiles = Record<string, string>;

/** MlData from file texts keyed by path below ml/ (pure; loadMlData supplies the real files). */
export function buildMlData(files: MlFiles): MlData {
  const json = <T>(path: string): T => {
    const text = files[path];
    if (text === undefined) throw new Error(`ml/${path} is missing`);
    try {
      return JSON.parse(text) as T;
    } catch (e) {
      throw new Error(`ml/${path}: ${(e as Error).message}`);
    }
  };
  const registry = json<Registry>('registry.json');
  const production = productionVersion(registry);
  const runs = Object.keys(files)
    .filter((p) => /^runs\/[^/]+\.json$/.test(p))
    .map((p) => json<RunRecord>(p))
    .sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : a.run_id < b.run_id ? -1 : 1));
  return {
    dataset: json('reports/dataset.json'),
    metrics: json('reports/metrics.json'),
    trainingLog: json('reports/training_log.json'),
    drift: json('reports/drift.json'),
    latency: json('reports/latency.json'),
    llmCompare: files['reports/llm_compare.json'] === undefined ? null : json('reports/llm_compare.json'),
    runs,
    registry,
    production,
    vocab: vocabSlice(json<ModelJson>(registry.versions[production].path)),
  };
}

let cached: MlData | undefined;

/** The real data in ml/, read at build time. Never ml/data/fixtures. */
export function loadMlData(): MlData {
  if (cached) return cached;
  // Globs sit inside the function so an island that imports only the pure helpers bundles none of the data.
  const raw = import.meta.glob(
    [
      '../../../ml/reports/*.json',
      '../../../ml/runs/*.json',
      '../../../ml/registry.json',
      '../../../ml/models/*.json',
    ],
    { query: '?raw', import: 'default', eager: true },
  ) as Record<string, string>;
  const files = Object.fromEntries(Object.entries(raw).map(([k, v]) => [k.replace(/^(\.\.\/)+ml\//, ''), v]));
  cached = buildMlData(files);
  return cached;
}
