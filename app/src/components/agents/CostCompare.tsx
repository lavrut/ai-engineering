import { useState } from 'preact/hooks';
import { COST_METRICS, bars, metricSummary, type CostCompareProps, type CostDeskView, type CostMetric } from '../../lib/agentsAids';
import NoRecordedRun from '../llms/NoRecordedRun';
import { AGENTS_CSS } from './agentsStyle';

const RECORD_TEXT =
  'These runs call the Claude API and cost money: the record command prints an estimate and asks before it spends anything. Then rescore and rebuild the derived data (the last two commands) and the app.';

const DESKS = [
  { id: 'single', label: 'Single agent' },
  { id: 'supervisor', label: 'Supervisor' },
] as const;

function DeskCells({ d }: { d: CostDeskView }) {
  if (!d.recorded) return <td colSpan={8}>not recorded (missing {d.missing})</td>;
  return (
    <>
      <td class="mc-num">
        {d.success} {d.band}
      </td>
      <td class="mc-num">{d.meanCost}</td>
      <td class="mc-num">{d.medianCost}</td>
      <td class="mc-num">{d.p50}</td>
      <td class="mc-num">{d.p90}</td>
      <td class="mc-num">
        {d.calls} ({d.callsRange})
      </td>
      <td class="mc-num">{d.tokensIn}</td>
      <td class="mc-num">
        {d.tokensOut} / {d.cacheRead}
      </td>
    </>
  );
}

export default function CostCompare({ engines, tickets }: CostCompareProps) {
  const [metric, setMetric] = useState<CostMetric>('cost');
  const m = COST_METRICS.find((x) => x.id === metric)!;
  const rows = bars(engines, metric);
  const missing = engines.flatMap((e) =>
    DESKS.flatMap((d) => {
      const v = e[d.id];
      return v.recorded ? [] : [{ e, d, v }];
    }),
  );

  return (
    <section class="mc" aria-label="Single agent and supervisor compared">
      <header class="mc-head">
        <p class="mc-eyebrow">Single agent vs supervisor · {tickets} tickets per desk</p>
        <p class="mc-label">From the recorded runs: no calls from this page</p>
      </header>

      <fieldset class="ag-pickers">
        <legend>Compare</legend>
        {COST_METRICS.map((x) => (
          <label key={x.id} class="ag-radio">
            <input type="radio" name="cc-metric" value={x.id} checked={metric === x.id} onChange={() => setMetric(x.id)} /> {x.label}
          </label>
        ))}
      </fieldset>

      <p class="mc-live" aria-live="polite">
        {metricSummary(engines, metric)}
      </p>

      <ul class="ag-tracks" aria-label={`${m.label}, single agent and supervisor, per engine`}>
        {rows.map((r) => (
          <li key={r.engine}>
            <p class="ag-name">{r.label}</p>
            {DESKS.map((d) => (
              <div key={d.id} class="ag-bar">
                <span>
                  {d.label}: {r[d.id].text}
                </span>
                <div class="ag-track" role="img" aria-label={`${r.label}, ${d.label.toLowerCase()}: ${r[d.id].text}, ${r[d.id].percent}% of the longest bar`}>
                  <span class={`ag-fill-${d.id}`} style={{ width: `${r[d.id].percent}%` }} />
                </div>
              </div>
            ))}
          </li>
        ))}
      </ul>

      {missing.map(({ e, d, v }) => (
        <div key={`${e.engine}-${d.id}`}>
          <p class="ag-name">
            {e.label}, {d.label.toLowerCase()}
          </p>
          <NoRecordedRun command={v.command} text={RECORD_TEXT} />
        </div>
      ))}

      <ul class="cc-sentences" aria-label="Supervisor against single agent, per engine">
        {engines
          .filter((e) => e.sentence)
          .map((e) => (
            <li key={e.engine}>{e.sentence}</li>
          ))}
      </ul>

      <div class="mc-scroll mc-focus" role="region" aria-label="Cost and success table" tabIndex={0}>
        <table class="mc-table ag-table-wide" aria-label="Every engine's single and supervisor desks over the same tickets">
          <thead>
            <tr>
              <th scope="col">Engine</th>
              <th scope="col">Desk</th>
              <th scope="col" class="mc-num">Success ± band</th>
              <th scope="col" class="mc-num">Mean cost</th>
              <th scope="col" class="mc-num">Median cost</th>
              <th scope="col" class="mc-num">p50 time</th>
              <th scope="col" class="mc-num">p90 time</th>
              <th scope="col" class="mc-num">Calls per ticket (min–max)</th>
              <th scope="col" class="mc-num">Tokens in (mean)</th>
              <th scope="col" class="mc-num">Out / cached in (mean)</th>
            </tr>
          </thead>
          <tbody>
            {engines.flatMap((e) =>
              DESKS.map((d) => (
                <tr key={`${e.engine}-${d.id}`}>
                  <th scope="row">{e.label}</th>
                  <td>{d.label}</td>
                  <DeskCells d={e[d.id]} />
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
      <p class="mc-hint">
        Cost and time are the recorded calls' own, from the proxy's ledger (a replay costs nothing). A success difference smaller than the noise band is not evidence
        of a difference. Tokens in are uncached; the Claude Agent SDK caches most of its input, so its cached column carries the bulk.
      </p>
      <style>{AGENTS_CSS + css}</style>
    </section>
  );
}

const css = `
.cc-sentences { margin: 0; padding-left: 1.1rem; display: grid; gap: 6px; font-size: var(--text-small); color: var(--ink); }
`;
