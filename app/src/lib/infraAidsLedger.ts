// LedgerView (lesson 5): spend per team against its budget lines (warn_usd, limit_usd from gateway.yaml), the top
// tags by cost, and the agents track's ledger per set beside the gateway's own figure for that set. Costs are
// ledger.json's unrounded floats; the aid formats them. Pure and browser-safe; infraData.ts reads ledger.json
// and capstone.json at build time. Re-exported by infraAids.ts.
import { ALL, pickId, type BudgetJson, type FiguresJson, type LedgerJson } from './infraAidsTypes';

export type TeamState = 'under' | 'warn' | 'over';
export type LedgerTeamView = {
  team: string;
  requests: number;
  cost_usd: number;
  cached: number;
  refused: number;
  /** The budget's lines; both null for a team with no budget (the budget stage refuses it). */
  limit_usd: number | null;
  warn_usd: number | null;
  state: TeamState;
};
/** One traced set: the agents track's ledger, and beside it what the gateway booked for the set and its cache
 *  hits (a hit books $0, so the two differ by what the hits were recorded at). desk: a desk set, which the gate's
 *  ledger line checks. */
export type AgentsSetView = { set: string; agents_usd: number; gateway_usd: number | null; cache_hits: number | null; desk: boolean };
export type LedgerViewProps = {
  teams: LedgerTeamView[];
  /** Teams with a budget and no traffic in the run (lab). */
  idle: string[];
  top_tags: { tag: string; cost_usd: number }[];
  agents: AgentsSetView[];
  about: LedgerJson['about'];
  /** 'all' or a team: what the team filter opens on. */
  initial: string;
};

/** over: past limit_usd (or no budget at all); warn: past warn_usd; under otherwise. */
export function teamState(cost: number, b: BudgetJson | undefined): TeamState {
  if (!b || cost > b.limit_usd) return 'over';
  if (b.warn_usd !== null && cost > b.warn_usd) return 'warn';
  return 'under';
}

/** `sets` is capstone.json's sets, for the gateway's figure beside each agents set; without it they are null. */
export function ledgerViewProps(
  ledger: LedgerJson,
  budgets: Record<string, BudgetJson>,
  opts: { sets?: Record<string, FiguresJson>; initial?: string } = {},
): LedgerViewProps {
  const teams = Object.entries(ledger.teams).map(([team, t]): LedgerTeamView => {
    const b = budgets[team];
    return { team, ...t, limit_usd: b?.limit_usd ?? null, warn_usd: b ? b.warn_usd : null, state: teamState(t.cost_usd, b) };
  });
  const agents = Object.entries(ledger.agents).map(([set, agents_usd]): AgentsSetView => {
    const f = opts.sets?.[set];
    return { set, agents_usd, gateway_usd: f ? f.cost_usd : null, cache_hits: f ? f.cache_hits : null, desk: set.startsWith('desk-') };
  });
  const known = [ALL, ...teams.map((t) => t.team)];
  return {
    teams,
    idle: Object.keys(budgets).filter((k) => !(k in ledger.teams)),
    top_tags: ledger.top_tags.map(([tag, cost_usd]) => ({ tag, cost_usd })),
    agents,
    about: ledger.about,
    initial: opts.initial === undefined ? ALL : pickId('LedgerView', 'team', known, opts.initial),
  };
}
