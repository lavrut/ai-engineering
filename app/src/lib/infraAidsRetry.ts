// RetryTimeline (lesson 3): each injector script (capstone.json's injector) as a timeline: the upstream's
// scripted answers, each attempt with its model, status and the backoff before the next, then the outcome and
// the status the caller got. Pure and browser-safe; infraData.ts reads capstone.json at build time.
// Re-exported by infraAids.ts.
import { pickId, type InjectorScriptJson } from './infraAidsTypes';

/** One try, numbered from 1; status null when the connection failed; backoff_ms is the wait before the next. */
export type RetryAttemptView = { n: number; model: string; status: number | null; backoff_ms: number };
export type RetryScriptView = {
  id: string;
  /** What the scripted upstream answers, in order. */
  script: (number | string)[];
  attempts: RetryAttemptView[];
  outcome: string;
  /** The status the caller got. */
  status: number;
  decisions: { stage: string; verdict: string }[];
};
export type RetryTimelineProps = { scripts: RetryScriptView[]; initial: string; about: string | null };

/** Every script, in capstone.json's order; `initial` is the one the aid opens on (default the first). `about` is
 *  capstone.json's about.injector (how the scripts were run), shown as written. */
export function retryTimelineProps(injector: Record<string, InjectorScriptJson>, opts: { initial?: string; about?: string } = {}): RetryTimelineProps {
  const scripts = Object.entries(injector).map(([id, s]): RetryScriptView => ({
    id,
    script: s.script,
    attempts: s.attempts.map((a, i) => ({ n: i + 1, model: a.model, status: a.status, backoff_ms: a.backoff_ms })),
    outcome: s.outcome,
    status: s.status,
    decisions: s.decisions.map(([stage, verdict]) => ({ stage, verdict })),
  }));
  if (scripts.length === 0) throw new Error('RetryTimeline: capstone.json has no injector scripts (rerun replay.py)');
  const initial = pickId('RetryTimeline', 'script', scripts.map((s) => s.id), opts.initial);
  return { scripts, initial, about: opts.about ?? null };
}

/** The total backoff a script waited, in ms. */
export const waitedMs = (s: RetryScriptView) => s.attempts.reduce((a, x) => a + x.backoff_ms, 0);
