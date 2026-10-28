// The AI Agents track's aids: the types of agents/data/derived/*.json (as written by agents/derive.py and
// agents/evals/run_evals.py), the engine and desk names, and the number formats every aid shares.
// Browser-safe. Re-exported by agentsAids.ts.

// ── Engines, desks and commands ─────────────────────────────────────────────
export type Engine = 'hand' | 'hand-go' | 'langgraph' | 'sdk';
export const ENGINES: Engine[] = ['hand', 'hand-go', 'langgraph', 'sdk'];
export const ENGINE_LABEL: Record<Engine, string> = {
  hand: 'Hand-built (Python)',
  'hand-go': 'Hand-built (Go)',
  langgraph: 'LangGraph',
  sdk: 'Claude Agent SDK',
};

export type Desk = 'single' | 'supervisor';
export const DESKS: Desk[] = ['single', 'supervisor'];
export const DESK_LABEL: Record<Desk, string> = { single: 'Single agent', supervisor: 'Supervisor' };

/** Rebuilds the derived files from the traces (free; after any recording). */
export const AGENTS_DERIVE_COMMAND = 'python agents/derive.py';
/** Rescores the traces into engine_scores.json (free; replays the judge). */
export const AGENTS_SCORE_COMMAND = 'python agents/evals/run_evals.py';

export const isEngine = (x: string): x is Engine => (ENGINES as string[]).includes(x);

// ── Number formats (every number an aid shows goes through one of these) ────
const NA = 'n/a';

/** US dollars to three significant figures, never in exponent form: $0.000663, $0.0457. */
export function usd(x: number | null | undefined): string {
  if (x === null || x === undefined) return NA;
  if (x === 0) return '$0';
  return Math.abs(x) >= 1 ? `$${x.toFixed(2)}` : `$${x.toPrecision(3)}`;
}

/** A rate as a percentage with one decimal; 0 and 1 read as 0% and 100%. */
export function pct(x: number | null | undefined): string {
  if (x === null || x === undefined) return NA;
  if (x === 0 || x === 1) return `${x * 100}%`;
  return `${(x * 100).toFixed(1)}%`;
}

/** Percentage points with one decimal, unsigned: 27.7. */
export const points = (x: number) => (Math.abs(x) * 100).toFixed(1);

/** A noise band as plus-or-minus percentage points: ±27.7 points. */
export const bandText = (b: number | null | undefined) => (b === null || b === undefined ? NA : `±${points(b)} points`);

/** Milliseconds as seconds with one decimal: 4919 -> 4.9 s. */
export function ms(x: number | null | undefined): string {
  if (x === null || x === undefined) return NA;
  return `${(x / 1000).toFixed(1)} s`;
}

/** A ratio with two decimals and a times sign: 4.31×. */
export function times(x: number | null | undefined): string {
  if (x === null || x === undefined) return NA;
  return `${x.toFixed(2)}×`;
}

/** A count or mean with thousands separators and at most one decimal: 3,217.9. */
export function num(x: number | null | undefined): string {
  if (x === null || x === undefined) return NA;
  return x.toLocaleString('en-US', { maximumFractionDigits: 1 });
}

export const yesNo = (x: boolean | null | undefined, none = 'not scored') => (x === null || x === undefined ? none : x ? 'yes' : 'no');

// ── Not recorded ────────────────────────────────────────────────────────────
/** What derive.py writes where a trace is missing: never a number. */
export type NotRecordedJson = { recorded: false; command: string; missing?: string[] };
export const isNotRecorded = (x: unknown): x is NotRecordedJson =>
  typeof x === 'object' && x !== null && (x as { recorded?: unknown }).recorded === false;

/** The empty state an island shows: "No recorded run yet" plus these commands, one per line. */
export type NotRecordedView = { recorded: false; command: string };

// ── Types (the derived files) ───────────────────────────────────────────────
export type About = Record<string, unknown> & { what: string };

/** One model call, with tokens, cost and time from the proxy's ledger (R62, R64). */
export type ModelStepJson = {
  kind: 'model';
  agent: string;
  name: string;
  tokens_in: number | null;
  tokens_out: number | null;
  cache_read: number | null;
  cost_usd: number | null;
  ms: number | null;
  /** patterns.json only: what the call produced (at most 240 characters). */
  result?: string | null;
  role?: string | null;
};

/** One tool call. `write` is set for label_ticket; `traced: false` means placed by its order in tool_calls. */
export type ToolStepJson = {
  kind: 'tool';
  agent: string;
  name: string;
  args: string | null;
  is_error: boolean | null;
  traced: boolean;
  write?: 'refused' | 'written';
};

export type HandoffStepJson = { kind: 'handoff'; agent: string; name: string; worker: string; task: string | null; changed: string[]; is_error: boolean | null };
export type StopStepJson = { kind: 'stop'; agent: string; name: string };
export type ErrorStepJson = { kind: 'error'; agent: string; name: string; message?: string };
export type StepJson = ModelStepJson | ToolStepJson | HandoffStepJson | StopStepJson | ErrorStepJson;

/** agents/data/derived/patterns.json */
export type PatternRunJson = { calls: number; cost_usd: number | null; ms: number | null; errors: number; reply: string | null; steps: StepJson[] };
export type PatternsJson = {
  about: About;
  engines: string[];
  tickets: string[];
  notes: Record<string, { note: string; same_calls: boolean | null; by_ticket: Record<string, Record<string, number>> }>;
  patterns: Record<string, Record<string, Record<string, PatternRunJson | NotRecordedJson>>>;
};

/** The scorer's row for one run (engine_scores.json runs[ticket]). */
export type ScoreRunJson = {
  task_success: boolean | null;
  category_ok: boolean | null;
  judge_pass: boolean | null;
  trajectory_ok: boolean | null;
  safety_fail: boolean;
  unconfirmed_write: boolean;
  forbidden_tool: boolean;
  incomplete: boolean;
  cost_usd: number | null;
  ms: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cache_read_tokens: number | null;
  sdk_cost_usd: number | null;
};

/** agents/data/derived/traces.json */
export type TraceRunJson = {
  steps: StepJson[];
  reply: string | null;
  stop: string | null;
  totals: { calls: number; cost_usd: number | null; ms: number | null; tokens_in: number | null; tokens_out: number | null; cache_read: number | null; cache_write: number | null };
  score: ScoreRunJson | null;
};
export type TracesJson = {
  about: About & { tickets: string };
  tickets: string[];
  loop_ticket: string;
  loop_reviews: Record<string, number>;
  ticket_text: Record<string, { category: string | null; text: string | null }>;
  runs: Record<string, Record<string, Record<string, TraceRunJson | NotRecordedJson>>>;
};

/** agents/data/derived/cost_compare.json */
export type CostDeskJson = {
  n: number;
  calls_per_ticket: { mean: number; min: number; max: number };
  tokens: Record<'in' | 'out' | 'cache_read', { sum: number; mean: number } | null>;
  mean_cost_usd: number | null;
  median_cost_usd: number | null;
  p50_ms: number | null;
  p90_ms: number | null;
  success_rate: number | null;
  band: number | null;
  status: string | null;
  tickets: { ticket: string; cost_usd: number | null; ms: number | null; calls: number; success: boolean | null }[];
};
export type CostRatioJson = { cost: number | null; latency: number | null; calls: number | null; success_delta: number | null; band: number | null };
export type CostCompareJson = {
  about: About;
  tickets: string[];
  gate: GateJson | null;
  engines: Record<string, { single: CostDeskJson | NotRecordedJson; supervisor: CostDeskJson | NotRecordedJson; supervisor_vs_single: CostRatioJson | null }>;
};

/** agents/data/derived/engine_scores.json */
export type GateJson = { engine: string | null; desk: string | null; reason: string };
export type ScoreRowJson = {
  n: number;
  complete: number;
  status: 'complete' | 'incomplete' | 'not recorded';
  success_rate: number | null;
  band: number | null;
  trajectory_rate: number | null;
  trajectory_n: number | null;
  mean_cost_usd: number | null;
  cost_basis: string | null;
  p50_ms: number | null;
  safety_fails: number;
  record?: string;
  runs?: Record<string, ScoreRunJson>;
};
export type EngineScoresJson = {
  about: Record<string, unknown>;
  gate: GateJson;
  judge: { model: string; lab_model: string; set: string; judged: number; note: string };
  scores: Record<string, Record<string, ScoreRowJson>>;
};

/** agents/data/derived/sets.json (lesson 7) */
export type SetJson = {
  engine: string;
  desks: string[];
  tickets: number;
  runs: number;
  expected_runs: number;
  complete: boolean;
  model_calls: number;
  recordings: number;
  /** referenced by a trace but not in the store: reported, never counted in recordings */
  missing: number;
  orphans: number;
  recorded_cost_usd: number | null;
  distinct_recorded_cost_usd: number | null;
  record: string;
  replay: string;
};
export type SetsJson = { about: About; sets: Record<string, SetJson> };

export type AgentsData = {
  patterns: PatternsJson;
  traces: TracesJson;
  costCompare: CostCompareJson;
  engineScores: EngineScoresJson;
  sets: SetsJson;
};

// ── Shared step text ────────────────────────────────────────────────────────
/** "1,272 tokens in, 194 out · $0.000224 · 2.3 s" (and the cached part when there is one). */
export function modelDetailOf(tokensIn: N, tokensOut: N, cacheRead: N, cost: N, time: N): string {
  const cached = cacheRead ? `, ${num(cacheRead)} cached` : '';
  return `${num(tokensIn)} tokens in, ${num(tokensOut)} out${cached} · ${usd(cost)} · ${ms(time)}`;
}

/** A number that may be missing (no ledger entry). */
export type N = number | null;

// ── Compact props ───────────────────────────────────────────────────────────
// Astro writes an island's props into the page as JSON in which every value is wrapped ([0, value])
// and every quote becomes &quot; (6 characters). The step lists are the bulk of the agents aids' props,
// so they travel as short tuples of raw numbers, and the islands expand them with the pure functions
// here and in the aid modules (traceStepView, patternStepView, ticketView): the same text, fewer bytes.
export const bit = (x: boolean | null | undefined): 0 | 1 | null => (x === null || x === undefined ? null : x ? 1 : 0);
export const fromBit = (x: 0 | 1 | null): boolean | null => (x === null ? null : x === 1);

/** "get_ticket(id="t001")", or the name and a note when the arguments were not logged. */
export const toolCallOf = (name: string, args: string | null) => (args === null ? `${name} (arguments not logged)` : `${name}(${args})`);

/** The record command, then the commands that rebuild what the app reads. */
export const withRebuild = (command: string, ...more: string[]) => [command, ...more].join('\n');
