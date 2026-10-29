// RouteTable (lesson 2): gateway.yaml's routes as rows, one per configured model plus the '*' row for every other
// model, which the course's config denies. Pick a model and see its route: the target, the fallback order, or
// the deny. Routing decides where a call goes, never what it says: the replay key is over the body's bytes.
// Pure and browser-safe; infraData.ts reads capstone.json at build time. Re-exported by infraAids.ts.
import type { ConfigJson } from './infraAidsTypes';

/** The row for every model the routes do not name. */
export const DENY_ROW = '*';

/** Why routing never edits the body: the replay proxy (and the cache) key a call by its bytes. */
export const REPLAY_KEY_NOTE =
  'The replay key is a hash over the method, path and body, so routing never edits the body: a rerouted model in the body would be a different key, a replay miss.';

export type RouteRule = 'route' | 'deny';
/** A route: the target and its fallbacks in order. On the '*' row, to is null: deny, or (deny_unknown off) the
 *  model is sent as itself. rpm and tpm are the target model's limits, null on the '*' row. */
export type RouteRow = { model: string; rule: RouteRule; to: string | null; fallback: string[]; rpm: number | null; tpm: number | null };
export type RouteTableProps = {
  rows: RouteRow[];
  /** The models gateway.yaml defines (section 1), each with its rate limits. */
  models: string[];
  upstream: string;
  replayKey: string;
};

export function routeTableProps(config: ConfigJson): RouteTableProps {
  const rows = Object.entries(config.routes).map(([model, r]): RouteRow => {
    const m = config.models[r.to];
    if (!m) throw new Error(`RouteTable: route ${model} goes to ${r.to}, which gateway.yaml does not define (rerun replay.py)`);
    return { model, rule: 'route', to: r.to, fallback: r.fallback, rpm: m.rpm, tpm: m.tpm };
  });
  rows.push({ model: DENY_ROW, rule: config.deny_unknown ? 'deny' : 'route', to: null, fallback: [], rpm: null, tpm: null });
  return { rows, models: Object.keys(config.models), upstream: config.upstream, replayKey: REPLAY_KEY_NOTE };
}

/** The row a model in a request meets: its own, or the '*' row. */
export function routeFor(p: RouteTableProps, model: string): RouteRow {
  return p.rows.find((r) => r.model === model && r.model !== DENY_ROW) ?? p.rows.find((r) => r.model === DENY_ROW)!;
}
