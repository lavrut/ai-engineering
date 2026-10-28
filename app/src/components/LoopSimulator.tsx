import { useEffect, useId, useRef, useState } from 'preact/hooks';
import { scenarios } from '../data/scenarios';
import { announce, callLabel, createSimulator, type ApiMessage, type Policy, type SimState, type ToolResult, type ToolUse } from '../lib/simulator';
import { css } from './loopSimulatorCss';

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
