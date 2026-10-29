import { useState } from 'preact/hooks';
import {
  artefactLine,
  diffLines,
  manualNote,
  passMark,
  validationLine,
  type GeneratedReportProps,
  type GroundingReportProps,
  type ValidationReportProps,
} from '../../lib/ontologyAids';
import Picker from '../agents/Picker';
import OntologyMark from './OntologyMark';
import { ONTOLOGY_CSS } from './ontologyStyle';

const DIFF_CLASS = { header: 'on-diff-meta', hunk: 'on-diff-meta', added: 'on-diff-add', removed: 'on-diff-del', same: undefined } as const;

function Generated({ artefacts }: GeneratedReportProps) {
  const [id, setId] = useState(artefacts[0].id);
  const a = artefacts.find((x) => x.id === id) ?? artefacts[0];
  return (
    <>
      <fieldset class="ag-pickers">
        <legend>Generated from the ontology</legend>
        <Picker label="Artefact" value={a.id} options={artefacts.map((x) => ({ id: x.id, label: x.id }))} onChange={setId} />
      </fieldset>
      <p class="mc-live" aria-live="polite">
        {artefactLine(a)}
      </p>
      {a.note && <p class="mc-hint">{a.note}</p>}
      <div class="dt-split">
        <div>
          <p class="mc-pre-label">Existing: {a.existingFrom}</p>
          <pre class={`mc-pre on-pre mc-scroll mc-focus${a.existingKind === 'text' ? ' on-wrap' : ''}`} role="region" aria-label={`Existing: ${a.existingFrom}`} tabIndex={0}>
            {a.existing}
          </pre>
        </div>
        <div>
          <p class="mc-pre-label">Generated: {a.generatedFrom}</p>
          <pre class={`mc-pre on-pre mc-scroll mc-focus${a.generatedKind === 'text' ? ' on-wrap' : ''}`} role="region" aria-label={`Generated: ${a.generatedFrom}`} tabIndex={0}>
            {a.generated}
          </pre>
        </div>
      </div>
      <p class="mc-pre-label">The diff: - is the existing line, + the generated one</p>
      <pre class="on-diff mc-scroll mc-focus" role="region" aria-label="Diff" tabIndex={0}>
        {diffLines(a.diff).map((l, i) => (
          <span key={i} class={DIFF_CLASS[l.kind]}>
            {l.text || ' '}
          </span>
        ))}
      </pre>
      <p class="mc-hint">Each line keeps its sign, so the diff reads the same without colour.</p>
    </>
  );
}

function Validation(p: ValidationReportProps) {
  const [failing, setFailing] = useState(false);
  const rows = failing ? p.sources.filter((s) => !s.ok) : p.sources;
  return (
    <>
      <fieldset class="ag-pickers">
        <legend>Sources</legend>
        <button type="button" class="mc-btn" aria-pressed={failing ? 'true' : 'false'} onClick={() => setFailing(!failing)}>
          Show failing sources only
        </button>
      </fieldset>
      <p class="mc-live" aria-live="polite">
        {validationLine(p, failing)}
      </p>
      <div class="mc-scroll mc-focus" role="region" aria-label="Sources table" tabIndex={0}>
        <table class="mc-table on-table" aria-label="Validation per source">
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col" class="mc-num">
                Records
              </th>
              <th scope="col" class="mc-num">
                Violations
              </th>
              <th scope="col">Records that broke a rule</th>
              <th scope="col" class="mc-num">
                Manual
              </th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.name} class={s.ok ? undefined : 'dt-row-fail'}>
                <th scope="row">{s.name}</th>
                <td class="mc-num">{s.records}</td>
                <td class="mc-num">{s.violations.reduce((a, [, n]) => a + n, 0)}</td>
                <td>{s.violating.length ? s.violating.join(', ') : '—'}</td>
                <td class="mc-num">{s.manual}</td>
                <td>
                  <OntologyMark mark={passMark(s.ok)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div class="mc-scroll mc-focus" role="region" aria-label="Violation types table" tabIndex={0}>
        <table class="mc-table on-table" aria-label="Violations per type">
          <thead>
            <tr>
              <th scope="col">Checkable constraint</th>
              <th scope="col" class="mc-num">
                Violations
              </th>
              <th scope="col" class="mc-num">
                Sources
              </th>
            </tr>
          </thead>
          <tbody>
            {p.types.map((t) => (
              <tr key={t.rule}>
                <th scope="row">{t.rule}</th>
                <td class="mc-num">{t.count}</td>
                <td class="mc-num">{t.sources}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {p.types.map((t) => (
        <blockquote key={t.rule} class="dt-quote">
          {t.rule}: {t.text}
        </blockquote>
      ))}
      <p class="mc-hint">{manualNote(p)}</p>
    </>
  );
}

export default function GroundingReport(props: GroundingReportProps) {
  return (
    <section class="mc" aria-label="Grounding report">
      <header class="mc-head">
        <p class="mc-eyebrow">{props.show === 'generated' ? 'Generated from the ontology' : 'Model outputs against the ontology'}</p>
        <p class="mc-label">Read from {props.show === 'generated' ? 'generated.json' : 'validation.json'}: no calls from this page</p>
      </header>
      {props.show === 'generated' ? <Generated {...props} /> : <Validation {...props} />}
      <style>{ONTOLOGY_CSS}</style>
    </section>
  );
}
