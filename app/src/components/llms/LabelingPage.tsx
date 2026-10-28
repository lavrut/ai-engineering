import { useEffect, useRef, useState } from 'preact/hooks';
import { RUBRIC_IDS, type RubricId } from '../../lib/llmsData';
import {
  LABELS_EVENT,
  labelsJsonl,
  mergeLabels,
  readStoredLabels,
  writeStoredLabels,
  type LabelingProps,
  type StoredLabels,
} from '../../lib/llmsAids';

type CopyState = 'idle' | 'copied' | 'failed';

export default function LabelingPage({ rubric, cases }: LabelingProps) {
  const [index, setIndex] = useState(0);
  const [stored, setStored] = useState<StoredLabels>({});
  const [saving, setSaving] = useState(true);
  const [exported, setExported] = useState(false);
  const [copy, setCopy] = useState<CopyState>('idle');
  const area = useRef<HTMLTextAreaElement>(null);

  // localStorage is invisible to the server render: read it after mounting.
  useEffect(() => {
    const { stored: s, available } = readStoredLabels();
    setStored(s);
    setSaving(available);
  }, []);

  const total = cases.length;
  if (!total) return null;
  const { labels, mine } = mergeLabels(cases, stored);
  const reviewed = mine.filter(Boolean).length;
  const c = cases[index];
  const current = labels[index];
  const changed = mine[index] && RUBRIC_IDS.some((r) => current[r] !== c.labels[r]);

  function save(next: StoredLabels) {
    setStored(next);
    setSaving(writeStoredLabels(next));
    setCopy('idle');
    try {
      window.dispatchEvent(new CustomEvent(LABELS_EVENT, { detail: next }));
    } catch {
      // no window events: other aids keep what they had
    }
  }
  const review = (l: typeof current) => save({ ...stored, [c.id]: { fp: c.fp, labels: l } });
  const setLabel = (r: RubricId, pass: boolean) => review({ ...current, [r]: pass });
  const backToDraft = () => {
    if (!mine[index]) return;
    const next = { ...stored };
    delete next[c.id];
    save(next);
  };
  const go = (to: number) => {
    if (to >= 0 && to < total) setIndex(to);
  };

  async function copyJsonl() {
    setExported(true);
    const text = labelsJsonl(cases, labels);
    try {
      await navigator.clipboard.writeText(text);
      setCopy('copied');
    } catch {
      setCopy('failed');
      // Select the text so the learner can copy it by hand.
      requestAnimationFrame(() => {
        area.current?.focus();
        area.current?.select();
      });
    }
  }

  const status = !mine[index] ? 'Draft: not reviewed yet.' : changed ? 'Reviewed: changed from the draft.' : 'Reviewed: confirmed as drafted.';
  const atStart = index === 0;
  const atEnd = index === total - 1;

  return (
    <section class="lp" aria-label="Label the replies">
      <header class="lp-head">
        <p class="lp-eyebrow">Label the replies</p>
        <p class="lp-progress" aria-live="polite">
          {reviewed} of {total} reviewed
        </p>
      </header>
      <div class="lp-bar" aria-hidden="true">
        <span style={{ width: `${(reviewed / total) * 100}%` }} />
      </div>
      <p class="lp-intro">
        Claude drafted these labels and a person reviewed them on 2026-10-28. Review them again: read the ticket and the reply, then set each criterion:
        changing a label or pressing Confirm marks the reply reviewed. Your labels stay in this browser.
      </p>
      {!saving && (
        <p class="lp-warn" role="note">
          Browser storage is blocked, so your labels last only while this page is open. Use Copy JSONL to keep them.
        </p>
      )}

      <nav class="lp-nav" aria-label="Replies">
        <button type="button" class="lp-btn" aria-disabled={atStart} onClick={() => go(index - 1)}>
          <span aria-hidden="true">← </span>Previous reply
        </button>
        <label class="lp-jump">
          <span class="visually-hidden">Go to reply</span>
          <select value={String(index)} onChange={(e) => go(Number((e.target as HTMLSelectElement).value))}>
            {cases.map((x, i) => (
              <option key={x.id} value={String(i)}>
                {`${x.id}${mine[i] ? ' ✓ reviewed' : ''}`}
              </option>
            ))}
          </select>
        </label>
        <button type="button" class="lp-btn" aria-disabled={atEnd} onClick={() => go(index + 1)}>
          Next reply<span aria-hidden="true"> →</span>
        </button>
      </nav>
      <p class="lp-where" aria-live="polite">
        Reply {index + 1} of {total} · {c.id}
      </p>

      <div class="lp-block">
        <p class="lp-label">Ticket {c.ticket_id}</p>
        <p class="lp-text">{c.ticket}</p>
      </div>
      <div class="lp-block">
        <p class="lp-label">Reply {c.id}</p>
        <p class="lp-text">{c.reply}</p>
      </div>

      <div class="lp-crits">
        {rubric.map((r) => {
          const id = r.id as RubricId;
          const name = `lp-${c.id}-${id}`;
          const draft = c.labels[id];
          return (
            <div key={id} class="lp-crit" role="radiogroup" aria-labelledby={`${name}-l`}>
              <p class="lp-crit-name" id={`${name}-l`}>
                <span class="lp-crit-id">{id}</span>
                <span class="lp-crit-text">{r.text}</span>
              </p>
              <div class="lp-options">
                {[true, false].map((pass) => (
                  <label key={String(pass)} class={`lp-option ${pass ? 'is-pass' : 'is-fail'}`}>
                    <input type="radio" name={name} checked={current[id] === pass} onChange={() => setLabel(id, pass)} />
                    <span aria-hidden="true">{pass ? '✓' : '✗'}</span>
                    {pass ? 'Pass' : 'Fail'}
                  </label>
                ))}
                {mine[index] && current[id] !== draft && <span class="lp-was">draft: {draft ? 'pass' : 'fail'}</span>}
              </div>
            </div>
          );
        })}
      </div>

      <p class="lp-status" aria-live="polite">
        {status}
      </p>
      <div class="lp-actions">
        <button type="button" class="lp-btn lp-primary" onClick={() => review(current)}>
          Confirm labels
        </button>
        <button type="button" class="lp-btn" aria-disabled={!mine[index]} onClick={backToDraft}>
          Back to the draft
        </button>
      </div>

      <div class="lp-export">
        <button type="button" class="lp-btn" onClick={copyJsonl}>
          Copy JSONL
        </button>
        <p class="lp-copy" aria-live="polite">
          {copy === 'copied'
            ? `Copied ${total} lines. Paste them over llms/data/reply_labels.jsonl, then run python3 triage_lab.py agreement.`
            : copy === 'failed'
              ? 'Could not copy automatically: select the text below and copy it.'
              : ''}
        </p>
        {exported && (
          <label class="lp-area">
            <span class="lp-label">reply_labels.jsonl ({total} lines)</span>
            <textarea ref={area} readOnly rows={6} spellcheck={false} value={labelsJsonl(cases, labels)} />
          </label>
        )}
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.lp { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.lp-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 16px; }
.lp-eyebrow, .lp-label {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.lp-progress { margin: 0; font-family: var(--font-mono); font-size: 0.8125rem; font-weight: 600; color: var(--ink); }
.lp-bar { height: 6px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }
.lp-bar span { display: block; height: 100%; background: var(--accent); }
.lp-intro { margin: 0; font-size: var(--text-small); color: var(--muted); }
.lp-warn { margin: 0; padding: 8px 12px; font-size: var(--text-small); color: var(--ink); border-left: 3px solid var(--warn); background: var(--surface); }
.lp-nav { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.lp-jump select {
  font: inherit; font-size: var(--text-small); color: var(--ink); background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--radius); padding: 5px 8px;
}
.lp-where { margin: 0; font-family: var(--font-mono); font-size: 0.8125rem; color: var(--muted); }
.lp-btn {
  font: inherit; font-size: var(--text-small); font-weight: 600; color: var(--ink); background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--radius); padding: 6px 12px; cursor: pointer;
}
.lp-btn[aria-disabled="true"] { color: var(--muted); cursor: default; border-style: dashed; }
.lp-primary { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.lp-block { padding: 10px 14px; min-width: 0; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.lp-text { margin: 4px 0 0; font-size: var(--text-small); line-height: 1.55; color: var(--ink); overflow-wrap: anywhere; }
.lp-crits { display: grid; gap: 8px; }
.lp-crit { padding: 10px 14px; border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.lp-crit-name { margin: 0 0 8px; display: grid; gap: 2px; }
.lp-crit-id { font-family: var(--font-mono); font-size: 0.8125rem; font-weight: 600; color: var(--ink); }
.lp-crit-text { font-size: var(--text-small); color: var(--muted); }
.lp-options { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.lp-option {
  display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; font-size: var(--text-small); font-weight: 600;
  color: var(--ink); border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.lp-option input { margin: 0; accent-color: var(--accent); }
.lp-option.is-pass:has(input:checked) { border-color: var(--good); background: color-mix(in srgb, var(--good) 14%, var(--surface)); }
.lp-option.is-fail:has(input:checked) { border-color: var(--bad); background: color-mix(in srgb, var(--bad) 14%, var(--surface)); }
.lp-was { font-family: var(--font-mono); font-size: 0.75rem; color: var(--muted); }
.lp-status { margin: 0; font-size: var(--text-small); font-weight: 600; color: var(--ink); }
.lp-actions, .lp-export { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
.lp-copy { margin: 0; font-size: var(--text-small); color: var(--ink); flex: 1 1 14rem; min-width: 0; }
.lp-copy:empty { display: none; }
.lp-area { display: grid; gap: 4px; width: 100%; min-width: 0; }
.lp-area textarea {
  width: 100%; box-sizing: border-box; font-family: var(--font-mono); font-size: 0.75rem; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); padding: 8px;
}
.lp-btn:focus-visible, .lp-jump select:focus-visible, .lp-option:has(input:focus-visible), .lp-area textarea:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}
`;
