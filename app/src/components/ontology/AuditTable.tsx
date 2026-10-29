import { useState } from 'preact/hooks';
import { auditCounts, auditLine, auditShown, gateMark, statusMark, type AuditTableProps } from '../../lib/ontologyAids';
import OntologyMark from './OntologyMark';
import { ONTOLOGY_CSS } from './ontologyStyle';

export default function AuditTable(p: AuditTableProps) {
  const [only, setOnly] = useState(false);
  const rows = auditShown(p.rows, only);
  const g = p.gate;

  return (
    <section class="mc" aria-label="Audit table">
      <header class="mc-head">
        <p class="mc-eyebrow">The audit · every artefact against the ontology</p>
        <p class="mc-label">Read from audit.json: no calls from this page</p>
      </header>

      <div class={`dt-status${g.ok ? '' : ' dt-status-bad'}`} role="status" aria-live="off">
        <p class="dt-verdict">
          <OntologyMark mark={gateMark(g)} />
        </p>
        <p>
          <span class="mc-pre-label">The gate's verdict, as audit.py wrote it: </span>
          {g.reason}
        </p>
        {p.narrowed && <p>The gate counts every artefact, not only the {p.artefacts.length} shown.</p>}
      </div>

      <fieldset class="ag-pickers">
        <legend>Facts</legend>
        <button type="button" class="mc-btn" aria-pressed={only ? 'true' : 'false'} onClick={() => setOnly(!only)}>
          Show disagreements only
        </button>
      </fieldset>
      <p class="mc-live" aria-live="polite">
        {auditLine(p, only)}
      </p>

      {rows.length === 0 ? (
        <p class="mc-hint">Every fact agrees.</p>
      ) : (
        <div class="mc-scroll mc-focus" role="region" aria-label="Audit rows table" tabIndex={0}>
          <table class="mc-table on-table" aria-label="Audit rows">
            <thead>
              <tr>
                <th scope="col">Artefact</th>
                <th scope="col">Fact</th>
                <th scope="col">Status</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.artefact} ${r.fact}`} class={r.status === 'agree' ? undefined : 'dt-row-fail'}>
                  <td>{r.artefact}</td>
                  <th scope="row">{r.fact}</th>
                  <td>
                    <OntologyMark mark={statusMark(r.status)} />
                  </td>
                  <td>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p class="mc-hint">Disagreements only shows the missing facts too. A fact names what was compared: enum:category is the category field's allowed values.</p>

      <div class="mc-scroll mc-focus" role="region" aria-label="Per artefact table" tabIndex={0}>
        <table class="mc-table on-table" aria-label="Audit per artefact">
          <thead>
            <tr>
              <th scope="col">Artefact</th>
              <th scope="col" class="mc-num">Agree</th>
              <th scope="col" class="mc-num">Disagree</th>
              <th scope="col" class="mc-num">Missing</th>
            </tr>
          </thead>
          <tbody>
            {p.artefacts.map((a) => {
              const c = auditCounts(p.rows.filter((r) => r.artefact === a));
              return (
                <tr key={a}>
                  <th scope="row">{a}</th>
                  <td class="mc-num">{c.agree}</td>
                  <td class="mc-num">{c.disagree}</td>
                  <td class="mc-num">{c.missing}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <style>{ONTOLOGY_CSS}</style>
    </section>
  );
}
