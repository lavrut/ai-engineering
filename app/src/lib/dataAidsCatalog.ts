// CatalogExplorer (lesson 2): browse catalog.json, pick a dataset, see where it lives, how big it is, its schema
// sketch, the track that writes it, the tracks that read it, its contract and its last commit. Pure and
// browser-safe; dataData.ts reads catalog.json at build time. Re-exported by dataAids.ts.
import { count, listText, plural, type CatalogEntryJson, type CatalogJson } from './dataAidsTypes';

export type DatasetView = {
  id: string;
  description: string;
  path: string;
  files: number;
  rows: number;
  bytes: number;
  producer: string;
  consumers: string[];
  contract: string | null;
  lastCommit: string | null;
  missing: boolean;
  /** The schema sketch as [field, type] pairs, in catalog.json's order. */
  schema: [string, string][];
};

export type CatalogExplorerProps = { datasets: DatasetView[]; initial: string };

function datasetView(id: string, e: CatalogEntryJson): DatasetView {
  return {
    id,
    description: e.description,
    path: e.path,
    files: e.files,
    rows: e.rows,
    bytes: e.bytes,
    producer: e.producer,
    consumers: e.consumers,
    contract: e.contract,
    lastCommit: e.last_commit,
    missing: e.missing,
    schema: Object.entries(e.schema),
  };
}

/** The datasets a lesson names (every one by default, in catalog.json's order); throws on an unknown id. */
export function catalogExplorerProps(catalog: CatalogJson, opts: { datasets?: string[]; initial?: string } = {}): CatalogExplorerProps {
  const known = Object.keys(catalog);
  const ids = opts.datasets ?? known;
  for (const id of ids) {
    if (!Object.hasOwn(catalog, id)) throw new Error(`CatalogExplorer: unknown dataset "${id}" (known: ${known.join(', ')})`);
  }
  const initial = opts.initial ?? ids[0];
  if (!ids.includes(initial)) throw new Error(`CatalogExplorer: initial "${initial}" is not one of ${ids.join(', ')}`);
  return { datasets: ids.map((id) => datasetView(id, catalog[id])), initial };
}

/** "complaints: 3,600 rows in 1 file, written by ml, read by mcp, under contract complaints.v1." */
export function datasetSummary(d: DatasetView): string {
  const contract = d.contract ? `under contract ${d.contract}` : 'no contract';
  const missing = d.missing ? ' Its files are missing.' : '';
  return `${d.id}: ${plural(d.rows, 'row')} in ${plural(d.files, 'file')}, written by ${d.producer}, read by ${listText(d.consumers)}, ${contract}.${missing}`;
}

/** The facts list beside the picker, as [label, value] pairs. */
export function datasetFacts(d: DatasetView): [string, string][] {
  return [
    ['Path', d.path],
    ['Files', count(d.files)],
    ['Rows', count(d.rows)],
    ['Size', `${count(d.bytes)} bytes`],
    ['Written by', d.producer],
    ['Read by', listText(d.consumers)],
    ['Contract', d.contract ?? 'none'],
    ['Last commit', d.lastCommit ?? 'not in git'],
  ];
}
