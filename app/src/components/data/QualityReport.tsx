import { useState } from 'preact/hooks';
import { days, entryStatus, markIcon, reportLine, tally, verdictView, type QualityReportProps, type VerdictView } from '../../lib/dataAids';
import Picker from '../agents/Picker';
import { DATA_CSS } from './dataStyle';

/** ✓ OK or ✗ FAIL: a symbol and a word, with the colour only as a border. */
export function Mark({ ok, text }: { ok: boolean; text: string }) {
  return (
    <span class={`dt-mark ${ok ? 'dt-mark-ok' : 'dt-mark-fail'}`}>
      {markIcon(ok)} {text}
    </span>
  );
}

/** One verdict per row: result, rule, field, detail. */
export function VerdictRows({ rows }: { rows: VerdictView[] }) {
  return (
    <tbody>
      {rows.map((v, i) => (
        <tr key={i} class={v.ok ? undefined : 'dt-row-fail'}>
          <td>
            <Mark ok={v.ok} text={v.mark} />
          </td>
          <td>{v.rule}</td>
          <td>{v.field}</td>
          <td>{v.detail}</td>
        </tr>
      ))}
    </tbody>
  );
}

export const VerdictHead = () => (
  <thead>
    <tr>
      <th scope="col">Result</th>
      <th scope="col">Rule</th>
      <th scope="col">Field</th>
      <th scope="col">Detail</th>
    </tr>
  </thead>
);

export default function QualityReport({ dataset, asOf, entries, grouped }: QualityReportProps) {
  const [key, setKey] = useState(entries[0].key);
  const [failsOnly, setFailsOnly] = useState(false);
  const e = entries.find((x) => x.key === key) ?? entries[0];
  const shown = (failsOnly ? e.verdicts.filter((v) => !v.ok) : e.verdicts).map(verdictView);

  return (
    <section class="mc" aria-label="Quality report">
      <header class="mc-head">
        <p class="mc-eyebrow">Quality and freshness · {dataset}</p>
        <p class="mc-label">Read from verdicts.json: no calls from this page</p>
      </header>

      {grouped && (
        <>
          <div class="mc-scroll mc-focus" role="region" aria-label="Trace sets table" tabIndex={0}>
            <table class="mc-table dt-table" aria-label="Trace sets">
              <thead>
                <tr>
                  <th scope="col">Set</th>
                  <th scope="col" class="mc-num">Files</th>
                  <th scope="col" class="mc-num">Verdicts</th>
                  <th scope="col" class="mc-num">OK</th>
                  <th scope="col" class="mc-num">FAIL</th>
                  <th scope="col" class="mc-num">Age</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((x) => {
                  const t = tally(x.verdicts);
                  return (
                    <tr key={x.key} aria-current={x.key === e.key ? 'true' : undefined}>
                      <th scope="row">{x.label}</th>
                      <td class="mc-num">{x.files ?? 1}</td>
                      <td class="mc-num">{x.verdicts.length}</td>
                      <td class="mc-num">{t.ok}</td>
                      <td class="mc-num">{t.failed}</td>
                      <td class="mc-num">{days(x.age)}</td>
                      <td>
                        <Mark ok={x.ok} text={entryStatus(x)} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p class="mc-hint">Each trace set is checked as one dataset: every file in it is validated, and a rule passes only when it passes in every file.</p>
        </>
      )}

      <fieldset class="ag-pickers">
        <legend>Verdicts</legend>
        {grouped && <Picker label="Trace set" value={e.key} options={entries.map((x) => ({ id: x.key, label: x.label }))} onChange={setKey} />}
        <button type="button" class="mc-btn" aria-pressed={failsOnly ? 'true' : 'false'} onClick={() => setFailsOnly(!failsOnly)}>
          Show failures only
        </button>
      </fieldset>

      <div class={`dt-status${e.ok ? '' : ' dt-status-bad'}`} role="status">
        <p class="dt-verdict">
          <Mark ok={e.ok} text={entryStatus(e)} />
        </p>
        <p>
          {e.path}: {days(e.age)} old as of {asOf}, the date verdicts.json was written (every age is pinned to it).
        </p>
        {e.expectFail && <p>This entry checks the old file against the new contract on purpose: it shows what would break.</p>}
      </div>

      <p class="mc-live" aria-live="polite">
        {reportLine(e, failsOnly)}
      </p>

      {shown.length === 0 ? (
        <p class="mc-hint">No verdict failed.</p>
      ) : (
        <div class="mc-scroll mc-focus" role="region" aria-label="Verdicts table" tabIndex={0}>
          <table class="mc-table dt-table" aria-label={`Verdicts for ${e.label}`}>
            <VerdictHead />
            <VerdictRows rows={shown} />
          </table>
        </div>
      )}
      <p class="mc-hint">One verdict per rule and field, sorted by rule, as both validators print them. (whole file) marks a rule about the file, not one field.</p>
      <style>{DATA_CSS}</style>
    </section>
  );
}
