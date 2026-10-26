import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { simFrames, type EvalSimProps } from '../../lib/llmsAids';
import NoRecordedRun from './NoRecordedRun';

const TICK_MS = 160;
const fmt = (n: number) => n.toLocaleString('en-US');
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

export default function EvalRunSim({ recorded, model, label, calls, reps, band, workers }: EvalSimProps) {
  const frames = useMemo(() => (recorded ? simFrames(calls, model, reps, workers) : []), [recorded, calls, model, reps, workers]);
  const [at, setAt] = useState(0);
  const [playing, setPlaying] = useState(false);
  const last = frames.length - 1;
  const stepRef = useRef<HTMLButtonElement>(null);
  const playRef = useRef<HTMLButtonElement>(null);
  const resetRef = useRef<HTMLButtonElement>(null);
  // Step and Play are disabled at the end; move keyboard focus to Reset so it is not dropped to the page.
  const keepFocus = (from: HTMLButtonElement | null) => {
    if (from && document.activeElement === from) resetRef.current?.focus();
  };

  useEffect(() => {
    if (!playing) return;
    if (at >= last) {
      setPlaying(false);
      keepFocus(playRef.current);
      return;
    }
    const t = setTimeout(() => setAt((i) => Math.min(i + 1, last)), TICK_MS);
    return () => clearTimeout(t);
  }, [playing, at, last]);

  if (!recorded) {
    return (
      <section class="ers" aria-label="Eval run simulator">
        <p class="ers-eyebrow">Eval run</p>
        <NoRecordedRun />
        <style>{css}</style>
      </section>
    );
  }

  const f = frames[at];
  const doneIds = new Set(
    frames
      .slice(1, at + 1)
      .map((x) => x.last?.id)
      .filter(Boolean),
  );
  const shownReps = reps.slice(0, f.repsShown);
  const scores = shownReps.map((r) => r.both);
  const spread = scores.length > 1 ? Math.max(...scores) - Math.min(...scores) : 0;
  const allShown = reps.length > 0 && f.repsShown === reps.length;
  const status = at === 0 ? 'ready' : f.done < f.total ? 'calls running' : allShown ? 'done' : 'repeats';

  return (
    <section class="ers" aria-label={`Eval run simulator: ${label}`}>
      <header class="ers-head">
        <p class="ers-eyebrow">
          Eval run · <span class="ers-run">{label}</span>
        </p>
        <p class="ers-label">Replays recorded latencies and tokens; no model is called</p>
      </header>

      <div class="ers-buttons">
        <button
          ref={stepRef}
          type="button"
          class="ers-btn ers-btn-primary"
          disabled={at >= last}
          onClick={() => {
            const next = Math.min(at + 1, last);
            setAt(next);
            if (next >= last) keepFocus(stepRef.current);
          }}
        >
          Step
        </button>
        <button ref={playRef} type="button" class="ers-btn" disabled={!playing && at >= last} onClick={() => setPlaying((p) => !p)}>
          {playing ? 'Pause' : 'Play'}
        </button>
        <button
          ref={resetRef}
          type="button"
          class="ers-btn"
          onClick={() => {
            setPlaying(false);
            setAt(0);
          }}
        >
          Reset
        </button>
      </div>

      <dl class="ers-state" aria-label="Run state">
        <div>
          <dt>Calls finished</dt>
          <dd>
            {f.done} of {f.total}
          </dd>
        </div>
        <div>
          <dt>Elapsed</dt>
          <dd>{(f.elapsedMs / 1000).toFixed(1)} s</dd>
        </div>
        <div>
          <dt>Input tokens</dt>
          <dd>{fmt(f.inputTokens)}</dd>
        </div>
        <div>
          <dt>Output tokens</dt>
          <dd>{fmt(f.outputTokens)}</dd>
        </div>
        <div>
          <dt>Cost</dt>
          <dd>${f.cost.toFixed(5)}</dd>
        </div>
        <div>
          <dt>Both right</dt>
          <dd>
            {f.correct} of {f.done}
          </dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd aria-live="polite">{status}</dd>
        </div>
      </dl>

      <p class="ers-note">
        {f.last
          ? `Last to finish: ${f.last.id}, ${fmt(f.last.ms)} ms, ${fmt(f.last.in)} in / ${fmt(f.last.out)} out.`
          : `${f.total} calls, ${workers} at a time, each taking the latency it took when it was recorded.`}
      </p>

      <ol class="ers-calls" aria-label="Calls in ticket order">
        {calls.map((c) => {
          const done = doneIds.has(c.id);
          const state = done ? (c.ok ? 'right' : 'wrong') : 'waiting';
          return (
            <li
              key={c.id}
              class={`ers-call${done ? ' is-done' : ''}${done && !c.ok ? ' is-wrong' : ''}${f.last?.id === c.id && f.repsShown === 0 ? ' is-new' : ''}`}
              aria-label={`${c.id}: ${state}`}
            >
              <span>{c.id}</span>
              <span aria-hidden="true">{done ? (c.ok ? '✓' : '✗') : ''}</span>
            </li>
          );
        })}
      </ol>

      <div class="ers-reps">
        <p class="ers-reps-title">Noise repeats: same prompt, same {f.total} tickets, run again</p>
        <ul>
          {reps.map((r, i) => {
            const shown = i < f.repsShown;
            return (
              <li key={r.rep}>
                <span class="ers-rep-name">Repeat {r.rep}</span>
                <span class="ers-bar" aria-hidden="true">
                  <span style={{ width: shown ? `${r.both * 100}%` : '0%' }} />
                </span>
                <span class="ers-rep-score">{shown ? pct(r.both) : '—'}</span>
              </li>
            );
          })}
        </ul>
        {allShown && (
          <p class="ers-band">
            Both-fields accuracy moves by up to {(spread * 100).toFixed(1)} points between repeats. The noise band for
            one run of {f.total} is ±{pct(band)} (1/√{reps[0].scored}): a difference smaller than that is not evidence.
          </p>
        )}
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.ers { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.ers-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.ers-eyebrow, .ers-state dt, .ers-reps-title {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.ers-run { text-transform: none; letter-spacing: 0; color: var(--ink); }
.ers-label {
  margin: 0; padding: 3px 8px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--muted); border: 1px dashed var(--line); border-radius: var(--radius);
}
.ers-buttons { display: flex; flex-wrap: wrap; gap: 8px; }
.ers-btn {
  font: inherit; font-size: var(--text-small); font-weight: 500; padding: 6px 14px; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); cursor: pointer;
}
.ers-btn-primary { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.ers-btn:disabled { color: var(--muted); background: transparent; border-color: var(--line); cursor: default; }
.ers-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ers-state {
  margin: 0; display: flex; flex-wrap: wrap; gap: 10px 24px; padding: 10px 14px;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius);
}
.ers-state dd {
  margin: 2px 0 0; font-family: var(--font-mono); font-size: var(--text-small); font-weight: 600;
  font-variant-numeric: tabular-nums; color: var(--ink);
}
.ers-note { margin: 0; font-size: var(--text-small); color: var(--muted); font-variant-numeric: tabular-nums; }
.ers-calls {
  margin: 0; padding: 0; list-style: none; display: grid; gap: 4px;
  grid-template-columns: repeat(auto-fill, minmax(3.75rem, 1fr));
}
.ers-call {
  display: flex; justify-content: space-between; gap: 2px; padding: 3px 6px;
  font-family: var(--font-mono); font-size: 0.75rem; color: var(--muted);
  background: transparent; border: 1px dashed var(--line); border-radius: var(--radius);
}
.ers-call.is-done { color: var(--ink); background: color-mix(in srgb, var(--good) 14%, var(--surface)); border: 1px solid var(--good); }
.ers-call.is-done.is-wrong { background: color-mix(in srgb, var(--bad) 14%, var(--surface)); border-color: var(--bad); }
.ers-call.is-new { outline: 2px solid var(--accent); outline-offset: 1px; }
.ers-reps {
  display: grid; gap: 8px; padding: 12px 14px; min-width: 0;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
}
.ers-reps ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 6px; }
.ers-reps li { display: grid; grid-template-columns: 5.5rem minmax(0, 1fr) 4rem; align-items: center; gap: 10px; }
.ers-rep-name, .ers-rep-score { font-family: var(--font-mono); font-size: 0.8125rem; color: var(--ink); font-variant-numeric: tabular-nums; }
.ers-rep-score { text-align: right; font-weight: 600; }
.ers-bar { height: 10px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }
.ers-bar span { display: block; height: 100%; background: var(--role-model); transition: width 400ms ease; }
.ers-band { margin: 0; font-size: var(--text-small); color: var(--ink); }
@media (prefers-reduced-motion: reduce) { .ers-bar span { transition: none; } }
`;
