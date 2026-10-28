import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';
import { binomialTail, explain, featureExplorerProps, unpackExplorer } from '../src/lib/mlAids';
import { loadMlData, scoreText, tfidf, tokenize, type ModelJson } from '../src/lib/mlData';

// The ML track, lessons 1-11: every number a lesson states is pinned here against its source, so a
// retrain, a re-sample or a re-record fails this test and names the lesson to update. Sources:
//  - ml/reports/*.json, ml/registry.json, ml/runs/*.json and ml/models/*.json (written by the track's
//    scripts, byte for byte; never by hand);
//  - ml/data/complaints.jsonl, the committed sample, recomputed here with the app's own tokenizer;
//  - ml/data/README.md and constants read from the source files by regex;
//  - the lessons' own Try-it output blocks: ml/python/tests/test_lesson_tryits.py runs every Try-it
//    script and checks those blocks line for line, so a number the prose repeats from a block is
//    pinned here against the block.
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '../..');
const lessonsDir = join(here, '../src/content/lessons/ml');
const src = (p: string) => readFileSync(join(repo, p), 'utf8');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const json = (p: string): any => JSON.parse(src(p));
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const report = (name: string): any => json(`ml/reports/${name}.json`);
const read = (n: string) => readFileSync(join(lessonsDir, `${n}.mdx`), 'utf8');
/** A lesson's body with runs of whitespace collapsed, so a pinned sentence may wrap. */
const flat = (n: string) => matter(read(n)).content.replace(/\s+/g, ' ');
/** The text block after the n-th "You should see" (or "starts") in a lesson's Try it. */
const commas = (n: number) => n.toLocaleString('en-US');
const f3 = (x: number) => x.toFixed(3);
const p1 = (x: number) => (x * 100).toFixed(1);
const constant = (p: string, name: string): number => {
  const m = new RegExp(`^${name}\\s*=\\s*([\\d_.]+)`, 'm').exec(src(p));
  if (!m) throw new Error(`${name} not found in ${p}`);
  return Number(m[1].replace(/_/g, ''));
};
/** The lines of the Try it's text blocks, as one string (pinned by test_lesson_tryits.py). */
const tryitText = (n: string) => {
  const body = matter(read(n)).content;
  const tryit = body.slice(body.indexOf('\n## Try it\n'));
  return [...tryit.matchAll(/```text\n([\s\S]*?)```/g)].map((m) => m[1]).join('');
};
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
};

type Row = { id: string; date: string; product: string; product_raw: string; narrative: string };
const rows: Row[] = src('ml/data/complaints.jsonl').trim().split('\n').map((l) => JSON.parse(l));
const V1: ModelJson = json('ml/models/v1.json');
const metrics = report('metrics');
const dataset = report('dataset');
const drift = report('drift');
const llm = report('llm_compare');
const latency = report('latency');
const log = report('training_log');
const registry = json('ml/registry.json');
const run = (id: string) => json(`ml/runs/${id}.json`);
const CR = 'Credit reporting or other personal consumer reports';
const train = rows.filter((r) => r.date <= '2021-12-31');
const test = rows.filter((r) => r.date >= '2022-01-01' && r.date <= '2022-12-31');
const LESSONS = ['01-what', '02-data', '03-features', '04-training', '05-evaluating', '06-vs-llm', '07-tracking',
  '08-registry', '09-serving', '10-drift', '11-capstone'];

describe('the ML lessons as a set', () => {
  it('run every Python, pip, pytest and uvicorn command in .venv-ml (P1), like the README', () => {
    for (const l of LESSONS) {
      const blocks = [...matter(read(l)).content.matchAll(/```bash\n([\s\S]*?)```/g)].map((m) => m[1]);
      for (const line of blocks.join('\n').split('\n')) {
        if (/^python3 -m venv (\.venv-ml|\/tmp\/mlflow-venv)\b/.test(line)) continue; // creating a venv
        const rest = line.replace(/(\.\.\/)*\.venv-ml\/bin\/\w+/g, 'VENV').replace(/\/tmp\/mlflow-venv\/bin\/\w+/g, 'VENV');
        expect(/(^|[\s(;&|'<])(python3?|pip3?|pytest|uvicorn)\s/.exec(rest), `${l}: ${line}`).toBeNull();
      }
    }
    expect(src('README.md')).toContain('python3 -m venv .venv-ml && .venv-ml/bin/pip install -r ml/python/requirements.txt');
    expect(read('01-what')).toContain('python3 -m venv .venv-ml && .venv-ml/bin/pip install -r ml/python/requirements.txt\ncd ml/python\n');
  });
});

describe('lesson 1 (what a trained model is): the numbers it states', () => {
  const l = flat('01-what');
  it('v1.json: its size, 5,539 words, 6 x 5,539 weights, trained on 2019-2021', () => {
    const bytes = statSync(join(repo, 'ml/models/v1.json')).size;
    expect(bytes).toBe(853279);
    expect(V1.vocabulary).toHaveLength(5539);
    expect(V1.coef.length * V1.coef[0].length).toBe(33234);
    expect(l).toContain(`\`ml/models/v1.json\`, ${commas(bytes)} bytes of JSON`);
    expect(l).toContain(`the ${commas(V1.vocabulary.length)} words the model knows`);
    expect(l).toContain(`6 × ${commas(V1.vocabulary.length)} = ${commas(V1.coef.length * V1.coef[0].length)} numbers`);
    expect(l).toContain(`a list of ${commas(V1.vocabulary.length)} numbers`);
    expect(l).toContain(`The training years give ${commas(V1.vocabulary.length)} words`);
    expect(dataset.splits.train).toEqual({ from: '2019-01-01', to: '2021-12-31', n: 1800 });
    expect(l).toContain('Training reads 1,800 labelled complaints from 2019–2021');
    expect(l).toContain('`trained_on` (2019-01-01 to 2021-12-31)');
    expect(tryitText('01-what')).toContain(`wrote .../ml/models/v1.json (${commas(bytes)} bytes)`);
  });

  it('80.8% on 600 complaints from 2022, ±4.1 points, the majority baseline 43.3%, the other 19.2%', () => {
    const t = metrics.models.logreg.test;
    expect([t.n, p1(t.accuracy), p1(t.band)]).toEqual([600, '80.8', '4.1']);
    expect(l).toContain(`On ${t.n} complaints from 2022, a year it never saw, v1 is right ${p1(t.accuracy)}% of the time, with a <Term t="noise band" /> of ±${p1(t.band)} points (1/√600)`);
    expect(l).toContain(`scores ${p1(metrics.models.majority.test.accuracy)}% on the same 600`);
    expect(l).toContain(`the other ${(100 - Number(p1(t.accuracy))).toFixed(1)}% go`);
  });

  it('the train.py table is metrics.json', () => {
    const lines = ['majority', 'nb', 'logreg'].flatMap((m) => ['train', 'test'].map((s) => {
      const x = metrics.models[m][s];
      return `${m.padEnd(8)}  ${s.padEnd(5)}  ${f3(x.accuracy).padStart(9)} ${f3(x.macro.f1).padStart(9)}  ${f3(x.band)}`;
    }));
    expect(tryitText('01-what')).toContain(['model     split   accuracy  macro F1   band', ...lines].join('\n'));
  });

  it('the settings section 1 names: seed 7, min_df 2, 20,000 features, C 1, NB 0.40 vs 0.76, 0.5', () => {
    const T = 'ml/python/train.py';
    expect([constant(T, 'SEED'), constant(T, 'C'), constant(T, 'NB_ALPHA'), constant(T, 'LOW_CONFIDENCE')]).toEqual([7, 1, 0.1, 0.5]);
    expect(src(T)).toContain('"min_df": 2,');
    expect(src(T)).toContain('"max_features": 20000,');
    expect(src(T)).toContain('0.40 accuracy\n# at alpha=1, 0.76 at 0.1');
    for (const s of ['`SEED = 7`', '`"min_df": 2`', '`"max_features": 20000`', '`C = 1.0`', '`NB_ALPHA = 0.1`',
      '0.40 accuracy at the default 1 against 0.76 at 0.1', '`LOW_CONFIDENCE = 0.5`']) expect(l).toContain(s);
  });

  it("credit reporting's intercept 1.235 and the collector sentence at 87.3%", () => {
    const i = V1.classes.indexOf(CR);
    expect(i).toBe(2);
    expect(f3(V1.intercept[i])).toBe('1.235');
    expect(tryitText('01-what')).toContain(`intercept [${V1.intercept.map((b) => Number(f3(b))).join(', ')}]`);
    const r = scoreText(V1, 'The collector keeps calling me about a debt I already paid.');
    expect([r.label, f3(r.scores[r.label]), r.low_confidence]).toEqual(['Debt collection', '0.873', false]);
    expect(l).toContain(`The third intercept, ${f3(V1.intercept[i])}, is credit reporting's`);
    expect(l).toContain(`overcomes that start, at ${p1(r.scores[r.label])}%`);
  });
});

describe('lesson 2 (the data): the numbers it states', () => {
  const l = flat('02-data');
  it('the sample: 42,622 XXXX, 3,600 lines, its bytes and hash, 10 to 23,091 characters, median 710, 4219949', () => {
    const xxxx = sum(rows.map((r) => r.narrative.split('XXXX').length - 1));
    expect(xxxx).toBe(42622);
    expect(l).toContain(`become \`XXXX\` (${commas(xxxx)} times in this sample)`);
    const bytes = statSync(join(repo, 'ml/data/complaints.jsonl')).size;
    expect([rows.length, bytes]).toEqual([3600, 4609144]);
    expect(l).toContain(`\`complaints.jsonl\` is ${commas(rows.length)} lines, ${commas(bytes)} bytes`);
    expect(V1.data_version.startsWith('sha256:5c2b05a0')).toBe(true);
    expect(l).toContain('`5c2b05a0…`');
    const lens = rows.map((r) => [...r.narrative].length);
    expect(dataset.narrative_chars).toEqual({ min: Math.min(...lens), median: median(lens), max: Math.max(...lens) });
    expect(l).toContain(`Narratives run from ${dataset.narrative_chars.min} to ${commas(dataset.narrative_chars.max)} characters, with a median of ${dataset.narrative_chars.median}.`);
    const longest = rows.find((r) => [...r.narrative].length === dataset.narrative_chars.max)!;
    expect(longest.id).toBe('4219949');
    expect(l).toContain(`The longest, complaint \`${longest.id}\`, is over the servers' 20,000-character limit`);
    expect(constant('ml/serve/server.py', 'MAX_CHARS')).toBe(20000);
  });

  it('the archive: 21 files, the 7 used and their 710,749,149 bytes; about 711 MB', () => {
    const files = [...src('ml/data/fetch_sample.py').matchAll(/\("CCDB_Export_\w+\.zip", (\d+),/g)].map((m) => Number(m[1]));
    expect(files).toHaveLength(7);
    expect(sum(files)).toBe(710749149);
    expect(src('ml/data/README.md')).toContain('| **Total** | 710,749,149 | |');
    expect(src('ml/data/README.md')).toContain('as 21 ZIP files');
    expect(l).toContain(`The course uses the ${files.length} that cover 2019–2024, ${commas(sum(files))} bytes in all.`);
    expect(l).toContain('21 ZIP files of every complaint');
    expect(l).toContain(`downloads about ${Math.round(sum(files) / 1e6)} MB`);
  });

  it('2023 has 172 old-name and 92 new-name credit reporting complaints', () => {
    const raw = dataset.counts_raw;
    const old = raw['Credit reporting, credit repair services, or other personal consumer reports']['2023'];
    expect([old, raw[CR]['2023'], raw['Credit reporting, credit repair services, or other personal consumer reports']['2024']]).toEqual([172, 92, 0]);
    expect(l).toContain(`2023 has ${old} credit reporting complaints under the old name and ${raw[CR]['2023']} under the new one`);
  });

  it('2,045,259 eligible narratives; 600 a year, 50 a month, at least 5 per category', () => {
    expect(src('ml/data/README.md')).toMatch(/\| \*\*Total\*\* \| 114,006 \| 163,060 \| 192,225 \| 322,189 \| 465,434 \| 788,345 \| 2,045,259 \|/);
    expect(l).toContain('There were 2,045,259 eligible narratives for 2019–2024 in these six categories.');
    expect([constant('ml/data/fetch_sample.py', 'PER_YEAR'), constant('ml/data/fetch_sample.py', 'MIN_PER_YEAR')]).toEqual([600, 60]);
    expect(l).toContain('The sample takes 600 per year, 50 per month, with seed 7.');
    expect(l).toContain('`PER_YEAR = 600` and `MIN_PER_YEAR = 60`');
    expect(l).toContain('gives each category 5 complaints a month: 60 a year');
  });

  it('the real against the sample shares (drift.json)', () => {
    const real = (y: string, c: string) => p1(drift.real_class_shares[y][c]);
    const samp = (y: string, c: string) => p1(drift.sample_class_shares[y][c]);
    const MT = 'Money transfer, virtual currency, or money service';
    expect([real('2019', CR), real('2024', CR), samp('2019', CR), samp('2024', CR)]).toEqual(['51.0', '80.0', '35.2', '46.7']);
    expect(l).toContain(`Credit reporting went from ${real('2019', CR)}% of real complaints in 2019 to ${real('2024', CR)}% in 2024. In the sample it goes only from ${samp('2019', CR)}% to ${samp('2024', CR)}%.`);
    expect(l).toContain(`Mortgage fell from ${real('2019', 'Mortgage')}% to ${real('2024', 'Mortgage')}%. In the sample it sits at the floor, ${samp('2023', 'Mortgage')}%, in 2023 and 2024.`);
    expect(samp('2024', 'Mortgage')).toBe('10.0');
    expect(l).toContain(`Money transfer fell from ${real('2019', MT)}% to ${real('2024', MT)}%, but stays at about 10% in the sample.`);
    expect(l).toContain(`In 2024, credit reporting is ${samp('2024', CR)}% of the sample but ${real('2024', CR)}% of real complaints, and mortgage is ${samp('2024', 'Mortgage')}% against ${real('2024', 'Mortgage')}%.`);
    expect(l).toContain('credit reporting would take about 80% of 2024 and money transfer would get about 8 complaints a year');
    expect(Math.round(drift.real_class_shares['2024'][CR] * 100)).toBe(80);
    expect(Math.round(drift.real_class_shares['2024'][MT] * 600)).toBe(8);
  });

  it('the splits: 1,800 / 600 / 1,200', () => {
    const s = dataset.splits;
    expect([s.train.n, s.test.n, s.stream.n]).toEqual([1800, 600, 1200]);
    expect(l).toContain('**train**, 2019–2021: 1,800 complaints');
    expect(l).toContain('**test**, 2022: 600 complaints');
    expect(l).toContain('**stream**, 2023–2024: 1,200 complaints');
  });

  it('random splits 0.763 to 0.803 against 0.808; credit reporting 97.3% found, 43.3% of 2022 against 37.1%', () => {
    const block = tryitText('02-data');
    const randoms = [...block.matchAll(/random split, seed \d: ([\d.]+)/g)].map((m) => m[1]).sort();
    expect(randoms).toEqual(['0.763', '0.775', '0.803']);
    const time = /time split: {3}([\d.]+)/.exec(block)![1];
    expect(time).toBe(f3(metrics.models.logreg.test.accuracy));
    expect(l).toContain(`random splits of 2019–2022 score between ${randoms[0]} and ${randoms[2]}, against ${time} for the time split`);
    expect(l).toContain(`they move by ${Math.round((Number(randoms[2]) - Number(randoms[0])) * 100)} points between seeds, about the ±${p1(metrics.models.logreg.test.band)}-point band`);
    const recall = metrics.models.logreg.test.per_class[CR].recall;
    expect(l).toContain(`(${p1(recall)}% of them on test, lesson 5), is ${p1(drift.sample_class_shares['2022'][CR])}% of it against ${p1(train.filter((r) => r.product === CR).length / train.length)}% of training`);
    expect(test.length).toBe(600);
  });

  it('old names by year; 12 XXXX marks in 3115053; 220 of 2023\'s 600', () => {
    const renamed: Record<string, number> = {};
    for (const r of rows) if (r.product_raw !== r.product) renamed[r.date.slice(0, 4)] = (renamed[r.date.slice(0, 4)] ?? 0) + 1;
    expect(renamed).toEqual({ 2019: 290, 2020: 305, 2021: 297, 2022: 331, 2023: 220 });
    expect(tryitText('02-data')).toContain(`published under an old name, by year: {${Object.entries(renamed).map(([y, n]) => `'${y}': ${n}`).join(', ')}}`);
    expect(l).toContain(`${renamed['2023']} of 2023's 600 do, and none in 2024`);
    const first = train[0];
    expect([first.id, first.narrative.split('XXXX').length - 1]).toEqual(['3115053', 12]);
  });
});

describe('lesson 3 (features): the numbers it states', () => {
  const l = flat('03-features');
  const cfg = V1.tokenizer;
  const df = new Map<string, number>();
  for (const r of train) for (const t of new Set(tokenize(r.narrative, cfg))) df.set(t, (df.get(t) ?? 0) + 1);
  const idf = (w: string) => V1.idf[V1.vocabulary.indexOf(w)];

  it('9,467 distinct training tokens, 5,539 kept at min_df 2; 26 tokenizer cases', () => {
    expect(df.size).toBe(9467);
    expect([...df.values()].filter((n) => n >= 2).length).toBe(V1.vocabulary.length);
    expect(l).toContain(`The 1,800 contain ${commas(df.size)} distinct tokens.`);
    expect(l).toContain(`That leaves ${commas(V1.vocabulary.length)}, kept in alphabetical order.`);
    expect(l).toContain(`one feature per vocabulary word, ${commas(V1.vocabulary.length)} in all`);
    expect(json('ml/data/fixtures/tokenize.json')).toHaveLength(26);
    expect(l).toContain('A shared file of 26 pinned cases, `ml/data/fixtures/tokenize.json`');
    expect(tokenize("On XX/XX/2022 I paid {$250.00} to İstanbul Café. Don't!", cfg)).toEqual(
      ['on', '2022', 'i', 'paid', '250', '00', 'to', 'stanbul', 'caf', "don't"]);
    expect(l).toContain("gives `on`, `2022`, `i`, `paid`, `250`, `00`, `to`, `stanbul`, `caf`, `don't`.");
  });

  it('the df and idf of the, credit, mortgage, escrow; idf from 1.094 (to) to 7.397', () => {
    for (const [w, n, v] of [['the', 1573, '1.135'], ['credit', 1005, '1.582'], ['mortgage', 202, '3.183'], ['escrow', 62, '4.353']] as const) {
      expect([df.get(w), f3(idf(w))]).toEqual([n, v]);
      expect(f3(Math.log(1801 / (1 + n)) + 1)).toBe(v);
      expect(l).toContain(`\`${w}\` is in ${commas(n)}${w === 'the' ? ' complaints' : ''}: idf ${v}.`);
    }
    const lo = Math.min(...V1.idf);
    expect([V1.vocabulary[V1.idf.indexOf(lo)], f3(lo), f3(Math.max(...V1.idf)), f3(Math.log(1801 / 3) + 1)]).toEqual(['to', '1.094', '7.397', '7.397']);
    expect(l).toContain('idf runs from 1.094 (`to`) up to 7.397, for a word in exactly two complaints.');
    expect(l).toContain(`a ${commas(dataset.narrative_chars.max)}-character complaint would have far larger numbers than a ${dataset.narrative_chars.min}-character one`);
  });

  it('the escrow sentence: escrow 0.786, the and my 0.102, eight weights', () => {
    const w = Object.fromEntries(tfidf(V1, 'My mortgage escrow went up. The escrow is wrong.').terms.map((t) => [t.term, f3(t.weight)]));
    expect(Object.keys(w)).toHaveLength(8);
    expect([w.escrow, w.the, w.my]).toEqual(['0.786', '0.102', '0.102']);
    expect(l).toContain('`escrow` appears twice with idf 4.353 and ends up at 0.786; `the` and `my` end up at 0.102.');
    expect(l).toContain('Square the eight weights and add them up');
  });

  it('2,947 unseen stream words; the leak: 7,273 = 5,539 + 1,734 = 1,180 + 554', () => {
    const stream = rows.filter((r) => r.date >= '2023-01-01');
    const vocab = new Set(V1.vocabulary);
    const unseen = new Set(stream.flatMap((r) => tokenize(r.narrative, cfg)).filter((t) => !vocab.has(t)));
    expect(unseen.size).toBe(2947);
    expect(l).toContain(`the 2023–2024 stream holds ${commas(unseen.size)} distinct words the vocabulary does not have`);
    expect(tryitText('03-features')).toContain(`${unseen.size} distinct words in 2023-2024 are not in the vocabulary`);
    const m = /fit on all years: {2}(\d+) words[\s\S]*?(\d+) extra words: (\d+) in one training complaint, (\d+) never in training/.exec(tryitText('03-features'))!;
    const [all, extra, once, never] = m.slice(1).map(Number);
    expect([all, extra, once, never]).toEqual([7273, 1734, 1180, 554]);
    expect(all - V1.vocabulary.length).toBe(extra);
    expect(once + never).toBe(extra);
    expect(l).toContain(`Fitting on every year adds ${commas(extra)} words. Only ${never} of them, \`leakword\` among them, never occur in the training years. The other ${commas(once)} do occur there`);
  });

  it('the FeatureExplorer: preset 5147718 at 26.1% (full model 25.7%), trustee 5 times, 1,200 of 5,539, 98.7% of 1,800', () => {
    const data = loadMlData();
    const p = featureExplorerProps(data);
    const first = p.presets[0];
    const e = explain(unpackExplorer(p.model), first.text);
    expect([first.id, first.category, e.prediction.label, p1(e.prediction.scores[e.prediction.label]), e.prediction.low_confidence])
      .toEqual(['5147718', 'Checking or savings account', CR, '26.1', true]);
    const full = scoreText(V1, first.text);
    expect([full.label, p1(full.scores[full.label])]).toEqual([CR, '25.7']);
    const top = e.terms[0];
    expect([top.term, top.count, f3(idf(top.term)), df.get(top.term), top.hasWeights]).toEqual(['trustee', 5, '7.397', 2, false]);
    expect(l).toContain(`The first preset, checking complaint \`${first.id}\`, is a miss: the page predicts credit reporting at ${p1(e.prediction.scores[CR])}%, flagged low confidence.`);
    expect(l).toContain('Its heaviest word is `trustee`, 5 times at idf 7.397, the largest idf there is: a word in just two training complaints.');
    expect(l).toContain(`the full model gives the same complaint ${p1(full.scores[CR])}%`);
    const a = data.explorer.agreement;
    expect([data.explorer.slice_size, data.explorer.full_size, a.n, p1(a.share)]).toEqual([1200, 5539, 1800, '98.7']);
    expect(l).toContain(`class weights only for the ${commas(data.explorer.slice_size)} most influential of the ${commas(data.explorer.full_size)} words`);
    expect(l).toContain(`its label matches the full model on ${p1(a.share)}% of ${commas(a.n)} complaints from 2022–2024`);
  });
});

describe('lesson 4 (training): the numbers it states', () => {
  const l = flat('04-training');
  type Cp = { iteration: number; loss: number; penalty: number; top_words?: Record<string, [string, number][]> };
  const cps: Cp[] = log.checkpoints;
  const at = (i: number) => cps.find((c) => c.iteration === i)!;
  const top = (i: number, c: string, n = 5) => at(i).top_words![c].slice(0, n).map(([w]) => w);

  it('the baseline 43.3%; 6 x 5,539 weights; NB_ALPHA 0.1; MAX_ITER 1000', () => {
    expect(l).toContain(`it scores ${p1(metrics.models.majority.test.accuracy)}% on 2022`);
    expect(l).toContain(`6 × ${commas(V1.vocabulary.length)} weights and 6 intercepts`);
    expect(l).toContain(`Lesson 3 turned every complaint into ${commas(V1.vocabulary.length)} numbers.`);
    expect(constant('ml/python/train.py', 'MAX_ITER')).toBe(1000);
    expect(l).toContain('`MAX_ITER = 1000` is a ceiling it never reaches');
  });

  it('ln 6 = 1.7918 at the start; converged at 44 with loss 0.6027 and penalty 0.2700', () => {
    expect([at(0).loss.toFixed(4), at(0).penalty, Math.log(6).toFixed(4)]).toEqual(['1.7918', 0, '1.7918']);
    expect([log.converged_at, at(44).loss.toFixed(4), at(44).penalty.toFixed(4)]).toEqual([44, '0.6027', '0.2700']);
    expect(l).toContain('so the loss is ln 6 = 1.7918 and the penalty 0. By iteration 44 it has converged: loss 0.6027, penalty 0.2700.');
    expect(l).toContain('the 44 iterations the full fit took');
  });

  it('the checkpoints 1, 2, 3, 5, 8, 13, 21, 34, 44; 1.0478 by iteration 5; < 0.001 over the last 10', () => {
    expect(cps.map((c) => c.iteration)).toEqual([0, 1, 2, 3, 5, 8, 13, 21, 34, 44]);
    expect(l).toContain('stopping at 1, 2, 3, 5, 8, 13, 21, 34 and 44 iterations');
    expect(cps.filter((c) => c.top_words).map((c) => c.iteration)).toEqual([1, 8, 21, 44]);
    expect(l).toContain("each category's top 8 words at iterations 1, 8, 21 and 44");
    const total = (c: Cp) => c.loss + c.penalty;
    expect(total(at(5)).toFixed(4)).toBe('1.0478');
    expect(l).toContain('Most of the drop happens by iteration 5, from 1.7918 to 1.0478; the last 10 iterations change the total by less than 0.001.');
    expect(Math.abs(total(at(34)) - total(at(44)))).toBeLessThan(0.001);
  });

  it('the top words at iterations 1 and 44', () => {
    expect(top(1, CR)).toEqual(expect.arrayContaining(['i', 'my', 'the']));
    expect(top(44, CR)).toEqual(['report', 'credit', 'experian', 'equifax', 'accounts']);
    expect(top(44, 'Mortgage')).toEqual(['mortgage', 'loan', 'escrow', 'payment', 'home']);
    expect(top(44, 'Money transfer, virtual currency, or money service', 3)).toEqual(['paypal', 'money', 'coinbase']);
    expect(l).toContain("By iteration 44 its top five are `report`, `credit`, `experian`, `equifax` and `accounts`. Mortgage's are `mortgage`, `loan`, `escrow`, `payment` and `home`; money transfer's start with `paypal`, `money` and `coinbase`.");
    const weights = (i: number) => Object.values(at(i).top_words!).flatMap((ws) => ws.map(([, w]) => w));
    expect(Math.max(...weights(1)).toFixed(3)).toBe('0.089');
    expect([Math.min(...weights(44)).toFixed(2), Math.max(...weights(44)).toFixed(2)]).toEqual(['1.19', '5.32']);
    expect(l).toContain('the top-8 weights grow from at most 0.089 at iteration 1 to between 1.19 and 5.32 at iteration 44');
  });

  it('the C sweep in the prose is the Try-it table; v1 at C = 1 is metrics.json', () => {
    const sweep = Object.fromEntries([...tryitText('04-training').matchAll(/^ +([\d.]+) +(\d+) +([\d.]+) +([\d.]+) +([\d.]+)$/gm)]
      .map((m) => [m[1], { iters: Number(m[2]), train: Number(m[3]), test: Number(m[4]), f1: m[5] }]));
    expect(Object.keys(sweep)).toEqual(['0.1', '1.0', '10.0', '100.0']);
    const pc = (x: number) => (x * 100).toFixed(1);
    expect(l).toContain(`\`C = 0.1\`: ${pc(sweep['0.1'].train)}% on train, ${pc(sweep['0.1'].test)}% on test.`);
    expect(l).toContain(`\`C = 1.0\`, the setting v1 uses: ${pc(sweep['1.0'].train)}% on train, ${pc(sweep['1.0'].test)}% on test.`);
    expect(l).toContain(`\`C = 10\`: ${pc(sweep['10.0'].train)}% on train, ${pc(sweep['10.0'].test)}% on test.`);
    expect([sweep['1.0'].iters, f3(sweep['1.0'].train), f3(sweep['1.0'].test), sweep['1.0'].f1]).toEqual(
      [log.converged_at, f3(metrics.models.logreg.train.accuracy), f3(metrics.models.logreg.test.accuracy), f3(metrics.models.logreg.test.macro.f1)]);
    expect(Math.abs(sweep['1.0'].test - sweep['10.0'].test)).toBeLessThan(metrics.models.logreg.test.band);
    expect(l).toContain(`inside the ±${p1(metrics.models.logreg.test.band)}-point band`);
  });

  it('NB 0.774 against logreg 0.769: 0.005 inside ±0.041', () => {
    const nb = metrics.models.nb.test.macro.f1;
    const lr = metrics.models.logreg.test.macro.f1;
    expect([f3(nb), f3(lr), f3(nb - lr), f3(metrics.models.logreg.test.band)]).toEqual(['0.774', '0.769', '0.005', '0.041']);
    expect(l).toContain('Naive Bayes scores 0.774 macro-F1 (lesson 5 defines it) and logistic regression 0.769. The difference is 0.005, far inside the ±0.041 noise band');
    expect(metrics.note).toMatch(/^v1 is logistic regression although Naive Bayes leads by 0\.005 test macro-F1/);
  });
});

describe('lesson 5 (evaluating): the numbers it states', () => {
  const l = flat('05-evaluating');
  const t = metrics.models.logreg.test;
  const cls: string[] = t.classes;
  const ix = (c: string) => cls.indexOf(c);
  const DC = 'Debt collection';
  const MT = 'Money transfer, virtual currency, or money service';
  const pc = (c: string) => t.per_class[c];

  it('debt collection: 48 called, 41 right, 76 true; 0.854 / 0.539 / 0.661; 35 never arrive', () => {
    const col = t.confusion.map((r: number[]) => r[ix(DC)]);
    const row = t.confusion[ix(DC)];
    expect([sum(col), row[ix(DC)], sum(row)]).toEqual([48, 41, 76]);
    expect([f3(pc(DC).precision), f3(pc(DC).recall), f3(pc(DC).f1)]).toEqual(['0.854', '0.539', '0.661']);
    expect(l).toContain('The model gave that answer 48 times and was right 41 times: 0.854.');
    expect(l).toContain('There were 76, and it found 41: 0.539.');
    expect(l).toContain(`would care that ${sum(row) - row[ix(DC)]} never arrive`);
    expect(l).toContain("debt collection's is 0.661");
  });

  it('credit reporting: recall 0.973, precision 0.793, F1 0.874; 60 and 260 test complaints', () => {
    expect([f3(pc(CR).recall), f3(pc(CR).precision), f3(pc(CR).f1)]).toEqual(['0.973', '0.793', '0.874']);
    expect(l).toContain('Credit reporting is the other way round: recall 0.973, precision 0.793, F1 0.874.');
    expect([pc(MT).support, pc(CR).support]).toEqual([60, 260]);
    expect(l).toContain('so money transfer, with 60 test complaints, weighs as much as credit reporting, with 260');
  });

  it('macro 0.769, weighted 0.800; baseline 43.3% and 0.101; +37.5 and 6.2 points; 37.1%', () => {
    expect([f3(t.macro.f1), f3(t.weighted.f1)]).toEqual(['0.769', '0.800']);
    expect(l).toContain("v1's macro-F1 is 0.769.");
    expect(l).toContain("v1's weighted F1 is 0.800.");
    const maj = metrics.models.majority;
    expect([p1(maj.test.accuracy), f3(maj.test.macro.f1), p1(maj.train.accuracy)]).toEqual(['43.3', '0.101', '37.1']);
    expect(Object.values(maj.test.per_class).filter((c) => (c as { f1: number }).f1 === 0)).toHaveLength(5);
    expect(l).toContain('Always answering credit reporting scores 43.3% accuracy on 2022');
    expect(l).toContain('Its macro-F1 is 0.101. It gets F1 0 on five of the six categories');
    expect(((t.accuracy - maj.test.accuracy) * 100).toFixed(1)).toBe('37.5');
    expect(((maj.test.accuracy - maj.train.accuracy) * 100).toFixed(1)).toBe('6.3'); // 43.33 − 37.06
    expect((Number(p1(maj.test.accuracy)) - Number(p1(maj.train.accuracy))).toFixed(1)).toBe('6.2'); // of the rounded shares
    expect(l).toContain(`v1's ${p1(t.accuracy)}% beats the baseline's 43.3% by 37.5 points`);
    expect(l).toContain('the baseline is also 6.2 points better than on the training years, 37.1%');
  });

  it('bands: ±4.1 for 600, ±2.4 for 1,800, ±12.9 for 60', () => {
    expect([p1(t.band), p1(metrics.models.logreg.train.band), p1(1 / Math.sqrt(60))]).toEqual(['4.1', '2.4', '12.9']);
    expect(l).toContain('±4.1 points for the 600 test complaints and ±2.4 for the 1,800 training ones');
    expect(l).toContain('a band of about ±12.9 points (1/√60)');
  });

  it('train against test: 90.2% and 80.8%, a 9.3-point drop (90.17 − 80.83); NB 92.7 / 81.8', () => {
    const tr = metrics.models.logreg.train.accuracy;
    expect([p1(tr), (tr * 100).toFixed(2), (t.accuracy * 100).toFixed(2), ((tr - t.accuracy) * 100).toFixed(1)]).toEqual(['90.2', '90.17', '80.83', '9.3']);
    expect(l).toContain('logistic regression gets 90.2% on the training years and 80.8% on 2022. The drop, 9.3 points (90.17% − 80.83%)');
    expect([p1(metrics.models.nb.train.accuracy), p1(metrics.models.nb.test.accuracy)]).toEqual(['92.7', '81.8']);
    expect(l).toContain('Naive Bayes shows the same pattern, 92.7% against 81.8%.');
    expect(l).toContain('Naive Bayes 0.774 and logistic regression 0.769 macro-F1');
  });

  it('the confusion cells: 31 of 76, 14 of 71, 8 and 9, 66 + 253 = 319, 40.8% and 9.7%', () => {
    const c = (a: string, b: string) => t.confusion[ix(a)][ix(b)];
    const CHK = 'Checking or savings account';
    expect([c(DC, CR), c('Credit card', CR), pc('Credit card').support, c(CHK, MT), c(MT, CHK)]).toEqual([31, 14, 71, 8, 9]);
    const col = t.confusion.map((r: number[]) => r[ix(CR)]);
    expect([sum(col) - c(CR, CR), c(CR, CR), sum(col)]).toEqual([66, 253, 319]);
    expect(l).toContain('31 of 76 debt collection complaints were called credit reporting.');
    expect(l).toContain('14 of 71 credit card complaints were called credit reporting.');
    expect(l).toContain('8 checking complaints called money transfer, 9 money transfer complaints called checking');
    expect(l).toContain('The credit reporting column collects 66 wrong answers on top of its 253 right ones.');
    expect([p1(31 / 76), p1(31 / 319)]).toEqual(['40.8', '9.7']);
    expect(l).toContain('The cell reads 31 of 76 debt collection complaints, 40.8% of the row, and 9.7% of the 319 complaints predicted credit reporting.');
  });

  it('the misses: 31 found by scoring v1.json, first 5117873, and 5148158 among them', () => {
    const missed = test.filter((r) => r.product === DC && scoreText(V1, r.narrative).label === CR);
    expect(missed).toHaveLength(31);
    expect(missed[0].id).toBe('5117873');
    expect(tryitText('05-evaluating')).toContain(`  ${missed[0].id} ${missed[0].narrative.slice(0, 160)}`);
    const quoted = missed.find((r) => r.id === '5148158')!;
    expect(quoted.narrative).toContain('I CHECKED MY REPORT AND THIS CREDIT LOAN IS ON IT, WHICH I HAVE NO KNOWLEDGE OF.');
    expect(l).toContain('complaint `5148158` reads "I CHECKED MY REPORT AND THIS CREDIT LOAN IS ON IT, WHICH I HAVE NO KNOWLEDGE OF."');
  });
});

describe('lesson 6 (against an LLM): the numbers it states', () => {
  const l = flat('06-vs-llm');
  type Line = { case_id: string; output: { category: string } | null; stop_reason: string; input_tokens: number; output_tokens: number; latency_ms: number; error: unknown };
  const lines: Line[] = src('ml/data/llm_responses/claude-haiku-5-5.jsonl').trim().split('\n').map((x) => JSON.parse(x));
  const C = 'ml/python/compare_llm.py';
  const [pin, pout] = [0.1, 0.5];
  const callCost = (i: number, o: number) => (i * pin + o * pout) / 1e6;
  const block = tryitText('06-vs-llm');
  const tally = Object.fromEntries([...block.matchAll(/^(both right|only v1|only Claude|both wrong) +(\d+)$/gm)].map((m) => [m[1], Number(m[2])]));
  const unsure = /v1 low confidence on (\d+): v1 right (\d+), Claude right (\d+)/.exec(block)!.slice(1).map(Number);

  it('the request: claude-haiku-5-5 at $0.10 / $0.50, 300 tokens, 8 workers; v1 is 853,279 bytes', () => {
    expect(src(C)).toContain('PRICES = {"claude-haiku-5-5": (0.10, 0.50)}');
    expect([constant(C, 'MAX_TOKENS'), constant(C, 'WORST_INPUT_TOKENS'), constant(C, 'DEFAULT_MAX_COST')]).toEqual([300, 3000, 10]);
    expect(src(C)).toContain('EST_TOKENS = (1100, 25)');
    expect(src(C)).toContain('workers: int = 8)');
    expect(l).toContain('each reply is capped at 300 tokens');
    expect(l).toContain('at $0.10 per million input tokens and $0.50 per million output tokens');
    expect(l).toContain('recorded with 8 calls in flight at once');
    expect(l).toContain(`is a ${commas(statSync(join(repo, 'ml/models/v1.json')).size)}-byte file of numbers`);
    expect(Math.max(...lines.map((x) => x.output_tokens))).toBeLessThanOrEqual(25); // "the answer itself is about 20"
    expect(l).toContain('the answer itself is about 20');
  });

  it('200 calls, all usable, $0.0204; estimate $0.025, worst $0.090, cap $10', () => {
    expect(lines).toHaveLength(200);
    expect(lines.every((x) => x.stop_reason === 'end_turn' && x.error === null && x.output)).toBe(true);
    const spent = sum(lines.map((x) => callCost(x.input_tokens, x.output_tokens)));
    expect(spent.toFixed(4)).toBe('0.0204');
    expect(l).toContain('The recording made 200 calls, all of which finished normally, and cost $0.0204.');
    expect([(200 * callCost(1100, 25)).toFixed(3), (200 * callCost(3000, 300)).toFixed(3)]).toEqual(['0.025', '0.090']);
    expect(l).toContain('Before it ran, the script estimated $0.025, with a worst case of $0.090.');
    expect(l).toContain('they print an estimate of $0.025, a worst case of $0.090 and the $10 cap');
    expect((spent / 200 * 1000).toFixed(4)).toBe(llm.claude.cost_per_1000.toFixed(4));
    expect(l).toContain('for 200 complaints it is two cents');
    expect(l).toContain('would cost about $0.02 and ask before calling');
  });

  it('the sample: 200 from 2022, seed 7; 87 credit reporting and 20 to 25 per other category', () => {
    const sample = json('ml/data/llm_sample.json');
    expect([sample.seed, sample.ids.length]).toEqual([7, 200]);
    const byId = new Map(test.map((r) => [r.id, r.product]));
    const counts: Record<string, number> = {};
    for (const id of sample.ids) counts[byId.get(id)!] = (counts[byId.get(id)!] ?? 0) + 1;
    expect(counts[CR]).toBe(87);
    const others = Object.entries(counts).filter(([c]) => c !== CR).map(([, n]) => n);
    expect([others.length, Math.min(...others), Math.max(...others)]).toEqual([5, 20, 25]);
    expect(l).toContain('the sample has 87 credit reporting complaints, and between 20 and 25 in each other category');
    expect(l).toContain('stratified by category with seed 7, saved in `ml/data/llm_sample.json`');
  });

  it('accuracy 86.5% against 81.0%, a 5.5-point gap inside ±7.1; 80.8% on all 600', () => {
    expect([llm.n, p1(llm.claude.accuracy), p1(llm.v1.accuracy), p1(llm.band)]).toEqual([200, '86.5', '81.0', '7.1']);
    expect(((llm.claude.accuracy - llm.v1.accuracy) * 100).toFixed(1)).toBe('5.5');
    expect(l).toContain('On those 200 complaints Claude is right 86.5% of the time and v1 81.0%. The gap is 5.5 points. The noise band for 200 complaints is ±7.1 points (1/√200)');
    expect(l).toContain(`v1 scores 81.0% here and ${p1(metrics.models.logreg.test.accuracy)}% on all 600 test complaints`);
    expect(l).toContain('The summary puts the 5.5-point gap inside the ±7.1-point band');
    expect(block).toContain(`200 complaints: v1 accuracy ${f3(llm.v1.accuracy)}, claude-haiku-5-5 ${f3(llm.claude.accuracy)} (noise band ±${f3(llm.band)}); ${llm.no_answer} Claude replies had no usable answer, counted wrong.`);
  });

  it('McNemar: 11 and 22 of 33, p ≈ 0.08; both right 151, both wrong 16', () => {
    expect(tally).toEqual({ 'both right': 151, 'only v1': 11, 'only Claude': 22, 'both wrong': 16 });
    expect(tally['both right'] + tally['only v1']).toBe(Math.round(llm.v1.accuracy * 200));
    expect(tally['both right'] + tally['only Claude']).toBe(Math.round(llm.claude.accuracy * 200));
    const binom = (n: number, k: number) => { let c = 1; for (let i = 0; i < k; i++) c = (c * (n - i)) / (i + 1); return c; };
    let tail = 0;
    for (let k = 0; k <= 11; k++) tail += binom(33, k) / 2 ** 33;
    expect((2 * tail).toFixed(2)).toBe('0.08');
    expect(l).toContain('v1 alone is right on 11, Claude alone on 22, and McNemar\'s test on those 33 gives p ≈ 0.08');
    expect(l).toContain('Both are right on 151 complaints and both wrong on 16. Of the 33 where they disagree, Claude is right on 22 and v1 on 11');
  });

  it('macro-F1 0.835 and 0.765; the per-category pairs', () => {
    const f = (side: string, c: string) => f3(llm[side].per_class_f1[c]);
    expect([f3(llm.claude.macro_f1), f3(llm.v1.macro_f1)]).toEqual(['0.835', '0.765']);
    expect(l).toContain('Macro-F1 is 0.835 for Claude and 0.765 for v1.');
    expect([f('claude', 'Checking or savings account'), f('v1', 'Checking or savings account')]).toEqual(['0.809', '0.636']);
    expect([f('claude', 'Credit card'), f('v1', 'Credit card')]).toEqual(['0.783', '0.651']);
    expect([f('claude', 'Mortgage'), f('v1', 'Mortgage')]).toEqual(['0.976', '0.900']);
    expect([f('claude', 'Debt collection'), f('v1', 'Debt collection')]).toEqual(['0.773', '0.773']);
    expect(l).toContain('Claude is ahead on checking (0.809 against 0.636), credit card (0.783 against 0.651) and mortgage (0.976 against 0.900), and the two tie on debt collection at 0.773');
  });

  it('latency 0.252 / 0.120 ms against 1,151.5 ms; $0.102 per 1,000; 788,345 narratives for about $80', () => {
    expect([f3(llm.v1.latency_p50_ms), f3(llm.v1.latency_p50_ms_go), llm.claude.latency_p50_ms]).toEqual(['0.252', '0.120', 1151.5]);
    expect(llm.v1.latency_p50_ms).toBe(latency.servers.python.p50_ms);
    const ms = lines.map((x) => x.latency_ms).sort((a, b) => a - b);
    expect((ms[99] + ms[100]) / 2).toBe(1151.5);
    expect(l).toContain('the median request took 0.252 ms on the Python server and 0.120 ms on the Go one. Claude\'s recorded calls took a median of 1,151.5 ms each.');
    expect([f3(llm.claude.cost_per_1000), llm.v1.cost_per_1000]).toEqual(['0.102', 0]);
    expect(l).toContain('Claude cost $0.102 per 1,000 complaints in the recording');
    expect(l).toContain('The cost row shows $0.000 against $0.102 per 1,000 complaints.');
    expect(src('ml/data/README.md')).toMatch(/\| \*\*Total\*\* \|[^\n]*\| 788,345 \| 2,045,259 \|/);
    expect(Math.round((788345 * llm.claude.cost_per_1000) / 1000 / 10) * 10).toBe(80);
    expect(l).toContain('in 2024 the CFPB received 788,345 narratives in these six categories, which at the recorded rate would cost about $80 to label once');
  });

  it('the combined design: 162 right, 43 of 72 flagged, 119 of 128 (93.0%), 175 (87.5%), 36% of the calls', () => {
    const [flagged, v1Right, claudeRight] = unsure;
    expect(unsure).toEqual([72, 43, 56]);
    const right = tally['both right'] + tally['only v1'];
    const confident = 200 - flagged;
    const confRight = right - v1Right;
    expect([right, confident, confRight, p1(confRight / confident), confRight + claudeRight, p1((confRight + claudeRight) / 200), Math.round(flagged / 2)])
      .toEqual([162, 128, 119, '93.0', 175, '87.5', 36]);
    expect(l).toContain('v1 is right on 162 complaints, 43 of them among the 72 it flagged. So on the 128 it was confident about, it is right 119 times, 93.0%. Send only the 72 flagged complaints to Claude and the combination gets 119 + 56 = 175 right, 87.5%, with 36% of the API calls.');
  });
});

describe('lesson 7 (tracking): the numbers it states', () => {
  const l = flat('07-tracking');
  const IDS = ['logreg-C0.3-2019-2021', 'logreg-C1-2019-2021', 'logreg-C1-2019-2022', 'logreg-C3-2019-2021', 'nb-alpha0.1-2019-2021'];
  const runs = IDS.map(run);
  const v1run = run('logreg-C1-2019-2021');

  it("v1's run: its params, windows, data and model hashes", () => {
    expect(v1run.params).toEqual({ model: 'logreg', C: 1, max_iter: 1000, min_df: 2, max_features: 20000, seed: 7 });
    expect(l).toContain('For v1, `model: logreg`, `C: 1.0`, `max_iter: 1000`, plus the shared `min_df: 2`, `max_features: 20000` and `seed: 7`.');
    expect(v1run.data_version).toBe(V1.data_version);
    expect(v1run.data_version.startsWith('sha256:5c2b05a0')).toBe(true);
    expect(l).toContain('`sha256:5c2b05a0…`');
    expect(v1run.model).toEqual({ path: 'models/v1.json', sha256: registry.versions.v1.sha256 });
    expect(v1run.model.sha256.startsWith('a804df41')).toBe(true);
    expect(l).toContain("v1's run names `models/v1.json` with `a804df41…`");
  });

  it('the four-run table, within 0.007 macro-F1 inside ±0.041; the fifth on 2023', () => {
    expect(readdirIds()).toEqual(IDS);
    expect(l).toContain('`ml/runs/` holds five records.');
    for (const id of IDS.filter((i) => i.endsWith('2021'))) {
      const t = run(id).metrics.test;
      expect([t.n, t.split]).toEqual([600, 'test']);
      expect(l).toContain(`| \`${id}\`${id === 'logreg-C1-2019-2021' ? ' (v1)' : ''} | ${f3(t.macro_f1)} | ${f3(t.accuracy)} |`);
    }
    const tied = ['nb-alpha0.1-2019-2021', 'logreg-C1-2019-2021', 'logreg-C3-2019-2021'].map((i) => run(i).metrics.test.macro_f1);
    expect(f3(Math.max(...tied) - Math.min(...tied))).toBe('0.007');
    expect(l).toContain('Naive Bayes, C = 1 and C = 3 are within 0.007 macro-F1 of each other, far inside the ±0.041 band.');
    const fifth = run('logreg-C1-2019-2022');
    expect([fifth.windows.train.to, fifth.windows.eval.from, fifth.windows.eval.to]).toEqual(['2022-12-31', '2023-01-01', '2023-06-30']);
  });

  it('times 2023-01-01 and 2023-07-01; all five at 256e694, clean; only v1 keeps its file', () => {
    expect(runs.map((r) => r.time)).toEqual(['2023-01-01T00:00:00Z', '2023-01-01T00:00:00Z', '2023-07-01T00:00:00Z', '2023-01-01T00:00:00Z', '2023-01-01T00:00:00Z']);
    expect(l).toContain('2023-01-01 for the four runs scored on 2022, 2023-07-01 for the retrain');
    expect(new Set(runs.map((r) => r.git_commit))).toEqual(new Set(['256e694e42f09fa011cf13aaade487fe543778e7']));
    expect(runs.every((r) => r.git_dirty === false)).toBe(true);
    expect(l).toContain('All five records name commit `256e694` with `git_dirty: false`.');
    expect(tryitText('07-tracking')).toContain(`<  "git_commit": "${v1run.git_commit}",`);
    const experiments = runs.filter((r) => r.time === '2023-01-01T00:00:00Z');
    expect(experiments.filter((r) => r.model.path).map((r) => r.run_id)).toEqual(['logreg-C1-2019-2021']);
    expect(l).toContain('Only v1\'s run keeps its model file. The other three experiment models were written to a temporary folder and deleted');
  });

  it('C = 3 against C = 1: one param, test moves < 0.02, train macro-F1 0.899 → 0.964 (+6.4 points)', () => {
    const a = v1run;
    const b = run('logreg-C3-2019-2021');
    expect(Object.keys(a.params).filter((k) => a.params[k] !== b.params[k])).toEqual(['C']);
    for (const k of ['accuracy', 'macro_f1', 'weighted_f1']) expect(Math.abs(a.metrics.test[k] - b.metrics.test[k])).toBeLessThan(0.02);
    expect(a.metrics.test.accuracy).toBe(b.metrics.test.accuracy);
    expect(l).toContain('`test.accuracy` is missing: both runs score exactly 0.808 on the test year.');
    expect([f3(a.metrics.train.macro_f1), f3(b.metrics.train.macro_f1), ((b.metrics.train.macro_f1 - a.metrics.train.macro_f1) * 100).toFixed(1)]).toEqual(['0.899', '0.964', '6.4']);
    expect(l).toContain('`train.macro_f1` goes from 0.899 to 0.964.');
    expect(l).toContain('training macro-F1 jumps 6.4 points');
    expect(Object.entries(registry.versions).map(([v, x]) => [v, (x as { run: string; stage: string }).run, (x as { stage: string }).stage]))
      .toEqual([['v1', 'logreg-C1-2019-2021', 'production'], ['v2', 'logreg-C1-2019-2022', 'staging']]);
    expect(l).toContain('only two runs became versions: v1 in production and v2 in staging');
  });

  it("section 1 is about 90 lines; the MLflow table's last rows come from v1's run", () => {
    const mlops = src('ml/python/mlops.py').split('\n');
    const start = mlops.findIndex((x) => x.startsWith('# ── 1. '));
    const end = mlops.findIndex((x) => x.startsWith('# ── 2. '));
    expect(Math.abs(end - start - 90)).toBeLessThanOrEqual(5);
    expect(l).toContain('its tracker is section 1 of `mlops.py`, about 90 lines of standard-library Python');
    const g = (x: number) => Number(x.toPrecision(6)).toString();
    const row = (label: string, v: string) => `${label.padEnd(28)}${v.padEnd(22)}${v.padEnd(22)}ok`;
    expect(tryitText('07-tracking')).toContain([
      row('metric train.macro_f1', g(v1run.metrics.train.macro_f1)),
      row('metric train.weighted_f1', g(v1run.metrics.train.weighted_f1)),
      row('model sha256', v1run.model.sha256.slice(0, 20)),
      '', 'the two records agree'].join('\n'));
    expect(src('ml/python/with_mlflow.py')).toContain('Tested on MLflow 3.17.2');
    expect(l).toContain('It was tested on MLflow 3.17.2.');
    expect(read('07-tracking')).toContain('/tmp/mlflow-venv/bin/pip install -r requirements.txt "mlflow==3.17.2"');
  });
});

function readdirIds(): string[] {
  return readdirSync(join(repo, 'ml/runs')).filter((f) => f.endsWith('.json')).map((f) => f.replace(/\.json$/, '')).sort();
}

describe('lesson 8 (registry): the numbers it states', () => {
  const l = flat('08-registry');
  const H = registry.history as { time: string; action: string; version: string; from_stage: string | null; to_stage: string; note: string }[];

  it('the four history events and the gate note', () => {
    expect(H.map((e) => [e.time.slice(0, 10), e.action, e.version, e.from_stage, e.to_stage])).toEqual([
      ['2023-01-01', 'register', 'v1', null, 'staging'],
      ['2023-01-01', 'promote', 'v1', 'staging', 'production'],
      ['2023-07-01', 'register', 'v2', null, 'staging'],
      ['2023-07-01', 'gate', 'v2', 'staging', 'staging'],
    ]);
    expect(l).toContain('The committed history has four:');
    expect(l).toContain('1. 2023-01-01, `register` v1: into staging. 2. 2023-01-01, `promote` v1: staging to production.');
    expect(l).toContain(`3. 2023-07-01, \`register\` v2: into staging, "${H[2].note}".`);
    expect(l).toContain(`4. 2023-07-01, \`gate\` v2: staging to staging, with the note \`${H[3].note}\`.`);
  });

  it('the gate numbers: 0.764 against 0.759, +0.005 inside 0.058 on 300, floor 0.50', () => {
    const v2 = registry.versions.v2.metrics;
    const holdout = run('logreg-C1-2019-2022');
    expect([f3(v2.macro_f1), v2.n, f3(v2.band), constant('ml/python/mlops.py', 'FLOOR')]).toEqual(['0.764', 300, '0.058', 0.5]);
    expect(holdout.metrics.holdout.macro_f1).toBe(v2.macro_f1);
    expect(H[3].note).toBe(`failed vs v1: macro-F1 ${f3(v2.macro_f1)} vs 0.759: gain +0.005 <= band ${f3(v2.band)} (n=${v2.n}); every category's F1 is at or above the floor 0.50`);
    expect(l).toContain('because the gate\'s gain of 0.005 was inside the 0.058 band');
  });

  it("the Try it's messages and the tampered file's hash", async () => {
    const mlops = src('ml/python/mlops.py');
    expect(mlops).toContain('raise ValueError("no production version to roll back from")');
    expect(mlops).toContain('raise ValueError(f"no previous production version before {current}")');
    expect(mlops).toContain('print(f"{args.version} -> {args.stage}")');
    expect(mlops).toContain('print(f"production is now {rollback(registry=reg, time=args.time)}")');
    const block = tryitText('08-registry');
    expect(block).toContain('ValueError: no production version to roll back from\n');
    expect(block).toContain('v1 -> production\nregistered v2\nValueError: no previous production version before v1\n');
    expect(src('ml/go/main.go')).toContain('fmt.Errorf("SHA-256 mismatch for %s: registry says %s, file is %s", version, entry.SHA256, actual)');
    const { createHash } = await import('node:crypto');
    const bytes = readFileSync(join(repo, 'ml/models/v1.json'));
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(registry.versions.v1.sha256);
    const tampered = createHash('sha256').update(Buffer.concat([bytes, Buffer.from(' ')])).digest('hex');
    expect(tampered.startsWith('95b43f4e')).toBe(true);
    expect(block).toContain(`startup failed: SHA-256 mismatch for v1: registry says ${registry.versions.v1.sha256}, file is ${tampered}\n`);
  });
});

describe('lesson 9 (serving): the numbers it states', () => {
  const l = flat('09-serving');
  const S = 'ml/serve/server.py';

  it('the limits: 20,000 characters, 131,072 bytes (128 KiB), and the two error bodies', () => {
    expect([constant(S, 'MAX_CHARS'), constant(S, 'MAX_BODY')]).toEqual([20000, 131072]);
    expect(131072 / 1024).toBe(128);
    expect(src('ml/go/main.go')).toMatch(/maxChars\s*=\s*20000/);
    expect(src('ml/go/main.go')).toMatch(/maxBody\s*=\s*131072/);
    expect(l).toContain('`MAX_CHARS = 20_000` and `MAX_BODY = 131_072` are the two limits.');
    expect(l).toContain('131,072 bytes (128 KiB) fits 20,000 characters');
    expect(l).toContain('| body over 131,072 bytes |');
    expect(src(S)).toContain('LENGTH_ERROR = {"error": f"text must be 1 to {MAX_CHARS} characters"}');
    expect(l).toContain('400 `{"error": "text must be 1 to 20000 characters"}`');
    expect(tryitText('09-serving')).toContain('{"error":"text must be 1 to 20000 characters"} 400\n{"error":"text must be 1 to 20000 characters"} 400');
  });

  it('parity: 28 valid texts (Go pins 13 identical and 5.6e-17 in TestParityAsLesson9States)', () => {
    const cases = json('ml/data/fixtures/predictions.json').cases as { input: string }[];
    expect(cases.filter((c) => [...c.input].length >= 1 && [...c.input].length <= 20000)).toHaveLength(28);
    expect(src('ml/go/main_test.go')).toContain('func TestParityAsLesson9States(t *testing.T) {');
    expect(l).toContain('The tests require 10⁻⁹.');
  });

  it('the benchmark: 500 requests and 20 warm-ups each, an Apple M5, the medians and p95s, 2.1 times', () => {
    const r = latency.request_set;
    expect([r.requests_per_server, r.warmup_per_server, r.distinct_texts, latency.machine.cpu]).toEqual([500, 20, 28, 'Apple M5']);
    expect(l).toContain('sent 500 requests to each server, one at a time over localhost, alternating between them, after 20 warm-up requests each. On the one machine measured, an Apple M5:');
    const py = latency.servers.python;
    const go = latency.servers.go;
    expect(l).toContain(`| Python (FastAPI, uvicorn, 1 worker) | ${f3(py.p50_ms)} ms | ${f3(py.p95_ms)} ms |`);
    expect(l).toContain(`| Go (\`net/http\`) | ${f3(go.p50_ms)} ms | ${f3(go.p95_ms)} ms |`);
    expect(latency.ratio_python_over_go.p50.toFixed(1)).toBe('2.1');
    expect(l).toContain('2.1 times faster at the median');
    expect(latency.note).toMatch(/^Numbers from ONE machine/);
  });

  it('the Go server listens on 127.0.0.1:8001; the Python one on 8000', () => {
    expect(src('ml/go/main.go')).toContain('port := fs.Int("port", 8001, "port on 127.0.0.1")');
    expect(src('ml/go/main.go')).toContain('log.Printf("serving %s on http://%s", m.Version, addr)');
    expect(l).toContain('The Go one prints `serving v1 on http://127.0.0.1:8001`.');
  });
});

describe('lesson 10 (drift): the numbers it states', () => {
  const l = flat('10-drift');
  const N = drift.null;
  const U = drift.null_unstratified;
  const M = 'ml/python/monitor.py';
  type Period = { month?: string; quarter?: string; n: number; psi_predictions: number; psi_words: number; alert_convention: boolean; alert_calibrated: boolean; top_shifted_words: { word: string; direction: string }[] };
  const months: Period[] = drift.months;
  const quarters: Period[] = drift.quarters;
  const month = (m: string) => months.find((p) => p.month === m)!;
  const quarter = (q: string) => quarters.find((p) => p.quarter === q)!;

  it('the settings: 50 a month, 68 stop words, top 50 words holding 23%, epsilon 1e-4, MIN_N 30, 5,000 draws, seed 7', () => {
    const st = drift.settings;
    expect([st.stop_words.length, st.top_k, st.epsilon, st.alert_at, st.min_n]).toEqual([68, 50, 1e-4, 0.2, 30]);
    expect([constant(M, 'TOP_K'), constant(M, 'MIN_N'), constant(M, 'DRAWS'), constant(M, 'ALERT_AT')]).toEqual([50, 30, 5000, 0.2]);
    expect(src(M)).toMatch(/^EPSILON = 1e-4/m);
    expect(st.stop_words).toEqual(expect.arrayContaining(['the', 'my', 'was']));
    expect(st.stop_words).not.toContain('not');
    expect(st.stop_words).not.toContain('no');
    expect(l).toContain('For words, 68 stop words such as "the", "my" and "was" are left out');
    const stop = new Set(st.stop_words);
    const vocab = new Set(V1.vocabulary);
    const counts = new Map<string, number>();
    for (const r of train) for (const t of tokenize(r.narrative, V1.tokenizer)) if (!stop.has(t)) counts.set(t, (counts.get(t) ?? 0) + 1);
    const known = [...counts.entries()].filter(([w]) => vocab.has(w)).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
    const share = sum(known.slice(0, 50).map(([, n]) => n)) / sum([...counts.values()]);
    expect(Math.round(share * 100)).toBe(23);
    expect(l).toContain('With it, the top 50 hold 23% of the remaining training tokens.');
    expect(l).toContain('`epsilon = 1e-4`');
    expect(l).toContain('`big` is `n >= MIN_N`, 30');
    expect([N.month.draws, N.month.seed, N.month.n, N.quarter.n]).toEqual([5000, 7, 50, 150]);
    expect(months).toHaveLength(24);
    expect(quarters).toHaveLength(8);
    expect(months.every((p) => p.n === 50) && quarters.every((p) => p.n === 150)).toBe(true);
  });

  it('the in-sample reference: 9 of 24 months, up to 0.345 in June 2024; v1 90% on training, 77-79% on 2023 and 2024', () => {
    const ins = drift.reference_training_in_sample.months as Period[];
    expect(ins.filter((p) => p.alert_convention)).toHaveLength(9);
    expect(ins.filter((p) => p.psi_words >= 0.2)).toHaveLength(0);
    const top = ins.reduce((a, b) => (b.psi_predictions > a.psi_predictions ? b : a));
    expect([top.month, f3(top.psi_predictions)]).toEqual(['2024-06', '0.345']);
    expect(l).toContain('**9 of the 24 stream months cross 0.2**, all on the predicted-category mix, as high as 0.345 in June 2024.');
    expect(Math.round(metrics.models.logreg.train.accuracy * 100)).toBe(90);
    const acc = ['2023', '2024'].map((y) => {
      const rs = rows.filter((r) => r.date.startsWith(y));
      return Math.round((rs.filter((r) => scoreText(V1, r.narrative).label === r.product).length / rs.length) * 100);
    });
    expect(acc).toEqual([79, 77]);
    expect(l).toContain('v1 is 90% right on its own training complaints and 77–79% right on months it never saw.');
  });

  it('against 2022: nothing crosses 0.2; the highest month is 0.192 in June 2024', () => {
    expect([...months, ...quarters].some((p) => p.alert_convention)).toBe(false);
    const top = months.reduce((a, b) => (b.psi_predictions > a.psi_predictions ? b : a));
    expect([top.month, f3(top.psi_predictions)]).toEqual(['2024-06', '0.192']);
    expect(l).toContain('The highest monthly value is 0.192 on predictions, in June 2024.');
  });

  it("the null: January 2022's counts and the other 550; 0.046 / 0.144 / 1.4%; within 0.134 and 0.062", () => {
    const jan = test.filter((r) => r.date.startsWith('2022-01'));
    const c = (p: string) => jan.filter((r) => r.product === p).length;
    expect([jan.length, c(CR), c('Debt collection'), c('Checking or savings account'), c('Credit card'), c('Mortgage'), c('Money transfer, virtual currency, or money service')])
      .toEqual([50, 20, 7, 6, 6, 6, 5]);
    expect(l).toContain('for January 2022, 20 credit reporting, 7 debt collection, 6 each of checking, credit card and mortgage, and 5 money transfer). Compare each draw with the remaining 550.');
    const mp = N.month.predictions;
    expect([f3(mp.p50), f3(mp.p95), p1(mp.share_at_or_above_alert_at)]).toEqual(['0.046', '0.144', '1.4']);
    expect(l).toContain('the median predictions PSI is 0.046 and the 95th percentile 0.144; 1.4% of the draws reach 0.2.');
    expect([f3(mp.within_2022_max), f3(N.month.words.within_2022_max)]).toEqual(['0.134', '0.062']);
    expect(l).toContain('The largest is 0.134 on predictions and 0.062 on words.');
  });

  it('the thresholds: months 0.144 and 0.062, quarters 0.053 and 0.026', () => {
    const t = (u: string, s: string) => N[u][s].threshold;
    expect(t('month', 'predictions')).toBe(N.month.predictions.p95);
    expect(t('month', 'words')).toBe(N.month.words.within_2022_max);
    expect([f3(t('month', 'predictions')), f3(t('month', 'words')), f3(t('quarter', 'predictions')), f3(t('quarter', 'words'))]).toEqual(['0.144', '0.062', '0.053', '0.026']);
    expect(l).toContain('For months that is 0.144 on predictions (the 95th percentile) and 0.062 on words (the within-2022 maximum).');
    expect(l).toContain('noise is smaller and so are the thresholds: 0.053 on predictions and 0.026 on words.');
    expect(tryitText('10-drift')).toContain(`null threshold at n=50: predictions ${t('month', 'predictions').toFixed(4)}, words ${t('month', 'words').toFixed(4)}`);
  });

  it('the random null: 0.102 / 0.306 / 15.0% (one month in seven), words 0.047 against 0.052', () => {
    const up = U.month.predictions;
    expect([f3(up.p50), f3(up.p95), p1(up.share_at_or_above_alert_at), Math.round(1 / up.share_at_or_above_alert_at)]).toEqual(['0.102', '0.306', '15.0', 7]);
    expect(l).toContain('Drawn at random, the no-drift median is 0.102 and the 95th percentile 0.306, and 15.0% of no-drift months reach 0.2.');
    expect(l).toContain('about one month in seven');
    expect(Math.round(up.p95 / N.month.predictions.p95)).toBe(2);
    expect(l).toContain('it would set the predictions threshold twice as high');
    expect([f3(N.month.words.p95), f3(U.month.words.p95)]).toEqual(['0.047', '0.052']);
    expect(l).toContain('(95th percentile 0.047 drawn like the sample, 0.052 at random)');
  });

  it('the monthly alerts: 3 of 24, the binomial tails 33.9%, 70.8%, 43.6%; 1.2 per signal', () => {
    const calib = months.filter((p) => p.alert_calibrated).map((p) => p.month!);
    expect(calib).toEqual(['2024-01', '2024-06', '2024-11']);
    expect([f3(month('2024-01').psi_predictions), f3(month('2024-06').psi_predictions), f3(month('2024-11').psi_words)]).toEqual(['0.171', '0.192', '0.082']);
    expect(l).toContain('**alerts in 3 of 24 months**. On predictions, January 2024 (0.171) and June 2024 (0.192), both above the 0.144 threshold and below 0.2; on words, November 2024 (0.082 against 0.062).');
    expect(N.expected_chance_alerts.month).toEqual({ per_signal: 1.2, either_signal_max: 2.4 });
    expect(l).toContain('24 months give at most 1.2 alerts per signal on average by chance');
    expect([p1(binomialTail(24, 0.05, 2)), p1(binomialTail(24, 0.05, 1)), p1(binomialTail(24, 0.1, 3))]).toEqual(['33.9', '70.8', '43.6']);
    expect(l).toContain('two or more on predictions happens up to 33.9% of the time with no drift, one or more on words up to 70.8%, and three or more months with an alert on either signal up to 43.6% (at most 10% a month for the two together)');
    const block = tryitText('10-drift');
    for (const m of calib) {
      const p = month(m);
      expect(block).toContain(`${m}    50    ${p.psi_predictions.toFixed(4)}     ${p.psi_words.toFixed(4)}  -     YES    ${p.top_shifted_words.map((w) => w.word + (w.direction === 'up' ? '+' : '-')).join(', ')}`);
    }
  });

  it('the quarterly alerts: 2024-Q1 (0.0277 against 0.026) and 2024-Q4; 0.4 by chance, 5.7% and 11.4%; 0.019 and 0.044 against 0.053', () => {
    expect(quarters.filter((p) => p.alert_calibrated).map((p) => p.quarter)).toEqual(['2024-Q1', '2024-Q4']);
    expect(quarters.filter((p) => p.alert_calibrated).every((p) => p.psi_words > N.quarter.words.threshold && p.psi_predictions < N.quarter.predictions.threshold)).toBe(true);
    expect(l).toContain('**two alerts in 8 quarters**, 2024-Q1 and 2024-Q4, both on words.');
    expect(N.expected_chance_alerts.quarter.per_signal).toBe(0.4);
    const q = binomialTail(8, 0.05, 2);
    expect([p1(q), p1(2 * q)]).toEqual(['5.7', '11.4']);
    expect(l).toContain('At most 0.4 per signal are expected by chance, and two or more on one signal happens up to 5.7% of the time with no drift; with two signals that is up to 11.4%.');
    expect(quarter('2024-Q1').psi_words.toFixed(4)).toBe('0.0277');
    expect(l).toContain('2024-Q1 (0.0277) barely clears a threshold (0.026)');
    const w = quarter('2024-Q4').top_shifted_words;
    expect(w.filter((x) => x.direction === 'up').map((x) => x.word)).toEqual(['report', 'accounts', 'chase']);
    expect(w.filter((x) => x.direction === 'down').map((x) => x.word)).toEqual(['told', 'sent']);
    expect(l).toContain('The words that moved most in 2024-Q4 were "report", "accounts" and "chase" (up) and "told" and "sent" (down).');
    expect([f3(quarter('2024-Q1').psi_predictions), f3(quarter('2024-Q2').psi_predictions)]).toEqual(['0.019', '0.044']);
    expect(l).toContain('the quarters holding January and June 2024 reach 0.019 and 0.044 against a 0.053 threshold');
  });

  it('what the sample hides: 72.4% to 80.0%, mortgage 3.8% to 1.5%, the sample 43.3% to 46.7%; 172 and 92', () => {
    const r = (y: string, c: string) => p1(drift.real_class_shares[y][c]);
    const s2 = (y: string, c: string) => p1(drift.sample_class_shares[y][c]);
    expect([r('2022', CR), r('2024', CR), r('2022', 'Mortgage'), r('2024', 'Mortgage'), s2('2022', CR), s2('2024', CR)]).toEqual(['72.4', '80.0', '3.8', '1.5', '43.3', '46.7']);
    expect(l).toContain('credit reporting was 72.4% of these six categories\' narratives in 2022 and 80.0% in 2024; mortgage fell from 3.8% to 1.5%.');
    expect(l).toContain('its credit reporting share only went from 43.3% to 46.7%');
    expect(l).toContain('In the sample\'s 2023, 172 credit reporting complaints use the old name and 92 the new one.');
  });
});

describe('lesson 11 (capstone): the numbers it states', () => {
  const l = flat('11-capstone');
  const r2 = run('logreg-C1-2019-2022');
  const v2m = r2.metrics.holdout;
  const v1m = r2.metrics.production_holdout;
  const gate = r2.metrics.gate;
  const MT = 'Money transfer, virtual currency, or money service';

  it("v2: v1's settings on 2019-2022, 2,400 complaints, 6,189 words, 952,999 bytes", () => {
    const V2 = json('ml/models/v2.json');
    expect(r2.params).toEqual(run('logreg-C1-2019-2021').params);
    expect(rows.filter((r) => r.date <= '2022-12-31')).toHaveLength(2400);
    expect(V2.vocabulary).toHaveLength(6189);
    expect(statSync(join(repo, 'ml/models/v2.json')).size).toBe(952999);
    expect(l).toContain('v2 learns from 2019–2022, 2,400 complaints instead of 1,800. Its vocabulary grows from 5,539 words to 6,189, and its file, `ml/models/v2.json`, is 952,999 bytes.');
  });

  it('the holdout: 300 complaints of 2023 H1, band 0.058, floor 0.5', () => {
    expect([v2m.n, v1m.n, f3(v2m.band), gate.floor]).toEqual([300, 300, '0.058', 0.5]);
    expect(r2.windows.eval).toEqual({ name: 'holdout', from: '2023-01-01', to: '2023-06-30' });
    expect(rows.filter((r) => r.date >= '2023-01-01' && r.date <= '2023-06-30')).toHaveLength(300);
    expect(l).toContain('300 complaints from January to June that neither model trained on');
    expect(l).toContain('With 300 complaints that is 0.058.');
    expect(l).toContain("No category's F1 may be below 0.5");
  });

  it('the result table, +0.005 inside ±0.058, 244 against 243, FAILED', () => {
    const low = (m: { per_class_f1: Record<string, number> }) => Object.entries(m.per_class_f1).reduce((a, b) => (b[1] < a[1] ? b : a));
    expect(low(v1m)[0]).toBe(MT);
    expect(low(v2m)[0]).toBe(MT);
    expect(l).toContain(`| Macro-F1 on 2023 H1 | ${f3(v1m.macro_f1)} | ${f3(v2m.macro_f1)} |`);
    expect(l).toContain(`| Accuracy | ${f3(v1m.accuracy)} | ${f3(v2m.accuracy)} |`);
    expect(l).toContain(`| Lowest category F1 | ${f3(low(v1m)[1])}, money transfer | ${f3(low(v2m)[1])}, money transfer |`);
    expect([f3(gate.gain), gate.passed, gate.production_version]).toEqual(['0.005', false, 'v1']);
    expect(l).toContain('The gain is +0.005 macro-F1, inside the ±0.058 band.');
    expect([Math.round(v2m.accuracy * 300), Math.round(v1m.accuracy * 300)]).toEqual([244, 243]);
    expect(l).toContain('v2 gets 244 of the 300 right and v1 gets 243: one complaint.');
    expect(l).toContain('the lowest, money transfer at 0.583, is above 0.5');
    expect(tryitText('11-capstone')).toContain(`FAILED vs v1\n  ${gate.reasons[0]}\n  ${gate.reasons[1]}\nexit code 1\n`);
    expect(registry.versions.v2.stage).toBe('staging');
  });

  it('the per-category pairs and 37,000 complaints to see a 0.005 gain', () => {
    const pair = (c: string) => `${f3(v2m.per_class_f1[c])} against ${f3(v1m.per_class_f1[c])}`;
    expect(l).toContain(`v2 is ahead on checking (${pair('Checking or savings account')}) and credit card (${pair('Credit card')}) and behind on debt collection (${pair('Debt collection')}) and mortgage (${pair('Mortgage')})`);
    expect(pair('Mortgage')).toBe('0.968 against 0.984');
    expect(Math.round(1 / gate.gain ** 2 / 1000)).toBe(37);
    expect(Math.round(1 / 0.0052 ** 2 / 1000)).toBe(37);
    expect(gate.gain.toFixed(4)).toBe('0.0052');
    expect(l).toContain('which needs about 37,000 holdout complaints (1/0.0052²)');
  });
});
