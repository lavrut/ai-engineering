// AuditTable (lesson 4): audit.json's rows, one per artefact per fact, each agree, disagree or missing (a symbol
// and a word), a filter to the disagreements, a count per artefact, and the gate's verdict as audit.py wrote it.
// Pure and browser-safe; ontologyData.ts reads audit.json at build time. Re-exported by ontologyAids.ts.
import { plural, type AuditGateJson, type AuditJson, type AuditRowJson, type AuditStatus, type MarkView } from './ontologyAidsTypes';

export type AuditTableProps = {
  /** audit.json's rows for the artefacts shown, unchanged and in its order. */
  rows: AuditRowJson[];
  /** The artefacts shown, in audit.json's order. */
  artefacts: string[];
  /** The whole audit's gate, even when the table shows fewer artefacts. */
  gate: AuditGateJson;
  narrowed: boolean;
};

/** Every artefact by default; a lesson may name some. An artefact the audit does not have fails the build. */
export function auditTableProps(audit: AuditJson, opts: { artefacts?: string[] } = {}): AuditTableProps {
  const known = [...new Set(audit.rows.map((r) => r.artefact))];
  const want = opts.artefacts ?? known;
  for (const a of want) {
    if (!known.includes(a)) throw new Error(`AuditTable: unknown artefact "${a}" (known: ${known.join(', ')})`);
  }
  const artefacts = known.filter((a) => want.includes(a));
  return { rows: audit.rows.filter((r) => artefacts.includes(r.artefact)), artefacts, gate: audit.gate, narrowed: artefacts.length < known.length };
}

const MARKS: Record<AuditStatus, MarkView> = {
  agree: { icon: '✓', word: 'AGREE', tone: 'ok' },
  disagree: { icon: '✗', word: 'DISAGREE', tone: 'fail' },
  missing: { icon: '?', word: 'MISSING', tone: 'warn' },
};
export const statusMark = (s: AuditStatus): MarkView => MARKS[s];

export function auditCounts(rows: AuditRowJson[]): Record<AuditStatus, number> {
  const n = (s: AuditStatus) => rows.filter((r) => r.status === s).length;
  return { agree: n('agree'), disagree: n('disagree'), missing: n('missing') };
}

/** The rows the filter keeps: every row, or only those that do not agree. */
export const auditShown = (rows: AuditRowJson[], disagreeOnly: boolean) => (disagreeOnly ? rows.filter((r) => r.status !== 'agree') : rows);

/** "71 facts from 7 artefacts: 70 agree, 1 disagree, 0 missing. Showing all 71." */
export function auditLine(p: AuditTableProps, disagreeOnly: boolean): string {
  const c = auditCounts(p.rows);
  const showing = disagreeOnly ? `Showing the ${auditShown(p.rows, true).length} that disagree or are missing.` : `Showing all ${p.rows.length}.`;
  return `${plural(p.rows.length, 'fact')} from ${plural(p.artefacts.length, 'artefact')}: ${c.agree} agree, ${c.disagree} disagree, ${c.missing} missing. ${showing}`;
}

/** The gate as a mark: RED when any fact disagrees, GREEN otherwise (missing facts are counted but allowed);
 *  the reason is shown as audit.py wrote it. */
export const gateMark = (g: AuditGateJson): MarkView => (g.ok ? { icon: '✓', word: 'GREEN', tone: 'ok' } : { icon: '✗', word: 'RED', tone: 'fail' });
