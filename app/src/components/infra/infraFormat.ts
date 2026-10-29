// What the five AI Infrastructure islands share: money and rate formats, the marks for an upstream answer, an
// outcome, a budget state and a gate line (each a symbol and a word, never colour alone), the sentence for a
// route, and the arrow-key move between rows. Pure and browser-safe.
import { DENY_ROW, type MarkView, type RouteRow, type TeamState } from '../../lib/infraAids';

/** "$0.1856": a cost in dollars at a fixed number of places. */
export const usd = (x: number, places = 4) => `$${x.toFixed(places)}`;

/** "2.1%": a rate as a percentage with one decimal. */
export const pct = (rate: number) => `${(rate * 100).toFixed(1)}%`;

/** An upstream answer: a status and its word, or dropped when the connection failed. */
export function statusMark(status: number | null): MarkView {
  if (status === null) return { icon: '✗', word: 'dropped', tone: 'fail' };
  if (status >= 200 && status < 300) return { icon: '✓', word: `${status} ok`, tone: 'ok' };
  if (status === 429) return { icon: '✗', word: `${status} rate limited`, tone: 'fail' };
  if (status >= 500) return { icon: '✗', word: `${status} server error`, tone: 'fail' };
  return { icon: '✗', word: `${status} refused`, tone: 'fail' };
}

const OUTCOMES: Record<string, MarkView> = {
  ok: { icon: '✓', word: 'ok', tone: 'ok' },
  cached: { icon: '✓', word: 'cached', tone: 'ok' },
  refused: { icon: '✗', word: 'refused', tone: 'fail' },
  failed: { icon: '✗', word: 'failed', tone: 'fail' },
};
/** A request's outcome; one the table does not know keeps its word with a neutral mark. */
export const outcomeMark = (o: string): MarkView => OUTCOMES[o] ?? { icon: '•', word: o, tone: 'info' };

const STATES: Record<TeamState, MarkView> = {
  under: { icon: '✓', word: 'under', tone: 'ok' },
  warn: { icon: '◐', word: 'warn', tone: 'warn' },
  over: { icon: '✗', word: 'over', tone: 'fail' },
};
/** A team's spend against its budget lines. */
export const stateMark = (s: TeamState): MarkView => STATES[s];

/** A gate line: ok or red. */
export const gateMark = (ok: boolean): MarkView => (ok ? { icon: '✓', word: 'ok', tone: 'ok' } : { icon: '✗', word: 'red', tone: 'fail' });

/** The live region's sentence for a route row. */
export function routeText(r: RouteRow): string {
  if (r.model === DENY_ROW) {
    return r.rule === 'deny' ? `${DENY_ROW}: deny, every model the routes do not name is refused.` : `${DENY_ROW}: route, every other model is sent upstream as itself.`;
  }
  const fallback = r.fallback.length > 0 ? r.fallback.join(' then ') : 'none';
  return `${r.model}: route to ${r.to}, fallback ${fallback}, ${r.rpm} rpm, ${r.tpm} tpm.`;
}

/** The row an arrow key moves to, clamped at the ends; Home and End jump to the first and the last. */
export function rowMove(i: number, key: string, n: number): number | null {
  if (key === 'ArrowDown' || key === 'ArrowRight') return Math.min(i + 1, n - 1);
  if (key === 'ArrowUp' || key === 'ArrowLeft') return Math.max(i - 1, 0);
  if (key === 'Home') return 0;
  if (key === 'End') return n - 1;
  return null;
}
