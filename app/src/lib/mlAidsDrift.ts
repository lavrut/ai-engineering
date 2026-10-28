// Logic for DriftChart (lesson 10): the drift periods, the null design and the chart geometry. Pure
// and browser-safe; re-exported by mlAids.ts.
import { roundSig } from './mlExplore';
import type { DriftPeriod, DriftReport, ShiftedWord } from './mlData';
import { pct, sig } from './mlAidsShared';

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
export type NullView = { p50: number; p95: number; within_2022_max: number; threshold: number; shareAtAlert: number };
/** The unstratified comparison null: what simple random draws (an unstratified stream) would give. */
export type UnstratifiedView = { p50: number; p95: number; shareAtAlert: number };
export type DriftUnitView = {
  periods: DriftPoint[];
  null: Record<Signal, NullView>;
  unstratified: Record<Signal, UnstratifiedView>;
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
    const nullView = (s: Signal): NullView => ({ p50: sig(nb[s].p50), p95: sig(nb[s].p95), within_2022_max: sig(nb[s].within_2022_max), threshold: sig(nb[s].threshold), shareAtAlert: sig(nb[s].share_at_or_above_alert_at) });
    const ub = drift.null_unstratified[u];
    if (ub.n !== nb.n) throw new Error(`drift.json: unstratified ${u} null has n=${ub.n}, not ${nb.n}`);
    const unstratView = (s: Signal): UnstratifiedView => ({ p50: sig(ub[s].p50), p95: sig(ub[s].p95), shareAtAlert: sig(ub[s].share_at_or_above_alert_at) });
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
      unstratified: { predictions: unstratView('predictions'), words: unstratView('words') },
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
 *  quarterly alerts that overlap a month alert (not independent evidence). A count that chance
 *  gives at least 10% of the time is read as within chance, even above the expectation. It also gives the
 *  either-signal chance, up to twice the per-signal one, since the reader watches two signals. */
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
  // At or below the expectation, or a count chance alone gives at least one time in ten: not evidence.
  if (seen <= v.expected.per_signal || binomialTail(n, rate, seen) >= 0.1) return `${head}${tail}: within what chance gives.`;
  // Two signals are two chances: by the union bound, the either-signal figure is up to twice the per-signal one.
  const either = `, and on either of the two signals up to ${pct(Math.min(1, 2 * binomialTail(n, rate, seen)))} (two signals, two chances)`;
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
  return `${head}${tail}${either}: weak evidence at most${reasons.length ? `, because ${reasons.join(', and ')}` : ''}.`;
}

/** Why the null is stratified, with the numbers: the sample's periods have fixed per-category quotas,
 *  so the null copies one 2022 period's category counts per draw. Simple random draws (what an
 *  unstratified stream would see) add class-mix noise this sample never has. */
export function nullDesignNote(p: DriftChartProps, u: DriftUnit): string {
  const v = p.units[u];
  const [s, us] = [v.null.predictions, v.unstratified.predictions];
  return (
    `The no-drift draws copy how the sample was drawn: each takes one 2022 ${u}'s count of complaints per category. ` +
    `Simple random draws of ${v.nullN}, the noise an unstratified stream would see, would put the predictions p95 at ` +
    `${us.p95.toFixed(3)} instead of ${s.p95.toFixed(3)}, and ${pct(us.shareAtAlert)} of no-drift ${u}s at or above ` +
    `${p.alertAt} instead of ${pct(s.shareAtAlert)}.`
  );
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
