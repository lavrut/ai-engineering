import { useState } from "preact/hooks";
import {
  alertCounts,
  chanceReading,
  driftGeometry,
  pct,
  spreadLabels,
  type DriftChartProps,
  type DriftPoint,
  type DriftUnit,
  type InSamplePoint,
  type Signal,
} from "../../lib/mlAids";

const BOX = {
  width: 560,
  height: 214,
  left: 46,
  right: 14,
  top: 16,
  bottom: 30,
};
const TITLE: Record<Signal, string> = {
  predictions: "Predicted-category mix",
  words: "Input words",
};
const ABOUT: Record<Signal, string> = {
  predictions: "what v1 says each period’s complaints are about",
  words: "which of the top training words the complaints use",
};
const f3 = (x: number) => x.toFixed(3);
const tick = (x: number) =>
  x === 0 ? "0" : x.toFixed(Math.round(x * 100) === x * 100 ? 2 : 3);

/** The default period: the first calibrated alert, else the last period. */
const firstAlert = (ps: DriftPoint[]) =>
  (
    ps.find((q) => q.predictions_calibrated || q.words_calibrated) ??
    ps[ps.length - 1]
  ).label;

function alertText(p: DriftPoint, s: Signal): string[] {
  const out: string[] = [];
  if (p[`${s}_calibrated`]) out.push("▲ calibrated alert");
  if (p[`${s}_convention`]) out.push("■ convention alert");
  return out;
}

function Chart(props: {
  signal: Signal;
  unit: DriftUnit;
  p: DriftChartProps;
  overlay: InSamplePoint[] | null;
  selected: string;
  onPick: (label: string) => void;
}) {
  const { signal: s, unit, p, overlay, selected, onPick } = props;
  const u = p.units[unit];
  const nul = u.null[s];
  const scale = u.scale[s];
  const g = driftGeometry(u.periods.length, scale.max, BOX);
  const right = BOX.width - BOX.right;
  const line = (vals: number[]) =>
    vals
      .map(
        (v, i) => `${i ? "L" : "M"}${g.x(i).toFixed(1)},${g.y(v).toFixed(1)}`,
      )
      .join(" ");
  const counts = alertCounts(u.periods)[s];
  const every = unit === "month" ? 6 : 2;
  const sel = u.periods.findIndex((q) => q.label === selected);
  const last = u.periods.length - 1;
  // Labels that share the right edge (the series names, and alerts in the last few periods) are spread apart vertically.
  const nearEnd = (i: number) => i >= last - 3;
  const endAlerts = u.periods.flatMap((q, i) =>
    q[`${s}_calibrated`] && nearEnd(i) ? [i] : [],
  );
  const floor = BOX.height - BOX.bottom - 4;
  const spread = spreadLabels(
    [
      ...endAlerts.map((i) => g.y(u.periods[i][s]) + 4),
      overlay ? g.y(overlay[last][s]) - 8 : floor,
      g.y(u.periods[last][s]) + 16,
    ].map((y) => Math.min(y, floor)),
    14,
  );
  const alertY = new Map(endAlerts.map((i, k) => [i, spread[k]]));
  const [overY, mainY] = spread.slice(endAlerts.length);
  const bandH = g.y(nul.p50) - g.y(nul.p95);
  const overConv = overlay
    ? overlay.filter((q) => q[`${s}_convention`]).length
    : 0;
  const summary =
    `${TITLE[s]} (${s}): PSI per ${unit}, ${u.periods[0].label} to ${u.periods[last].label}, against the 2022 reference. ` +
    `${counts.calibrated} above the calibrated threshold ${f3(nul.threshold)}; ${counts.convention} at or above the ${p.alertAt} convention.` +
    (overlay
      ? ` In-sample reference overlaid: ${overConv} at or above ${p.alertAt}.`
      : "") +
    " Every value is in the table below.";

  return (
    <figure class="dc-figure">
      <figcaption class="dc-title">
        <strong>{TITLE[s]}</strong>{" "}
        <span class="dc-sub">
          ({s}: {ABOUT[s]})
        </span>
      </figcaption>
      <div
        class="dc-svgbox"
        role="region"
        aria-label={`${TITLE[s]} chart`}
        tabIndex={0}
      >
        <svg
          viewBox={`0 0 ${BOX.width} ${BOX.height}`}
          role="img"
          aria-label={summary}
        >
          {scale.ticks.map((t) => (
            <g key={t}>
              <line
                class="dc-grid"
                x1={BOX.left}
                x2={right}
                y1={g.y(t)}
                y2={g.y(t)}
              />
              <text
                class="dc-tick"
                x={BOX.left - 6}
                y={g.y(t) + 4}
                text-anchor="end"
              >
                {tick(t)}
              </text>
            </g>
          ))}
          {u.periods.map((q, i) =>
            i % every === 0 ? (
              <text
                key={q.label}
                class="dc-tick"
                x={g.x(i)}
                y={BOX.height - 10}
                text-anchor="middle"
              >
                {q.label}
              </text>
            ) : null,
          )}
          <rect
            class="dc-band"
            x={BOX.left}
            width={right - BOX.left}
            y={g.y(nul.p95)}
            height={Math.max(1, g.y(nul.p50) - g.y(nul.p95))}
          />
          <text
            class="dc-ref"
            x={BOX.left + 4}
            y={bandH >= 14 ? g.y(nul.p95) + 11 : g.y(nul.p50) + 12}
          >
            null p50–p95
          </text>
          <line
            class="dc-conv"
            x1={BOX.left}
            x2={right}
            y1={g.y(p.alertAt)}
            y2={g.y(p.alertAt)}
          />
          <text class="dc-ref" x={BOX.left + 4} y={g.y(p.alertAt) - 4}>
            {p.alertAt} convention (rule of thumb)
          </text>
          <line
            class="dc-thr"
            x1={BOX.left}
            x2={right}
            y1={g.y(nul.threshold)}
            y2={g.y(nul.threshold)}
          />
          <text
            class="dc-ref dc-ref-strong"
            x={BOX.left + 4}
            y={g.y(nul.threshold) - 4}
          >
            calibrated threshold {f3(nul.threshold)}
          </text>

          {sel >= 0 && (
            <line
              class="dc-sel"
              x1={g.x(sel)}
              x2={g.x(sel)}
              y1={BOX.top}
              y2={BOX.height - BOX.bottom}
            />
          )}

          {overlay && (
            <g class="dc-over">
              <path d={line(overlay.map((q) => q[s]))} />
              {overlay.map((q, i) =>
                q[`${s}_convention`] ? (
                  <rect
                    key={q.label}
                    class="dc-conv-mark"
                    x={g.x(i) - 4}
                    y={g.y(q[s]) - 4}
                    width={8}
                    height={8}
                  />
                ) : (
                  <circle key={q.label} cx={g.x(i)} cy={g.y(q[s])} r={2.5} />
                ),
              )}
              <text
                class="dc-series-label"
                x={right}
                y={overY}
                text-anchor="end"
              >
                in-sample (misleading)
              </text>
            </g>
          )}

          <g class="dc-main">
            <path d={line(u.periods.map((q) => q[s]))} />
            {u.periods.map((q, i) => {
              const x = g.x(i);
              const y = g.y(q[s]);
              if (q[`${s}_calibrated`]) {
                return (
                  <g key={q.label}>
                    <path
                      class="dc-cal-mark"
                      d={`M${x},${y - 7} L${x + 6.5},${y + 4.5} L${x - 6.5},${y + 4.5} Z`}
                    />
                    <text
                      class="dc-ref dc-ref-strong"
                      x={x - 10}
                      y={alertY.get(i) ?? y + 4}
                      text-anchor="end"
                    >
                      {q.label}
                    </text>
                  </g>
                );
              }
              if (q[`${s}_convention`])
                return (
                  <rect
                    key={q.label}
                    class="dc-conv-mark"
                    x={x - 4}
                    y={y - 4}
                    width={8}
                    height={8}
                  />
                );
              return (
                <circle key={q.label} cx={x} cy={y} r={i === sel ? 4.5 : 3} />
              );
            })}
            <text class="dc-series-label" x={right} y={mainY} text-anchor="end">
              2022 reference
            </text>
          </g>

          {u.periods.map((q, i) => (
            <rect
              key={q.label}
              class="dc-hit"
              x={g.x(i) - Math.max(g.step, 8) / 2}
              y={BOX.top}
              width={Math.max(g.step, 8)}
              height={BOX.height - BOX.top - BOX.bottom}
              onClick={() => onPick(q.label)}
            >
              <title>
                {q.label}: {s} PSI {f3(q[s])}
                {alertText(q, s).length
                  ? ` (${alertText(q, s).join(", ")})`
                  : ""}
                , n = {q.n}
              </title>
            </rect>
          ))}
        </svg>
      </div>
    </figure>
  );
}

export default function DriftChart(p: DriftChartProps) {
  const [unit, setUnit] = useState<DriftUnit>("month");
  const [inSample, setInSample] = useState(false);
  const [selected, setSelected] = useState(() =>
    firstAlert(p.units.month.periods),
  );
  const [year, setYear] = useState(p.years[p.years.length - 1]);
  const u = p.units[unit];
  const overlay = unit === "month" && inSample ? p.inSample : null;
  const point =
    u.periods.find((q) => q.label === selected) ??
    u.periods[u.periods.length - 1];
  const yi = p.years.indexOf(year);
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
          2022
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
          Words that moved the word mix most (their share of the words PSI):
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

      <div class="dc-shares">
        <p class="dc-eyebrow">
          Class shares per year · real CFPB vs the committed sample
        </p>
        <div class="dc-group" role="group" aria-label="Class shares: year">
          {p.years.map((y) => (
            <button
              key={y}
              type="button"
              class="dc-toggle"
              aria-pressed={y === year}
              onClick={() => setYear(y)}
            >
              {y}
            </button>
          ))}
        </div>
        <div
          class="dc-scroll"
          role="region"
          aria-label="Class shares table"
          tabIndex={0}
        >
          <table
            class="dc-table dc-share-table"
            aria-label={`Class shares in ${year}: real vs sample`}
          >
            <thead>
              <tr>
                <th scope="col">Category</th>
                <th scope="col">Real (solid)</th>
                <th scope="col">Sample (hatched)</th>
              </tr>
            </thead>
            <tbody>
              {p.classes.map((c, i) => (
                <tr key={c}>
                  <th scope="row">{c}</th>
                  <td>
                    <span class="dc-bar dc-bar-real" aria-hidden="true">
                      <span style={{ width: `${p.real[i][yi] * 100}%` }} />
                    </span>
                    {pct(p.real[i][yi])}
                  </td>
                  <td>
                    <span class="dc-bar dc-bar-sample" aria-hidden="true">
                      <span style={{ width: `${p.sample[i][yi] * 100}%` }} />
                    </span>
                    {pct(p.sample[i][yi])}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p class="dc-hint">
          The sample is stratified by design, so it mutes the real shift in
          class mix (bars run from 0 to 100%). Drift measured on the sample
          cannot see that shift; on the full stream the predictions signal
          would.
        </p>
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.dc { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.dc-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.dc-eyebrow {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.dc-label {
  margin: 0; padding: 3px 8px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--muted); border: 1px dashed var(--line); border-radius: var(--radius);
}
.dc-controls { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; }
.dc-group { display: flex; flex-wrap: wrap; gap: 6px; }
.dc-toggle {
  font: inherit; font-size: var(--text-small); padding: 4px 12px; color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.dc-toggle[aria-pressed="true"] { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.dc-toggle:disabled { color: var(--muted); background: transparent; border-style: dashed; cursor: default; }
.dc-toggle:focus-visible, .dc-scroll:focus-visible, .dc-picker select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.dc-hint { margin: 0; font-size: var(--text-small); color: var(--muted); }
.dc-reading { margin: 0; font-weight: 600; color: var(--ink); }
.dc-note {
  margin: 0; padding: 10px 14px; font-size: var(--text-small); color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-left: 4px solid var(--warn); border-radius: var(--radius);
}
.dc-charts { display: grid; gap: 12px; min-width: 0; }
.dc-figure { margin: 0; min-width: 0; padding: 8px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.dc-title { margin: 0 0 4px; font-size: var(--text-small); color: var(--ink); }
.dc-sub { color: var(--muted); }
.dc-figure svg { display: block; width: 100%; max-width: 720px; margin: 0 auto; height: auto; overflow: visible; }
.dc-grid { stroke: var(--line); stroke-width: 1; }
.dc-tick { font-family: var(--font-mono); font-size: 11px; fill: var(--muted); }
.dc-band { fill: color-mix(in srgb, var(--accent) 14%, var(--surface)); }
.dc-thr { stroke: var(--ink); stroke-width: 1.5; }
.dc-conv { stroke: var(--muted); stroke-width: 1.5; stroke-dasharray: 6 4; }
.dc-ref, .dc-series-label {
  font-family: var(--font-mono); font-size: 11px; fill: var(--muted);
  paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round;
}
.dc-ref-strong, .dc-series-label { fill: var(--ink); }
.dc-sel { stroke: var(--accent); stroke-width: 1.5; stroke-dasharray: 2 3; }
.dc-main path { fill: none; stroke: var(--accent); stroke-width: 2; }
.dc-main circle { fill: var(--accent); stroke: var(--surface); stroke-width: 1.5; }
.dc-over path { fill: none; stroke: var(--role-tool); stroke-width: 2; stroke-dasharray: 5 4; }
.dc-over circle { fill: var(--surface); stroke: var(--role-tool); stroke-width: 1.5; }
.dc-cal-mark { fill: var(--bad); stroke: var(--surface); stroke-width: 1.5; }
.dc-conv-mark { fill: var(--surface); stroke: var(--warn); stroke-width: 2; }
.dc-hit { fill: transparent; cursor: pointer; }
.dc-hit:hover { fill: color-mix(in srgb, var(--accent) 8%, transparent); }
.dc-svgbox { min-width: 0; overflow-x: auto; }
.dc-svgbox:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 599px) { .dc-figure svg { min-width: 520px; } }
.dc-legend { margin: 0; padding: 0; list-style: none; display: grid; gap: 4px; font-size: var(--text-small); color: var(--muted); }
.dc-legend li { display: flex; align-items: center; gap: 8px; }
.dc-legend svg { width: 28px; height: 10px; flex: none; overflow: visible; }
.dc-key-main { stroke: var(--accent); stroke-width: 2; }
.dc-key-over { stroke: var(--role-tool); stroke-width: 2; stroke-dasharray: 5 4; }
.dc-picker { display: flex; flex-wrap: wrap; align-items: end; gap: 6px 14px; }
.dc-picker label { display: grid; gap: 4px; font-size: var(--text-small); color: var(--muted); }
.dc-picker select {
  font: inherit; font-family: var(--font-mono); font-size: var(--text-small); padding: 4px 8px;
  color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
}
.dc-detail { display: grid; gap: 6px; padding: 12px 14px; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.dc-detail-head { margin: 0; color: var(--ink); }
.dc-signals, .dc-words { margin: 0; padding-left: 1.25rem; font-size: var(--text-small); color: var(--ink); font-variant-numeric: tabular-nums; }
.dc-word, .dc-num { font-family: var(--font-mono); }
.dc-num { color: var(--muted); }
.dc-scroll { min-width: 0; max-width: 100%; overflow-x: auto; }
.dc-tablebox { max-height: 22rem; overflow-y: auto; }
.dc-table { width: 100%; border-collapse: collapse; font-size: var(--text-small); font-variant-numeric: tabular-nums; background: var(--surface); border: 1px solid var(--line); }
.dc-table th, .dc-table td { padding: 5px 10px; text-align: right; border-bottom: 1px solid var(--line); white-space: nowrap; }
.dc-table thead th { position: sticky; top: 0; background: var(--surface); font-family: var(--font-mono); font-size: var(--text-label); font-weight: 500; color: var(--muted); }
.dc-table th[scope="row"], .dc-table thead th:first-child { text-align: left; font-weight: 500; color: var(--ink); font-family: var(--font-mono); }
.dc-table td { font-family: var(--font-mono); color: var(--ink); }
.dc-table th.dc-left { text-align: left; }
.dc-table td.dc-alerts { font-family: var(--font-body); text-align: left; }
.dc-table tfoot th, .dc-table tfoot td { font-weight: 600; border-top: 2px solid var(--line); }
.dc-table tr.is-sel th, .dc-table tr.is-sel td { background: var(--surface-2); }
.dc-shares { display: grid; gap: 8px; min-width: 0; }
.dc-share-table th[scope="row"] { font-family: var(--font-body); white-space: normal; min-width: 9rem; }
.dc-bar { display: inline-block; width: 5rem; height: 8px; margin-right: 8px; background: var(--surface-2); border-radius: 999px; overflow: hidden; vertical-align: middle; }
.dc-bar span { display: block; height: 100%; }
.dc-bar-real span { background: var(--role-model); }
.dc-bar-sample span { background: repeating-linear-gradient(135deg, var(--role-dataset) 0 3px, transparent 3px 5px); border: 1px solid var(--role-dataset); box-sizing: border-box; }
`;
