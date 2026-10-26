// Build time only: reads llms/data/inspect_summary.json (committed). Returns null when the file
// is missing, so the aid shows its empty state. Pure helpers live in frameworkCompare.ts.
import type { InspectSummary } from './frameworkCompare';

export function loadInspectSummary(): InspectSummary | null {
  const found = import.meta.glob('../../../llms/data/inspect_summary.json', { eager: true, import: 'default' }) as Record<
    string,
    InspectSummary
  >;
  return Object.values(found)[0] ?? null;
}
