// CacheReport (lesson 4): the response cache over the capstone replay, per set and per team, with the lab's
// prompt cache beside it as the contrast (prompt_cache_* are the provider's prompt-cache tokens of the calls that
// reached the upstream). about.cache, shown verbatim, says where the hits came from and what was skipped.
// Pure and browser-safe; infraData.ts reads capstone.json at build time. Re-exported by infraAids.ts.
//
// No TTL recompute: capstone.json keeps no per-call timestamps or keys, so a different ttl_s cannot be replayed
// from it. The aid switches between the per-set and per-team views instead; the TTL try-it reruns replay.py.
import { ALL, pickId, type CapstoneJson, type FiguresJson } from './infraAidsTypes';

export type CacheKind = 'all' | 'set' | 'team';
export type CacheRow = {
  id: string;
  kind: CacheKind;
  calls: number;
  hits: number;
  /** As capstone.json has it: rounded to 4 places. */
  hit_rate: number;
  prompt_cache_read_tokens: number;
  prompt_cache_write_tokens: number;
};
export type CacheReportProps = {
  all: CacheRow;
  sets: CacheRow[];
  teams: CacheRow[];
  /** capstone.json's about.cache, verbatim. */
  about: string;
  ttl_s: number;
  skip_tools: boolean;
  /** 'all', a set or a team: what the aid opens on. */
  initial: string;
};

const row = (id: string, kind: CacheKind, f: FiguresJson): CacheRow => ({
  id,
  kind,
  calls: f.calls,
  hits: f.cache_hits,
  hit_rate: f.hit_rate,
  prompt_cache_read_tokens: f.prompt_cache_read_tokens,
  prompt_cache_write_tokens: f.prompt_cache_write_tokens,
});

export function cacheReportProps(c: CapstoneJson, opts: { initial?: string } = {}): CacheReportProps {
  const sets = Object.entries(c.sets).map(([id, f]) => row(id, 'set', f));
  const teams = Object.entries(c.teams).map(([id, f]) => row(id, 'team', f));
  const about = c.about.cache;
  if (about === undefined) throw new Error('CacheReport: capstone.json has no about.cache (rerun replay.py)');
  const known = [ALL, ...sets.map((r) => r.id), ...teams.map((r) => r.id)];
  const initial = opts.initial === undefined ? ALL : pickId('CacheReport', 'set or team', known, opts.initial);
  return { all: row(ALL, 'all', c.all), sets, teams, about, ttl_s: c.config.cache.ttl_s, skip_tools: c.config.cache.skip_tools, initial };
}

/** The row for 'all', a set or a team. */
export function cacheRow(p: CacheReportProps, id: string): CacheRow {
  const r = id === ALL ? p.all : [...p.sets, ...p.teams].find((x) => x.id === id);
  if (!r) throw new Error(`CacheReport: unknown set or team "${id}"`);
  return r;
}
