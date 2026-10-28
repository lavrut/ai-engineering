import { useState } from 'preact/hooks';
import { patternStepSummary, patternStepView, type PatternEngine, type PatternId, type PatternStepperProps } from '../../lib/agentsAids';
import NoRecordedRun from '../llms/NoRecordedRun';
import { useStepper } from '../mcp/useStepper';
import { AGENTS_CSS } from './agentsStyle';
import Picker from './Picker';

const RECORD_TEXT =
  'This run calls the Claude API and costs money: the command prints an estimate and asks before it spends anything. Then rebuild the derived data (the last command) and the app.';

export default function PatternStepper({ patterns, tickets, engines, runs, notes, initial }: PatternStepperProps) {
  const [patternId, setPattern] = useState<PatternId>(initial.pattern);
  const [ticket, setTicket] = useState(initial.ticket);
  const [engine, setEngine] = useState<PatternEngine>(initial.engine);
  const pattern = patterns.find((p) => p.id === patternId) ?? patterns[0];
  const run = runs[pattern.id][ticket][engine];
  const steps = run.recorded ? run.steps.map(patternStepView) : [];
  const s = useStepper(run.recorded ? run.steps.length : 1);
  const at = run.recorded ? Math.min(s.at, run.steps.length - 1) : 0;
  const choose = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    s.reset();
  };

  return (
    <section class="mc" aria-label="Pattern step by step">
      <header class="mc-head">
        <p class="mc-eyebrow">{pattern.name} · ticket {ticket}</p>
        <p class="mc-label">Recorded runs, replayed: no calls from this page</p>
      </header>

      <fieldset class="ag-pickers">
        <legend>Choose a run</legend>
        {patterns.length > 1 && <Picker label="Pattern" value={pattern.id} options={patterns.map((p) => ({ id: p.id, label: p.name }))} onChange={choose(setPattern)} />}
        <Picker label="Ticket" value={ticket} options={tickets.map((t) => ({ id: t, label: t }))} onChange={choose(setTicket)} />
        <Picker label="Language" value={engine} options={engines} onChange={choose(setEngine)} />
      </fieldset>

      <p class="mc-hint">
        {pattern.what} <span>{pattern.caption}</span>
      </p>

      {!run.recorded ? (
        <NoRecordedRun command={run.command} text={RECORD_TEXT} />
      ) : (
        <>
          <div class="mc-focus ag-group" role="group" aria-label="Steps" tabIndex={0} onKeyDown={s.onKeyDown}>
            <div class="mc-buttons">
              <button ref={s.prevRef} type="button" class="mc-btn" disabled={at <= 0} onClick={s.prev}>
                Previous
              </button>
              <button ref={s.nextRef} type="button" class="mc-btn mc-btn-primary" disabled={at >= s.last} onClick={s.next}>
                Next
              </button>
              <button ref={s.resetRef} type="button" class="mc-btn" onClick={s.reset}>
                Reset
              </button>
            </div>
            <ol class="mc-steps" aria-label="Steps in order">
              {steps.map((x, i) => (
                <li key={i} aria-current={i === at ? 'step' : undefined}>
                  {i === at ? '▸ ' : ''}
                  {i + 1}. {x.title}
                </li>
              ))}
            </ol>
          </div>

          <h3 class="mc-h">
            {at + 1}. {steps[at].title}
          </h3>
          <p class="mc-live" aria-live="polite">
            {patternStepSummary(run, at)}
          </p>
          <p class="ag-detail">{steps[at].detail}</p>
          {steps[at].result && (
            <p class="ag-result">
              <span class="mc-pre-label">What it produced: </span>
              {steps[at].result}
            </p>
          )}
          <details>
            <summary class="mc-hint">The final reply</summary>
            <p class="ag-reply">{run.reply}</p>
          </details>
        </>
      )}

      <div class="mc-scroll mc-focus" role="region" aria-label="Python and Go table" tabIndex={0}>
        <table class="mc-table" aria-label={`${pattern.name} on ${ticket}: Python and Go`}>
          <thead>
            <tr>
              <th scope="col">Language</th>
              <th scope="col" class="mc-num">Model calls</th>
              <th scope="col" class="mc-num">Cost</th>
              <th scope="col" class="mc-num">Time</th>
            </tr>
          </thead>
          <tbody>
            {engines.map((e) => {
              const r = runs[pattern.id][ticket][e.id];
              return (
                <tr key={e.id}>
                  <th scope="row">{e.label}</th>
                  {r.recorded ? (
                    <>
                      <td class="mc-num">{r.calls}</td>
                      <td class="mc-num">{r.cost}</td>
                      <td class="mc-num">{r.time}</td>
                    </>
                  ) : (
                    <td colSpan={3}>not recorded</td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {notes[pattern.id] && <p class="mc-hint">{notes[pattern.id]}.</p>}
      <p class="mc-hint">Cost and time are the recorded calls' own, from the proxy's ledger; a replay costs nothing.</p>
      <style>{AGENTS_CSS}</style>
    </section>
  );
}
