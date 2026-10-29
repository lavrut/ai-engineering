import { useState } from 'preact/hooks';
import { count, datasetFacts, datasetSummary, type CatalogExplorerProps } from '../../lib/dataAids';
import Picker from '../agents/Picker';
import { DATA_CSS } from './dataStyle';

export default function CatalogExplorer({ datasets, initial }: CatalogExplorerProps) {
  const [id, setId] = useState(initial);
  const d = datasets.find((x) => x.id === id) ?? datasets[0];

  return (
    <section class="mc" aria-label="Catalogue explorer">
      <header class="mc-head">
        <p class="mc-eyebrow">The catalogue · {datasets.length} datasets</p>
        <p class="mc-label">Read from catalog.json: no calls from this page</p>
      </header>

      <fieldset class="ag-pickers">
        <legend>Choose a dataset</legend>
        <Picker label="Dataset" value={d.id} options={datasets.map((x) => ({ id: x.id, label: x.id }))} onChange={setId} />
      </fieldset>

      <p class="mc-live" aria-live="polite">
        {datasetSummary(d)}
      </p>
      <p class="mc-hint">{d.description}</p>

      <ul class="dt-facts" aria-label={`About ${d.id}`}>
        {datasetFacts(d).map(([k, v]) => (
          <li key={k}>
            <span class="dt-key">{k}</span> <span class="dt-val">{v}</span>
          </li>
        ))}
      </ul>

      <div class="mc-scroll mc-focus" role="region" aria-label="Schema sketch table" tabIndex={0}>
        <table class="mc-table" aria-label={`Schema sketch of ${d.id}`}>
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Type</th>
            </tr>
          </thead>
          <tbody>
            {d.schema.map(([field, type]) => (
              <tr key={field}>
                <th scope="row">{field}</th>
                <td>{type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="mc-hint">
        The schema sketch is read from the rows themselves, not from a contract: each field and the JSON type its values have. A contract is the promise; the
        sketch is what the files hold today.
      </p>

      <div class="mc-scroll mc-focus" role="region" aria-label="Catalogue table" tabIndex={0}>
        <table class="mc-table dt-table" aria-label="The catalogue">
          <thead>
            <tr>
              <th scope="col">Dataset</th>
              <th scope="col">Written by</th>
              <th scope="col" class="mc-num">
                Rows
              </th>
              <th scope="col">Contract</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((x) => (
              <tr key={x.id} aria-current={x.id === d.id ? 'true' : undefined}>
                <th scope="row">{x.id}</th>
                <td>{x.producer}</td>
                <td class="mc-num">{count(x.rows)}</td>
                <td>{x.contract ?? 'none'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="mc-hint">Rows count records: a JSONL line, or one JSON file where a file is one record. Last commit is the newest commit that touched the files.</p>
      <style>{DATA_CSS}</style>
    </section>
  );
}
