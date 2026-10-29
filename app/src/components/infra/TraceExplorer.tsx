import { useId, useRef, useState } from 'preact/hooks';
import { filterTraces, verdictMark, type SampleTraceJson, type TraceExplorerProps, type TraceFilter } from '../../lib/infraAids';
import InfraMark from './InfraMark';
import { gateMark, outcomeMark, rowMove, statusMark, usd } from './infraFormat';
import { INFRA_CSS } from './infraStyle';

const cost = (x: number | null) => (x === null ? 'no cost' : usd(x, 7));

/** The live region's sentence for one trace. */
const traceText = ({ label, trace: t }: SampleTraceJson) =>
  `${label}: ${t.team}, tag ${t.tag}, status ${t.status}, ${t.outcome}, ${t.ms} ms, ${cost(t.cost_usd)}.`;

type Facet = 'team' | 'tag' | 'outcome';
const FACETS: [Facet, string][] = [
  ['team', 'Team'],
  ['tag', 'Tag'],
  ['outcome', 'Outcome'],
];

export default function TraceExplorer(p: TraceExplorerProps) {
  const [filter, setFilter] = useState<TraceFilter>({});
  const [pos, setPos] = useState(Math.max(0, p.traces.findIndex((t) => t.label === p.initial)));
  const [open, setOpen] = useState(p.initial);
  const refs = useRef<(HTMLTableRowElement | null)[]>([]);
  const baseId = useId();
  const shown = filterTraces(p, filter);
  const at = Math.min(pos, shown.length - 1);
  const opened = shown.find((t) => t.label === open);
  const values: Record<Facet, string[]> = { team: p.teams, tag: p.tags, outcome: p.outcomes };

  const setFacet = (f: Facet, v: string) => {
    setFilter({ ...filter, [f]: v === '' ? undefined : v });
    setPos(0);
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setOpen(shown[at].label);
      return;
    }
    const next = rowMove(at, e.key, shown.length);
    if (next === null) return;
    e.preventDefault();
    setPos(next);
    refs.current[next]?.focus();
  };
  const overall = p.gate.ok ? { icon: '✓', word: 'green', tone: 'ok' as const } : { icon: '✗', word: 'red', tone: 'fail' as const };

  return (
    <section class="mc" aria-label="Trace explorer">
      <header class="mc-head">
        <p class="mc-eyebrow">Traces · every stage's decision on one request</p>
        <p class="mc-label">Read from traces.sample.json and capstone.json: no calls from this page</p>
      </header>

      <fieldset class="ag-pickers">
        <legend>Filters</legend>
        {FACETS.map(([f, name]) => (
          <div class="ag-pick" key={f}>
            <label for={`${baseId}-${f}`}>{name}</label>
            <select id={`${baseId}-${f}`} value={filter[f] ?? ''} onChange={(e) => setFacet(f, (e.currentTarget as HTMLSelectElement).value)}>
              <option value="">any</option>
              {values[f].map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </div>
        ))}
      </fieldset>
      <p class="mc-live" role="status" aria-live="polite">
        {shown.length === 0 ? 'No trace matches these filters.' : traceText(shown[at])}
      </p>

      {shown.length === 0 ? (
        <p class="mc-hint">Set a filter back to any to see more.</p>
      ) : (
        <div class="mc-scroll mc-focus" role="region" aria-label="Matching traces table" tabIndex={0}>
          <table class="mc-table in-table in-wide" role="grid" aria-label="Matching traces">
            <thead>
              <tr>
                <th scope="col">Trace</th>
                <th scope="col">Team</th>
                <th scope="col">Tag</th>
                <th scope="col" class="mc-num">Status</th>
                <th scope="col">Outcome</th>
                <th scope="col" class="mc-num">ms</th>
                <th scope="col" class="mc-num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((x, i) => (
                <tr
                  key={x.label}
                  class="in-row"
                  tabIndex={i === at ? 0 : -1}
                  aria-selected={i === at ? 'true' : 'false'}
                  ref={(el) => {
                    refs.current[i] = el;
                  }}
                  onKeyDown={onKey}
                  onFocus={() => setPos(i)}
                  onClick={() => {
                    setPos(i);
                    setOpen(x.label);
                  }}
                >
                  <th scope="row">{x.label}</th>
                  <td role="gridcell">{x.trace.team}</td>
                  <td role="gridcell">{x.trace.tag}</td>
                  <td role="gridcell" class="mc-num">
                    {x.trace.status}
                  </td>
                  <td role="gridcell">
                    <InfraMark mark={outcomeMark(x.trace.outcome)} />
                  </td>
                  <td role="gridcell" class="mc-num">
                    {x.trace.ms}
                  </td>
                  <td role="gridcell" class="mc-num">
                    {x.trace.cost_usd === null ? 'none' : usd(x.trace.cost_usd, 7)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p class="mc-hint">Arrow keys move between the traces; Home and End jump to the first and the last; Enter or a click opens one.</p>

      {opened === undefined ? (
        <p class="mc-hint">None of the matching traces is open: pick one.</p>
      ) : (
        <div class="in-open" role="region" aria-label={`Open trace ${opened.label}`}>
          <h3>
            {opened.label} · run {opened.run} · {opened.trace.model} {opened.trace.path}
          </h3>
          <ol class="in-lines" aria-label="Decisions">
            {opened.trace.decisions.map((d) => (
              <li key={d.stage}>
                <span class="in-stage">{d.stage}:</span> <InfraMark mark={verdictMark(d.verdict)} /> ({d.detail})
              </li>
            ))}
          </ol>
          {opened.trace.attempts.length === 0 ? (
            <p class="mc-hint">No upstream attempts.</p>
          ) : (
            <ol class="in-lines" aria-label="Attempts">
              {opened.trace.attempts.map((a, i) => (
                <li key={i}>
                  Attempt {i + 1}: {a.model}, <InfraMark mark={statusMark(a.status)} />, {a.backoff_ms} ms of backoff{a.error !== null && ` (${a.error})`}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
      <p class="in-about">{Object.values(p.about).join(' ')}</p>

      <h3 class="mc-h">The capstone gate</h3>
      <div class={`dt-status${p.gate.ok ? '' : ' dt-status-bad'}`}>
        <p class="dt-verdict" data-testid="gate-verdict">
          <InfraMark mark={overall} />
        </p>
        <p>The gate's lines, as replay.py --gate wrote them.</p>
      </div>
      <div class="mc-scroll mc-focus" role="region" aria-label="Gate lines table" tabIndex={0}>
        <table class="mc-table in-table in-wide" aria-label="Gate lines">
          <thead>
            <tr>
              <th scope="col">Check</th>
              <th scope="col">Verdict</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {p.gate.lines.map((l) => (
              <tr key={l.name}>
                <th scope="row">{l.name}</th>
                <td>
                  <InfraMark mark={gateMark(l.ok)} />
                </td>
                <td>{l.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <style>{INFRA_CSS}</style>
    </section>
  );
}
