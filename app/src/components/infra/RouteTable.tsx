import { useId, useRef, useState } from 'preact/hooks';
import { DENY_ROW, routeFor, verdictMark, type RouteTableProps } from '../../lib/infraAids';
import InfraMark from './InfraMark';
import { routeText, rowMove } from './infraFormat';
import { INFRA_CSS } from './infraStyle';

const none = (x: string | number | null) => (x === null ? 'none' : String(x));

export default function RouteTable(p: RouteTableProps) {
  const [pos, setPos] = useState(0);
  const refs = useRef<(HTMLTableRowElement | null)[]>([]);
  const selectId = useId();
  const focused = p.rows[pos];
  // The select's value: a configured model, or the '*' row for any other model.
  const picked = focused.model !== DENY_ROW && p.models.includes(focused.model) ? focused.model : DENY_ROW;
  const row = routeFor(p, picked);
  const asked = picked === DENY_ROW ? 'another model' : picked;
  const rule = verdictMark(row.rule);
  const fallback = row.fallback.length > 0 ? row.fallback.join(' then ') : 'none';

  const pick = (model: string) => setPos(p.rows.indexOf(routeFor(p, model)));
  const onKey = (e: KeyboardEvent) => {
    const next = rowMove(pos, e.key, p.rows.length);
    if (next === null) return;
    e.preventDefault();
    setPos(next);
    refs.current[next]?.focus();
  };

  return (
    <section class="mc" aria-label="Route table">
      <header class="mc-head">
        <p class="mc-eyebrow">Routing · where a model in the request goes</p>
        <p class="mc-label">Read from capstone.json's config: no calls from this page</p>
      </header>

      <div class="ag-pick">
        <label for={selectId}>Model in the request</label>
        <select id={selectId} value={picked} onChange={(e) => pick((e.currentTarget as HTMLSelectElement).value)}>
          {p.models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
          <option value={DENY_ROW}>another model</option>
        </select>
      </div>
      <p class="in-pick" data-testid="route-pick">
        A request for {asked} meets the {row.model} row: <InfraMark mark={rule} />
        {row.rule === 'route' && row.to !== null && ` to ${row.to}, fallback ${fallback}, ${row.rpm} rpm, ${row.tpm} tpm.`}
        {row.rule === 'route' && row.to === null && ', sent upstream as itself.'}
        {row.rule === 'deny' && ', refused at the route stage before any upstream call.'}
      </p>
      <p class="mc-live" role="status" aria-live="polite">
        {routeText(focused)}
      </p>

      <div class="mc-scroll mc-focus" role="region" aria-label="Routes table" tabIndex={0}>
        <table class="mc-table in-table" role="grid" aria-label="Routes">
          <thead>
            <tr>
              <th scope="col">Model in the request</th>
              <th scope="col">Rule</th>
              <th scope="col">To</th>
              <th scope="col">Fallback</th>
              <th scope="col" class="mc-num">rpm</th>
              <th scope="col" class="mc-num">tpm</th>
            </tr>
          </thead>
          <tbody>
            {p.rows.map((r, i) => (
              <tr
                key={r.model}
                class="in-row"
                tabIndex={i === pos ? 0 : -1}
                aria-selected={i === pos ? 'true' : 'false'}
                ref={(el) => {
                  refs.current[i] = el;
                }}
                onKeyDown={onKey}
                onFocus={() => setPos(i)}
                onClick={() => setPos(i)}
              >
                <th scope="row">{r.model}</th>
                <td role="gridcell">
                  <InfraMark mark={verdictMark(r.rule)} />
                </td>
                <td role="gridcell">{none(r.to)}</td>
                <td role="gridcell">{r.fallback.length > 0 ? r.fallback.join(' → ') : 'none'}</td>
                <td role="gridcell" class="mc-num">
                  {none(r.rpm)}
                </td>
                <td role="gridcell" class="mc-num">
                  {none(r.tpm)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="mc-hint">
        Arrow keys move between the rows; Home and End jump to the first and the last. The * row is every model the routes do not name. The upstream is{' '}
        {p.upstream}.
      </p>
      <p class="mc-note">{p.replayKey}</p>
      <style>{INFRA_CSS}</style>
    </section>
  );
}
