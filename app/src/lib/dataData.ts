// The Data Fabric & Data Contracts track's data, read at build time: data/data/derived/{catalog,lineage}.json
// (data/python/catalog.py), verdicts.json (quality.py), gate.json (gate.py) and each contract's export,
// data/contracts/<id>.contract.json (contract.py --export).
//
// Build time only: the eager globs turn the files into imports, so an island must never import this module.
// The types and the logic are in dataAids.ts (pure, browser-safe); pass an island the small props it builds.
import { DATA_COMMANDS, type CatalogJson, type ContractJson, type DataData, type GateJson, type LineageJson, type VerdictsJson } from './dataAids';

export * from './dataAids';

let cache: DataData | undefined;

/** Fails loudly, naming the file and what writes it, when a file is missing or is not what the aids read. */
function check<T>(file: string, writer: string, value: unknown, keys: string[]): T {
  if (value === undefined) throw new Error(`dataData: ${file} is missing (${writer} writes it)`);
  if (typeof value !== 'object' || value === null) throw new Error(`dataData: ${file} is not a JSON object`);
  for (const k of keys) {
    if (!(k in value)) throw new Error(`dataData: ${file} has no "${k}" (rerun ${writer})`);
  }
  return value as T;
}

export function loadDataData(): DataData {
  if (!cache) {
    const derived = import.meta.glob('../../../data/data/derived/*.json', { import: 'default', eager: true }) as Record<string, unknown>;
    const exports = import.meta.glob('../../../data/contracts/*.contract.json', { import: 'default', eager: true }) as Record<string, unknown>;
    const get = (name: string) => derived[Object.keys(derived).find((k) => k.endsWith(`/derived/${name}.json`)) ?? ''];
    const at = (name: string) => `data/data/derived/${name}.json`;
    const contracts: Record<string, ContractJson> = {};
    for (const [path, value] of Object.entries(exports)) {
      const file = path.replace(/^(\.\.\/)+/, '');
      const c = check<ContractJson>(file, '.venv-data/bin/python data/python/contract.py --export', value, ['info', 'schema', 'quality', 'servicing', 'consumers']);
      contracts[c.info.id] = c;
    }
    if (Object.keys(contracts).length === 0) throw new Error('dataData: data/contracts has no *.contract.json (contract.py --export writes them)');
    cache = {
      catalog: check<CatalogJson>(at('catalog'), DATA_COMMANDS.catalog, get('catalog'), []),
      lineage: check<LineageJson>(at('lineage'), DATA_COMMANDS.catalog, get('lineage'), ['nodes', 'edges']),
      verdicts: check<VerdictsJson>(at('verdicts'), DATA_COMMANDS.quality, get('verdicts'), ['as_of', 'datasets']),
      gate: check<GateJson>(at('gate'), DATA_COMMANDS.gate, get('gate'), ['consumers', 'ok', 'reason']),
      contracts,
    };
  }
  return cache;
}
