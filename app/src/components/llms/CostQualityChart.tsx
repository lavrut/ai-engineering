import { useEffect, useRef, useState } from 'preact/hooks';
import { cheapestAbove, type CostChartProps, type CostPoint } from '../../lib/llmsAids';
import NoRecordedRun from './NoRecordedRun';

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const money = (x: number) => `$${x < 0.1 ? x.toFixed(3) : x.toFixed(2)}`;

// ── Scales (layout only; the numbers come from llmsAids) ────────────────────
const NICE = [1, 2, 5];
function niceBelow(v: number): number {
  const e = Math.floor(Math.log10(v));
  for (let k = e; k >= e - 1; k--) for (const m of [...NICE].reverse()) if (m * 10 ** k <= v) return m * 10 ** k;
  return 10 ** (e - 1);
}
function niceAbove(v: number): number {
  const e = Math.floor(Math.log10(v));
  for (let k = e; k <= e + 1; k++) for (const m of NICE) if (m * 10 ** k >= v) return m * 10 ** k;
  return 10 ** (e + 1);
}
/** 1-3-10 ticks inside the domain. */
function logTicks(lo: number, hi: number): number[] {
  const out: number[] = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
    for (const m of [1, 3]) {
      const v = m * 10 ** e;
      if (v >= lo * 0.999 && v <= hi * 1.001) out.push(Number(v.toPrecision(3)));
    }
  }
  return out;
}

type Placed = { x: number; y: number; anchor: 'start' | 'end'; lx: number; ly: number };
const LABEL_H = 14;
const CHAR_W = 6.4;

/** Direct labels beside each point, pushed apart vertically when two would overlap. */
function placeLabels(items: { x: number; y: number; text: string }[], left: number, right: number, top: number, bottom: number): Placed[] {
  const mid = left + (right - left) * 0.6;
  const placed: (Placed & { x0: number; x1: number })[] = [];
  const order = items.map((_, i) => i).sort((a, b) => items[a].y - items[b].y || items[a].x - items[b].x);
  const result: Placed[] = new Array(items.length);
  for (const i of order) {
    const { x, y, text } = items[i];
    const w = text.length * CHAR_W;
    const anchor = x > mid ? 'end' : 'start';
    const lx = anchor === 'start' ? x + 9 : x - 9;
    const x0 = anchor === 'start' ? lx : lx - w;
    const x1 = anchor === 'start' ? lx + w : lx;
    let ly = Math.max(top + LABEL_H - 3, y + 4);
    for (let tries = 0; tries < 2 * items.length + 1; tries++) {
      const hit = placed.find((p) => p.x0 < x1 && x0 < p.x1 && Math.abs(p.ly - ly) < LABEL_H);
      // Another point's dot under this label also counts as a collision.
      const dot = items.find((o, j) => j !== i && o.x > x0 - 6 && o.x < x1 + 6 && Math.abs(o.y - (ly - 4)) < 10);
      if (!hit && !dot) break;
      ly = hit ? hit.ly + LABEL_H : dot!.y + 4 + LABEL_H;
    }
    if (ly > bottom - 2) ly = bottom - 2;
    const p = { x, y, anchor, lx, ly, x0, x1 } as const;
    placed.push(p);
    result[i] = p;
  }
  return result;
}

function useWidth(initial: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setWidth(Math.max(260, Math.round(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export default function CostQualityChart({ recorded, split, prompts, initial, points }: CostChartProps) {
  const [prompt, setPrompt] = useState(initial);
  const [ref, width] = useWidth(640);

  // Axes are fixed across prompts, so switching prompt moves points, not the scale.
  const all = points.length ? points : [{ cost: 1, both: 1, band: 0 } as CostPoint];
  const xLo = niceBelow(Math.min(...all.map((p) => p.cost)) / 1.15);
  const xHi = niceAbove(Math.max(...all.map((p) => p.cost)) * 1.15);
  const yLo = Math.max(0, Math.floor((Math.min(...all.map((p) => p.both - p.band)) + 1e-9) * 10) / 10);
  const [targetMille, setTargetMille] = useState(Math.max(Math.round(yLo * 1000), 900));
  const target = targetMille / 1000;

  if (!recorded) {
    return (
      <section class="cq" aria-label="Cost and quality">
        <p class="cq-eyebrow">Cost and quality</p>
        <NoRecordedRun />
        <style>{css}</style>
      </section>
    );
  }

  const shown = points.filter((p) => p.prompt === prompt).sort((a, b) => a.cost - b.cost);
  const best = cheapestAbove(shown, target);
  const top = shown.reduce((a, b) => (b.both > a.both ? b : a), shown[0]);
  const splitName = split === 'all' ? 'all tickets' : `the ${split} split`;
  const n = shown[0]?.scored ?? 0;
  const band = shown[0]?.band ?? 0;

  // ── Geometry ──
  const H = width < 480 ? 300 : 340;
  const m = { l: 44, r: 12, t: 12, b: 40 };
  const x = (c: number) => m.l + ((Math.log10(c) - Math.log10(xLo)) / (Math.log10(xHi) - Math.log10(xLo))) * (width - m.l - m.r);
  const y = (v: number) => m.t + ((1 - v) / (1 - yLo)) * (H - m.t - m.b);
  const clampY = (v: number) => Math.min(1, Math.max(yLo, v));
  const yStep = 1 - yLo > 0.6 ? 0.2 : 0.1;
  const yTicks: number[] = [];
  for (let v = 1; v >= yLo - 1e-9; v -= yStep) yTicks.push(Math.round(v * 100) / 100);
  const labels = placeLabels(
    shown.map((p) => ({ x: x(p.cost), y: y(p.both), text: (p === best ? '✓ ' : '') + p.short })),
    m.l,
    width - m.r,
    m.t,
    H - m.b,
  );

  const status = best
    ? `${best.short} is the cheapest config at or above ${pct(target)}: ${money(best.cost)} per 1,000 tickets, ${pct(best.both)} both right.`
    : `No recorded config reaches ${pct(target)}. The most accurate is ${top.short} at ${pct(top.both)}.`;

  return (
    <section class="cq" aria-label="Cost and quality">
      <header class="cq-head">
        <p class="cq-eyebrow">Cost and quality · {splitName}, repeat 0</p>
        <label class="cq-picker" for="cq-prompt">
          <span class="cq-label">Prompt</span>
          <select id="cq-prompt" value={prompt} onChange={(e) => setPrompt((e.target as HTMLSelectElement).value)}>
            {prompts.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
      </header>

      <div class="cq-target">
        <label for="cq-target" class="cq-label">
          Target accuracy <span class="cq-target-value">{pct(target)}</span>
        </label>
        <input
          id="cq-target"
          type="range"
          min={Math.round(yLo * 1000)}
          max={1000}
          step={25}
          value={targetMille}
          aria-valuetext={pct(target)}
          onInput={(e) => setTargetMille(Number((e.target as HTMLInputElement).value))}
        />
      </div>
      <p class="cq-status" role="status">
        {status}
      </p>

      <p class="cq-axis-title" aria-hidden="true">
        ↑ Both fields right ({n} tickets, one run; bars show ± {pct(band)})
      </p>
      <div class="cq-plot" ref={ref}>
        <svg
          width={width}
          height={H}
          viewBox={`0 0 ${width} ${H}`}
          role="img"
          aria-label={`Scatter of ${shown.length} recorded configs with ${prompt}: x is the cost per 1,000 tickets on a log scale, y is the share with both fields right. The table below lists the same values.`}
        >
          {yTicks.map((v) => (
            <g key={`y${v}`}>
              <line class="cq-grid" x1={m.l} x2={width - m.r} y1={y(v)} y2={y(v)} />
              <text class="cq-tick" x={m.l - 6} y={y(v) + 4} text-anchor="end">
                {Math.round(v * 100)}%
              </text>
            </g>
          ))}
          {logTicks(xLo, xHi).map((c) => (
            <g key={`x${c}`}>
              <line class="cq-grid" x1={x(c)} x2={x(c)} y1={m.t} y2={H - m.b} />
              <text class="cq-tick" x={x(c)} y={H - m.b + 16} text-anchor="middle">
                ${c}
              </text>
            </g>
          ))}
          <line class="cq-axis" x1={m.l} x2={width - m.r} y1={H - m.b} y2={H - m.b} />
          <text class="cq-tick cq-xtitle" x={(m.l + width - m.r) / 2} y={H - 6} text-anchor="middle">
            Cost per 1,000 tickets, US$ (log scale) →
          </text>

          <line class="cq-target-line" x1={m.l} x2={width - m.r} y1={y(target)} y2={y(target)} />
          <text class="cq-tick" x={m.l + 4} y={y(target) - 5}>
            target {pct(target)}
          </text>

          {shown.map((p) => {
            const cx = x(p.cost);
            const y0 = y(clampY(p.both + p.band));
            const y1 = y(clampY(p.both - p.band));
            return (
              <g key={`e${p.name}`} class="cq-err">
                <line x1={cx} x2={cx} y1={y0} y2={y1} />
                <line x1={cx - 4} x2={cx + 4} y1={y0} y2={y0} />
                <line x1={cx - 4} x2={cx + 4} y1={y1} y2={y1} />
              </g>
            );
          })}
          {shown.map((p, i) => {
            const meets = p.both >= target - 1e-9;
            const l = labels[i];
            return (
              <g key={p.name} class={`cq-pt${meets ? ' is-met' : ''}${p === best ? ' is-best' : ''}`}>
                <title>{`${p.label}: ${money(p.cost)} per 1,000 tickets, ${pct(p.both)} ± ${pct(p.band)} both right`}</title>
                {Math.abs(l.ly - 4 - l.y) > 6 && <line class="cq-leader" x1={l.x} y1={l.y} x2={l.lx} y2={l.ly - 4} />}
                {p === best && <circle class="cq-ring" cx={l.x} cy={l.y} r={10} />}
                <circle class="cq-dot" cx={l.x} cy={l.y} r={5} />
                <text class="cq-label-text" x={l.lx} y={l.ly} text-anchor={l.anchor}>
                  {(p === best ? '✓ ' : '') + p.short}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <ul class="cq-key" aria-hidden="true">
        <li>
          <svg width="14" height="14" viewBox="0 0 14 14">
            <circle class="cq-dot cq-key-met" cx="7" cy="7" r="5" />
          </svg>
          filled: at or above the target
        </li>
        <li>
          <svg width="14" height="14" viewBox="0 0 14 14">
            <circle class="cq-dot" cx="7" cy="7" r="5" />
          </svg>
          hollow: below it
        </li>
        <li>✓ ringed: the cheapest that meets it</li>
      </ul>
      <p class="cq-note">
        Every point is one run of {n} tickets, so each carries a noise band of about ± {pct(band)} (1/√{n}). Points whose
        bars overlap are not shown to differ; a cheaper model that ties a bigger one is a real, usable result.
      </p>

      <div class="cq-scroll" role="region" aria-label="Cost and quality table" tabIndex={0}>
        <table class="cq-table">
          <caption class="cq-caption">
            {prompt} on {splitName}, cheapest first
          </caption>
          <thead>
            <tr>
              <th scope="col">Config</th>
              <th scope="col">Cost / 1,000 tickets</th>
              <th scope="col">Both right ± band</th>
              <th scope="col">Meets {pct(target)}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((p) => {
              const meets = p.both >= target - 1e-9;
              return (
                <tr key={p.name} class={p === best ? 'is-best' : undefined}>
                  <th scope="row">{p.short}</th>
                  <td>{money(p.cost)}</td>
                  <td class="cq-both">
                    {pct(p.both)} <span class="cq-pm">± {pct(p.band)}</span>
                  </td>
                  <td>{p === best ? '✓ cheapest' : meets ? 'yes' : 'no'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.cq { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.cq-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.cq-eyebrow, .cq-label, .cq-caption {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.cq-picker { display: inline-flex; align-items: center; gap: 8px; }
.cq-picker select {
  font: inherit; font-size: var(--text-small); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 5px 8px;
}
.cq-target { display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; }
.cq-target-value { color: var(--ink); font-weight: 600; text-transform: none; }
.cq-target input { width: 100%; accent-color: var(--accent); }
.cq-picker select:focus-visible, .cq-target input:focus-visible, .cq-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.cq-status {
  margin: 0; padding: 10px 14px; font-size: var(--text-small); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--good); border-radius: var(--radius);
}
.cq-axis-title { margin: 0; font-size: 0.8125rem; color: var(--muted); }
.cq-plot { min-width: 0; width: 100%; overflow: hidden; }
.cq-plot svg { display: block; max-width: 100%; height: auto; }
.cq-grid { stroke: var(--line); stroke-width: 1; }
.cq-axis { stroke: var(--muted); stroke-width: 1; }
.cq-tick { font-family: var(--font-mono); font-size: 11px; fill: var(--muted); }
.cq-xtitle { font-family: var(--font-body); font-size: 12px; }
.cq-target-line { stroke: var(--accent); stroke-width: 2; stroke-dasharray: 6 4; }
.cq-err line { stroke: var(--muted); stroke-width: 1.5; }
.cq-leader { stroke: var(--muted); stroke-width: 1; }
.cq-dot { fill: var(--surface); stroke: var(--muted); stroke-width: 2; }
.cq-pt.is-met .cq-dot, .cq-key-met { fill: var(--accent); stroke: var(--surface); }
.cq-ring { fill: none; stroke: var(--good); stroke-width: 2; }
.cq-label-text { font-family: var(--font-body); font-size: 11.5px; fill: var(--ink); paint-order: stroke; stroke: var(--bg); stroke-width: 3px; stroke-linejoin: round; }
.cq-pt.is-best .cq-label-text { font-weight: 700; }
.cq-key { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 4px 18px; font-size: var(--text-small); color: var(--muted); }
.cq-key li { display: flex; align-items: center; gap: 6px; }
.cq-note { margin: 0; font-size: var(--text-small); color: var(--muted); }
.cq-scroll { min-width: 0; max-width: 100%; overflow-x: auto; }
.cq-table { border-collapse: collapse; width: 100%; font-size: var(--text-small); color: var(--ink); }
.cq-caption { caption-side: top; text-align: left; padding: 0 0 6px; }
.cq-table th, .cq-table td { padding: 6px 6px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
.cq-table thead th { font-family: var(--font-mono); font-size: 0.75rem; font-weight: 500; color: var(--muted); white-space: normal; }
.cq-table td { font-family: var(--font-mono); font-size: 0.8125rem; }
.cq-both { white-space: normal; }
.cq-pm { color: var(--muted); white-space: nowrap; }
.cq-table th[scope="row"] { white-space: normal; }
.cq-table tr.is-best th, .cq-table tr.is-best td { font-weight: 700; background: color-mix(in srgb, var(--good) 12%, var(--surface)); }
`;
