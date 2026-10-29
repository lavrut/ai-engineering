import { useState } from 'preact/hooks';
import { consumerText, gateHeadline, type ConsumerRow, type LineageDiffProps, type VersionView } from '../../lib/dataAids';
import Picker from '../agents/Picker';
import { DATA_CSS } from './dataStyle';
import { Mark } from './QualityReport';

const rowId = (r: ConsumerRow) => `${r.name} on ${r.declared}`;

function Version({ v, rows, asOf }: { v: VersionView; rows: ConsumerRow[]; asOf: string }) {
  const mine = rows.filter((r) => r.declared === v.id);
  const warned = mine.some((r) => r.warning);
  return (
    <div class={`dt-version${v.status === 'deprecated' ? ' dt-deprecated' : ''}`} role="region" aria-label={v.id}>
      <h3>{v.id}</h3>
      <p>Status: {v.status}</p>
      <p>Sunset: {v.sunset ?? 'none'}</p>
      {v.sunset && (
        <p>
          {asOf <= v.sunset
            ? `No warning: as of ${asOf} the sunset is still ahead.`
            : `Past its sunset as of ${asOf}${warned ? ': gate.py warns.' : ', but gate.json has no warning: rerun gate.py.'}`}
        </p>
      )}
      <p>
        Dataset: {v.dataset} ({v.path})
      </p>
      <ul aria-label={`Consumers on ${v.id}`}>
        {v.consumers.map((c) => {
          const r = mine.find((x) => x.name === c.name);
          return (
            <li key={c.name}>
              {r ? <Mark ok={r.green} text={r.green ? 'OK' : 'FAIL'} /> : <span class="dt-mark">not in gate.json</span>} {c.name}
              <br />
              <span class="mc-hint">reads {c.reads}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function LineageDiff({ from, to, asOf, migration, migrationNote, fields, consumers, gate }: LineageDiffProps) {
  const [pick, setPick] = useState(consumers[0] ? rowId(consumers[0]) : '');
  const picked = consumers.find((r) => rowId(r) === pick) ?? consumers[0];

  return (
    <section class="mc" aria-label="Lineage diff">
      <header class="mc-head">
        <p class="mc-eyebrow">
          {from.id} → {to.id}
        </p>
        <p class="mc-label">Read from the contracts, lineage.json and gate.json: no calls from this page</p>
      </header>

      <div class="dt-split">
        <Version v={from} rows={consumers} asOf={asOf} />
        <Version v={to} rows={consumers} asOf={asOf} />
      </div>

      {migration ? (
        <p class="dt-edge">
          {migration.from} → {migration.to}, reading {migration.contract}
        </p>
      ) : (
        <p class="mc-hint">lineage.json has no edge from {from.dataset} to {to.dataset}.</p>
      )}
      {migrationNote && <blockquote class="dt-quote">{migrationNote}</blockquote>}

      <div class="mc-scroll mc-focus" role="region" aria-label="Field changes table" tabIndex={0}>
        <table class="mc-table dt-table" aria-label={`What changed from ${from.id} to ${to.id}`}>
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Change</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => (
              <tr key={f.field}>
                <th scope="row">{f.field}</th>
                <td>{f.change}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {picked && (
        <fieldset class="ag-pickers">
          <legend>Who reads which version</legend>
          <Picker label="Consumer" value={rowId(picked)} options={consumers.map((r) => ({ id: rowId(r), label: rowId(r) }))} onChange={setPick} />
        </fieldset>
      )}
      <p class="mc-live" aria-live="polite">
        {picked ? consumerText(picked, asOf) : 'No consumer declares either version.'}
      </p>

      <div class={`dt-status${gate.ok ? '' : ' dt-status-bad'}`} role="status" aria-live="off">
        <p class="dt-verdict">
          <Mark ok={gate.ok} text={gateHeadline(gate)} />
        </p>
        <p>
          <span class="mc-pre-label">The gate's verdict, as gate.py wrote it: </span>
          {gate.reason}
        </p>
      </div>
      <p class="mc-hint">The gate checks every consumer of every contract, so its verdict counts them all, not only the two versions above.</p>
      <style>{DATA_CSS}</style>
    </section>
  );
}
