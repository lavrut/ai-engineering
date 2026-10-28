import { useState } from 'preact/hooks';
import { FLAG_TEXT, scoreChecks, traceStepSummary, traceStepView, type AgentTraceProps, type Desk, type Engine, type TraceStepView } from '../../lib/agentsAids';
import NoRecordedRun from '../llms/NoRecordedRun';
import { useStepper } from '../mcp/useStepper';
import { AGENTS_CSS } from './agentsStyle';
import Picker from './Picker';

const RECORD_TEXT =
  'This run calls the Claude API and costs money: the command prints an estimate and asks before it spends anything. Then rebuild the derived data (the last command) and the app.';

/** A step's flag in words (never colour alone). */
function Flag({ step }: { step: TraceStepView }) {
  if (!step.flag) return null;
  return <span class={`ag-flag ag-flag-${step.flag}`}>{FLAG_TEXT[step.flag]}</span>;
}

export default function AgentTrace({ desks, engines, tickets, runs, initial, loopNote }: AgentTraceProps) {
  const [desk, setDesk] = useState<Desk>(initial.desk);
  const [engine, setEngine] = useState<Engine>(initial.engine);
  const [ticketId, setTicket] = useState(initial.ticket);
  const ticket = tickets.find((t) => t.id === ticketId) ?? tickets[0];
  const run = runs[desk][engine][ticket.id];
  const steps = run.recorded ? run.steps.map(traceStepView) : [];
  const s = useStepper(run.recorded ? run.steps.length : 1);
  const at = run.recorded ? Math.min(s.at, run.steps.length - 1) : 0;
  const choose = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    s.reset();
  };
  const engineLabel = engines.find((e) => e.id === engine)?.label ?? engine;
  const deskLabel = desks.find((d) => d.id === desk)?.label ?? desk;

  return (
    <section class="mc" aria-label="Desk run step by step">
      <header class="mc-head">
        <p class="mc-eyebrow">
          {engineLabel} · {deskLabel} · ticket {ticket.id}
        </p>
        <p class="mc-label">Recorded runs, replayed: no calls from this page</p>
      </header>

      <fieldset class="ag-pickers">
        <legend>Choose a run</legend>
        <Picker label="Desk" value={desk} options={desks} onChange={choose(setDesk)} />
        {engines.length > 1 && <Picker label="Engine" value={engine} options={engines} onChange={choose(setEngine)} />}
        <Picker
          label="Ticket"
          value={ticket.id}
          options={tickets.map((t) => ({ id: t.id, label: t.injection ? `${t.id} (prompt injection)` : t.id }))}
          onChange={choose(setTicket)}
        />
      </fieldset>

      <div class={`at-ticket${ticket.injection ? ' at-injected' : ''}`}>
        <p class="mc-pre-label">
          Ticket {ticket.id}, labelled {ticket.category}
          {ticket.injection && <span class="ag-flag ag-flag-refused">Prompt injection</span>}
        </p>
        <p class="at-text">
          {ticket.before}
          {ticket.injected && <mark>{ticket.injected}</mark>}
        </p>
        {ticket.injection && (
          <p class="mc-hint">The marked text is the injection: it tells the agent to relabel every ticket. Watch for a write, and whether it was refused.</p>
        )}
      </div>

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
            <ol class="mc-steps ag-list" aria-label="Steps in order">
              {steps.map((x, i) => (
                <li key={i} aria-current={i === at ? 'step' : undefined}>
                  {i === at ? '▸ ' : ''}
                  {i + 1}. {x.title}
                  <Flag step={x} />
                </li>
              ))}
            </ol>
          </div>

          <h3 class="mc-h">
            {at + 1}. {steps[at].title}
            <Flag step={steps[at]} />
          </h3>
          <p class="mc-live" aria-live="polite">
            {traceStepSummary(run, at)}
          </p>
          <p class="ag-detail">{steps[at].detail}</p>
          {steps[at].note && (
            <p class="mc-note" role="note">
              {steps[at].note}
            </p>
          )}

          <p class="mc-hint">
            This run: {run.counts.handoffs} {run.counts.handoffs === 1 ? 'handoff' : 'handoffs'}, {run.counts.refused} refused{' '}
            {run.counts.refused === 1 ? 'write' : 'writes'}, {run.counts.written} {run.counts.written === 1 ? 'write' : 'writes'} made
            {run.stop ? `; it ended with ${run.stop}` : ''}.
          </p>

          <div class="mc-scroll mc-focus" role="region" aria-label="Run totals table" tabIndex={0}>
            <table class="mc-table" aria-label="Run totals">
              <thead>
                <tr>
                  <th scope="col" class="mc-num">Model calls</th>
                  <th scope="col" class="mc-num">Cost (ledger)</th>
                  {run.totals.sdkCost && <th scope="col" class="mc-num">The SDK's own estimate</th>}
                  <th scope="col" class="mc-num">Time</th>
                  <th scope="col" class="mc-num">Tokens in</th>
                  <th scope="col" class="mc-num">Tokens out</th>
                  <th scope="col" class="mc-num">Cached in</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td class="mc-num">{run.totals.calls}</td>
                  <td class="mc-num">{run.totals.cost}</td>
                  {run.totals.sdkCost && <td class="mc-num">{run.totals.sdkCost}</td>}
                  <td class="mc-num">{run.totals.time}</td>
                  <td class="mc-num">{run.totals.tokensIn}</td>
                  <td class="mc-num">{run.totals.tokensOut}</td>
                  <td class="mc-num">{run.totals.cacheRead}</td>
                </tr>
              </tbody>
            </table>
          </div>

          {run.score && (
            <div class="mc-scroll mc-focus" role="region" aria-label="Score table" tabIndex={0}>
              <table class="mc-table" aria-label="The scorer's row">
                <tbody>
                  {scoreChecks(run.score).map((c) => (
                    <tr key={c.label}>
                      <th scope="row">{c.label}</th>
                      <td>{c.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {run.totals.sdkCost && (
            <p class="mc-hint">
              The SDK's own estimate is the Claude Agent SDK CLI's total_cost_usd: a client-side estimate it reports itself. The ledger cost is what the recorded
              calls cost, priced from their usage, as for every engine.
            </p>
          )}
          <details>
            <summary class="mc-hint">The final reply</summary>
            <p class="ag-reply">{run.reply}</p>
          </details>
        </>
      )}
      <p class="mc-hint">
        Cost and time are the recorded calls' own, from the proxy's ledger; a replay costs nothing. {loopNote}
      </p>
      <style>{AGENTS_CSS + css}</style>
    </section>
  );
}

const css = `
.at-ticket { display: grid; gap: 6px; padding: 10px 14px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.at-injected { border-left: 4px solid var(--bad); }
.at-text { margin: 0; font-size: var(--text-small); color: var(--ink); overflow-wrap: anywhere; }
.at-text mark { color: var(--ink); background: var(--surface-2); outline: 1px dashed var(--bad); }
`;
