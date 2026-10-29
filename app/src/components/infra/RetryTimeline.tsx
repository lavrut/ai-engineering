import { useRef, useState } from 'preact/hooks';
import { verdictMark, waitedMs, type RetryAttemptView, type RetryTimelineProps } from '../../lib/infraAids';
import InfraMark from './InfraMark';
import { outcomeMark, rowMove, statusMark } from './infraFormat';
import { INFRA_CSS } from './infraStyle';

/** The live region's sentence for one attempt. */
function attemptText(a: RetryAttemptView, of: number): string {
  const answer = a.status === null ? 'dropped the connection' : `answered ${statusMark(a.status).word}`;
  const then = a.n === of ? 'the last try' : `then ${a.backoff_ms} ms of backoff`;
  return `Attempt ${a.n} of ${of}: ${a.model} ${answer}, ${then}.`;
}

export default function RetryTimeline(p: RetryTimelineProps) {
  const [id, setId] = useState(p.initial);
  const [pos, setPos] = useState(0);
  const refs = useRef<(HTMLTableRowElement | null)[]>([]);
  const s = p.scripts.find((x) => x.id === id) ?? p.scripts[0];
  const n = s.attempts.length;
  const at = Math.min(pos, n - 1);

  const pick = (next: string) => {
    setId(next);
    setPos(0);
  };
  const onKey = (e: KeyboardEvent) => {
    const next = rowMove(at, e.key, n);
    if (next === null) return;
    e.preventDefault();
    setPos(next);
    refs.current[next]?.focus();
  };

  return (
    <section class="mc" aria-label="Retry timeline">
      <header class="mc-head">
        <p class="mc-eyebrow">Retries · one request against a scripted upstream</p>
        <p class="mc-label">Read from capstone.json's injector: no calls from this page</p>
      </header>

      <fieldset class="ag-pickers">
        <legend>Script</legend>
        {p.scripts.map((x) => (
          <button key={x.id} type="button" class="mc-btn" aria-pressed={x.id === s.id ? 'true' : 'false'} onClick={() => pick(x.id)}>
            {x.id}
          </button>
        ))}
      </fieldset>
      <p class="mc-hint">Scripted answers, in order (the last repeats): {s.script.join(', ')}</p>
      <p class="mc-live" role="status" aria-live="polite">
        {attemptText(s.attempts[at], n)}
      </p>

      <div class="mc-scroll mc-focus" role="region" aria-label="Attempts table" tabIndex={0}>
        <table class="mc-table in-table" role="grid" aria-label={`Attempts, ${s.id}`}>
          <thead>
            <tr>
              <th scope="col">Attempt</th>
              <th scope="col">Model</th>
              <th scope="col">Answer</th>
              <th scope="col" class="mc-num">
                Backoff before the next
              </th>
            </tr>
          </thead>
          <tbody>
            {s.attempts.map((a, i) => (
              <tr
                key={`${s.id} ${a.n}`}
                class="in-row"
                tabIndex={i === at ? 0 : -1}
                aria-selected={i === at ? 'true' : 'false'}
                ref={(el) => {
                  refs.current[i] = el;
                }}
                onKeyDown={onKey}
                onFocus={() => setPos(i)}
                onClick={() => setPos(i)}
              >
                <th scope="row">{a.n}</th>
                <td role="gridcell">{a.model}</td>
                <td role="gridcell">
                  <InfraMark mark={statusMark(a.status)} />
                </td>
                <td role="gridcell" class="mc-num">
                  {a.backoff_ms} ms
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="mc-hint">Arrow keys move between the attempts; Home and End jump to the first and the last.</p>

      <p class="in-pick" data-testid="retry-outcome">
        <InfraMark mark={outcomeMark(s.outcome)} />: the caller got {s.status} after {n} attempts and {waitedMs(s)} ms of backoff.
      </p>

      <h3 class="mc-h">Stage decisions</h3>
      <ol class="in-lines" aria-label={`Stage decisions, ${s.id}`}>
        {s.decisions.map((d) => (
          <li key={d.stage}>
            <span class="in-stage">{d.stage}:</span> <InfraMark mark={verdictMark(d.verdict)} />
          </li>
        ))}
      </ol>
      {p.about !== null && <p class="in-about">{p.about}</p>}
      <style>{INFRA_CSS}</style>
    </section>
  );
}
