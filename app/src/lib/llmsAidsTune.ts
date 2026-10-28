// Logic for the LLMs tuning aids: PromptAB, CostQualityChart and LoraDiagram. Pure and browser-safe;
// re-exported by llmsAids.ts.
import { accuracy, callCost, compare, grade, gradeRun, noiseBand, runName } from './llmsData';
import type { Comparison, Grade, Line, LlmsData, Outcome, Run, Ticket, TriageOutput } from './llmsData';
import { MODEL_NAMES, inSplit, runLabel, snippet, type Split } from './llmsAidsShared';
import { TASK_MODEL } from './llmsAidsJudge';

// ── Tuning: shared ──────────────────────────────────────────────────────────
/** Rep 0's lines of a run on the split, when they cover every ticket in it (else null). */
function repZeroOnSplit(run: Run, tickets: Ticket[]): Line[] | null {
  const ids = new Set(tickets.map((t) => t.id));
  const lines = run.lines.filter((l) => l.config.rep === 0 && ids.has(l.case_id));
  const covered = new Set(lines.map((l) => l.case_id));
  return tickets.length && covered.size === tickets.length ? lines : null;
}

// ── PromptAB ────────────────────────────────────────────────────────────────
export const DEFAULT_AB = { a: 'v1_basic', b: 'v2_rules' } as const;
/** The model's [category, priority], or why there is no answer. */
export type AbAnswer = [string, string] | Exclude<Outcome, 'ok'>;
export type AbTicket = { id: string; category: string; priority: string; text: string };
/** One prompt version: an answer per ticket, in AbProps.tickets order. */
export type AbPrompt = { version: string; answers: AbAnswer[] };
export type PromptAbProps = {
  recorded: boolean;
  label: string;
  split: Split;
  tickets: AbTicket[];
  prompts: AbPrompt[];
  a: string;
  b: string;
};

/**
 * Every recorded prompt version of the task model (no effort) that covers the split,
 * with rep 0's answer per ticket. Not recorded when either default prompt is missing.
 */
export function promptAbProps(data: LlmsData, opts: { a?: string; b?: string; split?: Split } = {}): PromptAbProps {
  const { a = DEFAULT_AB.a, b = DEFAULT_AB.b, split = 'dev' } = opts;
  const tickets = data.tickets.filter((t) => inSplit(t, split));
  const label = MODEL_NAMES[TASK_MODEL];
  const prompts: AbPrompt[] = [];
  for (const r of data.runs) {
    if (r.config.kind !== 'triage' || r.config.model !== TASK_MODEL || r.config.effort !== null) continue;
    const lines = repZeroOnSplit(r, tickets);
    if (!lines) continue;
    const byId = new Map(lines.map((l) => [l.case_id, l]));
    const answers = tickets.map((t): AbAnswer => {
      const l = byId.get(t.id)!;
      const g = grade(t, l);
      if (g.outcome !== 'ok') return g.outcome;
      const out = (l.output ?? {}) as Partial<TriageOutput>;
      return [String(out.category ?? ''), String(out.priority ?? '')];
    });
    prompts.push({ version: r.config.prompt_version, answers });
  }
  prompts.sort((x, y) => x.version.localeCompare(y.version));
  const has = (v: string) => prompts.some((p) => p.version === v);
  if (!data.recorded || !has(a) || !has(b)) return { recorded: false, label, split, tickets: [], prompts: [], a, b };
  return {
    recorded: true,
    label,
    split,
    tickets: tickets.map((t) => ({ id: t.id, category: t.category, priority: t.priority, text: snippet(t.text) })),
    prompts,
    a,
    b,
  };
}

/** Re-grade compact answers with the lab's own grade(). */
export function abGrades(tickets: AbTicket[], answers: AbAnswer[]): Grade[] {
  return tickets.map((t, i) => {
    const ans = answers[i];
    if (ans === 'error') return grade(t, { error: 'error' });
    if (typeof ans === 'string') return grade(t, { stop_reason: ans });
    return grade(t, { output: { category: ans[0], priority: ans[1] }, stop_reason: 'end_turn' });
  });
}

/** compare() on rep 0 of two prompt versions (B against A). */
export function abCompare(props: Pick<PromptAbProps, 'tickets' | 'prompts'>, a: string, b: string): Comparison {
  const of = (v: string) => {
    const p = props.prompts.find((x) => x.version === v);
    if (!p) throw new Error(`no recorded prompt ${v}`);
    return abGrades(props.tickets, p.answers);
  };
  return compare(of(a), of(b));
}

/** The A/B verdict in words. It uses compare()'s own `significant`, never a second test. */
export function abVerdict(c: Comparison): { larger: boolean; text: string } {
  return c.significant
    ? { larger: true, text: 'The difference is larger than the noise band: evidence of a real change.' }
    : { larger: false, text: 'The difference is not larger than the noise band: not evidence of a change.' };
}

// ── CostQualityChart ────────────────────────────────────────────────────────
/** One recorded config on the split (rep 0): dollars per 1,000 tickets and both-fields accuracy. */
export type CostPoint = {
  name: string;
  model: string;
  effort: string | null;
  prompt: string;
  /** runLabel, e.g. "Opus 5.5 · v2_rules · high effort". */
  label: string;
  /** Model and effort only, for a chart that shows one prompt: "Opus 5.5 · high". */
  short: string;
  cost: number;
  both: number;
  scored: number;
  band: number;
};
export type CostChartProps = { recorded: boolean; split: Split; prompts: string[]; initial: string; points: CostPoint[] };

/** Prompt versions recorded for the most models, so every point on one view is comparable. */
function comparablePrompts(points: CostPoint[]): string[] {
  const models = new Map<string, Set<string>>();
  for (const p of points) models.set(p.prompt, (models.get(p.prompt) ?? new Set()).add(p.model));
  const most = Math.max(0, ...[...models.values()].map((m) => m.size));
  return [...models.keys()].filter((v) => models.get(v)!.size === most).sort();
}

/**
 * One point per recorded triage config that covers the split: cost per 1,000 tickets
 * from rep 0's saved tokens and PRICES, and rep 0's both-fields accuracy and noise band.
 */
export function costChartProps(data: LlmsData, opts: { split?: Split; prompt?: string } = {}): CostChartProps {
  const { split = 'dev', prompt = DEFAULT_AB.b } = opts;
  const tickets = data.tickets.filter((t) => inSplit(t, split));
  const points: CostPoint[] = [];
  for (const r of data.runs) {
    if (r.config.kind !== 'triage') continue;
    const lines = repZeroOnSplit(r, tickets);
    if (!lines) continue;
    const grades = gradeRun({ config: r.config, lines }, tickets, 0);
    const scored = grades.filter((g) => g.outcome !== 'error').length;
    if (!scored) continue;
    const dollars = lines.reduce((s, l) => s + callCost(r.config.model, l.input_tokens, l.output_tokens), 0);
    const model = MODEL_NAMES[r.config.model] ?? r.config.model;
    points.push({
      name: runName(r.config),
      model: r.config.model,
      effort: r.config.effort,
      prompt: r.config.prompt_version,
      label: runLabel(r.config),
      short: r.config.effort ? `${model} · ${r.config.effort}` : model,
      cost: (dollars / tickets.length) * 1000,
      both: accuracy(grades, 'both_ok'),
      scored,
      band: noiseBand(scored),
    });
  }
  const prompts = comparablePrompts(points);
  if (!data.recorded || !prompts.includes(prompt)) return { recorded: false, split, prompts: [], initial: prompt, points: [] };
  return { recorded: true, split, prompts, initial: prompt, points: points.filter((p) => prompts.includes(p.prompt)) };
}

/** The cheapest point whose accuracy is at or above the target (ties: the more accurate, then by label). */
export function cheapestAbove<P extends Pick<CostPoint, 'cost' | 'both' | 'label'>>(points: P[], target: number): P | null {
  const ok = points.filter((p) => p.both >= target - 1e-9);
  ok.sort((x, y) => x.cost - y.cost || y.both - x.both || x.label.localeCompare(y.label));
  return ok[0] ?? null;
}

// ── LoraDiagram ─────────────────────────────────────────────────────────────
/** Qwen2.5-0.5B's hidden size: q_proj and o_proj are d × d. */
export const LORA_D = 896;
export const LORA_RANK = { min: 1, max: 64, initial: 8 } as const;

/** Trainable numbers for a rank-r adapter on one d × d matrix (A is d × r, B is r × d) against d². */
export function loraParams(d: number, r: number): { trainable: number; full: number; share: number } {
  if (!Number.isInteger(r) || r < 1 || r > d) throw new Error(`rank must be an integer in 1..${d}`);
  const trainable = 2 * d * r;
  const full = d * d;
  return { trainable, full, share: trainable / full };
}
