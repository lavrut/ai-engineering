// One drift signal's chart for DriftChart: the PSI per period, the null band, the thresholds and
// the alert marks, as SVG. All geometry and wording come from mlAids (driftGeometry, spreadLabels).
import {
  alertCounts,
  driftGeometry,
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
export const TITLE: Record<Signal, string> = {
  predictions: "Predicted-category mix",
  words: "Input words",
};
const ABOUT: Record<Signal, string> = {
  predictions: "what v1 says each period’s complaints are about",
  words: "which of the top training words the complaints use",
};
export const f3 = (x: number) => x.toFixed(3);
const tick = (x: number) =>
  x === 0 ? "0" : x.toFixed(Math.round(x * 100) === x * 100 ? 2 : 3);

export function alertText(p: DriftPoint, s: Signal): string[] {
  const out: string[] = [];
  if (p[`${s}_calibrated`]) out.push("▲ calibrated alert");
  if (p[`${s}_convention`]) out.push("■ convention alert");
  return out;
}

export function Chart(props: {
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
