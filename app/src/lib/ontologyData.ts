// The Ontology track's data, read at build time: ontology/data/derived/ontology.json (ontology.py export),
// generated.json (generate.py), validation.json (grounding.py), audit.json (audit.py) and governance.json (governance.py).
//
// Build time only: the eager glob turns the files into imports, so an island must never import this module.
// The types and the logic are in ontologyAids.ts (pure, browser-safe); pass an island the small props it builds.
import { ONTOLOGY_COMMANDS, type AuditJson, type GeneratedJson, type GovernanceJson, type OntologyData, type OntologyJson, type ValidationJson } from './ontologyAids';

export * from './ontologyAids';

let cache: OntologyData | undefined;

/** Fails loudly, naming the file and what writes it, when a file is missing or is not what the aids read. */
function check<T>(file: string, writer: string, value: unknown, keys: string[]): T {
  if (value === undefined) throw new Error(`ontologyData: ${file} is missing (${writer} writes it)`);
  if (typeof value !== 'object' || value === null) throw new Error(`ontologyData: ${file} is not a JSON object`);
  for (const k of keys) {
    if (!(k in value)) throw new Error(`ontologyData: ${file} has no "${k}" (rerun ${writer})`);
  }
  return value as T;
}

export function loadOntologyData(): OntologyData {
  if (!cache) {
    const derived = import.meta.glob('../../../ontology/data/derived/*.json', { import: 'default', eager: true }) as Record<string, unknown>;
    const get = (name: string) => derived[Object.keys(derived).find((k) => k.endsWith(`/derived/${name}.json`)) ?? ''];
    const at = (name: string) => `ontology/data/derived/${name}.json`;
    const file = <T>(name: keyof typeof ONTOLOGY_COMMANDS, keys: string[]) => check<T>(at(name), ONTOLOGY_COMMANDS[name], get(name), keys);
    cache = {
      ontology: file<OntologyJson>('ontology', ['source', 'classes', 'properties', 'relations', 'individuals', 'constraints', 'actions']),
      generated: file<GeneratedJson>('generated', ['compare']),
      validation: file<ValidationJson>('validation', ['sources', 'totals']),
      audit: file<AuditJson>('audit', ['rows', 'gate']),
      governance: file<GovernanceJson>('governance', ['tools', 'roles', 'actions', 'matrix', 'by_engine', 'totals', 'gate']),
    };
  }
  return cache;
}
