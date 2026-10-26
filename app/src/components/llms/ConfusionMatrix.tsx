import { useState } from 'preact/hooks';
import { NONE, cellTickets, matrixCounts, type MatrixProps } from '../../lib/llmsAids';
import NoRecordedRun from './NoRecordedRun';

const plural = (n: number) => `${n} ticket${n === 1 ? '' : 's'}`;
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

/** A category name that may wrap after its underscore (feature_ request). */
function Name({ value }: { value: string }) {
  const parts = value.split('_');
  return (
    <>
      {parts.map((p, i) => (
        <span key={i}>
          {p}
          {i < parts.length - 1 && (
            <>
              _<wbr />
            </>
          )}
        </span>
      ))}
    </>
  );
}

export default function ConfusionMatrix({ recorded, tickets, runs, initial, split, rep }: MatrixProps) {
  const [runId, setRunId] = useState(initial);
  const [cell, setCell] = useState<[string, string] | null>(null);

  if (!recorded) {
    return (
      <section class="cm2" aria-label="Confusion matrix">
        <p class="cm2-eyebrow">Confusion matrix</p>
        <NoRecordedRun />
        <style>{css}</style>
      </section>
    );
  }

  const run = runs.find((r) => r.name === runId) ?? runs[0];
  const { rows, cols, counts, max } = matrixCounts(tickets, run.answers);
  const right = tickets.filter((t, i) => run.answers[i] === t.category).length;
  const byId = new Map(tickets.map((t, i) => [t.id, { t, answer: run.answers[i] }]));
  const ids = cell ? cellTickets(tickets, run.answers, cell[0], cell[1]) : [];
  const splitName = split === 'all' ? 'all tickets' : `the ${split} split`;

  return (
    <section class="cm2" aria-label="Confusion matrix">
      <header class="cm2-head">
        <p class="cm2-eyebrow">Confusion matrix · category</p>
        <label class="cm2-picker">
          <span class="cm2-picker-label">Recorded run</span>
          <select
            value={run.name}
            onChange={(e) => {
              setRunId((e.target as HTMLSelectElement).value);
              setCell(null);
            }}
          >
            {runs.map((r) => (
              <option key={r.name} value={r.name}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
      </header>
      <p class="cm2-score">
        {right} of {tickets.length} right on category ({pct(right / tickets.length)}), on {splitName}, repeat {rep}.
      </p>

      <div class="cm2-scroll" role="region" aria-label="Confusion matrix table" tabIndex={0}>
        <table class="cm2-table">
          <caption class="cm2-caption">Rows: the expected category. Columns: the model&apos;s answer.</caption>
          <thead>
            <tr>
              <th scope="col" class="cm2-corner">
                <span>expected ↓</span>
                <span>answered →</span>
              </th>
              {cols.map((c) => (
                <th key={c} scope="col" class="cm2-colhead">
                  <span>{c}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={r}>
                <th scope="row" class="cm2-rowhead">
                  <Name value={r} />
                </th>
                {cols.map((c, ci) => {
                  const n = counts[ri][ci];
                  const diag = r === c;
                  const level = max ? n / max : 0;
                  const selected = cell?.[0] === r && cell?.[1] === c;
                  return (
                    <td
                      key={c}
                      class={`cm2-cell${diag ? ' cm2-diag' : ''}${n ? ' has-count' : ''}`}
                      style={n ? { '--level': `${Math.round(18 + level * 42)}%` } : undefined}
                    >
                      {n ? (
                        <button
                          type="button"
                          class="cm2-btn"
                          aria-pressed={selected}
                          aria-label={`Expected ${r}, answered ${c}: ${plural(n)}`}
                          onClick={() => setCell(selected ? null : [r, c])}
                        >
                          {n}
                        </button>
                      ) : (
                        <span class="cm2-zero" aria-label={`Expected ${r}, answered ${c}: none`}>
                          ·
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul class="cm2-legend" aria-hidden="true">
        <li>
          <span class="cm2-swatch cm2-swatch-good" /> right (diagonal)
        </li>
        <li>
          <span class="cm2-swatch cm2-swatch-bad" /> a mistake; darker means more tickets
        </li>
      </ul>

      <div class="cm2-detail" aria-live="polite">
        {cell ? (
          <>
            <p class="cm2-detail-title">
              Expected {cell[0]}, answered {cell[1]} · {plural(ids.length)}
            </p>
            <ul class="cm2-cases">
              {ids.map((id) => {
                const { t, answer } = byId.get(id)!;
                return (
                  <li key={id}>
                    <p class="cm2-case-head">
                      <span class="cm2-case-id">{id}</span>
                      <span class={`cm2-answer${answer === t.category ? ' is-right' : ' is-wrong'}`}>
                        {answer ? `answered ${answer}` : 'no answer'}
                      </span>
                      <span class="cm2-expected">expected {t.category}</span>
                    </p>
                    <p class="cm2-case-text">{t.text}</p>
                  </li>
                );
              })}
            </ul>
          </>
        ) : (
          <p class="cm2-hint">
            Pick a cell to list its tickets. The diagonal holds the right answers; every other cell with a number is a
            mistake{cols.includes(NONE) ? `, and ${NONE} means the model gave no answer` : ''}.
          </p>
        )}
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.cm2 { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.cm2-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.cm2-eyebrow, .cm2-picker-label, .cm2-caption {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.cm2-picker { display: inline-flex; align-items: center; gap: 8px; min-width: 0; max-width: 100%; }
.cm2-picker select {
  min-width: 0; max-width: 100%; font: inherit; font-size: var(--text-small); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 5px 8px;
}
.cm2-score { margin: 0; font-size: var(--text-small); color: var(--ink); }
.cm2-scroll { min-width: 0; max-width: 100%; overflow-x: auto; }
.cm2-table { border-collapse: separate; border-spacing: 3px; font-family: var(--font-mono); font-size: 0.8125rem; }
.cm2-caption { caption-side: top; text-align: left; padding: 0 3px 6px; text-transform: none; letter-spacing: 0; }
.cm2-corner { vertical-align: bottom; text-align: left; font-weight: 400; color: var(--muted); font-size: 0.6875rem; }
.cm2-corner span { display: block; white-space: nowrap; }
.cm2-colhead { vertical-align: bottom; padding: 0 0 4px; font-weight: 500; color: var(--ink); }
.cm2-colhead span { display: inline-block; writing-mode: vertical-rl; transform: rotate(180deg); white-space: nowrap; }
.cm2-rowhead { text-align: right; padding: 0 6px 0 0; font-weight: 500; color: var(--ink); max-width: 6.5rem; line-height: 1.2; }
.cm2-cell {
  width: 2.25rem; height: 2.25rem; padding: 0; text-align: center;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
}
.cm2-cell.cm2-diag { border-color: var(--good); }
.cm2-cell.has-count { background: color-mix(in srgb, var(--bad) var(--level), var(--surface)); border-color: var(--bad); }
.cm2-cell.cm2-diag.has-count { background: color-mix(in srgb, var(--good) var(--level), var(--surface)); border-color: var(--good); }
.cm2-btn {
  width: 100%; height: 100%; padding: 0; font: inherit; font-weight: 700; color: var(--ink);
  background: transparent; border: 0; border-radius: var(--radius); cursor: pointer;
}
.cm2-btn[aria-pressed="true"] { outline: 3px solid var(--accent); outline-offset: 1px; }
.cm2-btn:focus-visible, .cm2-picker select:focus-visible, .cm2-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.cm2-zero { color: var(--muted); }
.cm2-legend { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 4px 20px; font-size: var(--text-small); color: var(--muted); }
.cm2-legend li { display: flex; align-items: center; gap: 6px; }
.cm2-swatch { width: 12px; height: 12px; border-radius: 2px; flex: none; }
.cm2-swatch-good { background: color-mix(in srgb, var(--good) 50%, var(--surface)); border: 1px solid var(--good); }
.cm2-swatch-bad { background: color-mix(in srgb, var(--bad) 50%, var(--surface)); border: 1px solid var(--bad); }
.cm2-detail {
  padding: 12px 14px; min-width: 0; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
}
.cm2-detail-title { margin: 0 0 6px; font-family: var(--font-mono); font-size: var(--text-label); font-weight: 600; color: var(--ink); }
.cm2-hint { margin: 0; font-size: var(--text-small); color: var(--muted); }
.cm2-cases { margin: 0; padding: 0; list-style: none; max-height: 20rem; overflow: auto; }
.cm2-cases li { padding: 8px 0; border-top: 1px solid var(--line); }
.cm2-case-head { margin: 0 0 4px; display: flex; flex-wrap: wrap; gap: 4px 10px; font-family: var(--font-mono); font-size: 0.8125rem; }
.cm2-case-id { font-weight: 600; color: var(--ink); }
.cm2-answer.is-right { color: var(--good); }
.cm2-answer.is-wrong { color: var(--bad); }
.cm2-expected { color: var(--muted); }
.cm2-case-text { margin: 0; font-size: var(--text-small); line-height: 1.55; color: var(--ink); overflow-wrap: anywhere; }
`;
