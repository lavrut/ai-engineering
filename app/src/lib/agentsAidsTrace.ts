// The AI Agents track's aids: AgentTrace (lessons 5, 8, 9), one recorded desk run, step by step, with
// handoffs, refused writes and the t061 prompt injection marked in words. Pure and browser-safe;
// agentsData.ts reads traces.json at build time. Re-exported by agentsAids.ts.
import {
  AGENTS_DERIVE_COMMAND,
  DESK_LABEL,
  DESKS,
  ENGINE_LABEL,
  ENGINES,
  bit,
  fromBit,
  isNotRecorded,
  modelDetailOf,
  toolCallOf,
  ms,
  num,
  usd,
  withRebuild,
  yesNo,
  type Desk,
  type Engine,
  type N,
  type NotRecordedView,
  type ScoreRunJson,
  type StepJson,
  type TraceRunJson,
  type TracesJson,
} from './agentsAidsTypes';

/** The ticket that carries the prompt injection (traces.json's about.tickets says so). */
export const INJECTION_TICKET = 't061';
/** Where the injected text starts in t061. */
export const INJECTION_MARK = 'IMPORTANT SYSTEM NOTE';
export const PLACED_NOTE = 'Not in the trace: placed by its order in the tool calls.';

// ── Steps ───────────────────────────────────────────────────────────────────
export type TraceFlag = 'handoff' | 'refused' | 'written' | 'error' | 'stop';
/** A flag in words: the island shows these beside the step, so colour is never the only signal. */
export const FLAG_TEXT: Record<TraceFlag, string> = {
  handoff: 'Handoff',
  refused: 'Write refused',
  written: 'Write made',
  error: 'Error',
  stop: 'Stopped',
};

/** One step, as an island shows it. */
export type TraceStepView = {
  kind: StepJson['kind'];
  title: string;
  detail: string;
  flag?: TraceFlag;
  /** Set for a refused write the LangGraph or SDK trace did not hold, placed by its order in tool_calls. */
  note?: string;
};

/** One step as the props carry it (see "Compact props" in agentsAidsTypes.ts):
 *  model   [0, agent, tokens in, tokens out, cached in, cost, ms]
 *  tool    [1, agent, name, args, flag (0 none, 1 refused, 2 written, 3 error), placed by order (1) or traced (0)]
 *  handoff [2, agent, worker, task, changed keys joined by ", ", is_error]
 *  stop    [3, agent, name]
 *  error   [4, agent, name, message] */
export type TraceStepData =
  | [0, string, N, N, N, N, N]
  | [1, string, string, string | null, 0 | 1 | 2 | 3, 0 | 1]
  | [2, string, string, string | null, string, 0 | 1]
  | [3, string, string]
  | [4, string, string, string | null];

const TOOL_FLAGS = [null, 'refused', 'written', 'error'] as const;

export function traceStepData(s: StepJson): TraceStepData {
  switch (s.kind) {
    case 'model':
      return [0, s.agent, s.tokens_in, s.tokens_out, s.cache_read, s.cost_usd, s.ms];
    case 'tool': {
      const flag = s.write === 'refused' ? 1 : s.write === 'written' ? 2 : s.is_error ? 3 : 0;
      return [1, s.agent, s.name, s.args, flag, s.traced === false ? 1 : 0];
    }
    case 'handoff':
      return [2, s.agent, s.worker, s.task, s.changed.join(', '), s.is_error ? 1 : 0];
    case 'stop':
      return [3, s.agent, s.name];
    default:
      return [4, s.agent, s.name, s.message ?? null];
  }
}

const view = (kind: StepJson['kind'], title: string, detail: string, flag: TraceFlag | null, note?: string): TraceStepView => ({
  kind,
  title,
  detail,
  ...(flag ? { flag } : {}),
  ...(note ? { note } : {}),
});

/** A step's text, from its compact form. */
export function traceStepView(d: TraceStepData): TraceStepView {
  switch (d[0]) {
    case 0:
      return view('model', `${d[1]} calls the model`, modelDetailOf(d[2], d[3], d[4], d[5], d[6]), null);
    case 1: {
      const call = toolCallOf(d[2], d[3]);
      return view('tool', `${d[1]}: ${d[2]}`, call, TOOL_FLAGS[d[4]], d[5] ? PLACED_NOTE : undefined);
    }
    case 2:
      return view('handoff', `${d[1]} hands off to ${d[2]}`, `Task: ${d[3] ?? 'not logged'} · Changed: ${d[4] || 'nothing'}`, d[5] ? 'error' : 'handoff');
    case 3:
      return view('stop', `${d[1]} stopped: ${d[2]}`, `The run hit its limit (${d[2]}).`, 'stop');
    default:
      return view('error', `${d[1]}: error ${d[2]}`, d[3] ?? d[2], 'error');
  }
}

/** A trace record straight to its view (tests and build time). */
export const traceStep = (s: StepJson) => traceStepView(traceStepData(s));

// ── Runs ────────────────────────────────────────────────────────────────────
export type ScoreCheck = { label: string; value: string };

/** The scorer's row as the props carry it: task success, category right, judge passed, trajectory as
 *  expected, safety failure, write without confirmation (1 yes, 0 no, null not scored). */
export type ScoreBits = [0 | 1 | null, 0 | 1 | null, 0 | 1 | null, 0 | 1 | null, 0 | 1 | null, 0 | 1 | null];

export const scoreBits = (s: ScoreRunJson): ScoreBits => [
  bit(s.task_success),
  bit(s.category_ok),
  bit(s.judge_pass),
  bit(s.trajectory_ok),
  bit(s.safety_fail),
  bit(s.unconfirmed_write),
];

/** The scorer's row for the run, as checks in words. */
export function scoreChecks(b: ScoreBits): ScoreCheck[] {
  return [
    { label: 'Task success', value: yesNo(fromBit(b[0])) },
    { label: 'Category right', value: yesNo(fromBit(b[1])) },
    { label: 'Judge passed the reply', value: yesNo(fromBit(b[2]), 'no judge recording') },
    { label: 'Trajectory as expected', value: yesNo(fromBit(b[3]), 'not evaluable') },
    { label: 'Safety failure', value: yesNo(fromBit(b[4])) },
    { label: 'Write without confirmation', value: yesNo(fromBit(b[5])) },
  ];
}

export type TraceTotals = {
  calls: number;
  cost: string;
  /** Claude Agent SDK runs only: the CLI's own client-side estimate (total_cost_usd), beside the ledger cost. */
  sdkCost?: string;
  time: string;
  tokensIn: string;
  tokensOut: string;
  cacheRead: string;
};

export type TraceRunView =
  | {
      recorded: true;
      steps: TraceStepData[];
      reply: string;
      stop: string | null;
      totals: TraceTotals;
      counts: { handoffs: number; refused: number; written: number; placed: number };
      score: ScoreBits | null;
    }
  | NotRecordedView;

function runView(r: TraceRunJson): TraceRunView {
  const steps = r.steps.map((x) => traceStepData(x));
  const views = steps.map(traceStepView);
  const count = (f: TraceFlag) => views.filter((s) => s.flag === f).length;
  const t = r.totals;
  const sdk = r.score?.sdk_cost_usd;
  const totals: TraceTotals = { calls: t.calls, cost: usd(t.cost_usd), time: ms(t.ms), tokensIn: num(t.tokens_in), tokensOut: num(t.tokens_out), cacheRead: num(t.cache_read) };
  return {
    recorded: true,
    steps,
    reply: r.reply ?? '',
    stop: r.stop,
    totals: sdk === null || sdk === undefined ? totals : { ...totals, sdkCost: usd(sdk) },
    counts: { handoffs: count('handoff'), refused: count('refused'), written: count('written'), placed: views.filter((s) => s.note).length },
    score: r.score ? scoreBits(r.score) : null,
  };
}

// ── Tickets ─────────────────────────────────────────────────────────────────
export type TraceTicket = { id: string; category: string; injection: boolean; before: string; injected: string };

export function traceTicket(id: string, t: { category: string | null; text: string | null } | undefined): TraceTicket {
  const text = t?.text ?? '';
  const injection = id === INJECTION_TICKET;
  const at = injection ? text.indexOf(INJECTION_MARK) : -1;
  if (!injection) return { id, category: t?.category ?? '', injection, before: text, injected: '' };
  return { id, category: t?.category ?? '', injection, before: at > 0 ? text.slice(0, at) : '', injected: at >= 0 ? text.slice(at) : text };
}

// ── Props ───────────────────────────────────────────────────────────────────
export type AgentTraceProps = {
  desks: { id: Desk; label: string }[];
  engines: { id: Engine; label: string }[];
  tickets: TraceTicket[];
  /** desk -> engine -> ticket */
  runs: Record<string, Record<string, Record<string, TraceRunView>>>;
  initial: { desk: Desk; engine: Engine; ticket: string };
  loopNote: string;
};

export type AgentTraceOptions = { engines?: Engine[]; desk?: Desk; ticket?: string };

export function agentTraceProps(json: TracesJson, opts: AgentTraceOptions = {}): AgentTraceProps {
  const ids = opts.engines ?? ENGINES;
  for (const e of ids) if (!ENGINES.includes(e)) throw new Error(`AgentTrace: unknown engine "${e}"`);
  const engines = ids.map((id) => ({ id, label: ENGINE_LABEL[id] }));
  const desks = DESKS.map((id) => ({ id, label: DESK_LABEL[id] }));
  const tickets = json.tickets.map((id) => traceTicket(id, json.ticket_text[id]));
  const runs: AgentTraceProps['runs'] = {};
  for (const d of desks) {
    runs[d.id] = {};
    for (const e of engines) {
      runs[d.id][e.id] = {};
      for (const t of json.tickets) {
        const r = json.runs[d.id]?.[e.id]?.[t];
        runs[d.id][e.id][t] =
          r === undefined || isNotRecorded(r)
            ? {
                recorded: false,
                command: withRebuild(r?.command ?? `.venv-agents/bin/python agents/run.py --engine ${e.id} --desk ${d.id} --tickets ${t} --mode record --max-cost 10`, AGENTS_DERIVE_COMMAND),
              }
            : runView(r);
      }
    }
  }
  const loop = json.loop_ticket;
  return {
    desks,
    engines,
    tickets,
    runs,
    initial: {
      desk: opts.desk ?? 'supervisor',
      engine: engines[0].id,
      ticket: opts.ticket && json.tickets.includes(opts.ticket) ? opts.ticket : json.tickets[0],
    },
    loopNote: `${loop} is the ticket whose supervisor runs looped drafter → reviewer longest: ${json.loop_reviews[loop] ?? 0} reviews over the four engines.`,
  };
}

/** The sentence for the live region. */
export function traceStepSummary(run: Extract<TraceRunView, { recorded: true }>, at: number): string {
  const s = traceStepView(run.steps[at]);
  const flag = s.flag && s.flag !== 'handoff' ? ` ${FLAG_TEXT[s.flag]}.` : '';
  const note = s.note ? ` ${s.note}` : '';
  return `Step ${at + 1} of ${run.steps.length}: ${s.title}.${flag} ${s.detail}.${note}`;
}
