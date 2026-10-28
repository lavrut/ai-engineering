// The AI Agents track's aids: PatternStepper (lessons 2-4), one recorded run of a hand-built pattern,
// step by step, in Python or Go. Pure and browser-safe; agentsData.ts reads patterns.json at build time.
// Re-exported by agentsAids.ts.
import {
  AGENTS_DERIVE_COMMAND,
  isNotRecorded,
  modelDetailOf,
  toolCallOf,
  ms,
  usd,
  withRebuild,
  type N,
  type NotRecordedView,
  type PatternRunJson,
  type PatternsJson,
  type StepJson,
} from './agentsAidsTypes';

// ── The five patterns ───────────────────────────────────────────────────────
export type PatternId = 'chain' | 'route' | 'parallel' | 'orchestrate' | 'evaluate_optimize';

export type PatternInfo = {
  id: PatternId;
  /** The section title in patterns.py and patterns.go (pinned by the test). */
  name: string;
  /** The numbered section of patterns.py and patterns.go that holds the pattern. */
  section: number;
  what: string;
  caption: string;
};

const pattern = (id: PatternId, name: string, section: number, what: string): PatternInfo => ({
  id,
  name,
  section,
  what,
  caption: `In the code: patterns.py and patterns.go, section ${section}.`,
});

/** Anthropic's "Building effective agents" taxonomy, in the order patterns.py keeps. */
export const PATTERNS: PatternInfo[] = [
  pattern('chain', 'Prompt chaining', 2, 'Classify the ticket, check the category in code, then write the reply for that category.'),
  pattern('route', 'Routing', 3, 'One call picks one of four routes; that route’s specialist prompt writes the reply.'),
  pattern('parallel', 'Parallelization', 4, 'Three lookups run side by side, then one call summarizes what they found.'),
  pattern('orchestrate', 'Orchestrator-workers', 5, 'A lead call plans up to three subtasks, a worker does each, and a last call writes the reply.'),
  pattern('evaluate_optimize', 'Evaluator-optimizer', 6, 'Draft, judge against the rubric, revise; at most three judge calls.'),
];

const PATTERN_ENGINES = [
  { id: 'hand', label: 'Python (hand-built)' },
  { id: 'hand-go', label: 'Go (hand-built)' },
] as const;
export type PatternEngine = (typeof PATTERN_ENGINES)[number]['id'];

// ── Steps and runs ──────────────────────────────────────────────────────────
export type PatternStepView = {
  kind: 'model' | 'tool' | 'other';
  title: string;
  detail: string;
  /** What a model call produced (at most 240 characters); left out for tools and calls with no text. */
  result?: string;
};

/** One step as the props carry it (see "Compact props" in agentsAidsTypes.ts):
 *  model [0, agent, role, tokens in, tokens out, cached in, cost, ms, result]
 *  tool  [1, agent, name, args, is_error]
 *  other [2, agent, name, kind] */
export type PatternStepData =
  | [0, string, string, N, N, N, N, N, string | null]
  | [1, string, string, string | null, 0 | 1]
  | [2, string, string, string];

export function patternStepData(s: StepJson): PatternStepData {
  if (s.kind === 'model') return [0, s.agent, s.role ?? 'call', s.tokens_in, s.tokens_out, s.cache_read, s.cost_usd, s.ms, s.result ?? null];
  if (s.kind === 'tool') return [1, s.agent, s.name, s.args, s.is_error ? 1 : 0];
  return [2, s.agent, s.name, s.kind];
}

/** A step's text, from its compact form. */
export function patternStepView(d: PatternStepData): PatternStepView {
  if (d[0] === 0) {
    const step: PatternStepView = { kind: 'model', title: `${d[1]} · ${d[2]}`, detail: modelDetailOf(d[3], d[4], d[5], d[6], d[7]) };
    return d[8] ? { ...step, result: d[8] } : step;
  }
  if (d[0] === 1) {
    const call = toolCallOf(d[2], d[3]);
    return { kind: 'tool', title: `${d[1]} · ${d[2]}`, detail: call + (d[4] ? ' (error)' : '') };
  }
  return { kind: 'other', title: `${d[1]} · ${d[2]}`, detail: d[3] };
}

/** A trace record straight to its view (tests and build time). */
export const patternStep = (s: StepJson) => patternStepView(patternStepData(s));

export type PatternRunView =
  | { recorded: true; calls: number; cost: string; time: string; errors: number; reply: string; steps: PatternStepData[] }
  | NotRecordedView;

function runView(r: PatternRunJson): PatternRunView {
  return {
    recorded: true,
    calls: r.calls,
    cost: usd(r.cost_usd),
    time: ms(r.ms),
    errors: r.errors,
    reply: r.reply ?? '',
    steps: r.steps.map((x) => patternStepData(x)),
  };
}

// ── Props ───────────────────────────────────────────────────────────────────
export type PatternStepperProps = {
  patterns: PatternInfo[];
  tickets: string[];
  engines: { id: PatternEngine; label: string }[];
  /** pattern -> ticket -> engine */
  runs: Record<string, Record<string, Record<string, PatternRunView>>>;
  /** pattern -> did Python and Go make the same number of model calls */
  notes: Record<string, string>;
  initial: { pattern: PatternId; ticket: string; engine: PatternEngine };
};

export type PatternStepperOptions = { patterns?: PatternId[]; ticket?: string; engine?: PatternEngine };

export function patternStepperProps(json: PatternsJson, opts: PatternStepperOptions = {}): PatternStepperProps {
  const ids = opts.patterns ?? PATTERNS.map((p) => p.id);
  const patterns = ids.map((id) => {
    const p = PATTERNS.find((x) => x.id === id);
    if (!p || !json.patterns[id]) throw new Error(`PatternStepper: unknown pattern "${id}"`);
    return p;
  });
  const tickets = json.tickets;
  const engines = PATTERN_ENGINES.filter((e) => json.engines.includes(e.id)).map((e) => ({ ...e }));
  const runs: PatternStepperProps['runs'] = {};
  const notes: PatternStepperProps['notes'] = {};
  for (const p of patterns) {
    runs[p.id] = {};
    for (const t of tickets) {
      runs[p.id][t] = {};
      for (const e of engines) {
        const r = json.patterns[p.id]?.[t]?.[e.id];
        runs[p.id][t][e.id] =
          r === undefined || isNotRecorded(r)
            ? { recorded: false, command: withRebuild(r?.command ?? `python agents/run.py --set ${e.id === 'hand' ? 'patterns' : 'patterns-go'} --mode record --max-cost 10`, AGENTS_DERIVE_COMMAND) }
            : runView(r);
      }
    }
    notes[p.id] = json.notes[p.id]?.note ?? '';
  }
  const ticket = opts.ticket && tickets.includes(opts.ticket) ? opts.ticket : tickets[0];
  const engine = opts.engine && engines.some((e) => e.id === opts.engine) ? opts.engine : engines[0].id;
  return { patterns, tickets, engines, runs, notes, initial: { pattern: patterns[0].id, ticket, engine } };
}

/** The sentence for the live region. */
export function patternStepSummary(run: Extract<PatternRunView, { recorded: true }>, at: number): string {
  const s = patternStepView(run.steps[at]);
  return `Step ${at + 1} of ${run.steps.length}: ${s.title}. ${s.detail}.`;
}
