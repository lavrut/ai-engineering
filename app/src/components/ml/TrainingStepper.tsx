import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { lossChart, recordedTopWords, spreadLabels, type TrainingStepperProps } from '../../lib/mlAids';

const TICK_MS = 700;
const BOX = { width: 560, height: 220, left: 40, right: 140, top: 12, bottom: 44 };
const LINE_LABEL = { objective: 'loss + penalty', loss: 'loss', penalty: 'penalty' } as const;
const f4 = (x: number) => x.toFixed(4);

export default function TrainingStepper({ classes, C, convergedAt, note, checkpoints }: TrainingStepperProps) {
  const [at, setAt] = useState(0);
  const [playing, setPlaying] = useState(false);
  const last = checkpoints.length - 1;
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

  const chart = useMemo(() => lossChart(checkpoints, BOX), [checkpoints]);
  const wordMax = useMemo(
    () => Math.max(...checkpoints.flatMap((c) => (c.top ?? []).flat().map((w) => Math.abs(w[1] as number)))),
    [checkpoints],
  );
  const cp = checkpoints[at];
  const words = recordedTopWords(checkpoints, at);
  const status = at === 0 ? 'start: every weight is 0' : at >= last ? `converged at iteration ${convergedAt}` : 'training';
  const labelY = spreadLabels(
    chart.lines.map((l) => l.points[at].y + 4),
    17,
  );
  const path = (pts: { x: number; y: number }[]) => pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');

  return (
    <section class="ts" aria-label="Training stepper">
      <header class="ts-head">
        <p class="ts-eyebrow">Training · logistic regression, C = {C}</p>
        <p class="ts-label">Replays the recorded training log</p>
      </header>

      <div class="ts-buttons">
        <button
          ref={stepRef}
          type="button"
          class="ts-btn ts-btn-primary"
          disabled={at >= last}
          onClick={() => {
            const next = Math.min(at + 1, last);
            setAt(next);
            if (next >= last) keepFocus(stepRef.current);
          }}
        >
          Step
        </button>
        <button ref={playRef} type="button" class="ts-btn" disabled={!playing && at >= last} onClick={() => setPlaying((p) => !p)}>
          {playing ? 'Pause' : 'Play'}
        </button>
        <button
          ref={resetRef}
          type="button"
          class="ts-btn"
          onClick={() => {
            setPlaying(false);
            setAt(0);
          }}
        >
          Reset
        </button>
      </div>

      <dl class="ts-state" role="group" aria-label="Training state">
        <div>
          <dt>Iteration</dt>
          <dd>{cp.iteration}</dd>
        </div>
        <div class="ts-main">
          <dt>loss + penalty</dt>
          <dd>{f4(cp.objective)}</dd>
        </div>
        <div>
          <dt>loss</dt>
          <dd>{f4(cp.loss)}</dd>
        </div>
        <div>
          <dt>penalty</dt>
          <dd>{f4(cp.penalty)}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{status}</dd>
        </div>
      </dl>
      <p class="ts-live" aria-live="polite">
        Iteration {cp.iteration}: loss + penalty {f4(cp.objective)}
        {at > 0 ? `, down from ${f4(checkpoints[at - 1].objective)} at iteration ${checkpoints[at - 1].iteration}` : ''}.
      </p>

      <div class="ts-chart">
        <svg
          viewBox={`0 0 ${BOX.width} ${BOX.height}`}
          role="img"
          aria-label={`Line chart of loss + penalty, loss and penalty at iterations ${checkpoints
            .slice(0, at + 1)
            .map((c) => c.iteration)
            .join(', ')}`}
        >
          {chart.ticks.map((t) => (
            <g key={t.value}>
              <line class="ts-grid" x1={BOX.left} x2={BOX.width - BOX.right} y1={t.y} y2={t.y} />
              <text class="ts-tick" x={BOX.left - 6} y={t.y + 4} text-anchor="end">
                {t.value.toFixed(1)}
              </text>
            </g>
          ))}
          {checkpoints.map((c, i) => (
            <text key={c.iteration} class={`ts-tick${i === at ? ' is-now' : ''}`} x={chart.x(i)} y={BOX.height - BOX.bottom + 18} text-anchor="middle">
              {c.iteration}
            </text>
          ))}
          <text class="ts-tick" x={(BOX.left + BOX.width - BOX.right) / 2} y={BOX.height - 2} text-anchor="middle">
            iteration (recorded checkpoints)
          </text>
          {chart.lines.map((l, li) => {
            const pts = l.points.slice(0, at + 1);
            const end = pts[pts.length - 1];
            return (
              <g key={l.key} class={`ts-line ts-line-${l.key}`}>
                <path d={path(pts)} />
                {pts.map((p, i) => (
                  <circle key={i} cx={p.x} cy={p.y} r={l.key === 'objective' ? 3.5 : 2.5} />
                ))}
                <text class="ts-line-label" x={end.x + 7} y={labelY[li]}>
                  {LINE_LABEL[l.key]}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <p class="ts-note">{note}</p>

      <div class="ts-words">
        <p class="ts-eyebrow">
          {words ? `Top 8 words per class at iteration ${words.iteration}` : 'Top 8 words per class'}
        </p>
        {words ? (
          <>
            <p class="ts-hint">
              Recorded at iterations{' '}
              {checkpoints
                .filter((c) => c.top)
                .map((c) => c.iteration)
                .join(', ')}
              ; a bar is the word&apos;s weight for that class.
            </p>
            <div class="ts-classes">
              {classes.map((label, k) => (
                <div key={label} class="ts-class">
                  <p class="ts-class-name">{label}</p>
                  <ul aria-label={`${label}: top words`}>
                    {words.words[k].map(([w, v]) => (
                      <li key={w}>
                        <span class="ts-word">{w}</span>
                        <span class="ts-wbar" aria-hidden="true">
                          <span style={{ width: `${(Math.abs(v) / wordMax) * 100}%` }} />
                        </span>
                        <span class="ts-wval">{v.toFixed(4)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p class="ts-hint">No words yet: every weight starts at 0, so every class is equally likely (loss = ln 6).</p>
        )}
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.ts { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.ts-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.ts-eyebrow, .ts-state dt {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.ts-state dt { text-transform: none; letter-spacing: 0; }
.ts-label {
  margin: 0; padding: 3px 8px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--muted); border: 1px dashed var(--line); border-radius: var(--radius);
}
.ts-buttons { display: flex; flex-wrap: wrap; gap: 8px; }
.ts-btn {
  font: inherit; font-size: var(--text-small); font-weight: 500; padding: 6px 14px; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); cursor: pointer;
}
.ts-btn-primary { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.ts-btn:disabled { color: var(--muted); background: transparent; border-color: var(--line); cursor: default; }
.ts-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ts-state {
  margin: 0; display: flex; flex-wrap: wrap; gap: 10px 24px; padding: 10px 14px;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius);
}
.ts-state dd {
  margin: 2px 0 0; font-family: var(--font-mono); font-size: var(--text-small); font-weight: 600;
  font-variant-numeric: tabular-nums; color: var(--ink);
}
.ts-main dd { font-size: var(--text-body); }
.ts-live, .ts-note, .ts-hint { margin: 0; font-size: var(--text-small); color: var(--muted); font-variant-numeric: tabular-nums; }
.ts-live { color: var(--ink); }
.ts-chart { min-width: 0; padding: 8px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.ts-chart svg { display: block; width: 100%; height: auto; }
.ts-grid { stroke: var(--line); stroke-width: 1; }
.ts-tick { font-family: var(--font-mono); font-size: 11px; fill: var(--muted); }
.ts-tick.is-now { fill: var(--ink); font-weight: 700; }
.ts-line path { fill: none; stroke-width: 2; }
.ts-line-objective path { stroke: var(--accent); stroke-width: 3; }
.ts-line-objective circle { fill: var(--accent); }
.ts-line-loss path { stroke: var(--role-model); stroke-dasharray: 6 4; }
.ts-line-loss circle { fill: var(--role-model); }
.ts-line-penalty path { stroke: var(--role-tool); stroke-dasharray: 2 3; }
.ts-line-penalty circle { fill: var(--role-tool); }
.ts-line-label { font-family: var(--font-mono); font-size: 11px; fill: var(--ink); }
@media (max-width: 599px) { .ts-tick, .ts-line-label { font-size: 14px; } }
.ts-words {
  display: grid; gap: 8px; padding: 12px 14px; min-width: 0;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius);
}
.ts-classes { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(min(100%, 15rem), 1fr)); }
.ts-class { padding: 8px 10px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.ts-class-name { margin: 0 0 6px; font-size: var(--text-small); font-weight: 600; color: var(--ink); overflow-wrap: anywhere; }
.ts-class ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 3px; }
.ts-class li { display: grid; grid-template-columns: 6.5rem minmax(0, 1fr) 3.75rem; align-items: center; gap: 6px; }
.ts-word, .ts-wval { font-family: var(--font-mono); font-size: 0.75rem; color: var(--ink); overflow-wrap: anywhere; }
.ts-wval { text-align: right; font-variant-numeric: tabular-nums; }
.ts-wbar { height: 6px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }
.ts-wbar span { display: block; height: 100%; background: var(--role-model); transition: width 400ms ease; }
@media (prefers-reduced-motion: reduce) { .ts-wbar span { transition: none; } }
`;
