import { useState } from 'preact/hooks';
import { abCompare, abVerdict, type AbAnswer, type AbTicket, type PromptAbProps } from '../../lib/llmsAids';
import NoRecordedRun from './NoRecordedRun';

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const points = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1)} points`;
const plural = (n: number) => `${n} ticket${n === 1 ? '' : 's'}`;

function answerText(a: AbAnswer): string {
  return typeof a === 'string' ? `no answer (${a.replace('_', ' ')})` : `${a[0]} / ${a[1]}`;
}

function CaseList({
  title,
  ids,
  tickets,
  answersA,
  answersB,
}: {
  title: string;
  ids: string[];
  tickets: AbTicket[];
  answersA: AbAnswer[];
  answersB: AbAnswer[];
}) {
  if (!ids.length) return <p class="pab-none">{title}: none.</p>;
  const index = new Map(tickets.map((t, i) => [t.id, i]));
  return (
    <div class="pab-group">
      <p class="pab-group-title" aria-hidden="true">
        {title}: {plural(ids.length)}
      </p>
      <ul class="pab-cases" aria-label={`${title}: ${plural(ids.length)}`}>
        {ids.map((id) => {
          const i = index.get(id)!;
          const t = tickets[i];
          return (
            <li key={id} data-id={id}>
              <p class="pab-case-head">
                <span class="pab-case-id">{id}</span>
                <span class="pab-expected">
                  expected {t.category} / {t.priority}
                </span>
              </p>
              <p class="pab-case-answers">
                <span>A: {answerText(answersA[i])}</span>
                <span>B: {answerText(answersB[i])}</span>
              </p>
              <p class="pab-case-text">{t.text}</p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function PromptAB(props: PromptAbProps) {
  const { recorded, label, split, tickets, prompts } = props;
  const [a, setA] = useState(props.a);
  const [b, setB] = useState(props.b);

  if (!recorded) {
    return (
      <section class="pab" aria-label="Prompt A/B">
        <p class="pab-eyebrow">Prompt A/B</p>
        <NoRecordedRun />
        <style>{css}</style>
      </section>
    );
  }

  const c = abCompare(props, a, b);
  const verdict = abVerdict(c);
  const answersOf = (v: string) => prompts.find((p) => p.version === v)!.answers;
  const splitName = split === 'all' ? 'all tickets' : `the ${split} split`;
  const select = (id: string, name: string, value: string, set: (v: string) => void) => (
    <label class="pab-picker" for={id}>
      <span class="pab-picker-label">{name}</span>
      <select id={id} value={value} onChange={(e) => set((e.target as HTMLSelectElement).value)}>
        {prompts.map((p) => (
          <option key={p.version} value={p.version}>
            {p.version}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <section class="pab" aria-label="Prompt A/B">
      <header class="pab-head">
        <p class="pab-eyebrow">
          Prompt A/B · {label} · {splitName}, repeat 0
        </p>
        <div class="pab-pickers">
          {select('pab-a', 'Prompt A', a, setA)}
          {select('pab-b', 'Prompt B', b, setB)}
        </div>
      </header>

      <ul class="pab-scores" aria-label="Scores">
        {(
          [
            ['A', a, c.a],
            ['B', b, c.b],
          ] as const
        ).map(([side, version, score]) => (
          <li key={side} class="pab-score">
            <span class="pab-score-name">
              {side} · {version}
            </span>
            <span class="pab-score-value">
              {pct(score)} <span class="pab-band">± {pct(c.band)}</span>
            </span>
            <span class="pab-bar" aria-hidden="true">
              <span class="pab-fill" style={{ width: pct(score) }} />
              <span
                class="pab-whisker"
                style={{ left: pct(Math.max(0, score - c.band)), width: pct(Math.min(1, score + c.band) - Math.max(0, score - c.band)) }}
              />
            </span>
          </li>
        ))}
      </ul>
      <p class="pab-axis" aria-hidden="true">
        Bar: both fields right, 0 to 100%. Line: the ± noise band.
      </p>

      <div class={`pab-verdict${verdict.larger ? ' is-larger' : ''}`} role="status">
        {a === b ? (
          <p>A and B are the same prompt, so there is nothing to compare. Pick a different prompt for B.</p>
        ) : (
          <>
            <p class="pab-diff">
              <span aria-hidden="true">{verdict.larger ? '✓ ' : '≈ '}</span>
              B − A: {points(c.diff)}, noise band ± {pct(c.band)}.
            </p>
            <p>{verdict.text}</p>
          </>
        )}
      </div>
      <p class="pab-note">
        The band is 1/√n for the {Math.round(1 / c.band ** 2)} scored tickets: a rough one-sigma noise for a single run.
        A smaller difference could come from the model&apos;s run-to-run variation alone.
      </p>

      <CaseList title="Fixed by B" ids={c.fixed} tickets={tickets} answersA={answersOf(a)} answersB={answersOf(b)} />
      <CaseList title="Broken by B" ids={c.broken} tickets={tickets} answersA={answersOf(a)} answersB={answersOf(b)} />
      <style>{css}</style>
    </section>
  );
}

const css = `
.pab { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.pab-head { display: grid; gap: 8px; }
.pab-eyebrow, .pab-picker-label {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.pab-pickers { display: flex; flex-wrap: wrap; gap: 8px 20px; }
.pab-picker { display: inline-flex; align-items: center; gap: 8px; min-width: 0; max-width: 100%; }
.pab-picker select {
  min-width: 0; max-width: 100%; font: inherit; font-size: var(--text-small); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 5px 8px;
}
.pab-picker select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.pab-scores { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.pab-score { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 12px; align-items: baseline; }
.pab-score-name { font-family: var(--font-mono); font-size: 0.8125rem; color: var(--ink); overflow-wrap: anywhere; }
.pab-score-value { font-family: var(--font-mono); font-size: var(--text-small); font-weight: 600; color: var(--ink); text-align: right; }
.pab-band { font-weight: 400; color: var(--muted); }
.pab-bar {
  grid-column: 1 / -1; position: relative; height: 14px; background: var(--surface-2); border-radius: var(--radius);
}
.pab-fill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--accent); border-radius: var(--radius); }
.pab-whisker { position: absolute; top: 50%; height: 0; border-top: 2px solid var(--ink); }
.pab-whisker::before, .pab-whisker::after {
  content: ''; position: absolute; top: -7px; height: 12px; border-left: 2px solid var(--ink);
}
.pab-whisker::before { left: 0; }
.pab-whisker::after { right: 0; }
.pab-axis, .pab-note { margin: 0; font-size: var(--text-small); color: var(--muted); }
.pab-verdict {
  padding: 10px 14px; background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--muted);
  border-radius: var(--radius); font-size: var(--text-small); color: var(--ink);
}
.pab-verdict.is-larger { border-left-color: var(--good); }
.pab-verdict p { margin: 0; }
.pab-diff { font-family: var(--font-mono); font-weight: 600; }
.pab-none { margin: 0; font-size: var(--text-small); color: var(--muted); }
.pab-group { padding: 12px 14px; min-width: 0; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.pab-group-title { margin: 0 0 6px; font-family: var(--font-mono); font-size: var(--text-label); font-weight: 600; color: var(--ink); }
.pab-cases { margin: 0; padding: 0; list-style: none; max-height: 20rem; overflow: auto; }
.pab-cases li { padding: 8px 0; border-top: 1px solid var(--line); }
.pab-case-head, .pab-case-answers {
  margin: 0 0 4px; display: flex; flex-wrap: wrap; gap: 4px 12px; font-family: var(--font-mono); font-size: 0.8125rem;
}
.pab-case-id { font-weight: 600; color: var(--ink); }
.pab-expected { color: var(--muted); }
.pab-case-answers { color: var(--ink); }
.pab-case-text { margin: 0; font-size: var(--text-small); line-height: 1.55; color: var(--ink); overflow-wrap: anywhere; }
`;
