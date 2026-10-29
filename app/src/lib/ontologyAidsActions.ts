// ActionMatrix (lesson 4): governance.json's roles x actions grid. Each cell is the rule section 6 of the ontology
// gives that role and action (permitted, conditional or no: a symbol and a word), the conditions it names, and the
// recorded agents events governance.py judged there, in all or for one engine. The gate's verdict is shown as
// governance.py wrote it. Pure and browser-safe; ontologyData.ts reads governance.json at build time. Re-exported by
// ontologyAids.ts.
import { plural, type EventCounts, type GovernanceGateJson, type GovernanceJson, type MarkView, type MatrixRule } from './ontologyAidsTypes';

/** The filter value for every engine at once. */
export const ALL_ENGINES = 'all';

export type ActionCellView = {
  role: string;
  action: string;
  rule: MatrixRule;
  conditions: string[];
  /** Event counts per filter value: `all` and each engine. */
  counts: Record<string, EventCounts>;
  /** Conditional events per unmet condition, over every engine. */
  unmet: Record<string, number>;
};
export type ActionMatrixProps = {
  roles: string[];
  actions: string[];
  engines: string[];
  cells: ActionCellView[];
  /** [tool, action] in governance.json's order. */
  tools: [string, string][];
  conditions: { id: string; text: string }[];
  /** Every event, per filter value (`all` and each engine), summed over the desks. */
  totals: Record<string, EventCounts>;
  deskRuns: number;
  gate: GovernanceGateJson;
};

const zero = (): EventCounts => ({ events: 0, permitted: 0, conditional: 0, not_permitted: 0 });
const add = (a: EventCounts, b: EventCounts): EventCounts => ({
  events: a.events + b.events,
  permitted: a.permitted + b.permitted,
  conditional: a.conditional + b.conditional,
  not_permitted: a.not_permitted + b.not_permitted,
});

/** The whole grid, every role by every action. A matrix that misses a cell fails the build. */
export function actionMatrixProps(g: GovernanceJson): ActionMatrixProps {
  const engines = Object.keys(g.by_engine).sort();
  const cells = g.roles.flatMap((role) =>
    g.actions.map((action): ActionCellView => {
      const c = g.matrix.find((x) => x.role === role && x.action === action);
      if (!c) throw new Error(`ActionMatrix: governance.json has no cell for ${role} x ${action} (rerun governance.py)`);
      const counts: Record<string, EventCounts> = { [ALL_ENGINES]: { events: c.events, ...c.verdicts } };
      for (const e of engines) counts[e] = c.by_engine[e] ?? zero();
      return { role, action, rule: c.rule, conditions: c.conditions, counts, unmet: c.unmet };
    }),
  );
  const totals: Record<string, EventCounts> = {
    [ALL_ENGINES]: { events: g.totals.events, permitted: g.totals.permitted, conditional: g.totals.conditional, not_permitted: g.totals.not_permitted },
  };
  for (const e of engines) totals[e] = Object.values(g.by_engine[e]).reduce(add, zero());
  return {
    roles: g.roles,
    actions: g.actions,
    engines,
    cells,
    tools: Object.entries(g.tools),
    conditions: g.conditions.map((c) => ({ id: c.id, text: c.text })),
    totals,
    deskRuns: g.totals.desk_runs,
    gate: g.gate,
  };
}

const RULES: Record<MatrixRule, MarkView> = {
  permitted: { icon: '✓', word: 'PERMITTED', tone: 'ok' },
  conditional: { icon: '◐', word: 'CONDITIONAL', tone: 'warn' },
  no: { icon: '✗', word: 'NO', tone: 'fail' },
};
/** A rule as a symbol and a word: ✓ PERMITTED, ◐ CONDITIONAL, ✗ NO. */
export const ruleMark = (r: MatrixRule): MarkView => RULES[r];

/** The gate as a mark: GREEN when no event is not permitted; the reason is shown as governance.py wrote it. */
export const governanceGateMark = (g: GovernanceGateJson): MarkView => (g.ok ? { icon: '✓', word: 'GREEN', tone: 'ok' } : { icon: '✗', word: 'RED', tone: 'fail' });

export const engineName = (e: string) => (e === ALL_ENGINES ? 'all engines' : e);

/** "76 permitted", "42 conditional", "0 events": the verdicts a cell's events got, in words, zeros left out. */
export function countsText(c: EventCounts): string {
  if (c.events === 0) return '0 events';
  const parts = (['permitted', 'conditional', 'not_permitted'] as const).filter((k) => c[k] > 0).map((k) => `${c[k]} ${k.replace('_', ' ')}`);
  return `${plural(c.events, 'event')}: ${parts.join(', ')}`;
}

/** "if review_passed and human_confirmed", or "" for a rule with no condition. */
export const conditionsText = (conds: string[]) => (conds.length === 0 ? '' : `if ${conds.join(' and ')}`);

/** The live region's sentence for the focused cell under the filter. */
export function cellText(c: ActionCellView, engine: string): string {
  const rule = c.rule === 'no' ? 'no row permits it' : c.rule === 'permitted' ? 'permitted' : `conditional, ${conditionsText(c.conditions)}`;
  const unmet = Object.entries(c.unmet).sort(([a], [b]) => a.localeCompare(b)).map(([k, n]) => `${k} unmet on ${n}`);
  const why = engine === ALL_ENGINES && unmet.length > 0 ? ` (${unmet.join(', ')})` : '';
  return `${c.role} ${c.action}: ${rule}. ${engineName(engine)}: ${countsText(c.counts[engine])}${why}.`;
}

/** "621 events in 104 desk runs, all engines: 561 permitted, 60 conditional, 0 not permitted." */
export function matrixLine(p: ActionMatrixProps, engine: string): string {
  const t = p.totals[engine];
  const runs = engine === ALL_ENGINES ? ` in ${plural(p.deskRuns, 'desk run')}` : '';
  return `${plural(t.events, 'event')}${runs}, ${engineName(engine)}: ${t.permitted} permitted, ${t.conditional} conditional, ${t.not_permitted} not permitted.`;
}

/** The cell an arrow key moves to: [row, column], clamped at the edges; Home and End jump along the row. */
export function gridMove(row: number, col: number, key: string, rows: number, cols: number): [number, number] | null {
  if (key === 'ArrowRight') return [row, Math.min(col + 1, cols - 1)];
  if (key === 'ArrowLeft') return [row, Math.max(col - 1, 0)];
  if (key === 'ArrowDown') return [Math.min(row + 1, rows - 1), col];
  if (key === 'ArrowUp') return [Math.max(row - 1, 0), col];
  if (key === 'Home') return [row, 0];
  if (key === 'End') return [row, cols - 1];
  return null;
}
