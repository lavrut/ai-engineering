// The AI Infrastructure track's aids: the shapes of the three files they read (infra/data/derived/*.json, all
// written by replay.py), the command that rewrites them, and what every aid shares. Pure and browser-safe;
// infraData.ts reads the files at build time. Re-exported by infraAids.ts.
export { plural } from './dataAidsTypes';

// ── capstone.json ───────────────────────────────────────────────────────────
/** The figures replay.py keeps over every call, per set and per team. hit_rate is rounded to 4 places. */
export type FiguresJson = {
  calls: number;
  cache_hits: number;
  hit_rate: number;
  retries: number;
  refusals: number;
  cost_usd: number;
  p50_ms: number;
  p95_ms: number;
  prompt_cache_read_tokens: number;
  prompt_cache_write_tokens: number;
};
export type RouteJson = { to: string; fallback: string[] };
/** A team's lines: refuse past limit_usd, warn past warn_usd (null: no warn line). */
export type BudgetJson = { limit_usd: number; warn_usd: number | null };
/** config.to_json: gateway.yaml as the gateway loaded it ('*': deny becomes deny_unknown). */
export type ConfigJson = {
  upstream: string;
  models: Record<string, { rpm: number; tpm: number; price: string }>;
  routes: Record<string, RouteJson>;
  deny_unknown: boolean;
  cache: { ttl_s: number; skip_tools: boolean };
  retry: { attempts: number; base_ms: number; max_ms: number };
  limit: { max_wait_ms: number };
  budgets: Record<string, BudgetJson>;
  untagged: string;
  traces_dir: string;
};
export type CaseJson = { id: string; decisions: { stage: string; verdict: string }[]; status: number };
/** One upstream try: status null when the connection failed; backoff_ms is the wait before the next try. */
export type InjectorAttemptJson = { model: string; status: number | null; backoff_ms: number };
/** One request through the gateway against a scripted upstream; decisions are [stage, verdict] pairs. */
export type InjectorScriptJson = {
  script: (number | string)[];
  status: number;
  outcome: string;
  decisions: [string, string][];
  attempts: InjectorAttemptJson[];
};
/** [name, ok, detail], as replay.py --gate prints it. */
export type GateLineJson = [string, boolean, string];
export type GateJson = { ok: boolean; lines: GateLineJson[] };
export type CapstoneJson = {
  about: Record<string, string>;
  config: ConfigJson;
  calls: number;
  all: FiguresJson;
  sets: Record<string, FiguresJson>;
  teams: Record<string, FiguresJson>;
  cases: CaseJson[];
  injector: Record<string, InjectorScriptJson>;
  gate: GateJson;
};

// ── ledger.json ─────────────────────────────────────────────────────────────
export type LedgerTeamJson = { requests: number; cost_usd: number; cached: number; refused: number };
/** Costs are unrounded floats from ledger.summary; agents is the agents track's ledger per traced set. */
export type LedgerJson = {
  about: { ledger: string; agents: string };
  teams: Record<string, LedgerTeamJson>;
  tags: Record<string, number>;
  top_tags: [string, number][];
  agents: Record<string, number>;
};

// ── traces.sample.json ──────────────────────────────────────────────────────
/** A stage's decision: stage, verdict and detail, plus the stage's own keys (tag, team, key, to, wait_ms...). */
export type DecisionJson = { stage: string; verdict: string; detail: string; [key: string]: unknown };
export type TraceAttemptJson = { model: string; status: number | null; error: string | null; backoff_ms: number };
export type TraceJson = {
  id: string;
  parent_id: string | null;
  ts: number;
  team: string;
  tag: string;
  model: string;
  path: string;
  status: number;
  decisions: DecisionJson[];
  ms: number;
  usage: Record<string, number> | null;
  cost_usd: number | null;
  cached: boolean;
  attempts: TraceAttemptJson[];
  outcome: string;
};
export type SampleTraceJson = { label: string; run: string; trace: TraceJson };
export type TracesSampleJson = { about: Record<string, string>; traces: SampleTraceJson[] };

export type InfraData = { capstone: CapstoneJson; ledger: LedgerJson; traces: TracesSampleJson };

/** What rewrites each derived file (named when a file is missing): one replay writes all three. */
const REPLAY = '.venv-infra/bin/python infra/python/replay.py --start';
export const INFRA_COMMANDS = { capstone: REPLAY, ledger: REPLAY, 'traces.sample': REPLAY } as const;

/** The pick that means every set, team or trace at once. */
export const ALL = 'all';

/** The id asked for, or the first; an unknown id fails the build naming the known ones. */
export function pickId(aid: string, what: string, known: string[], id: string | undefined): string {
  if (id === undefined) return known[0];
  if (!known.includes(id)) throw new Error(`${aid}: unknown ${what} "${id}" (known: ${known.join(', ')})`);
  return id;
}

// ── Marks ───────────────────────────────────────────────────────────────────
export type Tone = 'ok' | 'fail' | 'warn' | 'info';
/** A mark is a symbol and a word, with colour only as a border: never colour alone. */
export type MarkView = { icon: string; word: string; tone: Tone };
const VERDICTS: Record<string, MarkView> = {
  allow: { icon: '✓', word: 'allow', tone: 'ok' },
  warn: { icon: '◐', word: 'warn', tone: 'warn' },
  refuse: { icon: '✗', word: 'refuse', tone: 'fail' },
  hit: { icon: '✓', word: 'hit', tone: 'ok' },
  miss: { icon: '○', word: 'miss', tone: 'info' },
  skip: { icon: '–', word: 'skip', tone: 'info' },
  route: { icon: '→', word: 'route', tone: 'ok' },
  deny: { icon: '✗', word: 'deny', tone: 'fail' },
  wait: { icon: '◐', word: 'wait', tone: 'warn' },
  ok: { icon: '✓', word: 'ok', tone: 'ok' },
  retried: { icon: '↻', word: 'retried', tone: 'warn' },
  fallback: { icon: '↪', word: 'fallback', tone: 'warn' },
  fail: { icon: '✗', word: 'fail', tone: 'fail' },
  booked: { icon: '✓', word: 'booked', tone: 'ok' },
  written: { icon: '✓', word: 'written', tone: 'ok' },
};
/** A stage verdict as a symbol and its own word (allow, warn, refuse; hit, miss, skip; ...); a verdict the table
 *  does not know (a team name from the tag stage, error) keeps its word with a neutral or failing mark. */
export const verdictMark = (verdict: string): MarkView =>
  VERDICTS[verdict] ?? (verdict === 'error' ? { icon: '✗', word: 'error', tone: 'fail' } : { icon: '•', word: verdict, tone: 'info' });
