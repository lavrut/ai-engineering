// The Ontology track's aids: the shapes of the four files they read (ontology/data/derived/*.json), the commands
// that rewrite them, and the number formats every aid shares. Pure and browser-safe; ontologyData.ts reads the
// files at build time. Re-exported by ontologyAids.ts.
export { count, listText, plural } from './dataAidsTypes';

// ── ontology.json (ontology.py export) ──────────────────────────────────────
export type ClassJson = { definition: string; id: string; label: string };
/** A literal type name ("string", "integer", ...), a closed list of a class's individuals, or a closed list of values. */
export type RangeJson = string | { individuals: string[] } | { values: string[] };
export type PropertyJson = { cardinality: 'one' | 'many'; domain: string; id: string; pattern?: string; range: RangeJson; required?: boolean };
export type RelationJson = { cardinality: 'one' | 'many'; domain: string; id: string; label: string; range: string };
export type IndividualJson = { class: string; definition: string; id: string; label?: string; order: number; source: string };
/** One condition of a checkable constraint: `category eq feature_request`, `priority in [normal, low]`. */
export type ConditionJson = { op: string; property: string; value: unknown };
export type ConstraintJson = {
  class: string;
  id: string;
  kind: 'checkable' | 'manual';
  phrases?: string[];
  source: string;
  text: string;
  then?: ConditionJson[];
  when?: ConditionJson[];
};
export type OntologyJson = {
  source: string;
  classes: ClassJson[];
  properties: PropertyJson[];
  relations: RelationJson[];
  individuals: IndividualJson[];
  constraints: ConstraintJson[];
};

// ── generated.json (generate.py) ────────────────────────────────────────────
export const ARTEFACT_IDS = ['definitions', 'schema', 'tool'] as const;
export type ArtefactId = (typeof ARTEFACT_IDS)[number];
/** The hand-written artefact beside the one generated from the ontology, and the unified diff between them. */
export type CompareJson = { diff: string; existing: unknown; generated: unknown; v3_rule_lines_equal_v2?: boolean };
export type GeneratedJson = { compare: Record<ArtefactId, CompareJson>; definitions_text: string; json_schema: unknown; tool_definition: unknown };

// ── validation.json (grounding.py) ──────────────────────────────────────────
/** One source of saved model outputs: its records, the checkable constraints they broke (by id, a count), the
 *  ids of the records that broke one, and per manual constraint the number of records it applies to. */
export type SourceJson = { manual: Record<string, number>; records: number; violating: string[]; violations: Record<string, number> };
export type ValidationJson = { sources: Record<string, SourceJson>; totals: { records: number; violations: Record<string, number> } };

// ── audit.json (audit.py) ───────────────────────────────────────────────────
export type AuditStatus = 'agree' | 'disagree' | 'missing';
export type AuditRowJson = { artefact: string; detail: string; fact: string; status: AuditStatus };
export type AuditGateJson = { disagree: number; missing: number; ok: boolean; reason: string };
export type AuditJson = { gate: AuditGateJson; rows: AuditRowJson[] };

export type OntologyData = { ontology: OntologyJson; generated: GeneratedJson; validation: ValidationJson; audit: AuditJson };

/** What rewrites each derived file (named when a file is missing). */
export const ONTOLOGY_COMMANDS = {
  ontology: '.venv-ontology/bin/python ontology/python/ontology.py export',
  generated: '.venv-ontology/bin/python ontology/python/generate.py',
  validation: '.venv-ontology/bin/python ontology/python/grounding.py',
  audit: '.venv-ontology/bin/python ontology/python/audit.py',
} as const;

// ── Marks ───────────────────────────────────────────────────────────────────
export type Tone = 'ok' | 'fail' | 'warn';
/** A mark is a symbol and a word, with colour only as a border: never colour alone. */
export type MarkView = { icon: string; word: string; tone: Tone };
export const passMark = (ok: boolean): MarkView => (ok ? { icon: '✓', word: 'PASS', tone: 'ok' } : { icon: '✗', word: 'FAIL', tone: 'fail' });
