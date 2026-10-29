import { useRef, useState } from 'preact/hooks';
import { ALL, type LedgerTeamView, type LedgerViewProps } from '../../lib/infraAids';
import InfraMark from './InfraMark';
import { rowMove, stateMark, usd } from './infraFormat';
import { INFRA_CSS } from './infraStyle';

const line = (x: number | null) => (x === null ? 'none' : usd(x, 2));

/** The live region's sentence for one team. */
function teamText(t: LedgerTeamView): string {
  const limit = t.limit_usd === null ? 'no budget' : `${usd(t.cost_usd)} of ${usd(t.limit_usd, 2)}`;
  const warn = t.warn_usd === null ? 'no warn line' : `warn line ${usd(t.warn_usd, 2)}`;
  return `${t.team}: ${limit}, ${warn}: ${t.state}. ${t.requests} requests, ${t.cached} cached, ${t.refused} refused.`;
}

/** "lab has a budget and sent no traffic in this run." */
function idleText(idle: string[]): string {
  const names = idle.length > 1 ? `${idle.slice(0, -1).join(', ')} and ${idle.at(-1)}` : idle[0];
  return `${names} ${idle.length > 1 ? 'have budgets' : 'has a budget'} and sent no traffic in this run.`;
}

export default function LedgerView(p: LedgerViewProps) {
  const [team, setTeam] = useState(p.initial);
  const [pos, setPos] = useState(0);
  const refs = useRef<(HTMLTableRowElement | null)[]>([]);
  const shown = team === ALL ? p.teams : p.teams.filter((t) => t.team === team);
  const at = Math.min(pos, shown.length - 1);

  const pick = (next: string) => {
    setTeam(next);
    setPos(0);
  };
  const onKey = (e: KeyboardEvent) => {
    const next = rowMove(at, e.key, shown.length);
    if (next === null) return;
    e.preventDefault();
    setPos(next);
    refs.current[next]?.focus();
  };

  return (
    <section class="mc" aria-label="Ledger view">
      <header class="mc-head">
        <p class="mc-eyebrow">The ledger · spend per team against its budget</p>
        <p class="mc-label">Read from ledger.json and capstone.json: no calls from this page</p>
      </header>

      <fieldset class="ag-pickers">
        <legend>Team</legend>
        {[ALL, ...p.teams.map((t) => t.team)].map((t) => (
          <button key={t} type="button" class="mc-btn" aria-pressed={team === t ? 'true' : 'false'} onClick={() => pick(t)}>
            {t}
          </button>
        ))}
      </fieldset>
      <p class="mc-live" role="status" aria-live="polite">
        {teamText(shown[at])}
      </p>

      <div class="mc-scroll mc-focus" role="region" aria-label="Spend by team table" tabIndex={0}>
        <table class="mc-table in-table in-wide" role="grid" aria-label={`Spend by team, ${team}`}>
          <thead>
            <tr>
              <th scope="col">Team</th>
              <th scope="col" class="mc-num">Requests</th>
              <th scope="col" class="mc-num">Spend</th>
              <th scope="col" class="mc-num">Limit</th>
              <th scope="col" class="mc-num">Warn line</th>
              <th scope="col">State</th>
              <th scope="col" class="mc-num">Cached</th>
              <th scope="col" class="mc-num">Refused</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((t, i) => (
              <tr
                key={t.team}
                class="in-row"
                tabIndex={i === at ? 0 : -1}
                aria-selected={i === at ? 'true' : 'false'}
                ref={(el) => {
                  refs.current[i] = el;
                }}
                onKeyDown={onKey}
                onFocus={() => setPos(i)}
                onClick={() => setPos(i)}
              >
                <th scope="row">{t.team}</th>
                <td role="gridcell" class="mc-num">
                  {t.requests}
                </td>
                <td role="gridcell" class="mc-num">
                  {usd(t.cost_usd)}
                </td>
                <td role="gridcell" class="mc-num">
                  {line(t.limit_usd)}
                </td>
                <td role="gridcell" class="mc-num">
                  {line(t.warn_usd)}
                </td>
                <td role="gridcell">
                  <InfraMark mark={stateMark(t.state)} />
                </td>
                <td role="gridcell" class="mc-num">
                  {t.cached}
                </td>
                <td role="gridcell" class="mc-num">
                  {t.refused}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="mc-hint">
        Arrow keys move between the teams; Home and End jump to the first and the last. ✓ under: below every line; ◐ warn: past the warn line; ✗ over: past the
        limit, or no budget, so the budget stage refuses.
      </p>
      {p.idle.length > 0 && <p class="mc-hint">{idleText(p.idle)}</p>}
      <p class="in-about">{p.about.ledger}</p>

      <h3 class="mc-h">Top tags by spend</h3>
      <div class="mc-scroll mc-focus" role="region" aria-label="Top tags table" tabIndex={0}>
        <table class="mc-table" aria-label="Top tags by spend">
          <thead>
            <tr>
              <th scope="col">Tag</th>
              <th scope="col" class="mc-num">Spend</th>
            </tr>
          </thead>
          <tbody>
            {p.top_tags.map((t) => (
              <tr key={t.tag}>
                <th scope="row">{t.tag}</th>
                <td class="mc-num">{usd(t.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 class="mc-h">The agents ledger beside the gateway's</h3>
      <div class="mc-scroll mc-focus" role="region" aria-label="Agents ledger table" tabIndex={0}>
        <table class="mc-table in-table in-wide" aria-label="Agents ledger beside the gateway">
          <thead>
            <tr>
              <th scope="col">Set</th>
              <th scope="col" class="mc-num">Agents ledger</th>
              <th scope="col" class="mc-num">Gateway ledger</th>
              <th scope="col" class="mc-num">Cache hits</th>
              <th scope="col">Checked by the gate</th>
            </tr>
          </thead>
          <tbody>
            {p.agents.map((a) => (
              <tr key={a.set}>
                <th scope="row">{a.set}</th>
                <td class="mc-num">{usd(a.agents_usd, 7)}</td>
                <td class="mc-num">{a.gateway_usd === null ? 'none' : usd(a.gateway_usd, 7)}</td>
                <td class="mc-num">{a.cache_hits === null ? 'none' : a.cache_hits}</td>
                <td>{a.desk ? 'yes, a desk set' : 'no'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="in-about">{p.about.agents}</p>
      <p class="mc-hint">A cache hit books $0 at the gateway, so a set with hits shows less there than in the agents ledger.</p>
      <style>{INFRA_CSS}</style>
    </section>
  );
}
