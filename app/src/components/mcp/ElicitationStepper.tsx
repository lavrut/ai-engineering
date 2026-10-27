import { useState } from 'preact/hooks';
import { type ElicitationStepperProps } from '../../lib/mcpAids';
import { BASE_CSS } from './mcpStyle';
import { useStepper } from './useStepper';

export default function ElicitationStepper({ tool, arguments: args, note, paths }: ElicitationStepperProps) {
  const [pathId, setPathId] = useState(paths[0].id);
  const path = paths.find((p) => p.id === pathId) ?? paths[0];
  const s = useStepper(path.steps.length);
  const step = path.steps[s.at];

  return (
    <section class="mc" aria-label="Confirmation step by step">
      <header class="mc-head">
        <p class="mc-eyebrow">
          {tool}({args.id}, {args.label}) · lumen-ops
        </p>
        <p class="mc-label">Real messages from the running server: no calls from this page</p>
      </header>

      <fieldset class="es-choice">
        <legend>Your answer to the question</legend>
        {paths.map((p) => (
          <label key={p.id}>
            <input
              type="radio"
              name="es-path"
              value={p.id}
              checked={pathId === p.id}
              onChange={() => {
                setPathId(p.id);
                s.reset();
              }}
            />{' '}
            {p.label}
          </label>
        ))}
      </fieldset>

      <div class="mc-focus es-group" role="group" aria-label="Steps" tabIndex={0} onKeyDown={s.onKeyDown}>
        <div class="mc-buttons">
          <button ref={s.prevRef} type="button" class="mc-btn" disabled={s.at <= 0} onClick={s.prev}>
            Previous
          </button>
          <button ref={s.nextRef} type="button" class="mc-btn mc-btn-primary" disabled={s.at >= s.last} onClick={s.next}>
            Next
          </button>
          <button ref={s.resetRef} type="button" class="mc-btn" onClick={s.reset}>
            Reset
          </button>
        </div>
        <ol class="mc-steps" aria-label="Steps in order">
          {path.steps.map((x, i) => (
            <li key={x.n} aria-current={i === s.at ? 'step' : undefined}>
              {i === s.at ? '▸ ' : ''}
              {x.n}. {x.from}: {x.title}
            </li>
          ))}
        </ol>
      </div>

      <h3 class="mc-h">
        {step.n}. {step.title} <span class="es-from">({step.from} sends)</span>
      </h3>
      <p class="mc-live" aria-live="polite">
        Step {step.n} of {path.steps.length}: {step.title}. {step.summary}
      </p>
      <div class="mc-scroll mc-focus" role="region" aria-label="Message" tabIndex={0}>
        <pre class="mc-pre">
          <code>{step.json}</code>
        </pre>
      </div>
      <p class="mc-hint">{note[0].toUpperCase() + note.slice(1)}.</p>

      <div class="mc-scroll mc-focus" role="region" aria-label="Comparison table" tabIndex={0}>
        <table class="mc-table" aria-label="Accept and decline compared">
          <thead>
            <tr>
              <th scope="col">Path</th>
              <th scope="col">Answer</th>
              <th scope="col">What the tool returned</th>
              <th scope="col" class="mc-num">Rows written</th>
            </tr>
          </thead>
          <tbody>
            {paths.map((p) => (
              <tr key={p.id}>
                <th scope="row">{p.label}</th>
                <td>{p.action}</td>
                <td class="es-out">{p.outcome}</td>
                <td class="mc-num">{p.rowsWritten}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <style>{BASE_CSS + css}</style>
    </section>
  );
}

const css = `
.es-choice { margin: 0; display: flex; flex-wrap: wrap; gap: 8px 20px; padding: 8px 12px; border: 1px solid var(--line); border-radius: var(--radius); }
.es-choice legend { padding: 0 6px; font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); }
.es-choice label { font-size: var(--text-small); color: var(--ink); cursor: pointer; min-height: 24px; }
.es-choice input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.es-group { display: grid; gap: 8px; }
.es-from { font-family: var(--font-mono); font-size: var(--text-label); font-weight: 400; color: var(--muted); }
.es-out { font-family: var(--font-mono); overflow-wrap: anywhere; }
`;
