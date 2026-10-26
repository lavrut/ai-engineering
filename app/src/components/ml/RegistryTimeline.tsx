import { useState } from 'preact/hooks';
import {
  simPromote,
  simRollback,
  simStart,
  type GateView,
  type RegistryTimelineProps,
  type SimEvent,
  type SimState,
} from '../../lib/mlAids';
import type { RegistryEvent, Stage } from '../../lib/mlData';

/** A shape per action, so the kind of event never rests on colour. */
const MARK: Record<string, string> = { register: '●', promote: '▲', archive: '■', rollback: '↺', gate: '◆' };
const n3 = (x: number) => x.toFixed(3);
const signed = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(3)}`;

function GateLine({ gate }: { gate: GateView }) {
  return (
    <span class="rg-gate">
      macro-F1 {n3(gate.candidate_macro_f1)} vs {n3(gate.production_macro_f1)} ({gate.version} vs {gate.production_version}, both on{' '}
      {gate.split}, n = {gate.n}): gain {signed(gate.gain)} {gate.gain > gate.band ? '>' : '≤'} band {n3(gate.band)}; per-category
      floor {gate.floor.toFixed(2)}.
    </span>
  );
}

function Event({ h, gate, simulated }: { h: RegistryEvent; gate: GateView | null; simulated?: boolean }) {
  const isGate = h.action === 'gate';
  const failed = isGate && h.note.startsWith('failed');
  const move = h.from_stage === h.to_stage ? `stays in ${h.to_stage}` : `${h.from_stage ?? 'new'} → ${h.to_stage}`;
  return (
    <li class={`rg-event rg-${h.action}${simulated ? ' is-sim' : ''}`}>
      <span class="rg-mark" aria-hidden="true">
        {MARK[h.action] ?? '•'}
      </span>
      <div class="rg-body">
        <p class="rg-line">
          {simulated ? <span class="rg-simtag">simulated</span> : <span class="rg-time">{h.time.slice(0, 10)}</span>}{' '}
          <strong>
            {h.action} {h.version}
          </strong>
          {isGate ? (
            <>
              : <span class={failed ? 'rg-failed' : 'rg-passed'}>{failed ? '✗ FAILED' : '✓ passed'}</span>, {move}
            </>
          ) : (
            <>: {move}</>
          )}
        </p>
        {isGate && gate && gate.version === h.version ? <GateLine gate={gate} /> : h.note && <span class="rg-note">{h.note}</span>}
      </div>
    </li>
  );
}

function StageTable({ label, stages, caption }: { label: string; stages: Record<string, Stage>; caption?: string }) {
  return (
    <table class="rg-table" aria-label={label}>
      {caption && <caption class="rg-caption">{caption}</caption>}
      <thead>
        <tr>
          <th scope="col">Version</th>
          <th scope="col">Stage</th>
        </tr>
      </thead>
      <tbody>
        {Object.keys(stages).map((v) => (
          <tr key={v}>
            <th scope="row">{v}</th>
            <td>
              <span class={`rg-stage rg-stage-${stages[v]}`}>{stages[v]}</span>
              {stages[v] === 'production' && <span class="rg-serving"> · the servers load this one</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function RegistryTimeline(p: RegistryTimelineProps) {
  const { versions, history, gate } = p;
  const [sim, setSim] = useState<SimState>(() => simStart(p));
  const [message, setMessage] = useState('Nothing simulated yet: the stages below are a copy of the real ones.');
  const candidate = gate?.version ?? versions.find((v) => v.stage === 'staging')?.version ?? versions[versions.length - 1].version;
  const realStages = Object.fromEntries(versions.map((v) => [v.version, v.stage]));

  const promote = () => {
    const r = simPromote(sim, candidate);
    if (r.error) return setMessage(`Promote refused: ${r.error}.`);
    const archived = r.state.history.find((h, i) => i >= sim.history.length && h.action === 'archive');
    setSim(r.state);
    setMessage(`${candidate} is now in production${archived ? `; ${archived.version} was archived` : ''}. Simulated: registry.json is unchanged.`);
  };
  const rollback = () => {
    const r = simRollback(sim);
    if (r.error) return setMessage(`Roll back refused: ${r.error}. Rollback needs an earlier production version to return to.`);
    const current = Object.keys(sim.stages).find((v) => sim.stages[v] === 'production');
    setSim(r.state);
    setMessage(`${r.restored} is back in production; ${current} was archived. Simulated: registry.json is unchanged.`);
  };
  const reset = () => {
    setSim(simStart(p));
    setMessage('Simulation reset to the real stages and history.');
  };

  return (
    <section class="rg" aria-label="Registry timeline">
      <header class="rg-head">
        <p class="rg-eyebrow">Model registry · ml/registry.json</p>
        <p class="rg-label">Append-only history</p>
      </header>

      <div class="rg-real">
        <div class="rg-scroll" role="region" aria-label="Current stages" tabIndex={0}>
          <table class="rg-table" aria-label="Current stage per version">
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">Stage</th>
                <th scope="col">Trained on</th>
                <th scope="col">Scored on</th>
                <th scope="col">Macro F1</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.version}>
                  <th scope="row">{v.version}</th>
                  <td>
                    <span class={`rg-stage rg-stage-${v.stage}`}>{v.stage}</span>
                  </td>
                  <td>
                    {v.trained_on.from.slice(0, 4)}–{v.trained_on.to.slice(0, 4)} ({v.model})
                  </td>
                  <td>
                    {v.metrics.split}, n = {v.metrics.n}
                  </td>
                  <td class="rg-num">{n3(v.metrics.macro_f1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p class="rg-eyebrow">Real history · as committed</p>
        <ol class="rg-timeline" aria-label="Real history (registry.json)">
          {history.map((h, i) => (
            <Event key={i} h={h} gate={gate} />
          ))}
        </ol>
        <p class="rg-hint">
          {gate && !gate.passed
            ? `The gate asks a candidate to beat production by more than the noise band and keep every category above the floor. ${gate.version} did not, so it stays in staging and ${gate.production_version} keeps serving. `
            : ''}
          Marks: ● register, ▲ promote, ◆ gate, ■ archive, ↺ rollback.
        </p>
      </div>

      <div class="rg-sim" role="region" aria-label="Simulation: promote anyway and roll back">
        <p class="rg-banner">
          <strong>SIMULATION</strong> · not real history. Computed in this page from a copy; nothing is written. The same steps
          for real, on a copy of ml/: <code>python mlops.py --root /tmp/ml-copy promote {candidate} production</code>, then{' '}
          <code>rollback</code>.
        </p>
        <div class="rg-buttons">
          <button type="button" class="rg-btn" onClick={promote}>
            Promote {candidate} anyway
          </button>
          <button type="button" class="rg-btn" onClick={rollback}>
            Roll back
          </button>
          <button type="button" class="rg-btn rg-btn-quiet" onClick={reset}>
            Reset simulation
          </button>
        </div>
        <p class="rg-status" role="status">
          {message}
        </p>
        <div class="rg-scroll" role="region" aria-label="Simulated stages" tabIndex={0}>
          <StageTable label="Simulated stage per version" stages={sim.stages} caption="SIMULATION: stages after these steps" />
        </div>
        <ol class="rg-timeline" aria-label="Simulated history (this page only)">
          {sim.history.map((h: SimEvent, i) => (
            <Event key={i} h={h} gate={gate} simulated={h.simulated} />
          ))}
        </ol>
        {JSON.stringify(sim.stages) !== JSON.stringify(realStages) && (
          <p class="rg-hint">
            Real stages unchanged: {Object.keys(realStages).map((v) => `${v} ${realStages[v]}`).join(', ')}.
          </p>
        )}
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.rg { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; min-width: 0; }
.rg-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.rg-eyebrow, .rg-caption {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.rg-caption { caption-side: top; text-align: left; padding: 0 0 6px; }
.rg-label {
  margin: 0; padding: 3px 8px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--muted); border: 1px dashed var(--line); border-radius: var(--radius);
}
.rg-real, .rg-sim { display: grid; gap: 12px; min-width: 0; }
.rg-scroll { min-width: 0; max-width: 100%; overflow-x: auto; }
.rg-scroll:focus-visible, .rg-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.rg-table { width: 100%; border-collapse: collapse; font-size: var(--text-small); background: var(--surface); border: 1px solid var(--line); }
.rg-table th, .rg-table td { padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--line); }
.rg-table thead th { font-family: var(--font-mono); font-size: var(--text-label); font-weight: 500; color: var(--muted); white-space: nowrap; }
.rg-table th[scope="row"] { font-family: var(--font-mono); font-weight: 600; color: var(--ink); }
.rg-table td { color: var(--ink); }
.rg-num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.rg-stage { display: inline-block; padding: 1px 8px; font-family: var(--font-mono); font-size: var(--text-label); border-radius: 999px; border: 1px solid var(--line); }
.rg-stage-production { font-weight: 700; border: 2px solid var(--good); }
.rg-stage-staging { border-style: dashed; }
.rg-stage-archived { color: var(--muted); text-decoration: line-through; }
.rg-serving { font-size: var(--text-label); color: var(--muted); }
.rg-timeline { margin: 0; padding: 0; list-style: none; display: grid; gap: 0; }
.rg-event { display: grid; grid-template-columns: 1.75rem minmax(0, 1fr); gap: 8px; position: relative; padding-bottom: 12px; }
.rg-event::before { content: ""; position: absolute; left: calc(0.875rem - 1px); top: 1.5rem; bottom: 0; width: 2px; background: var(--line); }
.rg-event:last-child::before { display: none; }
.rg-mark {
  width: 1.75rem; height: 1.75rem; display: grid; place-items: center; font-size: 0.8125rem; color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: 50%;
}
.rg-event.is-sim .rg-mark { border-style: dashed; border-color: var(--warn); }
.rg-body { min-width: 0; display: grid; gap: 2px; padding-top: 2px; }
.rg-line { margin: 0; font-size: var(--text-small); color: var(--ink); }
.rg-time { font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); }
.rg-simtag {
  display: inline-block; padding: 0 6px; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  color: var(--ink); border: 1px dashed var(--warn); border-radius: var(--radius);
}
.rg-failed { font-weight: 700; color: var(--bad); }
.rg-passed { font-weight: 700; color: var(--good); }
.rg-note, .rg-gate { font-size: var(--text-label); color: var(--muted); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.rg-hint, .rg-status { margin: 0; font-size: var(--text-small); color: var(--muted); }
.rg-status { color: var(--ink); font-weight: 600; }
.rg-sim {
  padding: 14px; border: 2px dashed var(--warn); border-radius: var(--radius);
  background: repeating-linear-gradient(135deg, var(--surface-2) 0 10px, var(--bg) 10px 20px);
}
.rg-banner {
  margin: 0; padding: 8px 12px; font-size: var(--text-small); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--warn); border-radius: var(--radius);
}
.rg-banner code { font-family: var(--font-mono); font-size: var(--text-label); overflow-wrap: anywhere; }
.rg-buttons { display: flex; flex-wrap: wrap; gap: 8px; }
.rg-btn {
  font: inherit; font-size: var(--text-small); font-weight: 500; padding: 6px 14px; color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); cursor: pointer;
}
.rg-btn-quiet { color: var(--muted); }
`;
