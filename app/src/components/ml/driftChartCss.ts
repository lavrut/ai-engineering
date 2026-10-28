// DriftChart's styles (tokens only), shared by the chart, the signal charts and the class-share panel.
export const css = `
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
