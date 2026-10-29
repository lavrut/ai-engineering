// The AI Infrastructure track's data, read at build time: infra/data/derived/capstone.json, ledger.json and
// traces.sample.json, all three written by replay.py. The fixture cases are inside capstone.json (cases).
//
// Build time only: the eager glob turns the files into imports, so an island must never import this module.
// The types and the logic are in infraAids.ts (pure, browser-safe); pass an island the small props it builds.
import { INFRA_COMMANDS, type CapstoneJson, type InfraData, type LedgerJson, type TracesSampleJson } from './infraAids';

export * from './infraAids';

let cache: InfraData | undefined;

/** Fails loudly, naming the file and what writes it, when a file is missing or is not what the aids read. */
function check<T>(file: string, writer: string, value: unknown, keys: string[]): T {
  if (value === undefined) throw new Error(`infraData: ${file} is missing (${writer} writes it)`);
  if (typeof value !== 'object' || value === null) throw new Error(`infraData: ${file} is not a JSON object`);
  for (const k of keys) {
    if (!(k in value)) throw new Error(`infraData: ${file} has no "${k}" (rerun ${writer})`);
  }
  return value as T;
}

export function loadInfraData(): InfraData {
  if (!cache) {
    const derived = import.meta.glob('../../../infra/data/derived/*.json', { import: 'default', eager: true }) as Record<string, unknown>;
    const get = (name: string) => derived[Object.keys(derived).find((k) => k.endsWith(`/derived/${name}.json`)) ?? ''];
    const file = <T>(name: keyof typeof INFRA_COMMANDS, keys: string[]) =>
      check<T>(`infra/data/derived/${name}.json`, INFRA_COMMANDS[name], get(name), keys);
    cache = {
      capstone: file<CapstoneJson>('capstone', ['about', 'config', 'calls', 'all', 'sets', 'teams', 'cases', 'injector', 'gate']),
      ledger: file<LedgerJson>('ledger', ['about', 'teams', 'tags', 'top_tags', 'agents']),
      traces: file<TracesSampleJson>('traces.sample', ['about', 'traces']),
    };
  }
  return cache;
}
