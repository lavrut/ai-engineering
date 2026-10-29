// The Data Fabric & Data Contracts track's aids: the shapes of the files they read, the number formats, and
// the verdict text every aid shares. Pure and browser-safe; dataData.ts reads the files at build time.
// Re-exported by dataAids.ts.

// ── The files ───────────────────────────────────────────────────────────────
/** One dataset in catalog.json (data/python/catalog.py). */
export type CatalogEntryJson = {
  bytes: number;
  consumers: string[];
  contract: string | null;
  description: string;
  files: number;
  last_commit: string | null;
  missing: boolean;
  path: string;
  producer: string;
  rows: number;
  /** A schema sketch: field name to JSON type, from the rows themselves. */
  schema: Record<string, string>;
};
export type CatalogJson = Record<string, CatalogEntryJson>;

export type LineageNodeJson = { id: string; kind: 'track' | 'consumer' | 'dataset'; track?: string; missing?: boolean };
export type LineageEdgeJson = { from: string; to: string; contract: string | null; version: string | null };
export type LineageJson = { nodes: LineageNodeJson[]; edges: LineageEdgeJson[] };

/** One verdict, as both validators print and quality.py stores it. */
export type VerdictJson = { rule: string; field: string | null; ok: boolean; detail: string };
/** One checked file (or trace set) in verdicts.json. `expect: 'fail'` marks the v1 file checked against v2. */
export type VerdictEntryJson = {
  age_days: number | null;
  contract: string;
  expect?: 'fail';
  files?: number;
  ok: boolean;
  path: string;
  verdicts: VerdictJson[];
};
export type VerdictsJson = { as_of: string; datasets: Record<string, VerdictEntryJson> };

export type GateRowJson = { name: string; declared: string; contract_exists: boolean; verdicts_ok: boolean; sunset: string | null; warning: boolean };
export type GateJson = { consumers: GateRowJson[]; ok: boolean; reason: string };

/** A contract as contract.py --export writes it (data/contracts/<id>.contract.json). */
export type FieldJson = { name: string; type: string; required?: boolean; enum?: string[]; pattern?: string; meaning?: string };
export type ContractJson = {
  info: { id: string; version: string; status: string; owner: string; dataset: string; description: string; sunset?: string; migration?: string };
  schema: { fields: FieldJson[] };
  quality: { row_count?: { min?: number; max?: number }; unique?: string[]; enum_coverage?: string[] };
  servicing: { freshness?: { max_age_days: number } };
  consumers: { name: string; version: string; reads: string }[];
};

export type DataData = {
  catalog: CatalogJson;
  lineage: LineageJson;
  verdicts: VerdictsJson;
  gate: GateJson;
  /** By contract id: tickets.v1, tickets.v2, complaints.v1, agent-traces.v1. */
  contracts: Record<string, ContractJson>;
};

/** What rewrites each derived file (named when a file is missing). */
export const DATA_COMMANDS = {
  catalog: '.venv-data/bin/python data/python/catalog.py',
  quality: '.venv-data/bin/python data/python/quality.py',
  gate: '.venv-data/bin/python data/python/gate.py',
} as const;

// ── Formats ─────────────────────────────────────────────────────────────────
/** 3600 -> "3,600". */
export const count = (n: number): string => n.toLocaleString('en-US');

/** An age as verdicts.json stores it (one decimal at most): "1.8 days", "1 day", "unknown" for no git date. */
export function days(x: number | null): string {
  if (x === null) return 'unknown';
  return `${x} ${x === 1 ? 'day' : 'days'}`;
}

/** ["agents", "app", "mcp"] -> "agents, app and mcp"; [] -> "no one". */
export function listText(xs: string[]): string {
  if (xs.length === 0) return 'no one';
  if (xs.length === 1) return xs[0];
  return `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`;
}

/** 1 -> "1 row", 3600 -> "3,600 rows". */
export const plural = (n: number, word: string) => `${count(n)} ${n === 1 ? word : `${word}s`}`;

// ── Verdicts ────────────────────────────────────────────────────────────────
export const SECTIONS = ['info', 'schema', 'quality', 'servicing', 'consumers'] as const;
export type Section = (typeof SECTIONS)[number];

const RULE_SECTION: Record<string, Section> = {
  required: 'schema',
  type: 'schema',
  enum: 'schema',
  pattern: 'schema',
  extra_field: 'schema',
  row_count: 'quality',
  unique: 'quality',
  enum_coverage: 'quality',
  freshness: 'servicing',
};

/** The contract section that declares a rule; null for `parse` and `contract`, which are about the file itself. */
export const ruleSection = (rule: string): Section | null => RULE_SECTION[rule] ?? null;

export type VerdictView = { mark: 'OK' | 'FAIL'; ok: boolean; rule: string; field: string; detail: string; section: Section | null };

/** A verdict as an aid shows it: OK or FAIL in words (beside a ✓ or ✗), never colour alone. */
export function verdictView(v: VerdictJson): VerdictView {
  return { mark: v.ok ? 'OK' : 'FAIL', ok: v.ok, rule: v.rule, field: v.field ?? '(whole file)', detail: v.detail, section: ruleSection(v.rule) };
}

export const markIcon = (ok: boolean) => (ok ? '✓' : '✗');

export function tally(vs: VerdictJson[]): { ok: number; failed: number } {
  const ok = vs.filter((v) => v.ok).length;
  return { ok, failed: vs.length - ok };
}

/** One checked file or trace set, as QualityReport and ContractViewer carry it (verdicts.json's entry, unrewritten). */
export type QualityEntryView = {
  /** The verdicts.json key: "tickets", "agent-traces/desk-hand". */
  key: string;
  /** What the aid calls it: the key, or the trace set's name. */
  label: string;
  contract: string;
  path: string;
  files: number | null;
  age: number | null;
  ok: boolean;
  /** The entry is meant to fail (the v1 file checked against v2). */
  expectFail: boolean;
  verdicts: VerdictJson[];
};

export function entryView(key: string, e: VerdictEntryJson): QualityEntryView {
  return {
    key,
    label: key.startsWith('agent-traces/') ? key.slice('agent-traces/'.length) : key,
    contract: e.contract,
    path: e.path,
    files: e.files ?? null,
    age: e.age_days,
    ok: e.ok,
    expectFail: e.expect === 'fail',
    verdicts: e.verdicts,
  };
}

/** "tickets against tickets.v1: 21 verdicts, 21 OK, 0 FAIL." */
export function entrySummary(e: QualityEntryView): string {
  const t = tally(e.verdicts);
  return `${e.label} against ${e.contract}: ${plural(e.verdicts.length, 'verdict')}, ${t.ok} OK, ${t.failed} FAIL.`;
}

/** PASS, FAIL, or "FAIL, as expected" for the entry that checks the old file against the new contract. */
export function entryStatus(e: QualityEntryView): string {
  if (e.ok) return e.expectFail ? 'PASS, though a failure was expected' : 'PASS';
  return e.expectFail ? 'FAIL, as expected' : 'FAIL';
}
