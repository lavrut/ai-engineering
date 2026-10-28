// The AI Agents track's aids: EngineScorecard (lessons 10-11): each engine x desk's success with its noise
// band, the capstone gate's verdict exactly as run_evals.py wrote it, and the tickets of one run. With 13
// tickets no engine clears the gate (ruling R66): that is the real result, shown as it is. Pure and
// browser-safe; agentsData.ts reads engine_scores.json at build time. Re-exported by agentsAids.ts.
import {
  AGENTS_DERIVE_COMMAND,
  AGENTS_SCORE_COMMAND,
  DESK_LABEL,
  DESKS,
  ENGINE_LABEL,
  ENGINES,
  ms,
  bandText,
  bit,
  fromBit,
  pct,
  usd,
  withRebuild,
  yesNo,
  type Desk,
  type Engine,
  type EngineScoresJson,
  type GateJson,
  type N,
  type ScoreRowJson,
} from './agentsAidsTypes';

/** The gate's minimum for success minus the band (run_evals.py MIN_SUCCESS; the test pins it against the
 *  verdict's own text). */
export const GATE_MIN_SUCCESS = 0.7;

export type ScoreRowView = {
  engine: Engine;
  desk: Desk;
  engineLabel: string;
  deskLabel: string;
  /** "Hand-built (Go), supervisor" */
  label: string;
  status: ScoreRowJson['status'];
  complete: string;
  success: string;
  band: string;
  /** success - band: what the gate compares with the minimum. */
  floor: string;
  clears: boolean;
  trajectory: string;
  safety: string;
  meanCost: string;
  p50: string;
  /** run_evals.py's cost_basis: recording_tokens (the ledger) or a fallback */
  costBasis: string | null;
  /** Set when the row is not complete: what records the missing runs, then rebuilds the scores. */
  command: string | null;
  value: { success: number | null; band: number | null };
};

export type GateView = { passed: boolean; headline: string; rule: string; reason: string; min: number; minText: string };

export type TicketScoreView = {
  ticket: string;
  success: string;
  category: string;
  judge: string;
  trajectory: string;
  safety: string;
  /** The ledger cost: what the recorded calls cost (R62), every engine alike. */
  cost: string;
  /** Claude Agent SDK runs only: the CLI's own client-side estimate (total_cost_usd). */
  sdkCost?: string;
  time: string;
};

/** One ticket as the props carry it (see "Compact props" in agentsAidsTypes.ts): ticket, task success,
 *  category right, judge passed, trajectory as expected, safety failure, ledger cost, ms, SDK estimate. */
export type TicketScoreData = [string, 0 | 1 | null, 0 | 1 | null, 0 | 1 | null, 0 | 1 | null, 0 | 1 | null, N, N, N];

export function ticketView(d: TicketScoreData): TicketScoreView {
  const v: TicketScoreView = {
    ticket: d[0],
    success: yesNo(fromBit(d[1])),
    category: yesNo(fromBit(d[2])),
    judge: yesNo(fromBit(d[3]), 'no judge recording'),
    trajectory: yesNo(fromBit(d[4]), 'not evaluable'),
    safety: yesNo(fromBit(d[5])),
    cost: usd(d[6]),
    time: ms(d[7]),
  };
  return d[8] === null ? v : { ...v, sdkCost: usd(d[8]) };
}

export type EngineScorecardProps = {
  rows: ScoreRowView[];
  gate: GateView;
  judge: string;
  /** `${engine}/${desk}` -> the tickets of that run; empty when the lesson turns the drill-down off */
  tickets: Record<string, TicketScoreData[]>;
  initial: string;
  /** Every row's cost comes from the proxy's ledger (cost_basis recording_tokens); otherwise the island says which do not. */
  costNote: string;
};

export type EngineScorecardOptions = { drillDown?: boolean };

export const rowKey = (r: { engine: string; desk: string }) => `${r.engine}/${r.desk}`;

function rowView(engine: Engine, desk: Desk, r: ScoreRowJson, min: number): ScoreRowView {
  const complete = r.status === 'complete' && r.success_rate !== null && r.band !== null;
  const floor = complete ? r.success_rate! - r.band! : null;
  return {
    engine,
    desk,
    engineLabel: ENGINE_LABEL[engine],
    deskLabel: DESK_LABEL[desk],
    label: `${ENGINE_LABEL[engine]}, ${DESK_LABEL[desk].toLowerCase()}`,
    status: r.status,
    complete: `${r.complete} of ${r.n}`,
    success: pct(r.success_rate),
    band: bandText(r.band),
    floor: pct(floor),
    clears: floor !== null && floor >= min && r.safety_fails === 0,
    trajectory: r.trajectory_rate === null ? 'n/a' : `${pct(r.trajectory_rate)} of ${r.trajectory_n}`,
    safety: String(r.safety_fails),
    meanCost: usd(r.mean_cost_usd),
    p50: ms(r.p50_ms),
    costBasis: r.cost_basis,
    command: r.status === 'complete' ? null : withRebuild(r.record ?? `.venv-agents/bin/python agents/run.py --set desk-${engine} --mode record --max-cost 10`, AGENTS_SCORE_COMMAND, AGENTS_DERIVE_COMMAND),
    value: { success: r.success_rate, band: r.band },
  };
}

export function gateView(g: GateJson, min: number): GateView {
  const passed = g.engine !== null && g.desk !== null;
  const minText = `${Math.round(min * 100)}%`;
  return {
    passed,
    headline: passed ? `The gate picks ${ENGINE_LABEL[g.engine as Engine] ?? g.engine}, ${g.desk}.` : 'No engine clears the gate.',
    rule: `The gate: success minus the noise band must reach ${minText}, with no safety failures.`,
    reason: g.reason,
    min,
    minText,
  };
}

export function engineScorecardProps(json: EngineScoresJson, opts: EngineScorecardOptions = {}): EngineScorecardProps {
  const min = GATE_MIN_SUCCESS;
  const rows: ScoreRowView[] = [];
  const tickets: EngineScorecardProps['tickets'] = {};
  for (const e of ENGINES) {
    for (const d of DESKS) {
      const r = json.scores[e]?.[d];
      if (!r) continue;
      rows.push(rowView(e, d, r, min));
      if (opts.drillDown === false) continue;
      tickets[rowKey({ engine: e, desk: d })] = Object.entries(r.runs ?? {}).map(([ticket, s]): TicketScoreData => [
        ticket,
        bit(s.task_success),
        bit(s.category_ok),
        bit(s.judge_pass),
        bit(s.trajectory_ok),
        bit(s.safety_fail),
        s.cost_usd,
        s.ms,
        s.sdk_cost_usd ?? null,
      ]);
    }
  }
  const j = json.judge;
  const judge = `Replies judged by ${j.model}: ${j.judged}. The LLMs lab's judge model was ${j.lab_model}. A reply with no judge recording has no judge verdict; it is never guessed.`;
  // Open the drill-down on the run the gate names (or came closest to clearing it), else the first complete row.
  const named = /the best, (\S+) (single|supervisor),/.exec(json.gate.reason);
  const pick = json.gate.engine ? `${json.gate.engine}/${json.gate.desk}` : named ? `${named[1]}/${named[2]}` : '';
  const first = rows.find((r) => r.status === 'complete') ?? rows[0];
  const initial = tickets[pick]?.length ? pick : rowKey(first);
  const other = rows.filter((r) => r.costBasis !== null && r.costBasis !== 'recording_tokens');
  const costNote = other.length
    ? `Not every cost here is from the proxy's ledger: ${other.map((r) => `${r.label} (${r.costBasis})`).join(', ')}.`
    : "Every cost here is from the proxy's ledger: what the recorded calls cost, priced from their usage.";
  return { rows, gate: gateView(json.gate, min), judge, tickets, initial, costNote };
}

/** The sentence for the live region when the drill-down changes. */
export function ticketSummary(row: ScoreRowView, tickets: { success: string }[]): string {
  if (!tickets.length) return `${row.label}: not recorded yet.`;
  const ok = tickets.filter((t) => t.success === 'yes').length;
  return `${row.label}: ${ok} of ${tickets.length} tickets succeeded (${row.success}).`;
}
