import { useId, useState } from 'preact/hooks';
import { allMatch, compareRows, runOptions, type InspectSummary } from '../../lib/frameworkCompare';
import NoRecordedRun from './NoRecordedRun';

export const SUMMARIZE_COMMAND = 'python llms/inspect/summarize.py';

export default function FrameworkCompare({ summary }: { summary: InspectSummary | null }) {
  const groupName = useId();
  const [runId, setRunId] = useState(summary?.runs[0]?.id ?? '');

  if (!summary) {
    return (
      <section class="fc" aria-label="Lab vs Inspect">
        <p class="fc-eyebrow">Lab vs Inspect</p>
        <NoRecordedRun
          command={SUMMARIZE_COMMAND}
          text="Run the Inspect tasks once in the Inspect environment, then summarise them. This reads saved answers and calls no API. Then rebuild the app."
        />
        <style>{css}</style>
      </section>
    );
  }

  const options = runOptions(summary);
  const rows = compareRows(summary, runId);
  const matched = rows.filter((r) => r.match).length;
  const run = summary.runs.find((r) => r.id === runId);
  const announce = `${run?.label ?? runId}: ${matched} of ${rows.length} numbers match.`;

  return (
    <section class="fc" aria-label="Lab vs Inspect">
      <p class="fc-eyebrow">
        Lab vs Inspect · inspect-ai {summary.inspect_version} · {allMatch(summary) ? 'every number matches' : 'some numbers differ'}
      </p>
      <div role="radiogroup" aria-label="Run to compare" class="fc-runs">
        {options.map((o) => (
          <label key={o.id} class={`fc-run${o.id === runId ? ' is-on' : ''}`}>
            <input type="radio" name={groupName} value={o.id} checked={o.id === runId} onChange={() => setRunId(o.id)} />
            <span>{o.label}</span>
          </label>
        ))}
      </div>
      <p class="fc-cmd">
        <span>Lab: </span>
        <code>{run?.lab_command}</code>
      </p>
      <p class="fc-cmd">
        <span>Inspect: </span>
        <code>{run?.command}</code>
      </p>
      <div class="fc-scroll">
        <table class="fc-table">
          <caption>Lab vs Inspect</caption>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              <th scope="col">Lab</th>
              <th scope="col">Inspect</th>
              <th scope="col">Stderr</th>
              <th scope="col">n</th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.metric}>
                <th scope="row">{r.metric}</th>
                <td class="fc-lab">{r.lab}</td>
                <td class="fc-inspect">{r.inspect}</td>
                <td>{r.stderr}</td>
                <td>{r.n}</td>
                <td class={r.match ? 'fc-ok' : 'fc-bad'}>{r.mark}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="fc-live" role="status">
        {announce}
      </p>
      <div class="fc-lists">
        <div>
          <h4 class="fc-h">Inspect gives you</h4>
          <ul aria-label="Inspect gives you">
            {summary.framework_gives.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
        <div>
          <h4 class="fc-h">You still own</h4>
          <ul aria-label="You still own">
            {summary.we_still_own.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.fc { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.fc-eyebrow {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.fc-runs { display: flex; flex-wrap: wrap; gap: 8px; }
.fc-run {
  display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; font-size: var(--text-small); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); cursor: pointer; min-width: 0;
}
.fc-run.is-on { border-color: var(--accent); background: var(--surface-2); }
.fc-run:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px; }
.fc-cmd { margin: 0; font-size: var(--text-small); color: var(--muted); overflow-wrap: anywhere; }
.fc-cmd code { font-family: var(--font-mono); font-size: 0.8125rem; color: var(--ink); }
.fc-scroll { overflow-x: auto; }
.fc-table { width: 100%; border-collapse: collapse; font-size: var(--text-small); color: var(--ink); }
.fc-table caption {
  text-align: left; padding-bottom: 6px; font-family: var(--font-mono); font-size: var(--text-label);
  font-weight: 600; color: var(--ink);
}
.fc-table th, .fc-table td { padding: 6px 10px; text-align: left; border-top: 1px solid var(--line); font-variant-numeric: tabular-nums; }
.fc-table thead th { font-family: var(--font-mono); font-size: var(--text-label); font-weight: 600; color: var(--muted); }
.fc-table tbody th { font-family: var(--font-mono); font-weight: 600; }
.fc-ok { font-weight: 600; color: var(--good); }
.fc-bad { font-weight: 600; color: var(--ink); }
.fc-live { margin: 0; font-size: var(--text-small); color: var(--muted); }
.fc-lists { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 12px; }
.fc-lists > div { padding: 12px 14px; min-width: 0; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.fc-h { margin: 0 0 6px; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
.fc-lists ul { margin: 0; padding-left: 18px; font-size: var(--text-small); line-height: 1.5; color: var(--ink); }
`;
