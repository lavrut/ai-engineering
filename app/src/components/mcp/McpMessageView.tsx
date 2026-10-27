import { type McpMessageViewProps } from '../../lib/mcpAids';
import { BASE_CSS } from './mcpStyle';
import { useStepper } from './useStepper';

export default function McpMessageView({ server, protocolVersion, capturedWith, steps }: McpMessageViewProps) {
  const s = useStepper(steps.length);
  const step = steps[s.at];

  return (
    <section class="mc" aria-label="MCP messages">
      <header class="mc-head">
        <p class="mc-eyebrow">
          {server} · spec {protocolVersion}
        </p>
        <p class="mc-label">Real messages from the running server: no calls from this page</p>
      </header>

      <div class="mc-focus mm-group" role="group" aria-label="Message pairs" tabIndex={0} onKeyDown={s.onKeyDown}>
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
        <ol class="mc-steps" aria-label="Methods in order">
          {steps.map((x, i) => (
            <li key={x.n} aria-current={i === s.at ? 'step' : undefined}>
              {i === s.at ? '▸ ' : ''}
              {x.method}
            </li>
          ))}
        </ol>
      </div>

      <h3 class="mc-h">
        {step.n}. {step.method}
      </h3>
      <p class="mc-hint">
        {step.note} Request id {String(step.id)}. Captured with {capturedWith}.
      </p>
      <p class="mc-live" aria-live="polite">
        {step.announce}
      </p>

      <div class="mm-pair">
        <div>
          <p class="mc-pre-label">Request</p>
          <div class="mc-scroll mc-focus" role="region" aria-label="Request" tabIndex={0}>
            <pre class="mc-pre">
              <code>{step.request}</code>
            </pre>
          </div>
        </div>
        <div>
          <p class="mc-pre-label">Result</p>
          <div class="mc-scroll mc-focus" role="region" aria-label="Result" tabIndex={0}>
            <pre class="mc-pre">
              <code>{step.result}</code>
            </pre>
          </div>
        </div>
      </div>
      <style>{BASE_CSS + css}</style>
    </section>
  );
}

const css = `
.mm-group { display: grid; gap: 8px; }
.mm-pair { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr)); min-width: 0; }
.mm-pair > div { min-width: 0; }
`;
