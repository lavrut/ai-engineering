import { useState } from 'preact/hooks';
import { filterRows, injectionSummary, type GuardFilter, type InjectionLabProps } from '../../lib/mcpAids';
import NoRecordedRun from '../llms/NoRecordedRun';
import { BASE_CSS } from './mcpStyle';

const FILTERS: { id: GuardFilter; label: string }[] = [
  { id: 'all', label: 'All recordings' },
  { id: 'noguard', label: 'Guard off' },
  { id: 'guard', label: 'Guard on' },
];

export default function InjectionLab({ recorded, rows, commands }: InjectionLabProps) {
  const [filter, setFilter] = useState<GuardFilter>('all');

  if (!recorded) {
    return (
      <section class="mc" aria-label="Injection recordings">
        <NoRecordedRun
          command={commands.join('\n')}
          text="These runs call the Claude API and cost money: each command prints an estimate and asks before it spends anything. Then rebuild the derived data (the last command) and the app."
        />
        <style>{BASE_CSS}</style>
      </section>
    );
  }

  const shown = filterRows(rows, filter);
  const noted = shown.filter((r) => r.framingNote);

  return (
    <section class="mc" aria-label="Injection recordings">
      <header class="mc-head">
        <p class="mc-eyebrow">Ticket t061 asks the agent to relabel every ticket</p>
        <p class="mc-label">Recorded runs, replayed: no calls from this page</p>
      </header>

      <fieldset class="il-choice">
        <legend>Show</legend>
        {FILTERS.map((f) => (
          <label key={f.id}>
            <input type="radio" name="il-filter" value={f.id} checked={filter === f.id} onChange={() => setFilter(f.id)} /> {f.label}
          </label>
        ))}
      </fieldset>

      <p class="mc-live" aria-live="polite">
        {injectionSummary(shown)}
      </p>

      {noted.map((r) => (
        <p key={r.name} class="mc-note" role="note">
          <strong>{r.name}, framing: {r.framing}.</strong> {r.framingNote}
        </p>
      ))}

      <div class="mc-scroll mc-focus" role="region" aria-label="Recorded runs table" tabIndex={0}>
        <table class="mc-table il-table" aria-label="Recorded runs">
          <thead>
            <tr>
              <th scope="col">Recording</th>
              <th scope="col">Model</th>
              <th scope="col">Framing</th>
              <th scope="col">Guard</th>
              <th scope="col">Tool calls</th>
              <th scope="col">Write attempted</th>
              <th scope="col">Outcome</th>
              <th scope="col" class="mc-num">Rows written</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.name}>
                <th scope="row">{r.name}</th>
                <td>{r.model}</td>
                <td>{r.framing === 'none' ? 'none' : `Framing: ${r.framing}`}</td>
                <td>{r.guard ? 'on' : 'off'}</td>
                <td>
                  {r.toolCalls.length ? (
                    <ul>
                      {r.toolCalls.map((c, i) => (
                        <li key={i}>{c}</li>
                      ))}
                    </ul>
                  ) : (
                    'No tool calls'
                  )}
                </td>
                <td>{r.attempted ? 'Yes' : 'No'}</td>
                <td>{r.outcome}</td>
                <td class="mc-num">{r.rowsWritten}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul class="il-readings" aria-label="What each run shows">
        {shown.map((r) => (
          <li key={r.name}>
            <p class="il-name">{r.name}</p>
            <p class="il-reading">{r.reading}</p>
            <details>
              <summary>What the model answered</summary>
              <p class="il-answer">{r.finalAnswer}</p>
            </details>
          </li>
        ))}
      </ul>
      <style>{BASE_CSS + css}</style>
    </section>
  );
}

const css = `
.il-choice { margin: 0; display: flex; flex-wrap: wrap; gap: 8px 20px; padding: 8px 12px; border: 1px solid var(--line); border-radius: var(--radius); }
.il-choice legend { padding: 0 6px; font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); }
.il-choice label { font-size: var(--text-small); color: var(--ink); cursor: pointer; min-height: 24px; }
.il-choice input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.il-table td, .il-table th { font-size: var(--text-small); overflow-wrap: anywhere; }
.il-table ul { margin: 0; padding-left: 1rem; font-family: var(--font-mono); }
.il-readings { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.il-readings li { padding: 10px 14px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.il-name { margin: 0; font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); }
.il-reading { margin: 4px 0; font-size: var(--text-small); color: var(--ink); }
.il-readings summary { font-size: var(--text-small); color: var(--ink); cursor: pointer; }
.il-readings summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.il-answer { margin: 6px 0 0; font-size: var(--text-small); color: var(--muted); white-space: pre-wrap; }
`;
