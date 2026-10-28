import { useState } from "preact/hooks";
import {
  alertCounts,
  chanceReading,
  nullDesignNote,
  type DriftChartProps,
  type DriftPoint,
  type DriftUnit,
} from "../../lib/mlAids";
import { Chart, TITLE, alertText, f3 } from "./DriftSignalChart";
import { ClassShares } from "./DriftClassShares";
import { css } from "./driftChartCss";

/** The default period: the first calibrated alert, else the last period. */
const firstAlert = (ps: DriftPoint[]) =>
  (
    ps.find((q) => q.predictions_calibrated || q.words_calibrated) ??
    ps[ps.length - 1]
  ).label;


export default function DriftChart(p: DriftChartProps) {
  const [unit, setUnit] = useState<DriftUnit>("month");
  const [inSample, setInSample] = useState(false);
  const [selected, setSelected] = useState(() =>
    firstAlert(p.units.month.periods),
  );
  const u = p.units[unit];
  const overlay = unit === "month" && inSample ? p.inSample : null;
  const point =
    u.periods.find((q) => q.label === selected) ??
    u.periods[u.periods.length - 1];
  const realConv = alertCounts(p.units.month.periods).predictions.convention;
  const inConv = p.inSample.filter((q) => q.predictions_convention).length;

  const switchUnit = (next: DriftUnit) => {
    setUnit(next);
    setSelected(firstAlert(p.units[next].periods));
  };

  return (
    <section class="dc" aria-label="Drift chart">
      <header class="dc-head">
        <p class="dc-eyebrow">
          Drift · PSI per period against the 2022 reference
        </p>
        <p class="dc-label">reports/drift.json</p>
      </header>

      <div class="dc-controls">
        <div class="dc-group" role="group" aria-label="View">
          {(["month", "quarter"] as const).map((k) => (
            <button
              key={k}
              type="button"
              class="dc-toggle"
              aria-pressed={unit === k}
              onClick={() => switchUnit(k)}
            >
              {k === "month" ? "Monthly" : "Quarterly"}
            </button>
          ))}
        </div>
        <button
          type="button"
          class="dc-toggle"
          aria-pressed={unit === "month" && inSample}
          disabled={unit !== "month"}
          onClick={() => setInSample((v) => !v)}
        >
          Overlay the in-sample training reference
        </button>
      </div>
      <p class="dc-hint">
        {unit === "month"
          ? `${u.periods.length} months of ${u.periods[0].n} complaints. `
          : `${u.periods.length} quarters of ${u.periods[0].n} complaints; the in-sample variant was computed for months only. `}
        Each period is compared with the reference: {p.reference}.
      </p>

      <p class="dc-reading" aria-live="polite">
        {chanceReading(p, unit)}
      </p>

      <div class="dc-charts">
        {(["predictions", "words"] as const).map((s) => (
          <Chart
            key={s}
            signal={s}
            unit={unit}
            p={p}
            overlay={overlay}
            selected={point.label}
            onPick={setSelected}
          />
        ))}
      </div>

      <ul class="dc-legend" aria-label="Chart key">
        <li>
          <svg viewBox="0 0 28 10" aria-hidden="true">
            <line class="dc-key-main" x1="0" x2="28" y1="5" y2="5" />
          </svg>
          2022 reference: PSI per {unit} (held-out data v1 never trained on)
        </li>
        {overlay && (
          <li>
            <svg viewBox="0 0 28 10" aria-hidden="true">
              <line class="dc-key-over" x1="0" x2="28" y1="5" y2="5" />
            </svg>
            in-sample (misleading): v1&apos;s own training rows as the reference
          </li>
        )}
        <li>
          <svg viewBox="0 0 28 10" aria-hidden="true">
            <rect class="dc-band" x="0" y="1" width="28" height="8" />
          </svg>
          null p50–p95: PSI with no drift, from {u.nullN}-complaint draws of
          2022 with a 2022 {unit}&apos;s category counts
        </li>
        <li>
          <svg viewBox="0 0 28 10" aria-hidden="true">
            <line class="dc-thr" x1="0" x2="28" y1="5" y2="5" />
          </svg>
          calibrated threshold: the larger of the null p95 and the biggest
          within-2022 value
        </li>
        <li>
          <svg viewBox="0 0 28 10" aria-hidden="true">
            <line class="dc-conv" x1="0" x2="28" y1="5" y2="5" />
          </svg>
          {p.alertAt} convention: an industry rule of thumb, not calibrated to
          this data
        </li>
        <li>
          ▲ calibrated alert: above the calibrated threshold (per signal and
          period)
        </li>
        <li>■ convention alert: PSI at or above {p.alertAt}</li>
      </ul>
      {overlay && (
        <p class="dc-note" role="note">
          <strong>Why in-sample misleads:</strong> {p.inSampleNote} With it,{" "}
          {inConv} of {p.inSample.length} months reach the {p.alertAt}{" "}
          convention on predictions; against the 2022 reference, {realConv} do.
        </p>
      )}

      <div class="dc-picker">
        <label>
          <span>Period</span>
          <select
            value={point.label}
            onChange={(e) => setSelected((e.target as HTMLSelectElement).value)}
          >
            {u.periods.map((q) => (
              <option key={q.label} value={q.label}>
                {q.label}
                {q.predictions_calibrated || q.words_calibrated ? " ▲" : ""}
              </option>
            ))}
          </select>
        </label>
        <span class="dc-hint">or click a period on a chart</span>
      </div>
      <div
        class="dc-detail"
        role="region"
        aria-label="Period detail"
        aria-live="polite"
      >
        <p class="dc-detail-head">
          <strong>{point.label}</strong> · {point.n} complaints
          {point.n < p.minN
            ? ` (fewer than ${p.minN}: shown, never alerted)`
            : ""}
        </p>
        <ul class="dc-signals">
          {(["predictions", "words"] as const).map((s) => {
            const thr = u.null[s].threshold;
            const marks = alertText(point, s);
            return (
              <li key={s}>
                {TITLE[s]} PSI <strong>{f3(point[s])}</strong>:{" "}
                {marks.length ? marks.join(", ") : "no alert"} (calibrated
                threshold {f3(thr)}, convention {p.alertAt})
              </li>
            );
          })}
        </ul>
        <p class="dc-hint">
          Words that moved the word mix most, with their part of the words PSI
          (total {f3(point.words)}):
        </p>
        <ol class="dc-words">
          {point.top.map((w) => (
            <li key={w.word}>
              <span class="dc-word">{w.word}</span>{" "}
              {w.direction === "up" ? "↑ used more often" : "↓ used less often"}{" "}
              <span class="dc-num">{w.contribution.toFixed(4)}</span>
            </li>
          ))}
        </ol>
      </div>

      <div
        class="dc-scroll dc-tablebox"
        role="region"
        aria-label={`Table: PSI by ${unit}`}
        tabIndex={0}
      >
        <table class="dc-table" aria-label={`PSI by ${unit}`}>
          <thead>
            <tr>
              <th scope="col">{unit === "month" ? "Month" : "Quarter"}</th>
              <th scope="col">n</th>
              <th scope="col">Predictions PSI</th>
              <th scope="col">Words PSI</th>
              <th scope="col" class="dc-left">
                Alerts
              </th>
              {overlay && <th scope="col">In-sample predictions</th>}
              {overlay && <th scope="col">In-sample words</th>}
            </tr>
          </thead>
          <tbody>
            {u.periods.map((q, i) => {
              const marks = (["predictions", "words"] as const).flatMap((s) =>
                alertText(q, s).map((m) => `${m} (${s})`),
              );
              return (
                <tr
                  key={q.label}
                  class={q.label === point.label ? "is-sel" : ""}
                >
                  <th scope="row">{q.label}</th>
                  <td>{q.n}</td>
                  <td>{f3(q.predictions)}</td>
                  <td>{f3(q.words)}</td>
                  <td class="dc-alerts">
                    {marks.length ? marks.join("; ") : "—"}
                  </td>
                  {overlay && (
                    <td>
                      {f3(overlay[i].predictions)}
                      {overlay[i].predictions_convention ? " ■" : ""}
                    </td>
                  )}
                  {overlay && (
                    <td>
                      {f3(overlay[i].words)}
                      {overlay[i].words_convention ? " ■" : ""}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row">Thresholds</th>
              <td>{u.nullN}</td>
              <td>{f3(u.null.predictions.threshold)}</td>
              <td>{f3(u.null.words.threshold)}</td>
              <td class="dc-alerts">calibrated; convention {p.alertAt}</td>
              {overlay && <td />}
              {overlay && <td />}
            </tr>
          </tfoot>
        </table>
      </div>
      <p class="dc-hint">{p.expectedNote}</p>
      <p class="dc-hint">{nullDesignNote(p, unit)}</p>

      <ClassShares p={p} />
      <style>{css}</style>
    </section>
  );
}
