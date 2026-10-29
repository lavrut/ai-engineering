// The Ontology track's aids: the shapes of the five files they read (ontology/data/derived/*.json), the commands
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
/** Section 6: the conditions a permission may name, the tool -> action table, and the permitted rows. */
export type ActionsJson = {
  conditions: { evaluated_from: string | string[]; id: string; text: string }[];
  tools: Record<string, string>;
  permitted: { action: string; conditions?: string[]; role: string }[];
};
export type OntologyJson = {
  source: string;
  classes: ClassJson[];
  properties: PropertyJson[];
  relations: RelationJson[];
  individuals: IndividualJson[];
  constraints: ConstraintJson[];
  actions: ActionsJson;
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

// ── governance.json (governance.py) ─────────────────────────────────────────
export type VerdictCounts = { conditional: number; not_permitted: number; permitted: number };
export type EventCounts = VerdictCounts & { events: number };
export type MatrixRule = 'permitted' | 'conditional' | 'no';
/** One role x action cell: the rule section 6 gives it, the conditions it names, and the recorded events it judged,
 *  in all and per engine; `unmet` counts the conditional events per condition that was not met; a write cell's
 *  `landed` counts the calls that reached the tools (not is_error). */
export type MatrixCellJson = {
  action: string;
  by_engine: Record<string, EventCounts>;
  conditions: string[];
  events: number;
  landed?: number;
  role: string;
  rule: MatrixRule;
  unmet: Record<string, number>;
  verdicts: VerdictCounts;
};
export type GovernanceGateJson = { ok: boolean; reason: string };
export type GovernanceJson = {
  actions: string[];
  approvals: Record<string, number>;
  by_engine: Record<string, Record<string, EventCounts>>;
  conditions: { evaluated_from: string | string[]; id: string; text: string }[];
  gate: GovernanceGateJson;
  matrix: MatrixCellJson[];
  roles: string[];
  skipped: { file: string; reason: string }[];
  tools: Record<string, string>;
  totals: { conditional: number; desk_runs: number; events: number; not_permitted: number; permitted: number; skipped: number; traces: number; unreadable: number; writes_landed: number };
  violations: { action: string | null; detail: string; file: string; role: string; tool: string }[];
};

export type OntologyData = { ontology: OntologyJson; generated: GeneratedJson; validation: ValidationJson; audit: AuditJson; governance: GovernanceJson };

/** What rewrites each derived file (named when a file is missing). */
export const ONTOLOGY_COMMANDS = {
  ontology: '.venv-ontology/bin/python ontology/python/ontology.py export',
  generated: '.venv-ontology/bin/python ontology/python/generate.py',
  validation: '.venv-ontology/bin/python ontology/python/grounding.py',
  audit: '.venv-ontology/bin/python ontology/python/audit.py',
  governance: '.venv-ontology/bin/python ontology/python/governance.py',
} as const;

// ── Marks ───────────────────────────────────────────────────────────────────
export type Tone = 'ok' | 'fail' | 'warn';
/** A mark is a symbol and a word, with colour only as a border: never colour alone. */
export type MarkView = { icon: string; word: string; tone: Tone };
export const passMark = (ok: boolean): MarkView => (ok ? { icon: '✓', word: 'PASS', tone: 'ok' } : { icon: '✗', word: 'FAIL', tone: 'fail' });
