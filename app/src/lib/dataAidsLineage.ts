// LineageDiff (lesson 6): two versions of one contract side by side: which consumer is on which (from the
// contracts' consumers sections and gate.json's rows), the deprecated version's sunset and warning, the
// migration edge between the two datasets (lineage.json), what changed in the schema, and the gate's verdict
// exactly as gate.py wrote it. Pure and browser-safe; dataData.ts reads the files at build time. Re-exported by
// dataAids.ts.
import { type ContractJson, type DataData, type FieldJson, type LineageEdgeJson } from './dataAidsTypes';

export type VersionView = {
  id: string;
  version: string;
  status: string;
  sunset: string | null;
  /** The file or glob the contract governs (info.dataset). */
  path: string;
  /** The catalogued dataset under this contract. */
  dataset: string;
  consumers: { name: string; reads: string }[];
};

export type ConsumerRow = { name: string; declared: string; green: boolean; sunset: string | null; warning: boolean };
export type FieldChange = { field: string; change: string };

export type LineageDiffProps = {
  from: VersionView;
  to: VersionView;
  asOf: string;
  /** The dataset-to-dataset edge from the old file to the new one, as lineage.json has it. */
  migration: LineageEdgeJson | null;
  /** The new version's own migration note (info.migration), verbatim. */
  migrationNote: string | null;
  fields: FieldChange[];
  consumers: ConsumerRow[];
  gate: { ok: boolean; reason: string };
};

const baseId = (id: string) => id.replace(/\.v\d+$/, '');

function versionView(data: DataData, c: ContractJson): VersionView {
  const dataset = Object.keys(data.catalog).find((k) => data.catalog[k].contract === c.info.id);
  if (!dataset) throw new Error(`LineageDiff: no catalogued dataset is under ${c.info.id}`);
  return {
    id: c.info.id,
    version: c.info.version,
    status: c.info.status,
    sunset: c.info.sunset ?? null,
    path: c.info.dataset,
    dataset,
    consumers: c.consumers.map(({ name, reads }) => ({ name, reads })),
  };
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** What changed for one field present in both versions: type, required, enum values, pattern. */
function changed(a: FieldJson, b: FieldJson): string | null {
  const out: string[] = [];
  if (a.type !== b.type) out.push(`type ${a.type} becomes ${b.type}`);
  if (Boolean(a.required) !== Boolean(b.required)) out.push(b.required ? 'becomes required' : 'becomes optional');
  if (!same(a.enum, b.enum)) {
    const gained = (b.enum ?? []).filter((x) => !(a.enum ?? []).includes(x));
    const lost = (a.enum ?? []).filter((x) => !(b.enum ?? []).includes(x));
    if (gained.length) out.push(`enum gains ${gained.join(', ')}`);
    if (lost.length) out.push(`enum loses ${lost.join(', ')}`);
  }
  if (a.pattern !== b.pattern) out.push(`pattern ${a.pattern ?? 'none'} becomes ${b.pattern ?? 'none'}`);
  return out.length ? out.join('; ') : null;
}

/** The field changes, by field name: a field in both versions where it changed, and a field only in one. */
export function fieldChanges(a: ContractJson, b: ContractJson): FieldChange[] {
  const inB = new Map(b.schema.fields.map((f) => [f.name, f]));
  const inA = new Set(a.schema.fields.map((f) => f.name));
  const out: FieldChange[] = [];
  for (const f of a.schema.fields) {
    const g = inB.get(f.name);
    if (!g) out.push({ field: f.name, change: `only in ${a.info.id}` });
    else {
      const c = changed(f, g);
      if (c) out.push({ field: f.name, change: c });
    }
  }
  for (const f of b.schema.fields) if (!inA.has(f.name)) out.push({ field: f.name, change: `only in ${b.info.id}` });
  return out.sort((x, y) => (x.field < y.field ? -1 : x.field > y.field ? 1 : 0));
}

export function lineageDiffProps(data: DataData, opts: { from: string; to: string }): LineageDiffProps {
  for (const id of [opts.from, opts.to]) {
    if (!data.contracts[id]) throw new Error(`LineageDiff: no contract "${id}" (known: ${Object.keys(data.contracts).sort().join(', ')})`);
  }
  if (baseId(opts.from) !== baseId(opts.to)) throw new Error(`LineageDiff: ${opts.from} and ${opts.to} are not versions of one contract`);
  const a = data.contracts[opts.from];
  const b = data.contracts[opts.to];
  const from = versionView(data, a);
  const to = versionView(data, b);
  const migration = data.lineage.edges.find((e) => e.from === from.dataset && e.to === to.dataset) ?? null;
  const consumers = data.gate.consumers
    .filter((g) => g.declared === opts.from || g.declared === opts.to)
    .map((g) => ({ name: g.name, declared: g.declared, green: g.contract_exists && g.verdicts_ok, sunset: g.sunset, warning: g.warning }));
  return {
    from,
    to,
    asOf: data.verdicts.as_of,
    migration,
    migrationNote: b.info.migration ?? null,
    fields: fieldChanges(a, b),
    consumers,
    gate: { ok: data.gate.ok, reason: data.gate.reason },
  };
}

export const gateHeadline = (g: LineageDiffProps['gate']) => (g.ok ? 'Gate: green' : 'Gate: red');

/** "mcp-read-server reads tickets.v1: OK. Sunset 2027-01-31; as of 2026-10-28 that is still ahead, so no warning yet." */
export function consumerText(r: ConsumerRow, asOf: string): string {
  const head = `${r.name} reads ${r.declared}: ${r.green ? 'OK' : 'FAIL'}.`;
  if (r.warning) return `${head} Past its sunset ${r.sunset}: a warning, not a failure.`;
  if (r.sunset) return `${head} Sunset ${r.sunset}; as of ${asOf} that is still ahead, so no warning yet.`;
  return head;
}
