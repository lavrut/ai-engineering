import { useState } from 'preact/hooks';
import { ticketSummary, ticketView, type EngineScorecardProps, type ScoreRowView } from '../../lib/agentsAids';
import NoRecordedRun from '../llms/NoRecordedRun';
import { AGENTS_CSS } from './agentsStyle';
import Picker from './Picker';

const RECORD_TEXT =
  'These runs call the Claude API and cost money: the record command prints an estimate and asks before it spends anything. Then rescore and rebuild the derived data (the last two commands) and the app.';

const clamp = (x: number) => Math.max(0, Math.min(100, x * 100));

/** What a row's mark says, for screen readers (the table below has the same numbers). */
function markLabel(r: ScoreRowView, minText: string): string {
  if (r.value.success === null) return `${r.label}: ${r.status}, no success rate.`;
  const side = r.clears ? `clears the ${minText} gate` : `below the ${minText} gate`;
  return `${r.label}: success ${r.success}, band ${r.band}, success minus band ${r.floor}: ${side}.`;
}

export default function EngineScorecard({ rows, gate, judge, tickets, initial, costNote }: EngineScorecardProps) {
  const [pick, setPick] = useState(initial);
  const picked = rows.find((r) => `${r.engine}/${r.desk}` === pick) ?? rows[0];
  const shown = (tickets[pick] ?? []).map(ticketView);
  const sdk = shown.some((t) => t.sdkCost);
  const drill = Object.keys(tickets).length > 0;
  const missing = rows.filter((r) => r.command);

  return (
    <section class="mc" aria-label="Engine scorecard">
      <header class="mc-head">
        <p class="mc-eyebrow">Every engine and desk on the same tickets</p>
        <p class="mc-label">Scored from the recorded runs: no calls from this page</p>
      </header>

      <div class={`es-gate${gate.passed ? '' : ' es-gate-closed'}`} role="status">
        <p class="es-verdict">{gate.headline}</p>
        <p class="mc-hint">{gate.rule}</p>
        <p class="es-reason">
          <span class="mc-pre-label">The scorer's verdict: </span>
          {gate.reason}
        </p>
      </div>

      <ul class="es-chart" aria-label={`Success rate with its noise band, against the ${gate.minText} gate`}>
        {rows.map((r) => {
          const s = r.value.success;
          const b = r.value.band;
          return (
            <li key={`${r.engine}/${r.desk}`}>
              <p class="ag-name">
                {r.label}: {s === null ? r.status : `${r.success} ${r.band}`}
              </p>
              <div class="es-track" role="img" aria-label={markLabel(r, gate.minText)}>
                {s !== null && b !== null && (
                  <>
                    <span class="es-band" style={{ left: `${clamp(s - b)}%`, width: `${clamp(s + b) - clamp(s - b)}%` }} />
                    <span class="es-dot" style={{ left: `${clamp(s)}%` }} />
                  </>
                )}
                <span class="es-line" style={{ left: `${gate.min * 100}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
      <p class="mc-hint">
        Each bar is the noise band around the success rate (the dot); the vertical line is the {gate.minText} gate. A desk clears it only when the left end of
        its band does.
      </p>

      {missing.map((r) => (
        <div key={`${r.engine}/${r.desk}`}>
          <p class="ag-name">
            {r.label}: {r.status}
          </p>
          <NoRecordedRun command={r.command!} text={RECORD_TEXT} />
        </div>
      ))}

      <div class="mc-scroll mc-focus" role="region" aria-label="Scores table" tabIndex={0}>
        <table class="mc-table ag-table-wide" aria-label="Scores per engine and desk">
          <thead>
            <tr>
              <th scope="col">Engine</th>
              <th scope="col">Desk</th>
              <th scope="col">Runs</th>
              <th scope="col" class="mc-num">
                Success
              </th>
              <th scope="col" class="mc-num">
                Band
              </th>
              <th scope="col" class="mc-num">
                Success − band
              </th>
              <th scope="col">Clears {gate.minText}?</th>
              <th scope="col" class="mc-num">
                Trajectory
              </th>
              <th scope="col" class="mc-num">
                Safety failures
              </th>
              <th scope="col" class="mc-num">
                Mean cost
              </th>
              <th scope="col" class="mc-num">
                p50 time
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.engine}/${r.desk}`}>
                <th scope="row">{r.engineLabel}</th>
                <td>{r.deskLabel}</td>
                <td>
                  {r.complete} ({r.status})
                </td>
                <td class="mc-num">{r.success}</td>
                <td class="mc-num">{r.band}</td>
                <td class="mc-num">{r.floor}</td>
                <td>{r.clears ? 'Yes' : 'No'}</td>
                <td class="mc-num">{r.trajectory}</td>
                <td class="mc-num">{r.safety}</td>
                <td class="mc-num">{r.meanCost}</td>
                <td class="mc-num">{r.p50}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="mc-hint">
        {judge} {costNote}
      </p>

      {drill && (
        <>
          <fieldset class="ag-pickers">
            <legend>Drill down</legend>
            <Picker label="Show the tickets of" value={pick} options={rows.map((r) => ({ id: `${r.engine}/${r.desk}`, label: r.label }))} onChange={setPick} />
          </fieldset>
          <p class="mc-live" aria-live="polite">
            {ticketSummary(picked, shown)}
          </p>
          {shown.length > 0 && (
            <div class="mc-scroll mc-focus" role="region" aria-label="Tickets table" tabIndex={0}>
              <table class="mc-table ag-table-wide" aria-label={`Tickets of ${picked.label}`}>
                <thead>
                  <tr>
                    <th scope="col">Ticket</th>
                    <th scope="col">Task success</th>
                    <th scope="col">Category right</th>
                    <th scope="col">Judge passed</th>
                    <th scope="col">Trajectory</th>
                    <th scope="col">Safety failure</th>
                    <th scope="col" class="mc-num">
                      Cost (ledger)
                    </th>
                    {sdk && (
                      <th scope="col" class="mc-num">
                        The SDK's own estimate
                      </th>
                    )}
                    <th scope="col" class="mc-num">
                      Time
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((t) => (
                    <tr key={t.ticket}>
                      <th scope="row">{t.ticket}</th>
                      <td>{t.success}</td>
                      <td>{t.category}</td>
                      <td>{t.judge}</td>
                      <td>{t.trajectory}</td>
                      <td>{t.safety}</td>
                      <td class="mc-num">{t.cost}</td>
                      {sdk && <td class="mc-num">{t.sdkCost ?? 'n/a'}</td>}
                      <td class="mc-num">{t.time}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {sdk && (
            <p class="mc-hint">
              The SDK's own estimate is the Claude Agent SDK CLI's total_cost_usd: a client-side estimate it reports itself. The ledger cost beside it is what
              the recorded calls cost, priced from their usage, as for every engine; the scores use the ledger.
            </p>
          )}
        </>
      )}
      <style>{AGENTS_CSS + css}</style>
    </section>
  );
}

const css = `
.es-gate { display: grid; gap: 4px; padding: 12px 16px; background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--good); border-radius: var(--radius); }
.es-gate-closed { border-left-color: var(--bad); }
.es-verdict { margin: 0; font-family: var(--font-display); font-size: var(--text-body); font-weight: 600; color: var(--ink); }
.es-reason { margin: 0; font-family: var(--font-mono); font-size: var(--text-small); color: var(--ink); overflow-wrap: anywhere; }
.es-chart { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.es-chart li { display: grid; gap: 4px; min-width: 0; }
.es-track { position: relative; height: 18px; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); }
.es-band { position: absolute; top: 5px; height: 6px; background: var(--role-grader); border-radius: 3px; }
.es-dot { position: absolute; top: 2px; width: 12px; height: 12px; margin-left: -6px; background: var(--ink); border: 2px solid var(--surface); border-radius: 50%; }
.es-line { position: absolute; top: -3px; bottom: -3px; width: 0; border-left: 2px dashed var(--ink); }
`;
