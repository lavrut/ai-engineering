// Logic for the MLOps aids: ModelVsLlm (lesson 6), RunTable (7) and RegistryTimeline (8). Pure and
// browser-safe; re-exported by mlAids.ts.
import type { GateResult, LatencyReport, LlmCompareReport, Registry, RegistryEvent, RunMetrics, RunRecord, Stage } from './mlData';
import { sig } from './mlAidsShared';

// ── ModelVsLlm ──────────────────────────────────────────────────────────────
/** What to run when reports/llm_compare.json is missing (compare_llm.py's own advice): record
 *  the paid answers once (prints the cost and asks first), then replay them into the report. */
export const LLM_COMMANDS = ['../../.venv-ml/bin/python compare_llm.py record', '../../.venv-ml/bin/python compare_llm.py compare']; // from ml/python, as lesson 6 runs them
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
