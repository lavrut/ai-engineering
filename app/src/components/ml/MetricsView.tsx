import { useState } from 'preact/hooks';
import { cellDetail, overfit, pct, shortName, type MetricsViewProps, type ModelId } from '../../lib/mlAids';

type Split = 'train' | 'test';
const SPLIT_LABEL: Record<Split, string> = { train: 'Train (2019–2021)', test: 'Test (2022)' };
const SPLIT_YEARS: Record<Split, string> = { train: '2019–2021', test: '2022' };
const n3 = (x: number) => x.toFixed(3);
const points = (x: number) => (x * 100).toFixed(1);
const plural = (n: number) => `${n} complaint${n === 1 ? '' : 's'}`;

export default function MetricsView({ classes, models, note }: MetricsViewProps) {
  const [modelId, setModelId] = useState<ModelId>('logreg');
  const [split, setSplit] = useState<Split>('test');
  const [cell, setCell] = useState<[number, number] | null>(null);
  const model = models.find((m) => m.id === modelId) ?? models[models.length - 1];
  const s = model[split];
  const gap = overfit(model);
  const detail = cell ? cellDetail(s, cell[0], cell[1]) : null;
  const max = Math.max(...s.confusion.flat());

  return (
    <section class="mv" aria-label="Metrics view">
      <header class="mv-head">
        <p class="mv-eyebrow">Evaluation · six categories</p>
      </header>

      <div class="mv-controls">
        <div class="mv-group" role="group" aria-label="Model">
          {models.map((m) => (
            <button
              key={m.id}
              type="button"
              class="mv-toggle"
              aria-pressed={m.id === model.id}
              onClick={() => {
                setModelId(m.id);
                setCell(null);
              }}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div class="mv-group" role="group" aria-label="Split">
          {(['train', 'test'] as const).map((k) => (
            <button
              key={k}
              type="button"
              class="mv-toggle"
              aria-pressed={k === split}
              onClick={() => {
                setSplit(k);
                setCell(null);
              }}
            >
              {SPLIT_LABEL[k]}
            </button>
          ))}
        </div>
      </div>

      <p class="mv-summary" role="status">
        {model.label} on {split} ({SPLIT_YEARS[split]}, {s.n.toLocaleString('en-US')} complaints): accuracy {pct(s.accuracy)} ±{' '}
        {points(s.band)} points (1/√{s.n.toLocaleString('en-US')}).
      </p>
      <p class="mv-overfit">
        {gap.drop > 0
          ? `Train ${pct(gap.train)}, test ${pct(gap.test)}: ${points(gap.drop)} points lower on test, ${
              gap.beyond ? `more than the ±${points(gap.band)}-point test band, so the drop is real: the model fits the complaints it learned from better than new ones (overfitting, plus a year of change).` : `within the ±${points(gap.band)}-point test band, so it may be noise.`
            }`
          : `Train ${pct(gap.train)}, test ${pct(gap.test)}: no drop on test, so no sign of overfitting (the most common category is commoner in 2022).`}
      </p>

      <div class="mv-scroll" role="region" aria-label="Per-category metrics" tabIndex={0}>
        <table class="mv-table" aria-label={`Per category: ${model.label} on ${split}`}>
          <thead>
            <tr>
              <th scope="col">Category</th>
              <th scope="col">Precision</th>
              <th scope="col">Recall</th>
              <th scope="col">F1</th>
              <th scope="col">Support</th>
            </tr>
          </thead>
          <tbody>
            {classes.map((c) => {
              const m = s.per_class[c];
              return (
                <tr key={c}>
                  <th scope="row">{c}</th>
                  <td>{n3(m.precision)}</td>
                  <td>{n3(m.recall)}</td>
                  <td>{n3(m.f1)}</td>
                  <td>{m.support}</td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row">Macro average</th>
              <td>{n3(s.macro.precision)}</td>
              <td>{n3(s.macro.recall)}</td>
              <td>{n3(s.macro.f1)}</td>
              <td>{s.n}</td>
            </tr>
            <tr>
              <th scope="row">Weighted average</th>
              <td>{n3(s.weighted.precision)}</td>
              <td>{n3(s.weighted.recall)}</td>
              <td>{n3(s.weighted.f1)}</td>
              <td>{s.n}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p class="mv-hint">
        Macro counts every category equally, so the small ones weigh as much as credit reporting; weighted averages by
        support, so the big category dominates. Precision: of the complaints called X, the share that were X. Recall: of
        the complaints that were X, the share called X.
      </p>

      <div class="mv-scroll" role="region" aria-label="Model comparison" tabIndex={0}>
        <table class="mv-table" aria-label={`All three models on ${split}`}>
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col">Accuracy</th>
              <th scope="col">Macro F1</th>
              <th scope="col">Weighted F1</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.id} class={m.id === model.id ? 'is-current' : ''}>
                <th scope="row">
                  {m.label}
                  {m.id === model.id && <span class="mv-current"> · shown</span>}
                </th>
                <td>{pct(m[split].accuracy)}</td>
                <td>{n3(m[split].macro.f1)}</td>
                <td>{n3(m[split].weighted.f1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="mv-note">
        <strong>Why v1 is logistic regression:</strong> {note}
      </p>

      <div class="mv-cm">
        <div class="mv-scroll" role="region" aria-label="Confusion matrix table" tabIndex={0}>
          <table>
            <caption class="mv-caption">
              Confusion matrix, {model.label} on {split}. Rows: the true category. Columns: the predicted one.
            </caption>
            <thead>
              <tr>
                <th scope="col" class="mv-corner">
                  <span>true ↓</span>
                  <span>predicted →</span>
                </th>
                {classes.map((c) => (
                  <th key={c} scope="col" class="mv-colhead" title={c}>
                    <span>{shortName(c)}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {classes.map((r, ri) => (
                <tr key={r}>
                  <th scope="row" class="mv-rowhead" title={r}>
                    {shortName(r)}
                  </th>
                  {classes.map((c, ci) => {
                    const n = s.confusion[ri][ci];
                    const diag = ri === ci;
                    const level = max ? n / max : 0;
                    const selected = cell?.[0] === ri && cell?.[1] === ci;
                    return (
                      <td
                        key={c}
                        class={`mv-cell${diag ? ' mv-diag' : ''}${n ? ' has-count' : ''}`}
                        style={n ? { '--level': `${Math.round(14 + level * 46)}%` } : undefined}
                      >
                        <button
                          type="button"
                          class="mv-btn"
                          aria-pressed={selected}
                          aria-label={`True ${r}, predicted ${c}: ${plural(n)} (${diag ? 'correct' : 'mistake'})`}
                          onClick={() => setCell(selected ? null : [ri, ci])}
                        >
                          {n ? n : <span class="mv-zero">·</span>}
                          {diag && (
                            <span class="mv-mark" aria-hidden="true">
                              ✓
                            </span>
                          )}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ul class="mv-legend">
          <li>
            <span class="mv-swatch mv-swatch-good" aria-hidden="true" /> ✓ correct: the diagonal
          </li>
          <li>
            <span class="mv-swatch mv-swatch-bad" aria-hidden="true" /> no mark: a mistake; darker means more complaints
          </li>
        </ul>
        <div class="mv-detail" aria-live="polite">
          {detail && cell ? (
            <p>
              {detail.n} of the {detail.rowTotal} {classes[cell[0]]} complaints were predicted {classes[cell[1]]} (
              {detail.correct ? 'correct' : 'mistake'}). Row share {pct(detail.rowTotal ? detail.n / detail.rowTotal : 0)}
              {detail.correct ? ' is this category’s recall' : ''}; of the {detail.colTotal} complaints predicted{' '}
              {classes[cell[1]]}, {pct(detail.colTotal ? detail.n / detail.colTotal : 0)}
              {detail.correct ? ', its precision' : ''}.
            </p>
          ) : (
            <p class="mv-hint">Pick a cell to read it as a share of its row (recall) and of its column (precision).</p>
          )}
        </div>
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.mv { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.mv-eyebrow, .mv-caption {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.mv-controls { display: grid; gap: 8px; }
.mv-group { display: flex; flex-wrap: wrap; gap: 6px; }
.mv-toggle {
  font: inherit; font-size: var(--text-small); padding: 4px 12px; color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.mv-toggle[aria-pressed="true"] { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.mv-toggle:focus-visible, .mv-scroll:focus-visible, .mv-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.mv-summary { margin: 0; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.mv-overfit, .mv-hint { margin: 0; font-size: var(--text-small); color: var(--muted); }
.mv-overfit { color: var(--ink); }
.mv-note {
  margin: 0; padding: 10px 14px; font-size: var(--text-small); color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-left: 4px solid var(--accent); border-radius: var(--radius);
}
.mv-scroll { min-width: 0; max-width: 100%; overflow-x: auto; }
.mv-table { width: 100%; border-collapse: collapse; font-size: var(--text-small); font-variant-numeric: tabular-nums; background: var(--surface); border: 1px solid var(--line); }
.mv-table th, .mv-table td { padding: 5px 10px; text-align: right; border-bottom: 1px solid var(--line); }
.mv-table thead th { font-family: var(--font-mono); font-size: var(--text-label); font-weight: 500; color: var(--muted); }
.mv-table th[scope="row"], .mv-table thead th:first-child { text-align: left; font-weight: 500; color: var(--ink); min-width: 9rem; }
.mv-table td { font-family: var(--font-mono); color: var(--ink); }
.mv-table tfoot th, .mv-table tfoot td { font-weight: 700; border-top: 2px solid var(--line); }
.mv-table tr.is-current th, .mv-table tr.is-current td { background: var(--surface-2); }
.mv-current { font-size: var(--text-label); color: var(--muted); font-weight: 400; }
.mv-cm { display: grid; gap: 8px; min-width: 0; }
.mv-cm table { border-collapse: separate; border-spacing: 3px; font-family: var(--font-mono); font-size: 0.8125rem; }
.mv-caption { caption-side: top; text-align: left; padding: 0 3px 6px; text-transform: none; letter-spacing: 0; }
.mv-corner { vertical-align: bottom; text-align: left; font-weight: 400; color: var(--muted); font-size: 0.6875rem; }
.mv-corner span { display: block; white-space: nowrap; }
.mv-colhead { vertical-align: bottom; padding: 0 0 4px; font-weight: 500; color: var(--ink); }
.mv-colhead span { display: inline-block; writing-mode: vertical-rl; transform: rotate(180deg); white-space: nowrap; }
.mv-rowhead { text-align: right; padding: 0 6px 0 0; font-weight: 500; color: var(--ink); white-space: nowrap; }
.mv-cell {
  width: 2.25rem; height: 2.25rem; padding: 0; text-align: center;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
}
.mv-cell.mv-diag { border-color: var(--good); }
.mv-cell.has-count { background: color-mix(in srgb, var(--bad) var(--level), var(--surface)); border-color: var(--bad); }
.mv-cell.mv-diag.has-count { background: color-mix(in srgb, var(--good) var(--level), var(--surface)); border-color: var(--good); }
.mv-btn {
  width: 100%; height: 100%; padding: 0; font: inherit; font-weight: 700; color: var(--ink);
  background: transparent; border: 0; border-radius: var(--radius); cursor: pointer;
}
.mv-btn[aria-pressed="true"] { outline: 3px solid var(--accent); outline-offset: 1px; }
.mv-zero { color: var(--muted); font-weight: 400; }
.mv-mark { margin-left: 1px; font-size: 0.6875rem; font-weight: 400; vertical-align: super; }
.mv-legend { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 4px 20px; font-size: var(--text-small); color: var(--muted); }
.mv-legend li { display: flex; align-items: center; gap: 6px; }
.mv-swatch { width: 12px; height: 12px; border-radius: 2px; flex: none; }
.mv-swatch-good { background: color-mix(in srgb, var(--good) 50%, var(--surface)); border: 1px solid var(--good); }
.mv-swatch-bad { background: color-mix(in srgb, var(--bad) 50%, var(--surface)); border: 1px solid var(--bad); }
.mv-detail { padding: 10px 14px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.mv-detail p { margin: 0; font-size: var(--text-small); color: var(--ink); }
.mv-detail p.mv-hint { color: var(--muted); }
`;
