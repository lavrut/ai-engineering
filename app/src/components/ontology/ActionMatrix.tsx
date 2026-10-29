import { useRef, useState } from 'preact/hooks';
import {
  ALL_ENGINES,
  cellText,
  conditionsText,
  countsText,
  engineName,
  governanceGateMark,
  gridMove,
  matrixLine,
  ruleMark,
  type ActionMatrixProps,
} from '../../lib/ontologyAids';
import OntologyMark from './OntologyMark';
import { ONTOLOGY_CSS } from './ontologyStyle';

export default function ActionMatrix(p: ActionMatrixProps) {
  const [engine, setEngine] = useState(ALL_ENGINES);
  const [pos, setPos] = useState<[number, number]>([0, 0]);
  const refs = useRef<Record<string, HTMLTableCellElement | null>>({});
  const cellAt = (r: number, a: number) => p.cells[r * p.actions.length + a];
  const focused = cellAt(pos[0], pos[1]);
  const g = p.gate;

  const onKey = (e: KeyboardEvent) => {
    const next = gridMove(pos[0], pos[1], e.key, p.roles.length, p.actions.length);
    if (next === null) return;
    e.preventDefault();
    setPos(next);
    refs.current[`${next[0]} ${next[1]}`]?.focus();
  };

  return (
    <section class="mc" aria-label="Action matrix">
      <header class="mc-head">
        <p class="mc-eyebrow">Governed action · which role may take which action</p>
        <p class="mc-label">Read from governance.json: no calls from this page</p>
      </header>

      <div class={`dt-status${g.ok ? '' : ' dt-status-bad'}`} role="status" aria-live="off">
        <p class="dt-verdict">
          <OntologyMark mark={governanceGateMark(g)} />
        </p>
        <p>
          <span class="mc-pre-label">The gate's verdict, as governance.py wrote it: </span>
          {g.reason}
        </p>
      </div>

      <fieldset class="ag-pickers">
        <legend>Engine</legend>
        {[ALL_ENGINES, ...p.engines].map((e) => (
          <button key={e} type="button" class="mc-btn" aria-pressed={engine === e ? 'true' : 'false'} onClick={() => setEngine(e)}>
            {e}
          </button>
        ))}
      </fieldset>
      <p class="mc-hint">{matrixLine(p, engine)}</p>
      <p class="mc-live" aria-live="polite">
        {cellText(focused, engine)}
      </p>

      <div class="mc-scroll mc-focus" role="region" aria-label="Action matrix table" tabIndex={0}>
        <table class="mc-table on-table am-table" role="grid" aria-label={`Roles and actions, ${engineName(engine)}`}>
          <thead>
            <tr>
              <th scope="col">Role</th>
              {p.actions.map((a) => (
                <th key={a} scope="col">
                  {a}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {p.roles.map((role, r) => (
              <tr key={role}>
                <th scope="row">{role}</th>
                {p.actions.map((action, a) => {
                  const c = cellAt(r, a);
                  const here = pos[0] === r && pos[1] === a;
                  return (
                    <td
                      key={action}
                      role="gridcell"
                      class="am-cell"
                      tabIndex={here ? 0 : -1}
                      aria-selected={here ? 'true' : 'false'}
                      ref={(el) => {
                        refs.current[`${r} ${a}`] = el;
                      }}
                      onKeyDown={onKey}
                      onFocus={() => setPos([r, a])}
                      onClick={() => setPos([r, a])}
                    >
                      <OntologyMark mark={ruleMark(c.rule)} />
                      {c.conditions.length > 0 && <span class="am-line">{conditionsText(c.conditions)}</span>}
                      <span class="am-line">{countsText(c.counts[engine])}</span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="mc-hint">
        Arrow keys move between the cells; Home and End jump along a row. A cell is the rule section 6 gives the role, then the recorded events it
        judged. NO means no row permits it.
      </p>

      <h3 class="mc-h">Conditions</h3>
      <dl class="am-conds">
        {p.conditions.map((c) => (
          <div key={c.id}>
            <dt>{c.id}</dt>
            <dd>{c.text}</dd>
          </div>
        ))}
      </dl>

      <h3 class="mc-h">Tools and the action each takes</h3>
      <div class="mc-scroll mc-focus" role="region" aria-label="Tools table" tabIndex={0}>
        <table class="mc-table am-tools" aria-label="Tools and actions">
          <thead>
            <tr>
              <th scope="col">Tool</th>
              <th scope="col">Action</th>
            </tr>
          </thead>
          <tbody>
            {p.tools.map(([tool, action]) => (
              <tr key={tool}>
                <th scope="row">{tool}</th>
                <td>{action}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <style>{ONTOLOGY_CSS}</style>
    </section>
  );
}
