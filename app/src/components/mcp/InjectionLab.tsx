import { useState } from 'preact/hooks';
import { filterRows, injectionSummary, simulatedSummary, type GuardFilter, type InjectionLabProps, type InjectionRow } from '../../lib/mcpAids';
import NoRecordedRun from '../llms/NoRecordedRun';
import { BASE_CSS } from './mcpStyle';

const FILTERS: { id: GuardFilter; label: string }[] = [
  { id: 'all', label: 'All runs' },
  { id: 'noguard', label: 'Guard off' },
  { id: 'guard', label: 'Guard on' },
];

/** One table of runs. Simulated rows carry a visible "simulated" badge in their first cell. */
function RunTable({ rows, label }: { rows: InjectionRow[]; label: string }) {
  return (
    <div class="mc-scroll mc-focus" role="region" aria-label={`${label} table`} tabIndex={0}>
      <table class="mc-table il-table" aria-label={label}>
        <thead>
          <tr>
            <th scope="col">{rows.some((r) => r.simulated) ? 'Run' : 'Recording'}</th>
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
          {rows.map((r) => (
            <tr key={r.name}>
              <th scope="row">
                {r.simulated && <span class="il-badge">simulated</span>} {r.name}
              </th>
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
  );
}

function Readings({ rows, label, answer }: { rows: InjectionRow[]; label: string; answer: string }) {
  return (
    <ul class="il-readings" aria-label={label}>
      {rows.map((r) => (
        <li key={r.name}>
          <p class="il-name">{r.name}</p>
          <p class="il-reading">{r.reading}</p>
          <details>
            <summary>{answer}</summary>
            <p class="il-answer">{r.finalAnswer}</p>
          </details>
        </li>
      ))}
    </ul>
  );
}

export default function InjectionLab({ recorded, rows, simulatedRows, commands }: InjectionLabProps) {
  const [filter, setFilter] = useState<GuardFilter>('all');
  const sim = filterRows(simulatedRows, filter);

  const simulatedSection = simulatedRows.length > 0 && (
    <section class="il-sim" aria-labelledby="il-sim-head">
      <h3 id="il-sim-head" class="il-sim-head">
        Simulated runs: a scripted model, not a real one
      </h3>
      <p class="mc-note" role="note">
        These rows come from <code>simulate.py</code>: a script that does what t061 asks, run through the real agent, guard and servers. No model was
        called. They show what the defences in code do when a model obeys.
      </p>
      <p class="mc-live" aria-live="polite">
        {simulatedSummary(sim)}
      </p>
      <RunTable rows={sim} label="Simulated runs" />
      <Readings rows={sim} label="What each simulated run shows" answer="What the script answered" />
    </section>
  );

  if (!recorded) {
    return (
      <section class="mc" aria-label="Injection recordings">
        <NoRecordedRun
          command={commands.join('\n')}
          text="These runs call the Claude API and cost money: each command prints an estimate and asks before it spends anything. Then rebuild the derived data (the last command) and the app."
        />
        {simulatedSection}
        <style>{BASE_CSS + css}</style>
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

      <RunTable rows={shown} label="Recorded runs" />
      <Readings rows={shown} label="What each run shows" answer="What the model answered" />
      {simulatedSection}
      <style>{BASE_CSS + css}</style>
    </section>
  );
}

const css = `
.il-choice { margin: 0; display: flex; flex-wrap: wrap; gap: 8px 20px; padding: 8px 12px; border: 1px solid var(--line); border-radius: var(--radius); }
.il-choice legend { padding: 0 6px; font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); }
.il-choice label { font-size: var(--text-small); color: var(--ink); cursor: pointer; min-height: 24px; }
.il-choice input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.il-table { min-width: 52rem; }
.il-table td, .il-table th { font-size: var(--text-small); }
.il-table th[scope="row"] { white-space: nowrap; }
.il-table td:nth-child(2), .il-table td:nth-child(3), .il-table td:nth-child(4), .il-table td:nth-child(6), .il-table td:nth-child(7) { white-space: nowrap; }
.il-table td:nth-child(5) { white-space: nowrap; }
.il-table ul { margin: 0; padding-left: 1rem; font-family: var(--font-mono); }
.il-readings { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.il-readings li { padding: 10px 14px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.il-name { margin: 0; font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); }
.il-reading { margin: 4px 0; font-size: var(--text-small); color: var(--ink); }
.il-readings summary { font-size: var(--text-small); color: var(--ink); cursor: pointer; }
.il-readings summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.il-answer { margin: 6px 0 0; font-size: var(--text-small); color: var(--muted); white-space: pre-wrap; }
.il-sim { display: grid; gap: 10px; margin-top: 8px; padding-top: 12px; border-top: 2px dashed var(--warn); }
.il-sim-head { margin: 0; font-family: var(--font-display); font-size: var(--text-body); color: var(--ink); }
.il-badge { display: inline-block; padding: 0 6px; border: 1px solid var(--warn); border-radius: var(--radius); font-family: var(--font-mono); font-size: var(--text-label); color: var(--ink); text-transform: uppercase; letter-spacing: 0.04em; }
`;
