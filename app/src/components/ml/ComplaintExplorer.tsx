import { useState } from 'preact/hooks';
import { balanceRows, filterSnippets, pct, shortName, splitOfYear, type ComplaintExplorerProps, type SnippetFilter } from '../../lib/mlAids';

const fmt = (n: number) => n.toLocaleString('en-US');

export default function ComplaintExplorer(p: ComplaintExplorerProps) {
  const [year, setYear] = useState<string | null>(null);
  const [filter, setFilter] = useState<SnippetFilter>({});
  const rows = balanceRows(p, year);
  const max = Math.max(...rows.map((r) => Math.max(r.share, r.real ?? 0)));
  const split = year ? splitOfYear(p.splits, year) : null;
  const yearTotal = rows.reduce((a, r) => a + r.count, 0);
  const shown = filterSnippets(p.snippets, filter);
  const snippetYears = [...new Set(p.snippets.map((s) => s.year))].sort();
  const toggle = (field: keyof SnippetFilter, value: string) => setFilter((f) => ({ ...f, [field]: f[field] === value ? null : value }));
  const bar = (x: number) => ({ width: `${max ? (x / max) * 100 : 0}%` });

  return (
    <section class="cx" aria-label="Complaint explorer">
      <header class="cx-head">
        <p class="cx-eyebrow">Complaint explorer · {fmt(p.n)} CFPB complaints</p>
        <ul class="cx-splits" aria-label="Time splits">
          {p.splits.map((s) => (
            <li key={s.name} class={`cx-split cx-split-${s.name}`}>
              {s.label} · {fmt(s.n)}
            </li>
          ))}
        </ul>
      </header>

      <div class="cx-years" role="group" aria-label="Year">
        <button type="button" class="cx-toggle" aria-pressed={year === null} onClick={() => setYear(null)}>
          All years
        </button>
        {p.years.map((y) => (
          <button key={y} type="button" class="cx-toggle" aria-pressed={year === y} onClick={() => setYear(y)}>
            {y} <span class="cx-toggle-split">{splitOfYear(p.splits, y).name}</span>
          </button>
        ))}
      </div>
      <p class="cx-status" aria-live="polite">
        {year && split
          ? `${year} · ${split.name} split · ${fmt(yearTotal)} complaints in the sample`
          : `All years, ${p.years[0]}–${p.years[p.years.length - 1]} · ${fmt(yearTotal)} complaints in the sample`}
      </p>

      <div class="cx-scroll" role="region" aria-label="Category balance table" tabIndex={0}>
        <table class="cx-table" aria-label={`Category balance, ${year ?? 'all years'}`}>
          <thead>
            <tr>
              <th scope="col">Category</th>
              <th scope="col" class="cx-num">Sample</th>
              <th scope="col">Share</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.category}>
                <th scope="row" title={r.category}>
                  {shortName(r.category).replace('/', '/\u200b')}
                </th>
                <td class="cx-num">{fmt(r.count)}</td>
                <td class="cx-bars">
                  <span class="cx-bar-row">
                    <span class="cx-bar-name">sample</span>
                    <span class="cx-bar" aria-hidden="true">
                      <span class="cx-fill cx-fill-sample" style={bar(r.share)} />
                    </span>
                    <span class="cx-bar-value">{pct(r.share)}</span>
                  </span>
                  <span class="cx-bar-row">
                    <span class="cx-bar-name">real</span>
                    {r.real === null ? (
                      <span class="cx-bar-none">per year only</span>
                    ) : (
                      <>
                        <span class="cx-bar" aria-hidden="true">
                          <span class="cx-fill cx-fill-real" style={bar(r.real)} />
                        </span>
                        <span class="cx-bar-value">{pct(r.real)}</span>
                      </>
                    )}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="cx-caption">
        <strong>The sample is stratified by design:</strong> 50 complaints a month, at least 5 per category, so its class
        mix is muted. <em>Real</em> is each category&apos;s share of that year&apos;s CFPB complaints with a narrative in these
        six categories; pick a year to compare. Details in <code>ml/data/README.md</code>.
      </p>

      <div class="cx-snippets">
        <p class="cx-eyebrow">Sample narratives</p>
        <div class="cx-filters">
          <div class="cx-group" role="group" aria-label="Snippets: category">
            {p.classes.map((c) => (
              <button key={c} type="button" class="cx-chip" aria-pressed={filter.category === c} title={c} onClick={() => toggle('category', c)}>
                {shortName(c)}
              </button>
            ))}
          </div>
          <div class="cx-group" role="group" aria-label="Snippets: year">
            {snippetYears.map((y) => (
              <button key={y} type="button" class="cx-chip" aria-pressed={filter.year === y} onClick={() => toggle('year', y)}>
                {y}
              </button>
            ))}
          </div>
          <button type="button" class="cx-clear" disabled={!filter.category && !filter.year} onClick={() => setFilter({})}>
            Clear snippet filters
          </button>
        </div>
        <p class="cx-count" aria-live="polite">
          {shown.length} of {p.snippets.length} snippets
        </p>
        <p class="cx-hint">Three per category, from the training years, cut to 200 characters as dataset.json keeps them.</p>
        <div class="cx-list" role="region" aria-label="Snippets" tabIndex={0}>
        <ul>
          {shown.map((s) => (
            <li key={s.id} class="cx-item">
              <p class="cx-meta">
                <span class="cx-id">{s.id}</span>
                <span>{s.date}</span>
                <span class="cx-cat">{s.category}</span>
                {s.product_raw !== s.category && <span class="cx-raw">published as “{s.product_raw}”</span>}
              </p>
              <p class="cx-text">{s.text}</p>
            </li>
          ))}
        </ul>
        </div>
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.cx { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.cx-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.cx-eyebrow {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.cx-splits { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 6px; }
.cx-split {
  padding: 2px 8px; font-family: var(--font-mono); font-size: 0.75rem; color: var(--ink);
  border: 1px solid var(--line); border-radius: 999px; background: var(--surface-2);
}
.cx-split-test { border-style: dashed; }
.cx-split-stream { border-style: dotted; border-width: 2px; }
.cx-years, .cx-group { display: flex; flex-wrap: wrap; gap: 6px; }
.cx-toggle, .cx-chip, .cx-clear {
  font: inherit; font-family: var(--font-mono); font-size: 0.8125rem; padding: 3px 10px; color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.cx-toggle-split { color: var(--muted); font-size: 0.6875rem; }
.cx-toggle[aria-pressed="true"], .cx-chip[aria-pressed="true"] { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.cx-toggle[aria-pressed="true"] .cx-toggle-split { color: inherit; }
.cx-clear { justify-self: start; border-radius: var(--radius); font-family: var(--font-body); font-size: var(--text-small); padding: 4px 12px; }
.cx-clear:disabled { color: var(--muted); background: transparent; cursor: default; }
.cx-toggle:focus-visible, .cx-chip:focus-visible, .cx-clear:focus-visible, .cx-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.cx-status, .cx-count { margin: 0; font-family: var(--font-mono); font-size: var(--text-small); font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.cx-scroll { min-width: 0; max-width: 100%; overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.cx-table { width: 100%; border-collapse: collapse; font-size: var(--text-small); }
.cx-table th, .cx-table td { padding: 6px 6px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: middle; }
.cx-table tbody tr:last-child th, .cx-table tbody tr:last-child td { border-bottom: 0; }
.cx-table thead th { font-family: var(--font-mono); font-size: var(--text-label); font-weight: 500; color: var(--muted); }
.cx-table tbody th { font-weight: 500; color: var(--ink); max-width: 8rem; }
.cx-num { text-align: right !important; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.cx-bars { min-width: 8rem; width: 55%; }
.cx-bar-row { display: grid; grid-template-columns: 2.5rem minmax(1.25rem, 1fr) 2.75rem; align-items: center; gap: 6px; }
.cx-bar-name { font-family: var(--font-mono); font-size: 0.6875rem; color: var(--muted); }
.cx-bar { height: 8px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }
.cx-fill { display: block; height: 100%; border-radius: 999px; }
.cx-fill-sample { background: var(--role-dataset); }
.cx-fill-real { background: repeating-linear-gradient(135deg, var(--role-model) 0 3px, transparent 3px 5px); border: 1px solid var(--role-model); box-sizing: border-box; }
.cx-bar-value { font-family: var(--font-mono); font-size: 0.75rem; text-align: right; color: var(--ink); font-variant-numeric: tabular-nums; }
.cx-bar-none { grid-column: 2 / 4; font-size: 0.75rem; color: var(--muted); }
.cx-caption, .cx-hint { margin: 0; font-size: var(--text-small); color: var(--muted); }
.cx-caption strong { color: var(--ink); }
.cx-snippets {
  display: grid; gap: 10px; padding: 12px 14px; min-width: 0;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius);
}
.cx-filters { display: grid; gap: 8px; }
.cx-list { max-height: 24rem; overflow: auto; overscroll-behavior: contain; }
.cx-list ul { margin: 0; padding: 0; list-style: none; }
.cx-list:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.cx-item { padding: 8px 0; border-top: 1px solid var(--line); }
.cx-meta { margin: 0 0 4px; display: flex; flex-wrap: wrap; gap: 4px 10px; font-family: var(--font-mono); font-size: 0.75rem; color: var(--muted); }
.cx-id { font-weight: 600; color: var(--ink); }
.cx-cat { color: var(--ink); }
.cx-raw { font-style: italic; }
.cx-text { margin: 0; font-size: var(--text-small); line-height: 1.55; color: var(--ink); overflow-wrap: anywhere; }
`;
