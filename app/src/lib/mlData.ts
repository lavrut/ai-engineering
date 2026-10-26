// The ML track's data, read at build time from ml/: the reports, the run logs, the
// registry, and the production model cut down for the feature explorer.
//
// Build time only: loadMlData's eager globs read every model and report, and Vite turns
// them into top-level imports, so an island must never import this module. The
// tokenizer, TF-IDF and scorer live in mlExplore.ts (pure, browser-safe, re-exported
// here for tests); pass an island `forExplorer(data.explorer)` and other small props.
import {
  COEF_SLICE_SIZE,
  explorerModel,
  forExplorer,
  labelAgreement,
  type ExplorerModel,
  type ScoringModel,
} from './mlExplore';

export * from './mlExplore';

// ── Types ───────────────────────────────────────────────────────────────────
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
  /** The production model for the feature explorer, exact (full vocabulary and idf, weights for
   *  the COEF_SLICE_SIZE most influential words). Give an island forExplorer(explorer). */
  explorer: ExplorerModel;
};

// ── Registry ────────────────────────────────────────────────────────────────
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
    explorer: buildExplorer(json<ModelJson>(registry.versions[production].path), files['data/complaints.jsonl']),
  };
}

/** Complaints sent in 2022-2024 (test and stream splits): unseen by v1, where agreement is measured. */
export const AGREEMENT_WINDOW = { from: '2022-01-01', to: '2024-12-31' };

/** explorerModel plus its measured agreement: how often the rounded explorer (what an island
 *  gets) labels a complaint the way the full model does, over AGREEMENT_WINDOW. */
export function buildExplorer(model: ModelJson, complaintsJsonl: string | undefined): ExplorerModel {
  if (complaintsJsonl === undefined) throw new Error('ml/data/complaints.jsonl is missing');
  const texts = complaintsJsonl
    .split('\n')
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as { date: string; narrative: string })
    .filter((r) => r.date >= AGREEMENT_WINDOW.from && r.date <= AGREEMENT_WINDOW.to)
    .map((r) => r.narrative);
  const exact = explorerModel(model, COEF_SLICE_SIZE);
  const share = labelAgreement(forExplorer(exact), model, texts);
  const pct = (share * 100).toFixed(1);
  return {
    ...exact,
    agreement: { share, n: texts.length, on: `complaints sent ${AGREEMENT_WINDOW.from} to ${AGREEMENT_WINDOW.to}` },
    note:
      `Weights for the ${exact.slice_size.toLocaleString('en-US')} most influential of ${model.version}'s ` +
      `${exact.full_size.toLocaleString('en-US')} words; the label matches the full model on ${pct}% of ` +
      `${texts.length.toLocaleString('en-US')} complaints from 2022–2024.`,
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
      '../../../ml/data/complaints.jsonl',
    ],
    { query: '?raw', import: 'default', eager: true },
  ) as Record<string, string>;
  const files = Object.fromEntries(Object.entries(raw).map(([k, v]) => [k.replace(/^(\.\.\/)+ml\//, ''), v]));
  cached = buildMlData(files);
  return cached;
}
