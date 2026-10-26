// The LLMs track data and scoring, mirroring llms/python/triage_lab.py so the app
// shows the same numbers as the Python and Go labs (pinned by
// llms/data/fixtures/expected_scores.json in tests/llmsdata.test.ts).
//
// Everything here is pure and browser-safe except loadLlmsData, which reads the
// real data at build time. Call it from an .astro wrapper and pass the island only
// what it needs. Cache keys need sha256, so they live in llmsKey.ts (node:crypto).

// ── Types ───────────────────────────────────────────────────────────────────
export const RUBRIC_IDS = ['addresses_issue', 'tone', 'no_false_promises'] as const;
export type RubricId = (typeof RUBRIC_IDS)[number];

export type Ticket = {
  id: string;
  text: string;
  category: string;
  priority: string;
  tags: string[];
  split: string; // "dev" | "test"
};

export type ReplyLabel = {
  id: string;
  ticket_id: string;
  reply: string;
  labels: Record<RubricId, boolean>;
};

export type Prompts = {
  triage: Record<string, string>;
  judge: string;
  rubric: { id: string; text: string }[];
};

export type TriageOutput = { category: string; priority: string; reply: string };
export type JudgeOutput = Record<RubricId, { pass: boolean; reason: string }>;

/** The request a saved line answers: kind, model, effort (null = none sent), prompt version (or judge order), rep. */
export type LineConfig = {
  kind: 'triage' | 'judge';
  model: string;
  effort: string | null;
  prompt_version: string;
  rep: number;
};

/** One saved response line, as written by triage_lab.py. */
export type Line = {
  key: string;
  config: LineConfig;
  case_id: string;
  output: TriageOutput | JudgeOutput | null;
  stop_reason: string | null;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  error: string | null;
};

/** A response file's configuration: a LineConfig without the rep (all reps share one file). */
export type RunConfig = Omit<LineConfig, 'rep'>;

/** One response file. `lines` keep file order, one per key; the latest line for a key wins (replay). */
export type Run = { config: RunConfig; lines: Line[] };

export type LlmsData = {
  tickets: Ticket[];
  labels: ReplyLabel[];
  prompts: Prompts;
  runs: Run[];
  /** False when responses/ holds no saved lines: aids then show "No recorded run yet". */
  recorded: boolean;
};

export type Outcome = 'ok' | 'error' | 'refusal' | 'max_tokens';
export type Grade = {
  case_id: string;
  category_ok: boolean;
  priority_ok: boolean;
  both_ok: boolean;
  outcome: Outcome;
};
export type Report = {
  n: number;
  errors: number;
  scored: number;
  category: number;
  priority: number;
  both: number;
  band: number;
};
export type Comparison = {
  a: number;
  b: number;
  diff: number;
  band: number;
  fixed: string[];
  broken: string[];
  significant: boolean;
};

// ── Configuration (section 1) ───────────────────────────────────────────────
/** Dollars per million tokens: [input, output]. */
export const PRICES: Record<string, [number, number]> = {
  'claude-haiku-5-5': [0.1, 0.5],
  'claude-sonnet-5-5': [2.0, 10.0],
  'claude-opus-5-5': [4.0, 20.0],
};

// ── Dataset and saved responses (sections 2 and 4) ──────────────────────────
/** One JSON object per line; a bad line names its file and line number. */
export function parseJsonl<T = Record<string, unknown>>(text: string, name: string): T[] {
  const rows: T[] = [];
  text.split('\n').forEach((line, i) => {
    if (!line.trim()) return;
    let row: unknown;
    try {
      row = JSON.parse(line);
    } catch (e) {
      throw new Error(`${name}:${i + 1}: ${(e as Error).message}`);
    }
    if (typeof row !== 'object' || row === null || Array.isArray(row)) {
      throw new Error(`${name}:${i + 1}: expected a JSON object`);
    }
    rows.push(row as T);
  });
  return rows;
}

/** The response file name for a configuration, without .jsonl: kind__model__version__effort. */
export function runName(config: RunConfig): string {
  return `${config.kind}__${config.model}__${config.prompt_version}__${config.effort || 'default'}`;
}

/** Canonical JSON as in the cache key: keys sorted, no spaces, non-ASCII kept as is (Python ensure_ascii=False). */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === 'object') {
    // Code-unit order, which matches Python's sort for the ASCII keys used here.
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((k) => [k, sortKeys((value as Record<string, unknown>)[k])]),
    );
  }
  return value;
}

const baseName = (path: string) => path.slice(path.lastIndexOf('/') + 1);

/** Build the data from file contents. `responses` maps a path (any prefix) ending in <runName>.jsonl to its text. */
export function buildLlmsData(files: {
  tickets: string;
  labels: string;
  prompts: string | Prompts;
  responses: Record<string, string>;
}): LlmsData {
  const prompts: Prompts = typeof files.prompts === 'string' ? JSON.parse(files.prompts) : files.prompts;
  const runs: Run[] = [];
  for (const path of Object.keys(files.responses).sort()) {
    const file = baseName(path);
    const latest = new Map<string, Line>();
    for (const line of parseJsonl<Line>(files.responses[path], file)) latest.set(line.key, line); // latest wins
    const lines = [...latest.values()];
    if (!lines.length) continue;
    const { kind, model, effort, prompt_version } = lines[0].config;
    const config: RunConfig = { kind, model, effort, prompt_version };
    for (const l of lines) {
      if (`${runName(l.config)}.jsonl` !== file) throw new Error(`${file}: line ${l.case_id} has config ${runName(l.config)}`);
    }
    runs.push({ config, lines });
  }
  return {
    tickets: parseJsonl<Ticket>(files.tickets, 'tickets.jsonl'),
    labels: parseJsonl<ReplyLabel>(files.labels, 'reply_labels.jsonl'),
    prompts,
    runs,
    recorded: runs.length > 0,
  };
}

let cached: LlmsData | undefined;

/** The real data in llms/data, read at build time. Never llms/data/fixtures (ruling R6). */
export function loadLlmsData(): LlmsData {
  if (cached) return cached;
  // Globs sit inside the function so an island that imports only the pure helpers bundles none of the data.
  const base = import.meta.glob(
    ['../../../llms/data/tickets.jsonl', '../../../llms/data/reply_labels.jsonl', '../../../llms/data/prompts.json'],
    { query: '?raw', import: 'default', eager: true },
  ) as Record<string, string>;
  const responses = import.meta.glob('../../../llms/data/responses/*.jsonl', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>;
  const pick = (name: string) => {
    const path = Object.keys(base).find((p) => baseName(p) === name);
    if (path === undefined) throw new Error(`llms/data/${name} is missing`);
    return base[path];
  };
  cached = buildLlmsData({
    tickets: pick('tickets.jsonl'),
    labels: pick('reply_labels.jsonl'),
    prompts: pick('prompts.json'),
    responses,
  });
  return cached;
}

// ── Code graders (section 5) ────────────────────────────────────────────────
type GradeLine = { output?: object | null; stop_reason?: string | null; error?: string | null };

/** Grade one saved line against its ticket. A line with no usable output is never correct. */
export function grade(ticket: Pick<Ticket, 'id' | 'category' | 'priority'>, line: GradeLine): Grade {
  let outcome: Outcome = 'ok';
  if (line.error) outcome = 'error';
  else if (line.stop_reason === 'refusal' || line.stop_reason === 'max_tokens') outcome = line.stop_reason;
  const output = (line.output ?? {}) as Partial<TriageOutput>;
  const category_ok = outcome === 'ok' && output.category === ticket.category;
  const priority_ok = outcome === 'ok' && output.priority === ticket.priority;
  return { case_id: ticket.id, category_ok, priority_ok, both_ok: category_ok && priority_ok, outcome };
}

/** Grade a triage run's lines (optionally only one rep) against the tickets. */
export function gradeRun(run: Run, tickets: Ticket[], rep?: number): Grade[] {
  const byId = new Map(tickets.map((t) => [t.id, t]));
  return run.lines
    .filter((l) => rep === undefined || l.config.rep === rep)
    .map((l) => {
      const t = byId.get(l.case_id);
      if (!t) throw new Error(`${runName(run.config)}: no ticket ${l.case_id}`);
      return grade(t, l);
    });
}

/** Share of graded cases that are correct. A refusal or cut-off counts as wrong; an error is left out. */
export function accuracy(grades: Grade[], field: 'category_ok' | 'priority_ok' | 'both_ok'): number {
  const counted = grades.filter((g) => g.outcome !== 'error');
  return counted.length ? counted.filter((g) => g[field]).length / counted.length : 0;
}

/** Count (expected, predicted) pairs: matrix[expected][predicted]. No answer is "(none)". */
export function confusionMatrix(pairs: [string, string | null | undefined][]): Record<string, Record<string, number>> {
  const matrix: Record<string, Record<string, number>> = {};
  for (const [expected, predicted] of pairs) {
    const row = (matrix[expected] ??= {});
    const p = predicted || '(none)';
    row[p] = (row[p] ?? 0) + 1;
  }
  return matrix;
}

// ── LLM judge and agreement (sections 6 and 7) ──────────────────────────────
/** One line per rubric item: its id, then what it asks. */
export function rubricText(rubric: Prompts['rubric']): string {
  return rubric.map((r) => `${r.id}: ${r.text}`).join('\n');
}

/** The judge's system prompt: the judge prompt with the rubric filled in. */
export function judgeSystem(prompts: Prompts): string {
  return prompts.judge.replaceAll('{rubric}', rubricText(prompts.rubric));
}

/** Pass/fail per criterion from a saved judge line; null for all when the line has no output. */
export function verdicts(line: { output?: object | null }): Record<RubricId, boolean | null> {
  const output = (line.output ?? null) as JudgeOutput | null;
  return Object.fromEntries(RUBRIC_IDS.map((id) => [id, output ? output[id].pass : null])) as Record<RubricId, boolean | null>;
}

/** Share of replies where judge and human agree on one criterion. An ungraded reply (null) is a disagreement. */
export function agreement(judgeV: (boolean | null)[], humanV: boolean[], criterion = ''): number {
  if (judgeV.length !== humanV.length || !judgeV.length) {
    throw new Error(`${criterion || 'criterion'}: need two equal-length, non-empty verdict lists`);
  }
  return judgeV.filter((j, i) => j === humanV[i]).length / judgeV.length;
}

/** Agreement beyond chance: (po - pe) / (1 - pe). If pe == 1 it is 1.0 when po == 1, else 0.0. */
export function cohenKappa(a: boolean[], b: boolean[]): number {
  if (a.length !== b.length || !a.length) throw new Error('need two equal-length, non-empty lists');
  const n = a.length;
  const po = a.filter((x, i) => x === b[i]).length / n;
  const pa = a.filter(Boolean).length / n;
  const pb = b.filter(Boolean).length / n;
  const pe = pa * pb + (1 - pa) * (1 - pb);
  if (pe === 1) return po === 1 ? 1.0 : 0.0;
  return (po - pe) / (1 - pe);
}

/** How many replies get a different verdict when only the rubric's position changes. */
export function positionFlips(beforeV: (boolean | null)[], afterV: (boolean | null)[]): number {
  if (beforeV.length !== afterV.length) throw new Error('need two equal-length verdict lists');
  return beforeV.filter((x, i) => x !== afterV[i]).length;
}

// ── Cost (section 8) ────────────────────────────────────────────────────────
/** Dollars for one call, from the token counts and PRICES. */
export function callCost(model: string, inputTokens: number, outputTokens: number): number {
  const price = PRICES[model];
  if (!price) throw new Error(`unknown model ${model}`);
  return (inputTokens * price[0] + outputTokens * price[1]) / 1_000_000;
}

// ── Report and compare (section 9) ──────────────────────────────────────────
/** Rough one-sigma noise on an accuracy: 1/sqrt(m), m = scored (non-error) lines across all reps. */
export function noiseBand(m: number): number {
  if (m <= 0) throw new Error('no scored cases');
  return 1 / Math.sqrt(m);
}

/** Scores for one configuration, all reps together. n = distinct cases (errors included). */
export function report(grades: Grade[]): Report {
  const scored = grades.filter((g) => g.outcome !== 'error').length;
  return {
    n: new Set(grades.map((g) => g.case_id)).size,
    errors: grades.length - scored,
    scored,
    category: accuracy(grades, 'category_ok'),
    priority: accuracy(grades, 'priority_ok'),
    both: accuracy(grades, 'both_ok'),
    band: noiseBand(scored),
  };
}

/** report() for a triage run, plus how many reps it holds. */
export function runReport(run: Run, tickets: Ticket[]): Report & { reps: number } {
  return { ...report(gradeRun(run, tickets)), reps: new Set(run.lines.map((l) => l.config.rep)).size };
}

/** Does B beat A on both_ok? One grade per case (duplicates throw). diff = b - a; band from the smaller
 *  scored count; significant is strict (|diff| > band). fixed/broken are sorted; errors count as not correct. */
export function compare(gradesA: Grade[], gradesB: Grade[]): Comparison {
  for (const grades of [gradesA, gradesB]) {
    if (new Set(grades.map((g) => g.case_id)).size !== grades.length) {
      throw new Error('compare needs exactly one grade per case; select a single rep');
    }
  }
  const a = accuracy(gradesA, 'both_ok');
  const b = accuracy(gradesB, 'both_ok');
  const rightA = new Map(gradesA.map((g) => [g.case_id, g.both_ok]));
  const rightB = new Map(gradesB.map((g) => [g.case_id, g.both_ok]));
  const shared = [...rightA.keys()].filter((c) => rightB.has(c)).sort();
  const scoredOf = (gs: Grade[]) => gs.filter((g) => g.outcome !== 'error').length;
  const band = noiseBand(Math.min(scoredOf(gradesA), scoredOf(gradesB)));
  return {
    a,
    b,
    diff: b - a,
    band,
    fixed: shared.filter((c) => rightB.get(c) && !rightA.get(c)),
    broken: shared.filter((c) => rightA.get(c) && !rightB.get(c)),
    significant: Math.abs(b - a) > band,
  };
}

/** Pass/fail for CI: fail below the minimum, or below the baseline by more than the band. */
export function gate(grades: Grade[], baselineGrades: Grade[], minimum = 0.7): { passed: boolean; reasons: string[] } {
  const r = report(grades);
  const base = report(baselineGrades);
  const reasons: string[] = [];
  if (r.both < minimum) reasons.push(`both ${r.both.toFixed(3)} is below the minimum ${minimum.toFixed(3)}`);
  if (r.both < base.both - r.band) {
    reasons.push(`both ${r.both.toFixed(3)} is more than the band ${r.band.toFixed(3)} below the baseline ${base.both.toFixed(3)}`);
  }
  return { passed: !reasons.length, reasons };
}
