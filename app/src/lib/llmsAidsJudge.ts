// Logic for the LLMs judging aids: JudgeCard, the reply labels (fingerprint, storage, merge, export),
// LabelingPage and AgreementView. Pure and browser-safe; cache keys come in as a function so
// node:crypto stays out. Re-exported by llmsAids.ts.
import { RUBRIC_IDS, agreement, cohenKappa, positionFlips, verdicts } from './llmsData';
import type { JudgeOutput, Line, LlmsData, Prompts, RubricId, TriageOutput } from './llmsData';

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
/** What JudgeCard.astro offers unless a lesson opts in to more: the 30 labelled replies (keeps the island small, R30). */
export const DEFAULT_JUDGE_GROUPS: readonly JudgeCaseGroup[] = ['labelled'];
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
