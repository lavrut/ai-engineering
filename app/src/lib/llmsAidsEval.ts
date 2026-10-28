// Logic for the LLMs evaluation aids: DatasetBrowser, ConfusionMatrix and EvalRunSim. Pure and
// browser-safe; re-exported by llmsAids.ts.
import { accuracy, callCost, confusionMatrix, grade, gradeRun, noiseBand, runName } from './llmsData';
import type { LlmsData, Run, Ticket, TriageOutput } from './llmsData';
import { CATEGORIES, DEFAULT_MATRIX_RUN, DEFAULT_SIM_RUN, NONE, PRIORITIES, WORKERS, inSplit, runLabel, snippet, type Split } from './llmsAidsShared';

// ── DatasetBrowser ──────────────────────────────────────────────────────────
export type TicketRow = Pick<Ticket, 'id' | 'category' | 'priority' | 'split' | 'tags'> & { text: string };
export type FilterField = 'category' | 'priority' | 'split' | 'tag';
export type TicketFilter = Partial<Record<FilterField, string | null>>;

/** Each ticket with its text cut to a snippet. */
export function ticketRows(tickets: Ticket[]): TicketRow[] {
  return tickets.map((t) => ({ id: t.id, text: snippet(t.text), category: t.category, priority: t.priority, tags: t.tags, split: t.split }));
}

/** Tickets matching every filter that is set (null or missing means any). */
export function filterTickets<T extends Omit<TicketRow, 'id' | 'text'>>(rows: T[], filter: TicketFilter): T[] {
  return rows.filter(
    (r) =>
      (!filter.category || r.category === filter.category) &&
      (!filter.priority || r.priority === filter.priority) &&
      (!filter.split || r.split === filter.split) &&
      (!filter.tag || r.tags.includes(filter.tag)),
  );
}

/** Values present in the data: categories and priorities in lab order (unknown ones last), splits and tags sorted. */
export function filterOptions(rows: Omit<TicketRow, 'id' | 'text'>[]): Record<FilterField, string[]> {
  const ordered = (seen: Set<string>, order: readonly string[]) => [
    ...order.filter((v) => seen.has(v)),
    ...[...seen].filter((v) => !order.includes(v)).sort(),
  ];
  return {
    category: ordered(new Set(rows.map((r) => r.category)), CATEGORIES),
    priority: ordered(new Set(rows.map((r) => r.priority)), PRIORITIES),
    split: [...new Set(rows.map((r) => r.split))].sort(),
    tag: [...new Set(rows.flatMap((r) => r.tags))].sort(),
  };
}

// ── ConfusionMatrix ─────────────────────────────────────────────────────────
export type MatrixTicket = { id: string; category: string; text: string };
/** One recorded config: the model's category for each ticket, in the same order as MatrixProps.tickets (null = no answer). */
export type MatrixRun = { name: string; label: string; answers: (string | null)[] };
export type MatrixProps = { recorded: boolean; tickets: MatrixTicket[]; runs: MatrixRun[]; initial: string; split: Split; rep: number };
export type MatrixCounts = { rows: string[]; cols: string[]; counts: number[][]; max: number };


/** The model's category per ticket id for one rep; null when the line has no usable answer. */
function runAnswers(run: Run, rep: number): Map<string, string | null> {
  const answers = new Map<string, string | null>();
  for (const l of run.lines) {
    if (l.config.rep !== rep) continue;
    const ok = !l.error && l.stop_reason !== 'refusal' && l.stop_reason !== 'max_tokens';
    answers.set(l.case_id, ok ? ((l.output as TriageOutput | null)?.category ?? null) : null);
  }
  return answers;
}

/**
 * Every recorded triage config that covers the whole split, with the model's
 * category per ticket for one rep. Not recorded when the chosen run is missing.
 */
export function matrixProps(data: LlmsData, opts: { run?: string; split?: Split; rep?: number } = {}): MatrixProps {
  const { run = DEFAULT_MATRIX_RUN, split = 'dev', rep = 0 } = opts;
  const tickets = data.tickets.filter((t) => inSplit(t, split));
  const runs: MatrixRun[] = [];
  for (const r of data.runs) {
    if (r.config.kind !== 'triage') continue;
    const answers = runAnswers(r, rep);
    if (!tickets.length || !tickets.every((t) => answers.has(t.id))) continue;
    runs.push({ name: runName(r.config), label: runLabel(r.config), answers: tickets.map((t) => answers.get(t.id) ?? null) });
  }
  if (!data.recorded || !runs.some((r) => r.name === run)) return { recorded: false, tickets: [], runs: [], initial: run, split, rep };
  return { recorded: true, tickets: tickets.map((t) => ({ id: t.id, category: t.category, text: snippet(t.text) })), runs, initial: run, split, rep };
}

/** Counts per (expected, answered) cell: 6 categories by 6 categories plus (none). */
export function matrixCounts(tickets: Pick<MatrixTicket, 'category'>[], answers: (string | null)[]): MatrixCounts {
  const m = confusionMatrix(tickets.map((t, i) => [t.category, answers[i]]));
  const seen = (vals: string[], order: readonly string[]) => [...order, ...[...new Set(vals)].filter((v) => !order.includes(v)).sort()];
  const rows = seen(tickets.map((t) => t.category), CATEGORIES);
  const extra = answers.filter((a): a is string => !!a && !(CATEGORIES as readonly string[]).includes(a));
  const cols = [...seen(extra, CATEGORIES), NONE];
  const counts = rows.map((r) => cols.map((c) => m[r]?.[c] ?? 0));
  return { rows, cols, counts, max: Math.max(0, ...counts.flat()) };
}

/** Ids of the tickets in one cell. */
export function cellTickets(tickets: Pick<MatrixTicket, 'id' | 'category'>[], answers: (string | null)[], expected: string, answered: string): string[] {
  return tickets.filter((t, i) => t.category === expected && (answers[i] || NONE) === answered).map((t) => t.id);
}

// ── EvalRunSim ──────────────────────────────────────────────────────────────
/** One call of a run: ticket id, recorded latency (ms), input and output tokens, both fields right. */
export type SimCall = { id: string; ms: number; in: number; out: number; ok: boolean };
/** One noise repeat: its both-fields accuracy and how many lines it scored. */
export type SimRep = { rep: number; both: number; scored: number };
export type EvalSimProps = {
  recorded: boolean;
  model: string;
  label: string;
  calls: SimCall[];
  reps: SimRep[];
  /** Noise band for one run of this size: noiseBand(scored lines of the smallest rep). */
  band: number;
  workers: number;
};

/** Rep 0's calls on the split, and the score of every rep, for one recorded config. */
export function evalSimProps(data: LlmsData, opts: { run?: string; split?: Split; workers?: number } = {}): EvalSimProps {
  const { run = DEFAULT_SIM_RUN, split = 'dev', workers = WORKERS } = opts;
  const found = data.recorded ? data.runs.find((r) => runName(r.config) === run) : undefined;
  if (!found) return { recorded: false, model: '', label: '', calls: [], reps: [], band: 0, workers };
  const tickets = data.tickets.filter((t) => inSplit(t, split));
  const byId = new Map(tickets.map((t) => [t.id, t]));
  const lines = found.lines.filter((l) => byId.has(l.case_id));
  const order = new Map(tickets.map((t, i) => [t.id, i]));
  const calls = lines
    .filter((l) => l.config.rep === 0)
    .sort((a, b) => order.get(a.case_id)! - order.get(b.case_id)!) // ticket order, not file (finish) order
    .map((l) => ({ id: l.case_id, ms: l.latency_ms, in: l.input_tokens, out: l.output_tokens, ok: grade(byId.get(l.case_id)!, l).both_ok }));
  const repIds = [...new Set(lines.map((l) => l.config.rep))].sort((a, b) => a - b);
  const reps = repIds.map((rep) => {
    const grades = gradeRun({ config: found.config, lines }, tickets, rep);
    return { rep, both: accuracy(grades, 'both_ok'), scored: grades.filter((g) => g.outcome !== 'error').length };
  });
  const minScored = Math.min(...reps.map((r) => r.scored));
  return {
    recorded: calls.length > 0,
    model: found.config.model,
    label: runLabel(found.config),
    calls,
    reps,
    band: minScored > 0 ? noiseBand(minScored) : 0,
    workers,
  };
}

export type SimFrame = {
  /** Calls finished so far, of total. */
  done: number;
  total: number;
  /** The call that just finished (null before the first, and on repeat frames it stays the final one). */
  last: SimCall | null;
  /** Wall-clock time when `last` finished, with `workers` calls in flight at once. */
  elapsedMs: number;
  inputTokens: number;
  outputTokens: number;
  cost: number;
  correct: number;
  /** How many noise repeats are on screen. */
  repsShown: number;
};

/**
 * The run as frames: an empty start, one frame per call in the order calls
 * finish (each starts when one of `workers` is free, in ticket order, and takes
 * its recorded latency), then one frame per noise repeat.
 */
export function simFrames(calls: SimCall[], model: string, reps: SimRep[], workers = WORKERS): SimFrame[] {
  const free = Array.from({ length: Math.max(1, workers) }, () => 0);
  const finished = calls.map((c, order) => {
    const w = free.indexOf(Math.min(...free));
    const end = free[w] + c.ms;
    free[w] = end;
    return { c, end, order };
  });
  finished.sort((a, b) => a.end - b.end || a.order - b.order);

  let f: SimFrame = { done: 0, total: calls.length, last: null, elapsedMs: 0, inputTokens: 0, outputTokens: 0, cost: 0, correct: 0, repsShown: 0 };
  const frames = [f];
  for (const { c, end } of finished) {
    f = {
      ...f,
      done: f.done + 1,
      last: c,
      elapsedMs: end,
      inputTokens: f.inputTokens + c.in,
      outputTokens: f.outputTokens + c.out,
      cost: f.cost + callCost(model, c.in, c.out),
      correct: f.correct + (c.ok ? 1 : 0),
    };
    frames.push(f);
  }
  for (let i = 1; i <= reps.length; i++) frames.push((f = { ...f, repsShown: i }));
  return frames;
}
