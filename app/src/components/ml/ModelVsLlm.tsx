import { llmGap, pct, type ModelVsLlmProps } from '../../lib/mlAids';

const n3 = (x: number) => x.toFixed(3);
const signed = (x: number) => `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(x).toFixed(3)}`;
const points = (x: number) => (x * 100).toFixed(1);
const ms = (x: number) => `${x.toLocaleString('en-US', { minimumFractionDigits: x < 10 ? 3 : 1, maximumFractionDigits: x < 10 ? 3 : 1 })} ms`;
const dollars = (x: number) => `$${x.toFixed(3)}`;

export default function ModelVsLlm({ compare: c, servers, commands }: ModelVsLlmProps) {
  const g = c ? llmGap(c) : null;
  const maxF1 = 1;

  return (
    <section class="ml" aria-label="Model versus LLM">
      <header class="ml-head">
        <p class="ml-eyebrow">v1 vs an LLM · same complaints</p>
        <p class="ml-label">Recorded answers, replayed: no calls from this page</p>
      </header>

      {c && g ? (
        <>
          <p class="ml-summary" role="status">
            On {c.n} test complaints ({c.split}), {c.claude.name} is right {pct(c.claude.accuracy)} of the time and {c.v1.name}{' '}
            {pct(c.v1.accuracy)}: a {points(Math.abs(g.gap))}-point gap,{' '}
            {g.inside
              ? `inside the ±${points(g.band)}-point band (1/√${c.n}). A sample this size can't tell the two apart on accuracy.`
              : `outside the ±${points(g.band)}-point band (1/√${c.n}), so the difference is more than noise.`}
          </p>
          <p class="ml-hint">
            Headline over all {c.n}, a reply with no usable answer counted as wrong (no answer: {c.no_answer}
            {c.answered_only.n !== c.n && c.answered_only.claude_accuracy !== null
              ? `; on the ${c.answered_only.n} answered, ${pct(c.answered_only.claude_accuracy)} vs ${pct(c.answered_only.v1_accuracy ?? 0)}`
              : ', so the answered-only numbers are the same'}
            ).
          </p>

          <div class="ml-scroll" role="region" aria-label="Side by side table" tabIndex={0}>
            <table class="ml-table" aria-label={`Side by side: ${c.v1.name} and ${c.claude.name}`}>
              <thead>
                <tr>
                  <th scope="col">Measure</th>
                  <th scope="col">{c.v1.name}</th>
                  <th scope="col">{c.claude.name}</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Accuracy</th>
                  <td>{pct(c.v1.accuracy)}</td>
                  <td>{pct(c.claude.accuracy)}</td>
                </tr>
                <tr>
                  <th scope="row">Macro F1</th>
                  <td>{n3(c.v1.macro_f1)}</td>
                  <td>{n3(c.claude.macro_f1)}</td>
                </tr>
                <tr>
                  <th scope="row">Cost per 1,000 complaints</th>
                  <td>
                    {dollars(c.v1.cost_per_1000)}
                    <span class="ml-sub"> runs on your own machine</span>
                  </td>
                  <td>
                    {dollars(c.claude.cost_per_1000)}
                    <span class="ml-sub"> API tokens</span>
                  </td>
                </tr>
                <tr>
                  <th scope="row">Latency, median</th>
                  <td>{ms(c.v1.latency_p50_ms)}</td>
                  <td>{ms(c.claude.latency_p50_ms)}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="ml-scroll" role="region" aria-label="F1 per category table" tabIndex={0}>
            <table class="ml-table ml-f1" aria-label="F1 per category">
              <thead>
                <tr>
                  <th scope="col">Category</th>
                  <th scope="col">{c.v1.name}</th>
                  <th scope="col">{c.claude.name}</th>
                  <th scope="col">Difference</th>
                </tr>
              </thead>
              <tbody>
                {c.classes.map((k, i) => {
                  const a = c.v1.per_class_f1[i];
                  const b = c.claude.per_class_f1[i];
                  return (
                    <tr key={k}>
                      <th scope="row">{k}</th>
                      <td>
                        <span class="ml-bar ml-bar-v1" aria-hidden="true">
                          <span style={{ width: `${(a / maxF1) * 100}%` }} />
                        </span>
                        {n3(a)}
                      </td>
                      <td>
                        <span class="ml-bar ml-bar-llm" aria-hidden="true">
                          <span style={{ width: `${(b / maxF1) * 100}%` }} />
                        </span>
                        {n3(b)}
                      </td>
                      <td>{signed(b - a)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p class="ml-hint">
            Each category holds only part of the {c.n}, so a per-category difference is noisier than the ±{points(g.band)} points
            on accuracy. Bars run from 0 to 1: solid for {c.v1.name}, hatched for {c.claude.name}.
          </p>
        </>
      ) : (
        <div class="ml-empty">
          <p class="ml-summary">No recorded run yet.</p>
          <p class="ml-hint">
            The comparison needs Claude&apos;s answers, recorded once (paid; it prints the cost and asks first). From{' '}
            <code>ml/python</code>, with <code>ANTHROPIC_API_KEY</code> set:
          </p>
          <pre class="ml-cmd">
            <code>{commands.join('\n')}</code>
          </pre>
        </div>
      )}

      <div class="ml-scroll" role="region" aria-label="Latency table" tabIndex={0}>
        <table class="ml-table" aria-label="Latency per request">
          <thead>
            <tr>
              <th scope="col">Serving</th>
              <th scope="col">Median</th>
              <th scope="col">p95</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">v1, Python server</th>
              <td>{ms(servers.python.p50_ms)}</td>
              <td>{ms(servers.python.p95_ms)}</td>
            </tr>
            <tr>
              <th scope="row">v1, Go server</th>
              <td>{ms(servers.go.p50_ms)}</td>
              <td>{ms(servers.go.p95_ms)}</td>
            </tr>
            {c && (
              <tr>
                <th scope="row">{c.claude.name}</th>
                <td>{ms(c.claude.latency_p50_ms)}</td>
                <td>not recorded</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <ul class="ml-what" aria-label="What each latency measures">
        <li>
          <strong>v1, Python server:</strong> {c ? c.v1.latency_note : `local CPU inference (${servers.python.command})`}
        </li>
        <li>
          <strong>v1, Go server:</strong> local CPU inference, same model and requests ({servers.go.command});{' '}
          {servers.ratio_p50.toFixed(2)}× faster than Python at the median
        </li>
        {c && (
          <li>
            <strong>{c.claude.name}:</strong> {c.claude.latency_note}
          </li>
        )}
      </ul>
      <p class="ml-note" role="note">
        <strong>Not like for like:</strong>{' '}
        {c ? `${c.latency_caveat} ` : ''}
        The servers ran {servers.requests} requests each on one machine ({servers.machine}); only their ratio carries over to
        other machines.
      </p>
      <style>{css}</style>
    </section>
  );
}

const css = `
.ml { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.ml-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.ml-eyebrow {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.ml-label {
  margin: 0; padding: 3px 8px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--muted); border: 1px dashed var(--line); border-radius: var(--radius);
}
.ml-summary { margin: 0; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.ml-hint { margin: 0; font-size: var(--text-small); color: var(--muted); }
.ml-note {
  margin: 0; padding: 10px 14px; font-size: var(--text-small); color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-left: 4px solid var(--warn); border-radius: var(--radius);
}
.ml-scroll { min-width: 0; max-width: 100%; overflow-x: auto; }
.ml-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ml-table { width: 100%; border-collapse: collapse; font-size: var(--text-small); font-variant-numeric: tabular-nums; background: var(--surface); border: 1px solid var(--line); }
.ml-table th, .ml-table td { padding: 6px 10px; text-align: right; border-bottom: 1px solid var(--line); vertical-align: top; }
.ml-table thead th { font-family: var(--font-mono); font-size: var(--text-label); font-weight: 500; color: var(--muted); }
.ml-table th[scope="row"], .ml-table thead th:first-child { text-align: left; font-weight: 500; color: var(--ink); min-width: 8rem; }
.ml-table td { font-family: var(--font-mono); color: var(--ink); white-space: nowrap; }
.ml-what { margin: 0; padding-left: 1.25rem; display: grid; gap: 4px; font-size: var(--text-small); color: var(--muted); }
.ml-what strong { color: var(--ink); font-weight: 600; }
.ml-sub { display: block; font-family: var(--font-body); font-size: var(--text-label); color: var(--muted); }
.ml-bar { display: inline-block; width: 4rem; height: 8px; margin-right: 8px; background: var(--surface-2); border-radius: 999px; overflow: hidden; vertical-align: middle; }
.ml-bar span { display: block; height: 100%; }
.ml-bar-v1 span { background: var(--role-model); }
.ml-bar-llm span { background: repeating-linear-gradient(135deg, var(--role-tool) 0 3px, transparent 3px 5px); border: 1px solid var(--role-tool); box-sizing: border-box; }
.ml-empty { display: grid; gap: 8px; padding: 12px 14px; background: var(--surface); border: 1px dashed var(--line); border-radius: var(--radius); }
.ml-cmd { margin: 0; padding: 8px 12px; font-family: var(--font-mono); font-size: var(--text-small); color: var(--ink); background: var(--surface-2); border-radius: var(--radius); overflow-x: auto; white-space: pre; }
.ml-empty code { font-family: var(--font-mono); }
`;
