import { type ToolBudgetProps } from '../../lib/mcpAids';
import { BASE_CSS } from './mcpStyle';

const n = (x: number) => x.toLocaleString('en-US');

export default function ToolBudget({ note, rows, totalTools, totalTokens }: ToolBudgetProps) {
  return (
    <section class="mc" aria-label="Tool budget">
      <header class="mc-head">
        <p class="mc-eyebrow">What the tool list costs in context</p>
        <p class="mc-label">Estimated from the real tools/list</p>
      </header>

      <ul class="tb-bars" aria-label="Estimated tokens per server">
        {rows.map((r) => (
          <li key={r.server}>
            <p class="tb-name">
              {r.server}: {r.tools} {r.tools === 1 ? 'tool' : 'tools'}, about {n(r.tokens)} tokens
            </p>
            <div
              class="tb-track"
              role="img"
              aria-label={`${r.server}: ${r.tools} ${r.tools === 1 ? 'tool' : 'tools'}, about ${n(r.tokens)} tokens, ${r.percent}% of the longest bar`}
            >
              <span style={{ width: `${r.percent}%` }} />
            </div>
          </li>
        ))}
      </ul>

      <p class="mc-live" aria-live="polite">
        An agent connected to both servers sends {totalTools} tool definitions, about {n(totalTokens)} estimated tokens, with every model request.
      </p>

      <div class="mc-scroll mc-focus" role="region" aria-label="Tool budget table" tabIndex={0}>
        <table class="mc-table" aria-label="Tool budget per server">
          <thead>
            <tr>
              <th scope="col">Server</th>
              <th scope="col" class="mc-num">Tools</th>
              <th scope="col" class="mc-num">Characters</th>
              <th scope="col" class="mc-num">Tokens (estimate)</th>
              <th scope="col" class="mc-num">Per tool</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.server}>
                <th scope="row">{r.server}</th>
                <td class="mc-num">{r.tools}</td>
                <td class="mc-num">{n(r.chars)}</td>
                <td class="mc-num">{n(r.tokens)}</td>
                <td class="mc-num">{n(r.perTool)}</td>
              </tr>
            ))}
            <tr>
              <th scope="row">Both</th>
              <td class="mc-num">{totalTools}</td>
              <td class="mc-num">{n(rows.reduce((a, r) => a + r.chars, 0))}</td>
              <td class="mc-num">{n(totalTokens)}</td>
              <td class="mc-num">{n(Math.round(totalTokens / totalTools))}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="mc-hint">{note[0].toUpperCase() + note.slice(1)}.</p>
      <style>{BASE_CSS + css}</style>
    </section>
  );
}

const css = `
.tb-bars { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.tb-name { margin: 0 0 4px; font-family: var(--font-mono); font-size: var(--text-small); color: var(--ink); overflow-wrap: anywhere; }
.tb-track { height: 14px; background: var(--surface-2); border: 1px solid var(--line); border-radius: 999px; overflow: hidden; }
.tb-track span { display: block; height: 100%; background: var(--role-tool); }
`;
