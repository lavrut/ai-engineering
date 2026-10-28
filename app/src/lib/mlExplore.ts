// The feature explorer's data and maths: tokenizer, TF-IDF and scoring, mirroring
// ml/python/train.py (tokenize and score) and ml/go/main.go sections 3-4. Pure and
// browser-safe, with no imports: islands import this module, never mlData.ts (whose
// eager globs Vite turns into top-level imports of every model and report in dev).
//
// Exactness: with a full model (ScoringModel) everything matches train.score (pinned by
// ml/data/fixtures/tokenize.json and predictions.json in tests/mldata.test.ts). With an
// ExplorerModel the tokens, the unknown words and the TF-IDF weights still match, because
// it carries the full vocabulary and idf (idf rounded to 4 significant figures by
// forExplorer); only the class weights are cut to the COEF_SLICE_SIZE most influential
// words, so its scores and label are approximate (`agreement` says how close).

// ── Types ───────────────────────────────────────────────────────────────────
/** train.FEATURES as exported in the model JSON. */
export type Tokenizer = {
  lowercase: 'ascii';
  pattern: string;
  drop_x_runs_from: number;
  stop_words: string[];
  min_df: number;
  max_features: number | null;
};

/** What exact scoring needs: a full model JSON fits. */
export type ScoringModel = {
  vocabulary: string[];
  idf: number[];
  classes: string[];
  /** One row per class, one column per vocabulary term. */
  coef: number[][];
  intercept: number[];
  majority_class: string;
  tokenizer: Tokenizer;
};

/** The production model for the explorer: the full vocabulary and idf, class weights for the
 *  `slice_size` words with the largest |weight| in any class. Built by explorerModel (exact,
 *  build time) and shrunk by forExplorer (rounded, for an island). */
export type ExplorerModel = {
  version: string;
  /** Every word the model knows, in model order, and its idf: TF-IDF is exact. */
  vocabulary: string[];
  idf: number[];
  classes: string[];
  intercept: number[];
  majority_class: string;
  tokenizer: Tokenizer;
  /** Vocabulary indices that keep their class weights, ascending. */
  coef_terms: number[];
  /** coef[class][k] is the weight of vocabulary[coef_terms[k]]; every other word weighs 0 here. */
  coef: number[][];
  /** Words with weights (coef_terms.length) and words in the model. */
  slice_size: number;
  full_size: number;
  /** How often the (rounded) explorer's label matches the full model's, on real complaints. */
  agreement: { share: number; n: number; on: string };
  /** One sentence the aid can show as is. */
  note: string;
};

export type Prediction = { label: string; scores: Record<string, number>; low_confidence: boolean };

/** One known word of a text: its count, idf, count x idf, and that value after L2 scaling. */
export type TermWeight = { term: string; index: number; count: number; idf: number; raw: number; weight: number };

export type Features = {
  /** Every token, in text order (repeats kept). */
  tokens: string[];
  /** Known words, in vocabulary order. */
  terms: TermWeight[];
  /** The L2 length of the raw row; 1 when no word is known (the row stays all zeros). */
  norm: number;
  /** Distinct tokens the vocabulary lacks, in first-seen order. */
  unknown: string[];
};

// ── Tokenizer (train.py section 3) ───────────────────────────────────────────
/** Split text into tokens exactly as train.tokenize and Go's tokenize do:
 *  1. lowercase A-Z only (no Unicode case folding);
 *  2. a token is a maximal run of [a-z0-9'] (every other character, any non-ASCII one too, separates);
 *  3. strip leading and trailing apostrophes;
 *  4. drop empty tokens, stop words, and tokens made only of x with length >= drop_x_runs_from (XX, XXXX);
 *  5. no stemming. */
export function tokenize(text: string, cfg: Tokenizer): string[] {
  if (cfg.lowercase !== 'ascii') throw new Error(`tokenize implements lowercase='ascii' only, not ${cfg.lowercase}`);
  const lowered = text.replace(/[A-Z]+/g, (s) => s.toLowerCase()); // the matched run is ASCII, so this is exact
  const stop = new Set(cfg.stop_words);
  const out: string[] = [];
  // Without the u flag the class matches UTF-16 code units; it holds only ASCII, so every
  // other code unit (surrogate halves included) is a separator, as in Python and Go.
  for (const [match] of lowered.matchAll(new RegExp(cfg.pattern, 'g'))) {
    const tok = match.replace(/^'+|'+$/g, '');
    if (!tok || stop.has(tok)) continue;
    if (tok.length >= cfg.drop_x_runs_from && /^x+$/.test(tok)) continue;
    out.push(tok);
  }
  return out;
}

// ── TF-IDF and scoring (train.py section 6, score) ───────────────────────────
/** Sums the way Python 3.12+ sum() does (Neumaier compensation), as Go's pySum does. */
function pySum(xs: number[]): number {
  let s = 0;
  let c = 0;
  for (const x of xs) {
    const t = s + x;
    c += Math.abs(s) >= Math.abs(x) ? s - t + x : x - t + s;
    s = t;
  }
  return c !== 0 && Number.isFinite(c) ? s + c : s;
}

const indexes = new WeakMap<string[], Map<string, number>>();
function indexOf(vocabulary: string[]): Map<string, number> {
  let index = indexes.get(vocabulary);
  if (!index) {
    index = new Map(vocabulary.map((t, i) => [t, i]));
    indexes.set(vocabulary, index);
  }
  return index;
}

/** A text's TF-IDF row: counts of known words x idf, scaled to length 1 (L2). */
export function tfidf(model: Pick<ScoringModel, 'vocabulary' | 'idf' | 'tokenizer'>, text: string): Features {
  const index = indexOf(model.vocabulary);
  const tokens = tokenize(text, model.tokenizer);
  const counts = new Map<number, number>();
  const unknown: string[] = [];
  for (const t of tokens) {
    const j = index.get(t);
    if (j === undefined) {
      if (!unknown.includes(t)) unknown.push(t);
    } else counts.set(j, (counts.get(j) ?? 0) + 1);
  }
  const cols = [...counts.keys()].sort((a, b) => a - b);
  const raws = cols.map((j) => counts.get(j)! * model.idf[j]);
  const norm = Math.sqrt(pySum(raws.map((w) => w * w))) || 1;
  const terms = cols.map((j, i) => ({
    term: model.vocabulary[j],
    index: j,
    count: counts.get(j)!,
    idf: model.idf[j],
    raw: raws[i],
    weight: raws[i] / norm,
  }));
  return { tokens, terms, norm, unknown };
}

const columns = new WeakMap<number[], Map<number, number>>();
/** Column of each vocabulary index in a sliced coef (ExplorerModel), or the index itself (full model). */
function columnOf(model: ScoringModel | ExplorerModel): (j: number) => number | undefined {
  if (!('coef_terms' in model)) return (j) => j;
  let cols = columns.get(model.coef_terms);
  if (!cols) {
    cols = new Map(model.coef_terms.map((j, k) => [j, k]));
    columns.set(model.coef_terms, cols);
  }
  return (j) => cols.get(j);
}

/** Score a text like train.score: softmax(coef . x + intercept), sums in vocabulary order.
 *  The label is the first class with the top probability. With no known word the scores are
 *  softmax(intercept) and the label is the majority class, flagged low_confidence; otherwise
 *  low_confidence means the top probability is below 0.5. Exact for a full model; for an
 *  ExplorerModel, words without kept weights add 0 (the vector itself is still exact). */
export function scoreText(model: ScoringModel | ExplorerModel, text: string): Prediction {
  const { terms } = tfidf(model, text);
  const col = columnOf(model);
  const weighted = terms.flatMap((t) => {
    const k = col(t.index);
    return k === undefined ? [] : [[k, t.weight] as const];
  });
  const logits = model.coef.map((row, c) => model.intercept[c] + pySum(weighted.map(([k, w]) => row[k] * w)));
  const top = Math.max(...logits);
  const exps = logits.map((z) => Math.exp(z - top));
  const total = pySum(exps);
  const probs = exps.map((e) => e / total);
  const scores = Object.fromEntries(model.classes.map((c, k) => [c, probs[k]]));
  if (!terms.length) return { label: model.majority_class, scores, low_confidence: true };
  let best = 0;
  probs.forEach((p, k) => {
    if (p > probs[best]) best = k;
  });
  return { label: model.classes[best], scores, low_confidence: probs[best] < LOW_CONFIDENCE };
}

export const LOW_CONFIDENCE = 0.5;


// ── The explorer's model ────────────────────────────────────────────────────
export const COEF_SLICE_SIZE = 1200; // ~150 KB as a prop at 4 s.f.; label agreement with v1 is measured by buildExplorer (pinned in mllessons.test.ts)

/** The explorer's model from a full one, exact: the full vocabulary and idf, and the class weights
 *  of the `size` words with the largest |weight| in any class (ties by vocabulary order). Pass the
 *  measured agreement and the note (mlData.ts measures them on real complaints). */
export function explorerModel(
  model: ScoringModel & { version: string },
  size: number = COEF_SLICE_SIZE,
  agreement: ExplorerModel['agreement'] = { share: NaN, n: 0, on: 'not measured' },
  note = '',
): ExplorerModel {
  const influence = (j: number) => Math.max(...model.coef.map((row) => Math.abs(row[j])));
  const keep = model.vocabulary
    .map((_, j) => [j, influence(j)] as const)
    .sort((a, b) => b[1] - a[1] || a[0] - b[0])
    .slice(0, size)
    .map(([j]) => j)
    .sort((a, b) => a - b);
  return {
    version: model.version,
    vocabulary: model.vocabulary,
    idf: model.idf,
    classes: model.classes,
    intercept: model.intercept,
    majority_class: model.majority_class,
    tokenizer: model.tokenizer,
    coef_terms: keep,
    coef: model.coef.map((row) => keep.map((j) => row[j])),
    slice_size: keep.length,
    full_size: model.vocabulary.length,
    agreement,
    note,
  };
}

/** x to `sig` significant figures, as a number (so JSON stays short). */
export function roundSig(x: number, sig: number): number {
  return x === 0 || !Number.isFinite(x) ? x : Number(x.toPrecision(sig));
}

/** The explorer's model as an island prop: idf, weights and intercepts rounded to `sig`
 *  significant figures (default 4); everything else unchanged. */
export function forExplorer(model: ExplorerModel, sig = 4): ExplorerModel {
  const r = (x: number) => roundSig(x, sig);
  return {
    ...model,
    idf: model.idf.map(r),
    intercept: model.intercept.map(r),
    coef: model.coef.map((row) => row.map(r)),
  };
}

/** Share of texts where `model` gives the same label as `full`. */
export function labelAgreement(model: ScoringModel | ExplorerModel, full: ScoringModel, texts: string[]): number {
  if (!texts.length) throw new Error('labelAgreement needs texts');
  return texts.filter((t) => scoreText(model, t).label === scoreText(full, t).label).length / texts.length;
}
