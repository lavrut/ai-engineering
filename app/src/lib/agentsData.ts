// The AI Agents track's data, read at build time from agents/data/derived/ (patterns, traces,
// cost_compare and sets written by agents/derive.py; engine_scores by agents/evals/run_evals.py, from the
// committed traces of user-approved recordings).
//
// Build time only: the eager glob turns the files into imports, so an island must never import this
// module. The types and the logic are in agentsAids.ts (pure, browser-safe); pass an island the small
// props that agentsAids builds from this.
import type { AgentsData, CostCompareJson, EngineScoresJson, PatternsJson, SetsJson, TracesJson } from './agentsAids';

export * from './agentsAids';

let cache: AgentsData | undefined;

/** Fails loudly, naming the file and what writes it, when a file is missing or is not what the aids read. */
function check<T>(name: string, value: unknown, keys: string[]): T {
  const writer = name === 'engine_scores' ? 'python agents/evals/run_evals.py' : 'python agents/derive.py';
  if (value === undefined) throw new Error(`agentsData: agents/data/derived/${name}.json is missing (${writer} writes it)`);
  if (typeof value !== 'object' || value === null) throw new Error(`agentsData: agents/data/derived/${name}.json is not a JSON object`);
  for (const k of keys) {
    if (!(k in value)) throw new Error(`agentsData: agents/data/derived/${name}.json has no "${k}" (rerun ${writer})`);
  }
  return value as T;
}

export function loadAgentsData(): AgentsData {
  if (!cache) {
    const raw = import.meta.glob('../../../agents/data/derived/*.json', { import: 'default', eager: true }) as Record<string, unknown>;
    const get = (name: string) => raw[Object.keys(raw).find((k) => k.endsWith(`/derived/${name}.json`)) ?? ''];
    cache = {
      patterns: check<PatternsJson>('patterns', get('patterns'), ['engines', 'tickets', 'notes', 'patterns']),
      traces: check<TracesJson>('traces', get('traces'), ['tickets', 'loop_ticket', 'loop_reviews', 'ticket_text', 'runs']),
      costCompare: check<CostCompareJson>('cost_compare', get('cost_compare'), ['engines', 'tickets']),
      engineScores: check<EngineScoresJson>('engine_scores', get('engine_scores'), ['gate', 'judge', 'scores']),
      sets: check<SetsJson>('sets', get('sets'), ['sets']),
    };
  }
  return cache;
}
