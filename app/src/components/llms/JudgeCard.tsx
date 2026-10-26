import { useState } from 'preact/hooks';
import { JUDGED_PROMPTS, type CaseVerdicts, type JudgeCardProps, type JudgeCase, type Order, type Verdict } from '../../lib/llmsAids';
import NoRecordedRun from './NoRecordedRun';

const ORDER_TEXT: Record<Order, string> = { after: 'Rubric after the reply', before: 'Rubric before the reply' };
const isGraded = (v: CaseVerdicts | undefined): v is Verdict[] => Array.isArray(v);

/** A pass/fail chip: a glyph and the word, so colour is never the only signal. */
export function VerdictChip({ pass }: { pass: boolean | null }) {
  if (pass === null) return <span class="vchip is-none">– none</span>;
  return <span class={`vchip ${pass ? 'is-pass' : 'is-fail'}`}>{pass ? '✓ pass' : '✗ fail'}</span>;
}

function caseName(c: JudgeCase): string {
  return c.group === 'labelled' ? `${c.caseId} · ticket ${c.ticketId}` : `${c.ticketId} · ${c.group} reply`;
}

export default function JudgeCard({ recorded, rubric, tickets, cases, initial }: JudgeCardProps) {
  const [value, setValue] = useState(initial);
  const [order, setOrder] = useState<Order>('after');

  if (!recorded) {
    return (
      <section class="jc" aria-label="Judge card">
        <p class="jc-eyebrow">LLM judge</p>
        <NoRecordedRun />
        <style>{css}</style>
      </section>
    );
  }

  const c = cases.find((x) => x.value === value) ?? cases[0];
  const both = !!c.orders.after && !!c.orders.before;
  const shown: Order = c.orders[order] ? order : c.orders.after ? 'after' : 'before';
  const v = c.orders[shown];
  const other = both ? c.orders[shown === 'after' ? 'before' : 'after'] : undefined;
  const passes = isGraded(v) ? v.filter((x) => x.pass).length : 0;
  const groups: { label: string; items: JudgeCase[] }[] = [
    { label: 'Labelled replies (both rubric orders)', items: cases.filter((x) => x.group === 'labelled') },
    ...JUDGED_PROMPTS.map((p) => ({ label: `Test-split replies · ${p}`, items: cases.filter((x) => x.group === p) })),
  ].filter((g) => g.items.length);
  const radio = `jc-order-${c.value.replace(/[^a-z0-9]/gi, '')}`;

  return (
    <section class="jc" aria-label="Judge card">
      <header class="jc-head">
        <p class="jc-eyebrow">LLM judge · Opus 5.5</p>
        <label class="jc-picker">
          <span class="jc-label">Judged reply</span>
          <select value={c.value} onChange={(e) => setValue((e.target as HTMLSelectElement).value)}>
            {groups.map((g) => (
              <optgroup key={g.label} label={g.label}>
                {g.items.map((x) => (
                  <option key={x.value} value={x.value}>
                    {caseName(x)}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
      </header>

      {both && (
        <div class="jc-orders" role="radiogroup" aria-label="Where the rubric sat in the judge's message">
          {(['after', 'before'] as const).map((o) => (
            <label key={o} class="jc-order">
              <input type="radio" name={radio} checked={shown === o} onChange={() => setOrder(o)} />
              {ORDER_TEXT[o]}
            </label>
          ))}
        </div>
      )}

      <div class="jc-block">
        <p class="jc-label">Ticket {c.ticketId}</p>
        <p class="jc-text">{tickets[c.ticketId]}</p>
      </div>
      <div class="jc-block">
        <p class="jc-label">{c.group === 'labelled' ? `Reply ${c.caseId} (labelled)` : `Reply from Haiku 5.5 · ${c.group}`}</p>
        <p class="jc-text">{c.reply}</p>
      </div>

      <p class="visually-hidden" aria-live="polite">
        {`${caseName(c)}, ${ORDER_TEXT[shown].toLowerCase()}: ${isGraded(v) ? `${passes} of ${v.length} criteria pass` : 'the judge gave no verdict'}.`}
      </p>
      {isGraded(v) ? (
        <ul class="jc-verdicts">
          {v.map((x, i) => {
            const item = rubric[i];
            const flips = isGraded(other) && other[i].pass !== x.pass;
            return (
              <li key={item?.id ?? i} class="jc-verdict">
                <div class="jc-verdict-head">
                  <VerdictChip pass={x.pass} />
                  <span class="jc-crit">{item?.id}</span>
                </div>
                <p class="jc-crit-text">{item?.text}</p>
                <p class="jc-reason">{x.reason}</p>
                {flips && (
                  <p class="jc-flip">
                    ↔ This verdict changes with the order: with the {shown === 'after' ? 'rubric before' : 'rubric after'} the reply
                    the judge said {x.pass ? 'fail' : 'pass'}.
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p class="jc-none" role="note">
          Not graded: {(v?.notGraded ?? '').replace(/\.$/, '')}. The lab counts a reply the judge could not grade as a disagreement.
        </p>
      )}
      <style>{css}</style>
    </section>
  );
}

export const chipCss = `
.vchip {
  display: inline-block; padding: 1px 8px; font-family: var(--font-mono); font-size: 0.75rem; font-weight: 600;
  white-space: nowrap; border: 1px solid var(--line); border-radius: 999px; color: var(--ink); background: var(--surface);
}
.vchip.is-pass { border-color: var(--good); color: var(--good); background: color-mix(in srgb, var(--good) 12%, var(--surface)); }
.vchip.is-fail { border-color: var(--bad); color: var(--bad); background: color-mix(in srgb, var(--bad) 12%, var(--surface)); }
.vchip.is-none { color: var(--muted); border-style: dashed; }
`;

const css = `
.jc { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.jc-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.jc-eyebrow, .jc-label {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.jc-picker { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; max-width: 100%; }
.jc-picker select {
  min-width: 0; max-width: 100%; font: inherit; font-size: var(--text-small); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 5px 8px;
}
.jc-orders { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: var(--text-small); color: var(--ink); }
.jc-order { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
.jc-order input { accent-color: var(--accent); margin: 0; }
.jc-block { padding: 10px 14px; min-width: 0; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.jc-text { margin: 4px 0 0; font-size: var(--text-small); line-height: 1.55; color: var(--ink); overflow-wrap: anywhere; }
.jc-verdicts { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.jc-verdict { padding: 10px 14px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); min-width: 0; }
.jc-verdict-head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
.jc-crit { font-family: var(--font-mono); font-size: 0.8125rem; font-weight: 600; color: var(--ink); overflow-wrap: anywhere; }
.jc-crit-text { margin: 4px 0 0; font-size: var(--text-small); color: var(--muted); }
.jc-reason { margin: 6px 0 0; font-size: var(--text-small); line-height: 1.55; color: var(--ink); }
.jc-flip { margin: 6px 0 0; padding-left: 8px; font-size: var(--text-small); color: var(--ink); border-left: 3px solid var(--warn); }
.jc-none { margin: 0; padding: 10px 14px; font-size: var(--text-small); color: var(--ink); border: 1px dashed var(--line); border-radius: var(--radius); }
.jc-picker select:focus-visible, .jc-order input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
${chipCss}
`;
