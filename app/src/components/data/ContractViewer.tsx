import { useState } from 'preact/hooks';
import { SECTIONS, fieldRule, infoLines, qualityLines, sectionLine, verdictView, verdictsFrom, type ContractViewerProps } from '../../lib/dataAids';
import Picker from '../agents/Picker';
import { useStepper } from '../mcp/useStepper';
import { DATA_CSS } from './dataStyle';
import { VerdictHead, VerdictRows } from './QualityReport';

type Row = string[];

/** The rows of one section, under its column names. */
function sectionTable(c: ContractViewerProps['contract'], i: number): { cols: string[]; rows: Row[] } {
  switch (SECTIONS[i]) {
    case 'info':
      return { cols: ['Key', 'Value'], rows: infoLines(c) };
    case 'schema':
      return {
        cols: ['Field', 'Type', 'Required', 'Rule', 'Meaning'],
        rows: c.schema.fields.map((f) => [f.name, f.type, f.required ? 'yes' : 'no', fieldRule(f), f.meaning ?? '']),
      };
    case 'quality':
      return { cols: ['Rule', 'Value'], rows: qualityLines(c.quality) };
    case 'servicing':
      return { cols: ['Promise', 'Value'], rows: c.servicing.freshness ? [['freshness', `at most ${c.servicing.freshness.max_age_days} days old`]] : [] };
    default:
      return { cols: ['Consumer', 'Version', 'Reads'], rows: c.consumers.map((x) => [x.name, x.version, x.reads]) };
  }
}

export default function ContractViewer({ contract, checks }: ContractViewerProps) {
  const s = useStepper(SECTIONS.length);
  const [key, setKey] = useState(checks[0]?.key ?? '');
  const entry = checks.find((e) => e.key === key) ?? checks[0];
  const section = SECTIONS[s.at];
  const table = sectionTable(contract, s.at);
  const from = entry ? verdictsFrom(entry, section).map(verdictView) : [];

  return (
    <section class="mc" aria-label="Contract viewer">
      <header class="mc-head">
        <p class="mc-eyebrow">
          {contract.info.id} · {contract.info.status}
        </p>
        <p class="mc-label">Read from {contract.info.id}.contract.json: no calls from this page</p>
      </header>

      <div class="mc-focus ag-group" role="group" aria-label="Sections" tabIndex={0} onKeyDown={s.onKeyDown}>
        <div class="mc-buttons">
          <button ref={s.prevRef} type="button" class="mc-btn" disabled={s.at <= 0} onClick={s.prev}>
            Previous
          </button>
          <button ref={s.nextRef} type="button" class="mc-btn mc-btn-primary" disabled={s.at >= s.last} onClick={s.next}>
            Next
          </button>
          <button ref={s.resetRef} type="button" class="mc-btn" onClick={s.reset}>
            Reset
          </button>
        </div>
        <ol class="mc-steps" aria-label="Sections in order">
          {SECTIONS.map((x, i) => (
            <li key={x} aria-current={i === s.at ? 'step' : undefined}>
              {i === s.at ? '▸ ' : ''}
              {i + 1}. {x}
            </li>
          ))}
        </ol>
      </div>

      <p class="mc-live" aria-live="polite">
        {sectionLine(contract, s.at, entry)}
      </p>

      <div class="dt-split">
        <div>
          <h3 class="mc-h">
            {s.at + 1}. {section}
          </h3>
          <div class="mc-scroll mc-focus" role="region" aria-label="Contract section table" tabIndex={0}>
            <table class="mc-table dt-table" aria-label={`${section} of ${contract.info.id}`}>
              <thead>
                <tr>
                  {table.cols.map((c) => (
                    <th key={c} scope="col">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((r, i) => (
                  <tr key={i}>
                    <th scope="row">{r[0]}</th>
                    {r.slice(1).map((v, j) => (
                      <td key={j}>{v}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {entry && (
          <div>
            <h3 class="mc-h">Verdicts from this section</h3>
            {checks.length > 1 && (
              <fieldset class="ag-pickers">
                <legend>Which file</legend>
                <Picker label="Checked file" value={entry.key} options={checks.map((e) => ({ id: e.key, label: e.key }))} onChange={setKey} />
              </fieldset>
            )}
            {from.length === 0 ? (
              <p class="mc-hint">The {section} section declares no rule, so no verdict comes from it.</p>
            ) : (
              <div class="mc-scroll mc-focus" role="region" aria-label="Section verdicts table" tabIndex={0}>
                <table class="mc-table dt-table" aria-label={`Verdicts on ${entry.key} from ${section}`}>
                  <VerdictHead />
                  <VerdictRows rows={from} />
                </table>
              </div>
            )}
            <p class="mc-hint">
              {entry.path}, checked against {contract.info.id} by quality.py; the Go validator prints the same lines.
            </p>
          </div>
        )}
      </div>
      <style>{DATA_CSS}</style>
    </section>
  );
}
