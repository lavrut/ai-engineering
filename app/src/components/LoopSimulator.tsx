import { useEffect, useId, useRef, useState } from 'preact/hooks';
import { scenarios } from '../data/scenarios';
import { announce, callLabel, createSimulator, type ApiMessage, type Policy, type SimState, type ToolResult, type ToolUse } from '../lib/simulator';

type ScenarioId = keyof typeof scenarios;
type Props = {
  scenario: ScenarioId;
  /** Show the tool policy switch (ask before write tools / allow everything). */
  showPolicy?: boolean;
};

const SIM_LABEL = 'Simulated — no real model is called';
const POLICIES: { value: Policy; label: string }[] = [
  { value: 'ask-writes', label: 'Ask before write tools' },
  { value: 'allow-all', label: 'Allow everything' },
];
const SCENARIO_IDS = Object.keys(scenarios) as ScenarioId[];

export default function LoopSimulator({ scenario: initialScenario, showPolicy = false }: Props) {
  const [scenarioId, setScenarioId] = useState<ScenarioId>(initialScenario);
  const [policy, setPolicy] = useState<Policy>('ask-writes');
  const [firstSim] = useState(() => createSimulator(scenarios[initialScenario], 'ask-writes'));
  const sim = useRef(firstSim);
  const [state, setState] = useState<SimState>(() => sim.current.state());
  const id = useId();

  const convRef = useRef<HTMLDivElement>(null);
  const jsonRef = useRef<HTMLPreElement>(null);
  const stepRef = useRef<HTMLButtonElement>(null);
  const approveRef = useRef<HTMLButtonElement>(null);
  const hadPending = useRef(false);

  // Keep the newest entry in view inside each panel's own scroll box (never the page).
  useEffect(() => {
    const conv = convRef.current;
    if (conv) conv.scrollTop = conv.scrollHeight;
    const pre = jsonRef.current;
    const newest = pre?.querySelector<HTMLElement>('.is-new');
    if (pre && newest) pre.scrollTop = Math.max(0, newest.offsetTop - 8);
  }, [state]);

  // Focus follows the approval card in and back out.
  useEffect(() => {
    if (state.pending && !hadPending.current) approveRef.current?.focus();
    else if (!state.pending && hadPending.current && (!document.activeElement || document.activeElement === document.body)) {
      stepRef.current?.focus();
    }
    hadPending.current = state.pending !== null;
  }, [state.pending]);

  function switchScenario(next: ScenarioId) {
    setScenarioId(next);
    sim.current = createSimulator(scenarios[next], policy);
    setState(sim.current.state());
  }

  function switchPolicy(next: Policy) {
    setPolicy(next);
    setState(sim.current.reset(next));
  }

  const { messages, pending, done, stopReason, turn, tokens } = state;
  const current = scenarios[scenarioId];
  const blocked = done || pending !== null;
  const status = pending ? 'awaiting approval' : done ? 'done' : messages.length === 1 ? 'ready' : 'running';

  return (
    <section class="loopsim" aria-label={`Loop simulator: ${current.title}`}>
      <header class="ls-head">
        <p class="ls-eyebrow">Loop simulator</p>
        <label class="ls-picker">
          <span class="ls-picker-label">Scenario</span>
          <select value={scenarioId} onChange={(e) => switchScenario((e.target as HTMLSelectElement).value as ScenarioId)}>
            {SCENARIO_IDS.map((s) => (
              <option key={s} value={s}>
                {scenarios[s].title}
              </option>
            ))}
          </select>
        </label>
      </header>

      <div class="ls-controls">
        <div class="ls-buttons">
          <button ref={stepRef} type="button" class="ls-btn ls-btn-primary" disabled={blocked} onClick={() => setState(sim.current.step())}>
            Step
          </button>
          <button type="button" class="ls-btn" disabled={blocked} onClick={() => setState(sim.current.runToEnd())}>
            Run to end
          </button>
          <button type="button" class="ls-btn" onClick={() => setState(sim.current.reset())}>
            Reset
          </button>
        </div>
        <p class="ls-simlabel">{SIM_LABEL}</p>
        {showPolicy && (
          <fieldset class="ls-policy">
            <legend>Tool policy</legend>
            {POLICIES.map((p) => (
              <label key={p.value}>
                <input
                  type="radio"
                  name={`${id}-policy`}
                  value={p.value}
                  checked={policy === p.value}
                  onChange={() => switchPolicy(p.value)}
                />
                <span>{p.label}</span>
              </label>
            ))}
            <span class="ls-policy-note">Changing it restarts the run.</span>
          </fieldset>
        )}
      </div>

      {pending && (
        <div class="ls-approval" role="group" aria-labelledby={`${id}-approval`}>
          <p class="ls-approval-title" id={`${id}-approval`}>
            Approval needed
          </p>
          <p class="ls-approval-text">
            The model wants to run <code>{pending.name}</code>, a write tool. The harness holds the call until you decide.
          </p>
          <dl class="ls-args">
            {Object.entries(pending.input).map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>
                  <pre>{v}</pre>
                </dd>
              </div>
            ))}
          </dl>
          <div class="ls-buttons">
            <button ref={approveRef} type="button" class="ls-btn ls-btn-primary" onClick={() => setState(sim.current.approve())}>
              Approve
            </button>
            <button type="button" class="ls-btn" onClick={() => setState(sim.current.deny())}>
              Deny
            </button>
          </div>
        </div>
      )}

      <div class="ls-grid">
        <dl class="ls-state" aria-label="Loop state">
          <div>
            <dt>Turn</dt>
            <dd>{turn}</dd>
          </div>
          <div>
            <dt>stop_reason</dt>
            <dd>{stopReason ?? '—'}</dd>
          </div>
          <div>
            <dt>≈ tokens</dt>
            <dd>{tokens.toLocaleString('en-US')}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{status}</dd>
          </div>
        </dl>
        <p class="visually-hidden" aria-live="polite">
          {announce(state)}
        </p>

        <div class="ls-panel">
          <p class="ls-panel-title">Conversation</p>
          <div class="ls-conversation" ref={convRef} tabIndex={0} role="region" aria-label="Conversation">
            {messages.map((m, i) => (
              <Message key={i} message={m} index={i} previous={messages[i - 1]} pendingId={pending?.id ?? null} />
            ))}
          </div>
        </div>

        <div class="ls-panel ls-json">
          <p class="ls-panel-title">
            <code>messages</code> as sent to the API
          </p>
          <pre ref={jsonRef} tabIndex={0} role="region" aria-label="messages JSON">
            {'['}
            {messages.map((m, i) => (
              <span key={i} class={`ls-json-entry${i === messages.length - 1 ? ' is-new' : ''}`}>
                {jsonLines(m, i < messages.length - 1)}
              </span>
            ))}
            {']'}
          </pre>
        </div>
      </div>
      <style>{css}</style>
    </section>
  );
}

function Message({
  message,
  index,
  previous,
  pendingId,
}: {
  message: ApiMessage;
  index: number;
  previous: ApiMessage | undefined;
  pendingId: string | null;
}) {
  const at = <span class="ls-msg-index">{`messages[${index}]`}</span>;
  if (typeof message.content === 'string') {
    return (
      <div class="ls-msg is-user">
        <p class="ls-role">User {at}</p>
        <p class="ls-text">{message.content}</p>
      </div>
    );
  }
  if (message.role === 'user') {
    const calls = previous && typeof previous.content !== 'string' ? previous.content : [];
    const nameOf = (r: ToolResult) => calls.find((b): b is ToolUse => b.type === 'tool_use' && b.id === r.tool_use_id)?.name;
    return (
      <div class="ls-msg is-results">
        <p class="ls-role">User · tool results {at}</p>
        {message.content.map((b) =>
          b.type === 'tool_result' ? (
            <div key={b.tool_use_id} class={`ls-result${b.is_error ? ' is-error' : ''}`}>
              <p class="ls-result-head">
                <span>{`${nameOf(b) ?? 'tool'} → ${b.tool_use_id}`}</span>
                {b.is_error && <span class="ls-tag ls-tag-bad">error</span>}
              </p>
              <pre class="ls-result-body">{b.content}</pre>
            </div>
          ) : null,
        )}
      </div>
    );
  }
  return (
    <div class="ls-msg is-assistant">
      <p class="ls-role">Assistant {at}</p>
      {message.content.map((b, i) =>
        b.type === 'text' ? (
          <p key={i} class="ls-text">
            {b.text}
          </p>
        ) : b.type === 'tool_use' ? (
          <p key={i} class="ls-call-line">
            <code class="ls-call">{callLabel(b)}</code>
            {b.id === pendingId && <span class="ls-tag ls-tag-warn">awaiting approval</span>}
          </p>
        ) : null,
      )}
    </div>
  );
}

/** One block per JSON line, indented with padding so wrapped lines hang under their own start. */
function jsonLines(m: ApiMessage, comma: boolean) {
  const lines = JSON.stringify(m, null, 2).split('\n');
  return lines.map((line, i) => {
    const text = line.trimStart();
    return (
      <span key={i} class="ls-line" style={{ paddingLeft: `${line.length - text.length + 4}ch` }}>
        {text}
        {comma && i === lines.length - 1 ? ',' : ''}
      </span>
    );
  });
}

const css = `
.loopsim {
  margin: 32px 0;
  max-width: none;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}
@media (min-width: 1100px) {
  .loopsim { width: max(100%, min(var(--wide-w), calc(100vw - 400px))); }
}
.ls-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.ls-eyebrow, .ls-panel-title, .ls-role, .ls-approval-title, .ls-state dt, .ls-policy legend, .ls-picker-label {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.ls-picker { display: inline-flex; align-items: center; gap: 8px; min-width: 0; }
.ls-picker select {
  min-width: 0; max-width: 100%; font: inherit; font-size: var(--text-small); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 5px 8px;
}

.ls-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 20px; }
.ls-buttons { display: flex; flex-wrap: wrap; gap: 8px; }
.ls-btn {
  font: inherit; font-size: var(--text-small); font-weight: 500; padding: 6px 14px; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); cursor: pointer;
}
.ls-btn-primary { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.ls-btn:disabled { color: var(--muted); background: transparent; border-color: var(--line); cursor: default; }
.ls-btn:focus-visible, .ls-picker select:focus-visible, .ls-policy input:focus-visible,
.ls-conversation:focus-visible, .ls-json pre:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ls-simlabel {
  margin: 0 0 0 auto; padding: 3px 8px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--muted); border: 1px dashed var(--line); border-radius: var(--radius);
}
.ls-policy {
  flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 16px;
  margin: 0; padding: 0; border: 0; min-width: 0;
}
.ls-policy legend { float: left; padding: 0; }
@media (max-width: 599px) {
  .ls-policy legend { float: none; width: 100%; }
  .ls-policy-note { flex-basis: 100%; }
}
.ls-policy label { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-small); color: var(--ink); cursor: pointer; }
.ls-policy input { accent-color: var(--accent); margin: 0; }
.ls-policy-note { font-size: var(--text-small); color: var(--muted); }

.ls-approval {
  display: grid; gap: 10px; padding: 14px 16px; min-width: 0;
  background: var(--surface); border: 1px solid var(--warn); border-left-width: 4px; border-radius: var(--radius);
}
.ls-approval-title { color: var(--warn); font-weight: 600; }
.ls-approval-text { margin: 0; font-size: var(--text-small); color: var(--ink); }
.ls-approval-text code { font-family: var(--font-mono); font-size: 0.875em; }
.ls-args { margin: 0; display: grid; gap: 6px; }
.ls-args > div { display: grid; grid-template-columns: 4.5rem minmax(0, 1fr); gap: 8px; align-items: start; }
.ls-args dt { font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); padding-top: 5px; }
.ls-args dd { margin: 0; min-width: 0; }
.ls-args pre {
  margin: 0; padding: 4px 8px; max-height: 8rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere;
  font-family: var(--font-mono); font-size: 0.8125rem; line-height: 1.5; color: var(--ink);
  background: var(--surface-2); border-radius: var(--radius);
}

.ls-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
@media (min-width: 1100px) {
  .ls-grid { grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr) 10rem; align-items: start; }
  .ls-grid .ls-state { grid-column: 3; grid-row: 1; flex-direction: column; }
  .ls-grid .ls-panel { grid-row: 1; }
}
.ls-state {
  margin: 0; display: flex; flex-wrap: wrap; gap: 10px 24px; padding: 10px 14px;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius);
}
.ls-state dd {
  margin: 2px 0 0; font-family: var(--font-mono); font-size: var(--text-small); font-weight: 600;
  font-variant-numeric: tabular-nums; color: var(--ink);
}

.ls-panel { min-width: 0; display: grid; grid-template-rows: auto minmax(0, 1fr); border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
.ls-panel-title { padding: 8px 12px; border-bottom: 1px solid var(--line); background: var(--surface-2); border-radius: var(--radius) var(--radius) 0 0; }
.ls-panel-title code { font-family: var(--font-mono); text-transform: none; color: var(--ink); }
.ls-conversation, .ls-json pre { height: 26rem; overflow: auto; overscroll-behavior: contain; }
.ls-conversation { padding: 4px 14px 14px; }

.ls-msg { padding: 10px 0; border-bottom: 1px solid var(--line); }
.ls-msg:last-child { border-bottom: 0; }
.ls-role { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }
.ls-role::before { content: ''; flex: none; width: 8px; height: 8px; border-radius: 2px; align-self: center; background: var(--role-user); }
.is-assistant .ls-role::before { background: var(--role-model); }
.is-results .ls-role::before { background: var(--role-tool); }
.ls-msg-index { margin-left: auto; text-transform: none; letter-spacing: 0; font-size: 0.75rem; }
.ls-text { margin: 4px 0; font-size: var(--text-small); line-height: 1.55; color: var(--ink); }
.ls-call-line { margin: 6px 0; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
.ls-call {
  padding: 2px 10px; font-family: var(--font-mono); font-size: 0.8125rem; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: 999px; overflow-wrap: anywhere;
}
.ls-tag {
  padding: 0 7px; font-family: var(--font-mono); font-size: 0.75rem; letter-spacing: 0.04em;
  border: 1px solid currentColor; border-radius: 999px;
}
.ls-tag-warn { color: var(--warn); }
.ls-tag-bad { color: var(--bad); }
.ls-result { margin: 6px 0 6px 12px; padding-left: 10px; border-left: 2px solid var(--line); }
.ls-result.is-error { border-left-color: var(--bad); }
.ls-result-head {
  margin: 0 0 4px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px;
  font-family: var(--font-mono); font-size: 0.75rem; color: var(--muted);
}
.ls-result-body {
  margin: 0; padding: 6px 8px; max-height: 9rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere;
  font-family: var(--font-mono); font-size: 0.8125rem; line-height: 1.5; color: var(--ink);
  background: var(--surface-2); border-radius: var(--radius);
}
.ls-result.is-error .ls-result-body { color: var(--bad); }

.ls-json pre {
  position: relative; margin: 0; padding: 10px 12px; white-space: pre-wrap; overflow-wrap: anywhere;
  font-family: var(--font-mono); font-size: 0.75rem; line-height: 1.55; color: var(--ink); background: var(--surface);
  border-radius: 0 0 var(--radius) var(--radius);
}
.ls-line { display: block; text-indent: -2ch; }
.ls-json-entry { display: block; position: relative; border-left: 2px solid transparent; margin-left: -12px; padding-left: 10px; }
.ls-json-entry.is-new { border-left-color: var(--accent); background: var(--surface-2); }
.ls-json-entry.is-new::after {
  content: 'new'; position: absolute; top: 2px; right: 0; padding: 0 6px;
  font-size: 0.6875rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent);
}
`;
