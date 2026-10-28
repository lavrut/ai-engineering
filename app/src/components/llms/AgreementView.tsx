import { useEffect, useState } from 'preact/hooks';
import { RUBRIC_IDS, type RubricId } from '../../lib/llmsData';
import {
  LABELS_EVENT,
  LABELS_KEY,
  agreementStats,
  mergeLabels,
  parseStoredLabels,
  readStoredLabels,
  type AgreementProps,
  type Labels,
  type Order,
  type StoredLabels,
} from '../../lib/llmsAids';
import NoRecordedRun from './NoRecordedRun';
import { VerdictChip, chipCss } from './JudgeCard';

const ORDER_TEXT: Record<Order, string> = { after: 'Rubric after the reply', before: 'Rubric before the reply' };
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const replies = (n: number) => `${n} repl${n === 1 ? 'y' : 'ies'}`;

/** A rubric id that may wrap after each underscore (no_false_<wbr>promises). */
function Breakable({ value }: { value: string }) {
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

export default function AgreementView({ recorded, rubric, cases }: AgreementProps) {
  const [order, setOrder] = useState<Order>('after');
  const [onlyMisses, setOnlyMisses] = useState(true);
  const [stored, setStored] = useState<StoredLabels>({});

  // The learner's labels live in localStorage, which the server render cannot see.
  useEffect(() => {
    setStored(readStoredLabels().stored);
    const onEdit = (e: Event) => {
      // Sanitise like a stored value: the detail comes from LabelingPage on this page.
      setStored(parseStoredLabels(JSON.stringify({ replies: (e as CustomEvent).detail ?? {} })));
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === LABELS_KEY || e.key === null) setStored(readStoredLabels().stored);
    };
    window.addEventListener(LABELS_EVENT, onEdit);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(LABELS_EVENT, onEdit);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  if (!recorded) {
    return (
      <section class="av" aria-label="Judge agreement">
        <p class="av-eyebrow">Judge vs human labels</p>
        <NoRecordedRun />
        <style>{css}</style>
      </section>
    );
  }

  const fileLabels = cases.map((c) => Object.fromEntries(RUBRIC_IDS.map((r, i) => [r, c.file[i]])) as Labels);
  const merged = mergeLabels(
    cases.map((c, i) => ({ id: c.id, fp: c.fp, labels: fileLabels[i] })),
    stored,
  );
  const human = merged.labels.map((l) => RUBRIC_IDS.map((r) => l[r]));
  const mine = merged.mine.filter(Boolean).length;
  const stats = agreementStats(cases, human, order);
  const flipped = cases.map((c) => c.before.some((v, i) => v !== c.after[i]));
  const rows = cases
    .map((c, i) => ({ c, i, flips: flipped[i], miss: RUBRIC_IDS.some((_, k) => c[order][k] !== human[i][k]) }))
    .filter((r) => !onlyMisses || r.flips || r.miss);
  const n = cases.length;

  const source =
    mine === 0
      ? `Human labels: the committed labels in reply_labels.jsonl (drafted by Claude, reviewed by a person on 2026-10-28).`
      : mine === n
        ? `Human labels: your labels from this browser for all ${n} replies.`
        : `Human labels: your labels from this browser for ${replies(mine)}; the committed labels in reply_labels.jsonl for the other ${n - mine}.`;

  return (
    <section class="av" aria-label="Judge agreement">
      <p class="av-eyebrow">Judge vs human labels · {replies(n)}</p>
      <p class="av-source" aria-live="polite">
        {source}
      </p>

      <div class="av-controls">
        <p class="av-order" aria-live="polite">
          Showing: {ORDER_TEXT[order]}
        </p>
        <button type="button" class="av-btn" onClick={() => setOrder(order === 'after' ? 'before' : 'after')}>
          Swap order
        </button>
      </div>

      <div class="av-scroll" role="region" aria-label="Agreement per criterion" tabIndex={0}>
        <table class="av-table av-stats">
          <caption class="av-caption">How often the judge matches the human label, and kappa (agreement beyond chance).</caption>
          <thead>
            <tr>
              <th scope="col">Criterion</th>
              <th scope="col">Agreement</th>
              <th scope="col">Kappa</th>
              <th scope="col">Flips</th>
            </tr>
          </thead>
          <tbody>
            {stats.map((s) => (
              <tr key={s.id}>
                <th scope="row" class="av-crit">
                  <Breakable value={s.id} />
                </th>
                <td>{pct(s.agreement)}</td>
                <td>{s.kappa.toFixed(2)}</td>
                <td>
                  {s.flips} of {n}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="av-note">
        Flips count replies whose verdict changes when only the rubric&apos;s position changes. They are the same in both orders.
      </p>

      <label class="av-filter">
        <input type="checkbox" checked={onlyMisses} onChange={(e) => setOnlyMisses((e.target as HTMLInputElement).checked)} />
        Only rows where judge and human disagree, or the verdict flips
      </label>
      <p class="visually-hidden" aria-live="polite">
        {`${rows.length} of ${n} replies shown.`}
      </p>

      <div class="av-scroll" role="region" aria-label="Judge and human verdicts per reply" tabIndex={0}>
        <table class="av-table av-rows">
          <caption class="av-caption">
            Judge verdict per reply ({ORDER_TEXT[order].toLowerCase()}); a ≠ line gives the human label where it differs.
          </caption>
          <thead>
            <tr>
              <th scope="col">Reply</th>
              {rubric.map((r) => (
                <th key={r.id} scope="col" class="av-crit">
                  {r.id.replaceAll('_', ' ')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ c, i, flips }) => (
              <tr key={c.id} class={flips ? 'is-flipped' : undefined}>
                <th scope="row" class="av-id">
                  <span class="visually-hidden">{`Reply ${c.id}`}</span>
                  <span aria-hidden="true">{c.id}</span>
                  {merged.mine[i] && <span class="av-mine"> (yours)</span>}
                  {flips && <span class="av-flip">↔ flips</span>}
                </th>
                {RUBRIC_IDS.map((r: RubricId, k) => {
                  const j = c[order][k];
                  const h = human[i][k];
                  const flip = c.before[k] !== c.after[k];
                  return (
                    <td key={r} class={`av-cell${j !== h ? ' is-miss' : ''}${flip ? ' is-flip' : ''}`}>
                      <VerdictChip pass={j} />
                      {j !== h && <span class="av-human">≠ human {h ? 'pass' : 'fail'}</span>}
                      {flip && <span class="visually-hidden"> (changes with the order)</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && <p class="av-empty">The judge matches every human label here, and no verdict flips.</p>}
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.av { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.av-eyebrow, .av-caption {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.av-caption { caption-side: top; text-align: left; padding: 0 0 6px; text-transform: none; letter-spacing: 0; }
.av-source { margin: 0; font-size: var(--text-small); color: var(--ink); }
.av-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; }
.av-order { margin: 0; font-family: var(--font-mono); font-size: 0.8125rem; font-weight: 600; color: var(--ink); }
.av-btn {
  font: inherit; font-size: var(--text-small); font-weight: 600; color: var(--accent-ink); background: var(--accent);
  border: 1px solid var(--accent); border-radius: var(--radius); padding: 6px 14px; cursor: pointer;
}
/* position: relative keeps the visually-hidden (absolute) spans inside the scroll box. */
.av-scroll { position: relative; min-width: 0; max-width: 100%; overflow-x: auto; }
.av-table { width: 100%; border-collapse: collapse; font-size: var(--text-small); }
.av-table th, .av-table td { padding: 6px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
.av-table thead th { font-family: var(--font-mono); font-size: 0.75rem; font-weight: 600; color: var(--muted); }
.av-stats td { font-family: var(--font-mono); color: var(--ink); white-space: nowrap; }
.av-crit { font-family: var(--font-mono); font-weight: 600; color: var(--ink); }
.av-rows thead .av-crit { color: var(--muted); }
.av-id { font-family: var(--font-mono); font-weight: 600; color: var(--ink); white-space: nowrap; }
.av-mine { display: block; font-weight: 400; font-size: 0.75rem; color: var(--muted); }
.av-flip {
  display: block; margin-top: 2px; font-size: 0.75rem; font-weight: 600; color: var(--ink);
  padding-left: 6px; border-left: 3px solid var(--warn);
}
.av-rows tr.is-flipped { background: color-mix(in srgb, var(--warn) 12%, var(--surface)); }
.av-cell { min-width: 4rem; }
.av-human { display: block; margin-top: 3px; font-family: var(--font-mono); font-size: 0.75rem; color: var(--ink); }
.av-note { margin: 0; font-size: var(--text-small); color: var(--muted); }
.av-filter { display: flex; align-items: flex-start; gap: 8px; font-size: var(--text-small); color: var(--ink); cursor: pointer; }
.av-filter input { margin: 3px 0 0; accent-color: var(--accent); }
.av-empty { margin: 8px 0 0; font-size: var(--text-small); color: var(--muted); }
.av-btn:focus-visible, .av-filter input:focus-visible, .av-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
${chipCss}
`;
