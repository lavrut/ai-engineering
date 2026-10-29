// GroundingReport (lesson 3), in two views. "generated": the three artefacts generate.py builds from the ontology
// (the prompt definitions, the JSON schema, the MCP tool) beside the hand-written ones, with the unified diff.
// "validation": what grounding.py found in the saved model outputs: counts per source and per violation type,
// pass and fail marked by text as well as colour, and the manual constraints as a count. Pure and browser-safe;
// ontologyData.ts reads generated.json, validation.json and ontology.json at build time. Re-exported by
// ontologyAids.ts.
import { ARTEFACT_IDS, listText, plural, type ArtefactId, type OntologyData } from './ontologyAidsTypes';

// ── generated ───────────────────────────────────────────────────────────────
/** One artefact. A JSON artefact ships pretty-printed (2 spaces) and parses back to generated.json's value. */
export type ArtefactView = {
  id: ArtefactId;
  /** From the diff's own header: "prompts.json triage.v2_rules", "generated definitions". */
  existingFrom: string;
  generatedFrom: string;
  existing: string;
  existingKind: 'text' | 'json';
  generated: string;
  generatedKind: 'text' | 'json';
  diff: string;
  added: number;
  removed: number;
  note: string | null;
};
export type GeneratedReportProps = { show: 'generated'; artefacts: ArtefactView[] };

export type DiffLine = { kind: 'header' | 'hunk' | 'added' | 'removed' | 'same'; text: string };

/** The unified diff a line at a time, each marked by its sign (+, -) as well as its kind. */
export function diffLines(diff: string): DiffLine[] {
  return diff
    .replace(/\n$/, '')
    .split('\n')
    .map((text) => {
      if (text.startsWith('+++') || text.startsWith('---')) return { kind: 'header', text };
      if (text.startsWith('@@')) return { kind: 'hunk', text };
      if (text.startsWith('+')) return { kind: 'added', text };
      if (text.startsWith('-')) return { kind: 'removed', text };
      return { kind: 'same', text };
    });
}

const shown = (v: unknown): [string, 'text' | 'json'] => (typeof v === 'string' ? [v, 'text'] : [JSON.stringify(v, null, 2), 'json']);

function artefactView(id: ArtefactId, d: OntologyData): ArtefactView {
  const c = d.generated.compare[id];
  if (!c) throw new Error(`generated.json: compare has no "${id}"`);
  const lines = diffLines(c.diff);
  const from = (sign: string) => lines.find((l) => l.text.startsWith(`${sign} `))?.text.slice(4) ?? '';
  const [existing, existingKind] = shown(c.existing);
  const [generated, generatedKind] = shown(c.generated);
  return {
    id,
    existingFrom: from('---'),
    generatedFrom: from('+++'),
    existing,
    existingKind,
    generated,
    generatedKind,
    diff: c.diff,
    added: lines.filter((l) => l.kind === 'added').length,
    removed: lines.filter((l) => l.kind === 'removed').length,
    note: c.v3_rule_lines_equal_v2 ? "The v3_examples prompt's rule lines are the same as v2_rules', so this one comparison covers both." : null,
  };
}

/** "schema: triage_lab.py TRIAGE_SCHEMA against generated json_schema: 7 lines added, 7 removed." */
export const artefactLine = (a: ArtefactView) => `${a.id}: ${a.existingFrom} against ${a.generatedFrom}: ${plural(a.added, 'line')} added, ${a.removed} removed.`;

// ── validation ──────────────────────────────────────────────────────────────
export type SourceView = {
  name: string;
  records: number;
  /** [constraint id, records that broke it], in validation.json's order. */
  violations: [string, number][];
  violating: string[];
  /** Constraints left to a person for these records: the manual ones, plus any checkable one they could not run. */
  manual: number;
  /** Checkable constraints these records could not run (a property they read is absent), so counted as manual. */
  unchecked: string[];
  ok: boolean;
};
export type ViolationTypeView = { rule: string; text: string; count: number; sources: number };
export type ValidationReportProps = {
  show: 'validation';
  records: number;
  violations: number;
  /** The ontology's manual constraints, and its checkable ones. */
  manualConstraints: number;
  checkable: number;
  sources: SourceView[];
  types: ViolationTypeView[];
};

const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

function validationProps(d: OntologyData): ValidationReportProps {
  const { sources: json, totals } = d.validation;
  const constraints = d.ontology.constraints;
  const checkableIds = constraints.filter((c) => c.kind === 'checkable').map((c) => c.id);
  const sources = Object.entries(json).map(([name, s]): SourceView => ({
    name,
    records: s.records,
    violations: Object.entries(s.violations),
    violating: s.violating,
    manual: Object.keys(s.manual).length,
    unchecked: Object.keys(s.manual).filter((id) => checkableIds.includes(id)),
    ok: Object.keys(s.violations).length === 0,
  }));
  // The totals are the sources added up; a file where they are not is stale, and the aid would contradict itself.
  const records = sum(sources.map((s) => s.records));
  if (records !== totals.records) throw new Error(`validation.json: the sources hold ${records} records, but totals says ${totals.records} (rerun grounding.py)`);
  const rules = [...new Set([...checkableIds, ...Object.keys(totals.violations)])];
  const types = rules.map((rule): ViolationTypeView => {
    const per = sources.map((s) => s.violations.find(([r]) => r === rule)?.[1] ?? 0);
    const count = sum(per);
    const want = totals.violations[rule] ?? 0;
    if (count !== want) throw new Error(`validation.json: the sources hold ${count} ${rule} violations, but totals says ${want} (rerun grounding.py)`);
    return { rule, text: constraints.find((c) => c.id === rule)?.text ?? '(not in the ontology)', count, sources: per.filter((n) => n > 0).length };
  });
  return {
    show: 'validation',
    records,
    violations: sum(types.map((t) => t.count)),
    manualConstraints: constraints.filter((c) => c.kind === 'manual').length,
    checkable: checkableIds.length,
    sources,
    types,
  };
}

export const sourceStatus = (s: SourceView) => (s.ok ? 'PASS' : 'FAIL');

/** "700 records from 16 sources: 7 violations of 1 checkable constraint, in 6 sources. Showing all 16 sources." */
export function validationLine(p: ValidationReportProps, failingOnly: boolean): string {
  const failing = p.sources.filter((s) => !s.ok).length;
  const showing = failingOnly ? `Showing the ${failing} that fail.` : `Showing all ${plural(p.sources.length, 'source')}.`;
  return (
    `${plural(p.records, 'record')} from ${plural(p.sources.length, 'source')}: ${plural(p.violations, 'violation')} of ` +
    `${plural(p.checkable, 'checkable constraint')}, in ${plural(failing, 'source')}. ${showing}`
  );
}

/** Why Manual reads 24 for most sources and more for some: a checkable constraint whose property is absent. */
export function manualNote(p: ValidationReportProps): string {
  const base = `${plural(p.manualConstraints, 'manual constraint')} are left to a person: no program can check them. Manual counts the constraints each source's records still need judged`;
  const more = p.sources.filter((s) => s.unchecked.length > 0);
  if (more.length === 0) return `${base}.`;
  const rules = [...new Set(more.flatMap((s) => s.unchecked))];
  const counts = [...new Set(more.map((s) => s.manual))].join(' or ');
  return `${base}; ${plural(more.length, 'source')} count ${counts}, because their records lack a property ${listText(rules)} reads, so it could not run either.`;
}

// ── Both ────────────────────────────────────────────────────────────────────
export type GroundingReportProps = GeneratedReportProps | ValidationReportProps;

export function groundingReportProps(d: OntologyData, opts: { show: 'generated' | 'validation' }): GroundingReportProps {
  if (opts.show === 'generated') return { show: 'generated', artefacts: ARTEFACT_IDS.map((id) => artefactView(id, d)) };
  if (opts.show === 'validation') return validationProps(d);
  throw new Error(`GroundingReport: show must be "generated" or "validation", not "${String(opts.show)}"`);
}
