import { useState } from 'preact/hooks';
import { diffRuns, pct, sortRuns, type FlatValue, type RunRow, type RunTableProps, type SortKey } from '../../lib/mlAids';

const SORTS: { key: SortKey; label: string; sentence: string }[] = [
  { key: 'time', label: 'Newest', sentence: 'newest first' },
  { key: 'accuracy', label: 'Accuracy', sentence: 'accuracy, highest first' },
  { key: 'macro_f1', label: 'Macro F1', sentence: 'macro F1, highest first' },
  { key: 'weighted_f1', label: 'Weighted F1', sentence: 'weighted F1, highest first' },
];
const n3 = (x: number) => x.toFixed(3);
const years = (w: { from: string; to: string }) => (w.from.slice(0, 4) === w.to.slice(0, 4) ? w.from.slice(0, 4) : `${w.from.slice(0, 4)}–${w.to.slice(0, 4)}`);
const evalLabel = (r: RunRow) => (r.eval.name === 'test' ? `test ${years(r.eval)}` : `${r.eval.name} ${r.eval.from.slice(0, 7)}…${r.eval.to.slice(0, 7)}`);
const knob = (r: RunRow) => (r.params.C !== undefined ? `C = ${r.params.C}` : r.params.alpha !== undefined ? `α = ${r.params.alpha}` : '');
const shown = (v: FlatValue) => (v === null ? '—' : typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(4)) : String(v));
const signed = (x: number) => `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(x).toFixed(4)}`;

export default function RunTable({ runs }: RunTableProps) {
  const [sort, setSort] = useState<SortKey>('time');
  const v1Run = runs.find((r) => r.stage === 'production')?.id ?? runs[0].id;
  const other = runs.find((r) => r.id !== v1Run && r.eval.name === runs.find((x) => x.id === v1Run)?.eval.name && r.params.model === runs.find((x) => x.id === v1Run)?.params.model);
  const [a, setA] = useState(v1Run);
  const [b, setB] = useState(other?.id ?? runs[runs.length - 1].id);
  const rows = sortRuns(runs, sort);
  const ra = runs.find((r) => r.id === a)!;
  const rb = runs.find((r) => r.id === b)!;
  const d = diffRuns(ra, rb);
  const sortInfo = SORTS.find((s) => s.key === sort)!;
  // One noise band per eval split (1/√n), in the order the splits first appear.
  const noise = runs
    .map((r) => ({ name: r.eval.name, n: r.metrics.n, band: r.metrics.band }))
    .filter((x, i, all) => all.findIndex((y) => y.name === x.name && y.n === x.n) === i);

  return (
    <section class="rt" aria-label="Run table">
      <header class="rt-head">
        <p class="rt-eyebrow">Tracked runs · ml/runs/*.json</p>
        <p class="rt-label">python mlops.py runs</p>
      </header>

      <div class="rt-group" role="group" aria-label="Sort runs by">
        <span class="rt-group-label">Sort by:</span>
        {SORTS.map((s) => (
          <button key={s.key} type="button" class="rt-toggle" aria-pressed={s.key === sort} onClick={() => setSort(s.key)}>
            {s.label}
          </button>
        ))}
      </div>
      <p class="rt-status" role="status">
        {runs.length} runs, sorted by {sortInfo.sentence}. Each is scored on its own eval split: compare metrics only between
        runs with the same one.
      </p>

      <div class="rt-scroll" role="region" aria-label="Tracked runs table" tabIndex={0}>
        <table class="rt-table" aria-label="Tracked runs">
          <thead>
            <tr>
              <th scope="col">Run</th>
              <th scope="col">Params</th>
              <th scope="col">Evaluated on</th>
              {(['accuracy', 'macro_f1', 'weighted_f1'] as const).map((k, i) => (
                <th key={k} scope="col" aria-sort={sort === k ? 'descending' : undefined}>
                  {['Accuracy', 'Macro F1', 'Weighted F1'][i]}
                  {sort === k && <span aria-hidden="true"> ↓</span>}
                </th>
              ))}
              <th scope="col">Data · model · git</th>
              <th scope="col">Registry</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} class={r.id === a || r.id === b ? 'is-picked' : ''}>
                <th scope="row">
                  <span class="rt-id">{r.id}</span>
                  <span class="rt-sub">
                    {r.date} · trained {years(r.train)}
                  </span>
                </th>
                <td class="rt-params">
                  {r.params.model} {knob(r)}
                  <span class="rt-sub">
                    min_df {r.params.min_df} · max {Number(r.params.max_features).toLocaleString('en-US')} · seed {r.params.seed}
                  </span>
                </td>
                <td class="rt-text">
                  {evalLabel(r)}
                  <span class="rt-sub">n = {r.metrics.n}</span>
                </td>
                <td>{pct(r.metrics.accuracy)}</td>
                <td>{n3(r.metrics.macro_f1)}</td>
                <td>{n3(r.metrics.weighted_f1)}</td>
                <td class="rt-hashes">
                  <span title={r.dataVersion}>data {r.dataVersion.replace('sha256:', '').slice(0, 8)}</span>
                  <span title={r.modelHash}>model {r.modelHash.slice(0, 8)}</span>
                  <span>
                    git {r.gitCommit ? r.gitCommit.slice(0, 7) : 'none'}
                    {r.gitDirty ? ' (dirty)' : ''}
                  </span>
                </td>
                <td class="rt-text">
                  {r.version ? (
                    <>
                      <strong>{r.version}</strong> {r.stage}
                      <span class="rt-sub">{r.hashMatches ? 'hash matches registry ✓' : 'hash differs from registry ✗'}</span>
                    </>
                  ) : (
                    <span class="rt-none">not registered</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="rt-noise">
        On the same split, differences smaller than the noise band are within noise:{' '}
        {noise.map((x) => `±${x.band.toFixed(3)} on ${x.name} (1/√${x.n})`).join(', ')}.
      </p>
      <p class="rt-hint">
        Hashes are prefixes (hover for the full SHA-256). Same data hash: the same complaints file. A registered version&apos;s
        model hash must equal the registry&apos;s, or the file was swapped.
      </p>

      <div class="rt-pick">
        <label>
          <span>Run A</span>
          <select value={a} onChange={(e) => setA((e.target as HTMLSelectElement).value)}>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.id}
                {r.version ? ` (${r.version})` : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Run B</span>
          <select value={b} onChange={(e) => setB((e.target as HTMLSelectElement).value)}>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.id}
                {r.version ? ` (${r.version})` : ''}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div class="rt-diff" role="region" aria-label="Run diff" aria-live="polite">
        <p class="rt-eyebrow">
          What changed · python mlops.py compare {a} {b}
        </p>
        {a === b ? (
          <p class="rt-hint">Pick two different runs.</p>
        ) : (
          <>
            {!d.sameEval && (
              <p class="rt-warn">
                <strong>⚠ Metrics not comparable:</strong> A was scored on {evalLabel(ra)}, B on {evalLabel(rb)}. Different complaints,
                so a higher number need not mean a better model.
              </p>
            )}
            {!d.sameData && <p class="rt-warn">⚠ Different data versions: the complaints file changed between these runs.</p>}
            <div class="rt-scroll" tabIndex={0} role="region" aria-label="Changed settings">
              <table class="rt-table rt-small" aria-label="Changed settings">
                <thead>
                  <tr>
                    <th scope="col">Setting</th>
                    <th scope="col">A</th>
                    <th scope="col">B</th>
                  </tr>
                </thead>
                <tbody>
                  {d.windows.map((w) => (
                    <tr key={w.key}>
                      <th scope="row">{w.key} window</th>
                      <td class="rt-text">{w.a}</td>
                      <td class="rt-text">{w.b}</td>
                    </tr>
                  ))}
                  {d.params.map((p) => (
                    <tr key={p.key}>
                      <th scope="row">{p.key}</th>
                      <td>{shown(p.a)}</td>
                      <td>{shown(p.b)}</td>
                    </tr>
                  ))}
                  {!d.params.length && (
                    <tr>
                      <td colSpan={3} class="rt-text rt-none">
                        No parameter changed.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div class="rt-scroll" tabIndex={0} role="region" aria-label="Changed metrics">
              <table class="rt-table rt-small" aria-label="Changed metrics">
                <thead>
                  <tr>
                    <th scope="col">Metric</th>
                    <th scope="col">A</th>
                    <th scope="col">B</th>
                    <th scope="col">B − A</th>
                  </tr>
                </thead>
                <tbody>
                  {d.metrics.map((m) => (
                    <tr key={m.key}>
                      <th scope="row" class="rt-key">
                        {m.key}
                      </th>
                      <td class={typeof m.a === 'string' ? 'rt-text' : ''}>{shown(m.a)}</td>
                      <td class={typeof m.b === 'string' ? 'rt-text' : ''}>{shown(m.b)}</td>
                      <td>{m.delta === null ? '—' : signed(m.delta)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p class="rt-hint">
              Only what differs is listed; metrics equal in both runs are left out. “—”: the other run has no such metric.
            </p>
          </>
        )}
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.rt { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.rt-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.rt-eyebrow {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted); overflow-wrap: anywhere;
}
.rt-label {
  margin: 0; padding: 3px 8px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--muted); border: 1px dashed var(--line); border-radius: var(--radius);
}
.rt-group { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.rt-group-label { font-size: var(--text-small); color: var(--muted); margin-right: 4px; }
.rt-toggle {
  font: inherit; font-size: var(--text-small); padding: 4px 12px; color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.rt-toggle[aria-pressed="true"] { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.rt-toggle:focus-visible, .rt-scroll:focus-visible, .rt-pick select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.rt-status, .rt-noise { margin: 0; font-size: var(--text-small); color: var(--ink); }
.rt-hint { margin: 0; font-size: var(--text-small); color: var(--muted); }
.rt-scroll { min-width: 0; max-width: 100%; overflow-x: auto; }
.rt-table { width: 100%; border-collapse: collapse; font-size: var(--text-small); font-variant-numeric: tabular-nums; background: var(--surface); border: 1px solid var(--line); }
.rt-table th, .rt-table td { padding: 6px 10px; text-align: right; border-bottom: 1px solid var(--line); vertical-align: top; }
.rt-table thead th { font-family: var(--font-mono); font-size: var(--text-label); font-weight: 500; color: var(--muted); white-space: nowrap; }
.rt-table th[scope="row"], .rt-table thead th:first-child { text-align: left; font-weight: 500; color: var(--ink); }
.rt-table td { font-family: var(--font-mono); color: var(--ink); white-space: nowrap; }
.rt-table td.rt-text, .rt-table td.rt-params { font-family: var(--font-body); text-align: left; }
.rt-table tr.is-picked th, .rt-table tr.is-picked td { background: var(--surface-2); }
.rt-id { display: block; font-family: var(--font-mono); white-space: nowrap; }
.rt-sub { display: block; font-family: var(--font-body); font-size: var(--text-label); color: var(--muted); white-space: nowrap; }
.rt-hashes { text-align: left !important; }
.rt-hashes span { display: block; font-size: var(--text-label); }
.rt-none { color: var(--muted); }
.rt-key { font-family: var(--font-mono); font-size: var(--text-label); overflow-wrap: break-word; min-width: 12rem; max-width: 22rem; }
.rt-small td.rt-text { white-space: normal; }
.rt-pick { display: flex; flex-wrap: wrap; gap: 10px 20px; }
.rt-pick label { display: grid; gap: 4px; font-size: var(--text-small); color: var(--muted); min-width: 0; max-width: 100%; }
.rt-pick select {
  font: inherit; font-family: var(--font-mono); font-size: var(--text-small); max-width: 100%; padding: 4px 8px;
  color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
}
.rt-diff { display: grid; gap: 8px; padding: 12px 14px; min-width: 0; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); }
.rt-warn {
  margin: 0; padding: 8px 12px; font-size: var(--text-small); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--warn); border-radius: var(--radius);
}
`;
