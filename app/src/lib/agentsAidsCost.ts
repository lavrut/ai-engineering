// The AI Agents track's aids: CostCompare (lesson 6, when more agents hurt): every engine's single and
// supervisor desks over the 13 tickets, as bars and a table. Pure and browser-safe; agentsData.ts reads
// cost_compare.json at build time. Re-exported by agentsAids.ts.
import {
  AGENTS_DERIVE_COMMAND,
  AGENTS_SCORE_COMMAND,
  bandText,
  ENGINE_LABEL,
  ENGINES,
  isNotRecorded,
  ms,
  num,
  pct,
  points,
  times,
  usd,
  withRebuild,
  type CostCompareJson,
  type CostDeskJson,
  type CostRatioJson,
  type Engine,
} from './agentsAidsTypes';

export type CostDeskView =
  | {
      recorded: true;
      n: number;
      success: string;
      band: string;
      meanCost: string;
      medianCost: string;
      p50: string;
      p90: string;
      calls: string;
      callsRange: string;
      tokensIn: string;
      tokensOut: string;
      cacheRead: string;
      /** The raw values the bars are drawn from. */
      value: { cost: number | null; latency: number | null; calls: number };
    }
  | { recorded: false; command: string; missing: string };

export type CostRatioView = { cost: string; latency: string; calls: string; successDelta: string; beyondBand: boolean };

export type CostEngineView = {
  engine: Engine;
  label: string;
  single: CostDeskView;
  supervisor: CostDeskView;
  ratio: CostRatioView | null;
  /** The ratios and the success difference in one sentence; null when a desk is not recorded. */
  sentence: string | null;
};

export type CostCompareProps = { engines: CostEngineView[]; tickets: number };

function deskView(d: CostDeskJson | { recorded: false; command: string; missing?: string[] }): CostDeskView {
  if (isNotRecorded(d)) {
    return { recorded: false, command: withRebuild(d.command, AGENTS_SCORE_COMMAND, AGENTS_DERIVE_COMMAND), missing: (d.missing ?? []).join(', ') };
  }
  const c = d.calls_per_ticket;
  return {
    recorded: true,
    n: d.n,
    success: pct(d.success_rate),
    band: bandText(d.band),
    meanCost: usd(d.mean_cost_usd),
    medianCost: usd(d.median_cost_usd),
    p50: ms(d.p50_ms),
    p90: ms(d.p90_ms),
    calls: num(c.mean),
    callsRange: `${c.min}–${c.max}`,
    tokensIn: num(d.tokens.in?.mean),
    tokensOut: num(d.tokens.out?.mean),
    cacheRead: num(d.tokens.cache_read?.mean),
    value: { cost: d.mean_cost_usd, latency: d.p50_ms, calls: c.mean },
  };
}

function ratioView(r: CostRatioJson): CostRatioView {
  const delta = r.success_delta;
  return {
    cost: times(r.cost),
    latency: times(r.latency),
    calls: times(r.calls),
    successDelta: delta === null ? 'n/a' : `${delta >= 0 ? '+' : '-'}${points(delta)} points`,
    beyondBand: delta !== null && r.band !== null && Math.abs(delta) > r.band,
  };
}

/** One engine's comparison in words; the success difference is read against the noise band. */
export function costSentence(label: string, r: CostRatioJson): string {
  const v = ratioView(r);
  const head = `${label}: the supervisor desk costs ${v.cost} the single agent per ticket, takes ${v.latency} as long at the median and makes ${v.calls} the model calls.`;
  if (r.success_delta === null || r.band === null) return head;
  const dir = r.success_delta >= 0 ? 'higher' : 'lower';
  const reading = v.beyondBand
    ? `more than the ±${points(r.band)}-point noise band.`
    : `inside the ±${points(r.band)}-point noise band, so not evidence of a difference.`;
  return `${head} Its success rate is ${points(r.success_delta)} points ${dir}, ${reading}`;
}

export function costCompareProps(json: CostCompareJson): CostCompareProps {
  const engines = ENGINES.filter((e) => json.engines[e]).map((e): CostEngineView => {
    const row = json.engines[e];
    const r = row.supervisor_vs_single;
    return {
      engine: e,
      label: ENGINE_LABEL[e],
      single: deskView(row.single),
      supervisor: deskView(row.supervisor),
      ratio: r ? ratioView(r) : null,
      sentence: r ? costSentence(ENGINE_LABEL[e], r) : null,
    };
  });
  return { engines, tickets: json.tickets.length };
}

// ── Bars ────────────────────────────────────────────────────────────────────
export type CostMetric = 'cost' | 'latency' | 'calls';
export const COST_METRICS: { id: CostMetric; label: string; ratio: keyof CostRatioView }[] = [
  { id: 'cost', label: 'Mean cost per ticket', ratio: 'cost' },
  { id: 'latency', label: 'Median time (p50)', ratio: 'latency' },
  { id: 'calls', label: 'Model calls per ticket', ratio: 'calls' },
];

export type Bar = { text: string; percent: number };
export type BarRow = { engine: Engine; label: string; single: Bar; supervisor: Bar };

const metricText = (m: CostMetric, v: number) => (m === 'cost' ? usd(v) : m === 'latency' ? ms(v) : num(v));

/** Bars for one metric on one scale (the longest is 100%), for the engines with both desks recorded. */
export function bars(engines: CostEngineView[], metric: CostMetric): BarRow[] {
  const both = engines.filter((e) => e.single.recorded && e.supervisor.recorded);
  const val = (d: CostDeskView) => (d.recorded ? (d.value[metric] ?? 0) : 0);
  const max = Math.max(...both.flatMap((e) => [val(e.single), val(e.supervisor)]), 0);
  const bar = (d: CostDeskView): Bar => ({ text: metricText(metric, val(d)), percent: max > 0 ? Math.round((val(d) / max) * 1000) / 10 : 0 });
  return both.map((e) => ({ engine: e.engine, label: e.label, single: bar(e.single), supervisor: bar(e.supervisor) }));
}

/** The sentence for the live region when the metric changes. */
export function metricSummary(engines: CostEngineView[], metric: CostMetric): string {
  const m = COST_METRICS.find((x) => x.id === metric)!;
  const parts = engines.filter((e) => e.ratio).map((e) => `${e.label} ${e.ratio![m.ratio]}`);
  const lead = `Showing ${m.label[0].toLowerCase()}${m.label.slice(1)}.`;
  return parts.length ? `${lead} Supervisor ÷ single: ${parts.join(', ')}.` : lead;
}
