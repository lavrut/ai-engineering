import { useState } from 'preact/hooks';
import {
  BUDGET,
  SUMMARY_TOKENS,
  SYSTEM_TOKENS,
  TOOL_SCHEMA_TOKENS,
  TURN_SIZES,
  WINDOW,
  addTurn,
  initialMeter,
  total,
  type MeterState,
  type TurnKind,
} from '../lib/compaction';

const SCALE = BUDGET * 1.2;
const AUTO_CYCLE: TurnKind[] = ['short', 'file', 'tools'];
const fmt = (n: number) => n.toLocaleString('en-US');
const pct = (n: number) => `${(n / SCALE) * 100}%`;

const BUTTONS: { kind: TurnKind; label: string }[] = [
  { kind: 'short', label: 'Add short reply' },
  { kind: 'file', label: 'Add large file read' },
  { kind: 'tools', label: 'Add several tool calls' },
];

export default function ContextMeter() {
  const [state, setState] = useState<MeterState>(initialMeter);

  function add(kinds: TurnKind[]) {
    setState((s) => kinds.reduce((acc, k) => addTurn(acc, k).state, s));
  }

  const history = state.history.reduce((a, b) => a + b, 0);
  const segments = [
    { key: 'system', label: 'System prompt', tokens: SYSTEM_TOKENS },
    { key: 'tools', label: 'Tool schemas', tokens: TOOL_SCHEMA_TOKENS },
    { key: 'summary', label: 'Summary', tokens: state.summary ?? 0 },
    { key: 'history', label: 'History', tokens: history },
  ];
  const used = total(state);

  return (
    <section class="cm" aria-label="Context window meter">
      <header class="cm-head">
        <p class="cm-eyebrow">Context window meter</p>
        <p class="cm-label">Illustrative token estimates</p>
      </header>

      <div class="cm-buttons">
        {BUTTONS.map((b) => (
          <button key={b.kind} type="button" class="cm-btn" onClick={() => add([b.kind])}>
            {b.label}
          </button>
        ))}
        <button
          type="button"
          class="cm-btn cm-btn-primary"
          onClick={() => add(Array.from({ length: 20 }, (_, i) => AUTO_CYCLE[i % AUTO_CYCLE.length]))}
        >
          Auto-run 20 turns
        </button>
        <button type="button" class="cm-btn" onClick={() => setState(initialMeter())}>
          Reset
        </button>
      </div>

      <div class="cm-barwrap">
        <div class="cm-bar" role="img" aria-label={`${fmt(used)} of ${fmt(BUDGET)} budget tokens used`}>
          {segments.map((s) => (
            <span key={s.key} class={`cm-seg cm-seg-${s.key}`} style={{ width: pct(s.tokens) }} />
          ))}
          <span class="cm-budget-line" style={{ left: pct(BUDGET) }} />
        </div>
        <p class="cm-budget-label" style={{ left: pct(BUDGET) }}>
          Budget {fmt(BUDGET)}
        </p>
      </div>
      <p class="cm-note">
        The bar runs to {fmt(Math.round(SCALE))} tokens so the budget line is visible. The model&apos;s full window is{' '}
        {fmt(WINDOW)} tokens, far off the right edge. The harness compacts at the budget, well before the window fills.
      </p>

      <ul class="cm-legend">
        {segments.map((s) => (
          <li key={s.key}>
            <span class={`cm-swatch cm-seg-${s.key}`} aria-hidden="true" />
            <span class="cm-legend-name">{s.label}</span>
            <span class="cm-num">{fmt(s.tokens)}</span>
          </li>
        ))}
      </ul>

      <p class="cm-stats">
        <span class="cm-num">Total: {fmt(used)}</span>
        <span>Compactions: {state.compactions}</span>
        <span class="cm-turns">Turns since last compaction: {state.history.length}</span>
      </p>
      <p class="cm-sizes">
        Turn sizes: short reply {fmt(TURN_SIZES.short)}, large file read {fmt(TURN_SIZES.file)}, several tool calls{' '}
        {fmt(TURN_SIZES.tools)}.
      </p>

      {state.compactions > 0 && (
        <div class="cm-explain" role="status">
          <p class="cm-explain-title">Compacted: history folded into a summary</p>
          <p>
            The total went over the {fmt(BUDGET)} budget. The harness asked the model for a summary (about{' '}
            {fmt(SUMMARY_TOKENS)} tokens) and started a fresh conversation from it.
          </p>
          <p>
            It never edits or deletes earlier turns. Current Claude models reject a history that was changed after the
            fact, so the conversation must be append-only: the only way to shrink it is to start a new one.
          </p>
        </div>
      )}
      <style>{css}</style>
    </section>
  );
}

const css = `
.cm { margin: 32px 0; display: grid; gap: 12px; min-width: 0; }
.cm-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.cm-eyebrow, .cm-explain-title {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.cm-label {
  margin: 0; padding: 3px 8px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--muted); border: 1px dashed var(--line); border-radius: var(--radius);
}
.cm-buttons { display: flex; flex-wrap: wrap; gap: 8px; }
.cm-btn {
  font: inherit; font-size: var(--text-small); font-weight: 500; padding: 6px 14px; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); cursor: pointer;
}
.cm-btn-primary { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.cm-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.cm-barwrap { position: relative; padding-bottom: 22px; }
.cm-bar {
  position: relative; display: flex; height: 36px; overflow: hidden;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
}
.cm-seg { display: block; height: 100%; flex: none; transition: width 600ms ease; }
.cm-seg-system { background: var(--role-harness); }
.cm-seg-tools { background: var(--role-tool); }
.cm-seg-summary { background: var(--role-memory); }
.cm-seg-history { background: var(--role-model); }
.cm-budget-line { position: absolute; top: -2px; bottom: -2px; width: 0; border-left: 2px dashed var(--ink); }
.cm-budget-label {
  position: absolute; bottom: 0; margin: 0; transform: translateX(-100%); padding-right: 4px;
  font-family: var(--font-mono); font-size: var(--text-label); color: var(--ink); white-space: nowrap;
}
.cm-note, .cm-sizes { margin: 0; font-size: var(--text-small); color: var(--muted); }
.cm-legend { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 6px 24px; }
.cm-legend li { display: flex; align-items: center; gap: 8px; font-size: var(--text-small); }
.cm-swatch { width: 12px; height: 12px; border-radius: 2px; flex: none; }
.cm-num, .cm-stats { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.cm-legend .cm-num { font-weight: 600; }
.cm-stats { margin: 0; display: flex; flex-wrap: wrap; gap: 4px 24px; font-size: var(--text-small); color: var(--ink); }
.cm-explain {
  padding: 12px 16px; background: var(--surface); border: 1px solid var(--line);
  border-left: 4px solid var(--role-memory); border-radius: var(--radius);
}
.cm-explain p { margin: 0 0 6px; font-size: var(--text-small); color: var(--ink); }
.cm-explain p:last-child { margin-bottom: 0; }
.cm-explain-title { color: var(--ink); font-weight: 600; }
@media (prefers-reduced-motion: reduce) { .cm-seg { transition: none; } }
`;
