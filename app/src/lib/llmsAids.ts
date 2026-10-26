// Logic for the LLMs track's aids: evaluation (DatasetBrowser, ConfusionMatrix,
// EvalRunSim) and judging (JudgeCard, AgreementView, LabelingPage). Pure and
// browser-safe; cache keys come in as a function so node:crypto stays out. The *Props builders run in the .astro
// wrappers at build time and keep only the small derived data each island needs
// (ruling R30): never whole runs or saved lines.
import {
  RUBRIC_IDS,
  accuracy,
  agreement,
  callCost,
  cohenKappa,
  confusionMatrix,
  grade,
  gradeRun,
  noiseBand,
  positionFlips,
  runName,
  verdicts,
} from './llmsData';
import type { JudgeOutput, Line, LlmsData, Prompts, RubricId, Run, RunConfig, Ticket, TriageOutput } from './llmsData';

/** The allowed answers, in triage_lab.py's order (section 1). */
export const CATEGORIES = ['billing', 'bug', 'account', 'how_to', 'feature_request', 'outage'] as const;
export const PRIORITIES = ['low', 'normal', 'high', 'urgent'] as const;
/** The confusion-matrix column for a line with no answer (refusal, cut-off or error). */
export const NONE = '(none)';
export const RECORD_COMMAND = 'python triage_lab.py record --plan all';
export const SNIPPET_CHARS = 140;
/** triage_lab.py's default --workers. */
export const WORKERS = 8;

export const DEFAULT_MATRIX_RUN = 'triage__claude-haiku-5-5__v1_basic__default';
export const DEFAULT_SIM_RUN = 'triage__claude-haiku-5-5__v2_rules__default';

export type Split = 'dev' | 'test' | 'all';

/** The first 140 characters, with an ellipsis when cut. */
export function snippet(text: string, n = SNIPPET_CHARS): string {
  return text.length > n ? `${text.slice(0, n)}…` : text;
}

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

const MODEL_NAMES: Record<string, string> = {
  'claude-haiku-5-5': 'Haiku 5.5',
  'claude-sonnet-5-5': 'Sonnet 5.5',
  'claude-opus-5-5': 'Opus 5.5',
};

/** "Haiku 5.5 · v1_basic", plus " · high effort" when an effort was sent. */
export function runLabel(config: RunConfig): string {
  const parts = [MODEL_NAMES[config.model] ?? config.model, config.prompt_version];
  if (config.effort) parts.push(`${config.effort} effort`);
  return parts.join(' · ');
}

const inSplit = (t: Ticket, split: Split) => split === 'all' || t.split === split;

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

// ── Judging: shared ─────────────────────────────────────────────────────────
export const JUDGE_MODEL = 'claude-opus-5-5';
/** The model whose saved test-split replies were judged (triage_lab's TASK_MODEL, no effort, rep 0). */
export const TASK_MODEL = 'claude-haiku-5-5';
/** The prompts whose test-split replies were judged (ruling R25), in this order in the picker. */
export const JUDGED_PROMPTS = ['v1_basic', 'v2_rules'] as const;
export type Order = 'before' | 'after';
/** The judge's cache key for one request; the .astro wrapper passes llmsKey's judgeKey (node:crypto). */
export type JudgeKeyFn = (ticketText: string, reply: string, order: Order) => string;
export type RubricItem = Prompts['rubric'][number];

/** The judge run for one rubric order, indexed by cache key (latest line per key, as replay reads it). */
function judgeIndex(data: LlmsData, order: Order): Map<string, Line> {
  const run = data.runs.find((r) => r.config.kind === 'judge' && r.config.model === JUDGE_MODEL && r.config.prompt_version === order);
  return new Map((run?.lines ?? []).map((l) => [l.key, l]));
}

const graded = (l: Line) => !l.error && l.stop_reason !== 'refusal' && l.stop_reason !== 'max_tokens' && !!l.output;

// ── JudgeCard ───────────────────────────────────────────────────────────────
export type Verdict = { pass: boolean; reason: string };
/** One verdict per rubric item in RUBRIC_IDS order, or why the judge gave none. */
export type CaseVerdicts = Verdict[] | { notGraded: string };
export type JudgeCaseGroup = 'labelled' | (typeof JUDGED_PROMPTS)[number];
export const JUDGE_CASE_GROUPS: readonly JudgeCaseGroup[] = ['labelled', ...JUDGED_PROMPTS];
export type JudgeCase = {
  /** Picker value: the label id (r001) or prompt:ticket (v2_rules:t041). */
  value: string;
  group: JudgeCaseGroup;
  /** The saved line's case_id: a label id, or a ticket id for test replies. */
  caseId: string;
  ticketId: string;
  reply: string;
  orders: Partial<Record<Order, CaseVerdicts>>;
};
export type JudgeCardProps = {
  recorded: boolean;
  rubric: RubricItem[];
  /** Ticket text by id, once per ticket. */
  tickets: Record<string, string>;
  cases: JudgeCase[];
  initial: string;
};

function caseVerdicts(l: Line): CaseVerdicts {
  if (!graded(l)) return { notGraded: l.error ?? `the judge stopped with ${l.stop_reason ?? 'no output'}` };
  const out = l.output as JudgeOutput;
  return RUBRIC_IDS.map((id) => ({ pass: out[id].pass, reason: out[id].reason }));
}

/** The latest rep-0 triage line per ticket for one prompt (TASK_MODEL, no effort). */
function savedReplies(data: LlmsData, variant: string): Map<string, string> {
  const run = data.runs.find(
    (r) => r.config.kind === 'triage' && r.config.model === TASK_MODEL && r.config.effort === null && r.config.prompt_version === variant,
  );
  const replies = new Map<string, string>();
  for (const l of run?.lines ?? []) {
    if (l.config.rep === 0 && graded(l)) replies.set(l.case_id, (l.output as TriageOutput).reply);
  }
  return replies;
}

/**
 * Every recorded judge case: the labelled replies in each order the judge saw
 * them, then the judged test-split replies of each JUDGED_PROMPTS prompt (order
 * after). Lines are found by cache key, never by position (R25). `groups` keeps
 * only some of the three groups (all by default), to keep the island small.
 */
export function judgeCardProps(
  data: LlmsData,
  keyFor: JudgeKeyFn,
  opts: { initial?: string; groups?: readonly JudgeCaseGroup[] } = {},
): JudgeCardProps {
  const groups = opts.groups ?? JUDGE_CASE_GROUPS;
  const empty: JudgeCardProps = { recorded: false, rubric: data.prompts.rubric, tickets: {}, cases: [], initial: opts.initial ?? '' };
  if (!data.recorded) return empty;
  const byId = new Map(data.tickets.map((t) => [t.id, t]));
  const index = { after: judgeIndex(data, 'after'), before: judgeIndex(data, 'before') };
  const cases: JudgeCase[] = [];

  for (const label of groups.includes('labelled') ? data.labels : []) {
    const ticket = byId.get(label.ticket_id);
    if (!ticket) continue;
    const orders: JudgeCase['orders'] = {};
    for (const order of ['after', 'before'] as const) {
      const line = index[order].get(keyFor(ticket.text, label.reply, order));
      if (line) orders[order] = caseVerdicts(line);
    }
    if (Object.keys(orders).length) cases.push({ value: label.id, group: 'labelled', caseId: label.id, ticketId: ticket.id, reply: label.reply, orders });
  }
  for (const variant of JUDGED_PROMPTS.filter((p) => groups.includes(p))) {
    const replies = savedReplies(data, variant);
    for (const ticket of data.tickets.filter((t) => t.split === 'test')) {
      const reply = replies.get(ticket.id);
      const line = reply === undefined ? undefined : index.after.get(keyFor(ticket.text, reply, 'after'));
      if (line && reply !== undefined) {
        cases.push({ value: `${variant}:${ticket.id}`, group: variant, caseId: ticket.id, ticketId: ticket.id, reply, orders: { after: caseVerdicts(line) } });
      }
    }
  }
  if (!cases.length) return empty;
  const tickets = Object.fromEntries(cases.map((c) => [c.ticketId, byId.get(c.ticketId)!.text]));
  const initial = cases.some((c) => c.value === opts.initial) ? opts.initial! : cases[0].value;
  return { recorded: true, rubric: data.prompts.rubric, tickets, cases, initial };
}

// ── Reply labels: fingerprint, storage, merge, export ───────────────────────
/** localStorage key for the learner's reply labels. */
export const LABELS_KEY = 'aie:reply-labels';
/** Window CustomEvent (detail: StoredLabels) fired after the learner changes a label on this page. */
export const LABELS_EVENT = 'aie:reply-labels';

export type Labels = Record<RubricId, boolean>;
/** A reply the learner reviewed: the labels they chose and a fingerprint of the reply text they judged. */
export type StoredLabel = { fp: string; labels: Labels };
/** Reviewed replies by label id. A reply is reviewed when, and only when, it has an entry. */
export type StoredLabels = Record<string, StoredLabel>;
type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;

/** FNV-1a (32-bit) of the reply text as hex: enough to notice that a reply changed since it was labelled. */
export function replyFingerprint(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

const isLabels = (x: unknown): x is Labels =>
  !!x && typeof x === 'object' && RUBRIC_IDS.every((id) => typeof (x as Record<string, unknown>)[id] === 'boolean');

/** The stored labels, keeping only well-formed entries; anything unreadable is empty. */
export function parseStoredLabels(raw: string | null): StoredLabels {
  try {
    const replies = raw ? JSON.parse(raw)?.replies : null;
    if (!replies || typeof replies !== 'object' || Array.isArray(replies)) return {};
    const out: StoredLabels = {};
    for (const [id, e] of Object.entries(replies as Record<string, { fp?: unknown; labels?: unknown }>)) {
      if (e && typeof e.fp === 'string' && isLabels(e.labels)) {
        out[id] = { fp: e.fp, labels: Object.fromEntries(RUBRIC_IDS.map((r) => [r, (e.labels as Labels)[r]])) as Labels };
      }
    }
    return out;
  } catch {
    return {};
  }
}

/** window.localStorage, or null where reading it throws (blocked storage). */
export function browserStorage(): StorageLike | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** The learner's labels; `available` is false when storage is missing or throws. */
export function readStoredLabels(storage: StorageLike | null = browserStorage()): { stored: StoredLabels; available: boolean } {
  if (!storage) return { stored: {}, available: false };
  try {
    return { stored: parseStoredLabels(storage.getItem(LABELS_KEY)), available: true };
  } catch {
    return { stored: {}, available: false };
  }
}

/** Saves the learner's labels; false when storage is missing or throws (the page keeps them in memory). */
export function writeStoredLabels(stored: StoredLabels, storage: StorageLike | null = browserStorage()): boolean {
  if (!storage) return false;
  try {
    storage.setItem(LABELS_KEY, JSON.stringify({ v: 1, replies: stored }));
    return true;
  } catch {
    return false;
  }
}

/** The labels to use per reply: the learner's when stored for the same reply text, else the file's. */
export function mergeLabels(cases: { id: string; fp: string; labels: Labels }[], stored: StoredLabels): { labels: Labels[]; mine: boolean[] } {
  const mine = cases.map((c) => stored[c.id]?.fp === c.fp);
  return { labels: cases.map((c, i) => (mine[i] ? { ...stored[c.id].labels } : { ...c.labels })), mine };
}

/**
 * reply_labels.jsonl text, exactly as Python's json.dumps(line, ensure_ascii=False)
 * writes it: keys id, ticket_id, reply, labels (in RUBRIC_IDS order), ", " and ": "
 * separators, one line per reply with a trailing newline.
 */
export function labelsJsonl(cases: { id: string; ticket_id: string; reply: string }[], labels: Labels[]): string {
  const s = JSON.stringify;
  return cases
    .map((c, i) => {
      const l = RUBRIC_IDS.map((id) => `${s(id)}: ${labels[i][id]}`).join(', ');
      return `{"id": ${s(c.id)}, "ticket_id": ${s(c.ticket_id)}, "reply": ${s(c.reply)}, "labels": {${l}}}\n`;
    })
    .join('');
}

// ── LabelingPage ────────────────────────────────────────────────────────────
export type LabelCase = { id: string; ticket_id: string; ticket: string; reply: string; labels: Labels; fp: string };
export type LabelingProps = { rubric: RubricItem[]; cases: LabelCase[] };

/** Every labelled reply with its ticket's full text and the drafted labels from reply_labels.jsonl. */
export function labelingProps(data: LlmsData): LabelingProps {
  const byId = new Map(data.tickets.map((t) => [t.id, t.text]));
  return {
    rubric: data.prompts.rubric,
    cases: data.labels.map((l) => ({
      id: l.id,
      ticket_id: l.ticket_id,
      ticket: byId.get(l.ticket_id) ?? '',
      reply: l.reply,
      labels: Object.fromEntries(RUBRIC_IDS.map((r) => [r, l.labels[r]])) as Labels,
      fp: replyFingerprint(l.reply),
    })),
  };
}

// ── AgreementView ───────────────────────────────────────────────────────────
/** One labelled reply: its fingerprint, drafted labels and the judge's verdicts per order (RUBRIC_IDS order; null = not graded). */
export type AgreementCase = { id: string; fp: string; file: boolean[]; before: (boolean | null)[]; after: (boolean | null)[] };
export type AgreementProps = { recorded: boolean; rubric: RubricItem[]; cases: AgreementCase[] };
export type CriterionStats = { id: RubricId; agreement: number; kappa: number; flips: number; disagree: string[] };

/** The judge's verdicts on every labelled reply in both orders. Not recorded unless every reply has a line in each. */
export function agreementProps(data: LlmsData, keyFor: JudgeKeyFn): AgreementProps {
  const empty: AgreementProps = { recorded: false, rubric: data.prompts.rubric, cases: [] };
  if (!data.recorded || !data.labels.length) return empty;
  const byId = new Map(data.tickets.map((t) => [t.id, t]));
  const index = { after: judgeIndex(data, 'after'), before: judgeIndex(data, 'before') };
  const cases: AgreementCase[] = [];
  for (const label of data.labels) {
    const ticket = byId.get(label.ticket_id);
    const line = (o: Order) => (ticket ? index[o].get(keyFor(ticket.text, label.reply, o)) : undefined);
    const before = line('before');
    const after = line('after');
    if (!before || !after) return empty;
    const v = (l: Line) => RUBRIC_IDS.map((id) => verdicts(l)[id]);
    cases.push({ id: label.id, fp: replyFingerprint(label.reply), file: RUBRIC_IDS.map((id) => label.labels[id]), before: v(before), after: v(after) });
  }
  return { recorded: true, rubric: data.prompts.rubric, cases };
}

/**
 * Per criterion, as `triage_lab.py agreement` prints it: agreement, Cohen's kappa
 * (an ungraded verdict counts as fail, like Python's bool(None)), position flips
 * between the two orders, and the replies where judge and human disagree.
 */
export function agreementStats(cases: AgreementCase[], human: boolean[][], order: Order): CriterionStats[] {
  return RUBRIC_IDS.map((id, i) => {
    const judgeV = cases.map((c) => c[order][i]);
    const humanV = human.map((h) => h[i]);
    return {
      id,
      agreement: agreement(judgeV, humanV, id),
      kappa: cohenKappa(judgeV.map((x) => !!x), humanV),
      flips: positionFlips(cases.map((c) => c.before[i]), cases.map((c) => c.after[i])),
      disagree: cases.filter((_, n) => judgeV[n] !== humanV[n]).map((c) => c.id),
    };
  });
}
