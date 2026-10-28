// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { forExplorer, loadMlData, scoreText, tfidf, type ModelJson } from '../src/lib/mlData';
import {
  DEBOUNCE_MS,
  SNIPPET_MAX,
  balanceRows,
  complaintExplorerProps,
  explain,
  featureExplorerProps,
  featurePresets,
  filterSnippets,
  metricsViewProps,
  overfit,
  packExplorer,
  unpackExplorer,
  lossChart,
  recordedTopWords,
  shortName,
  spreadLabels,
  trainingStepperProps,
  versionOf,
} from '../src/lib/mlAids';
import ComplaintExplorer from '../src/components/ml/ComplaintExplorer';
import FeatureExplorer from '../src/components/ml/FeatureExplorer';
import TrainingStepper from '../src/components/ml/TrainingStepper';
import MetricsView from '../src/components/ml/MetricsView';

// The full model and the shared fixtures are read with node:fs; the aids only ever get derived props.
const here = dirname(fileURLToPath(import.meta.url));
const ml = join(here, '../../ml');
const readJson = (p: string) => JSON.parse(readFileSync(join(ml, p), 'utf8'));
const V1: ModelJson = readJson('models/v1.json');
const TOKENIZE: { name: string; input: string; tokens: string[] }[] = readJson('data/fixtures/tokenize.json');

const data = loadMlData();
const CR = 'Credit reporting or other personal consumer reports';
const DEBT = 'Debt collection';
const MONEY = 'Money transfer, virtual currency, or money service';
const CHECKING = 'Checking or savings account';
const size = (v: unknown) => JSON.stringify(v).length;
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }));
// mlAids.ts re-exports these modules; the islands import mlAids only.
const ML_LIB = ['mlAids.ts', 'mlAidsShared.ts', 'mlAidsBasics.ts', 'mlAidsOps.ts', 'mlAidsDrift.ts'];

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ── ComplaintExplorer ───────────────────────────────────────────────────────
// Literals from ml/reports/dataset.json (python train.py --model logreg --version v1 --write-reports)
// and the share table in ml/data/README.md (python fetch_sample.py sample), the same numbers as
// drift.json's real_class_shares (python monitor.py --write-reports).
describe('complaint explorer data', () => {
  const p = complaintExplorerProps(data);

  it('names the three time splits with their years and sizes', () => {
    expect(p.splits.map((s) => [s.label, s.n, s.years])).toEqual([
      ['train 2019–21', 1800, ['2019', '2020', '2021']],
      ['test 2022', 600, ['2022']],
      ['stream 2023–24', 1200, ['2023', '2024']],
    ]);
  });

  it('gives the sample balance overall and per year, beside the real share', () => {
    const all = balanceRows(p, null);
    const cr = all.find((r) => r.category === CR)!;
    expect(cr.count).toBe(1471);
    expect(cr.share).toBeCloseTo(1471 / 3600, 12);
    expect(cr.real).toBeNull(); // real shares exist per year only
    expect(all.reduce((s, r) => s + r.count, 0)).toBe(3600);

    const y2019 = balanceRows(p, '2019');
    expect(y2019.find((r) => r.category === CR)).toMatchObject({ count: 211 });
    expect(y2019.find((r) => r.category === CR)!.share).toBeCloseTo(0.352, 3); // README: 51.0 / 35.2
    expect(y2019.find((r) => r.category === CR)!.real).toBeCloseTo(0.51, 3);
    const y2024 = balanceRows(p, '2024');
    expect(y2024.find((r) => r.category === CR)!.share).toBeCloseTo(0.467, 3); // README: 80.0 / 46.7
    expect(y2024.find((r) => r.category === CR)!.real).toBeCloseTo(0.8, 3);
    expect(y2024.find((r) => r.category === 'Mortgage')!.real).toBeCloseTo(0.015, 3); // README: 1.5 / 10.0
    expect(y2024.find((r) => r.category === 'Mortgage')!.share).toBeCloseTo(0.1, 12);
  });

  it('keeps the report snippets (at most 200 characters) and filters them by category and year', () => {
    expect(p.snippets).toHaveLength(18);
    expect(Math.max(...p.snippets.map((s) => s.text.length))).toBeLessThanOrEqual(SNIPPET_MAX);
    expect(filterSnippets(p.snippets, { category: 'Mortgage' }).map((s) => s.id)).toEqual(['3700143', '3185955', '3435317']);
    expect(filterSnippets(p.snippets, { category: 'Mortgage', year: '2019' }).map((s) => s.id)).toEqual(['3185955', '3435317']);
    expect(filterSnippets(p.snippets, { year: '2022' })).toEqual([]);
    expect(p.snippets.find((s) => s.id === '4187485')).toMatchObject({ category: 'Credit card', product_raw: 'Credit card or prepaid card' });
  });

  it('fails clearly when the real share of a category is missing', () => {
    const drift = { ...data.drift, real_class_shares: { ...data.drift.real_class_shares, '2024': {} } };
    expect(() => complaintExplorerProps({ dataset: data.dataset, drift })).toThrow(/real share.*2024/);
  });
});

describe('ComplaintExplorer', () => {
  const p = complaintExplorerProps(data);

  it('shows the split badges and the stratified-sample caption', () => {
    const { container } = render(<ComplaintExplorer {...p} />);
    const badges = within(screen.getByRole('list', { name: 'Time splits' }));
    expect(badges.getAllByRole('listitem').map((l) => l.textContent)).toEqual([
      'train 2019–21 · 1,800',
      'test 2022 · 600',
      'stream 2023–24 · 1,200',
    ]);
    expect(container.textContent).toContain('stratified by design');
    expect(container.textContent).toContain('ml/data/README.md');
  });

  it('switches the balance between all years and one year, with the real share beside the sample', () => {
    render(<ComplaintExplorer {...p} />);
    const table = () => screen.getByRole('table', { name: /Category balance/ });
    const row = (name: string) => within(table()).getByRole('row', { name: new RegExp(`^${shortName(name)}`) });
    expect(row(CR).textContent).toContain('1,471');
    expect(row(CR).textContent).toContain('40.9%');
    expect(row(CR).textContent).toContain('per year only');

    const y2024 = screen.getByRole('button', { name: /^2024/ });
    fireEvent.click(y2024);
    expect(y2024.getAttribute('aria-pressed')).toBe('true');
    expect(row(CR).textContent).toContain('280');
    expect(row(CR).textContent).toContain('46.7%');
    expect(row(CR).textContent).toContain('80.0%');
    expect(row('Mortgage').textContent).toContain('1.5%');
    expect(screen.getByText(/^2024 · stream split · 600 complaints/)).toBeTruthy();
  });

  it('filters the sample narratives by category and year, and says how many it shows', () => {
    const { container } = render(<ComplaintExplorer {...p} />);
    expect(screen.getByText('18 of 18 snippets')).toBeTruthy();
    const filters = within(screen.getByRole('group', { name: 'Snippets: category' }));
    fireEvent.click(filters.getByRole('button', { name: 'Mortgage' }));
    expect(screen.getByText('3 of 18 snippets')).toBeTruthy();
    fireEvent.click(within(screen.getByRole('group', { name: 'Snippets: year' })).getByRole('button', { name: '2019' }));
    expect(screen.getByText('2 of 18 snippets')).toBeTruthy();
    expect([...container.querySelectorAll('.cx-id')].map((e) => e.textContent)).toEqual(['3185955', '3435317']);
    click('Clear snippet filters');
    expect(screen.getByText('18 of 18 snippets')).toBeTruthy();
  });

  it('names the product as published when it was renamed', () => {
    const { container } = render(<ComplaintExplorer {...p} />);
    expect(container.textContent).toContain('published as “Credit card or prepaid card”');
  });
});

// ── FeatureExplorer ─────────────────────────────────────────────────────────
// Preset ids, labels and weights from the Python model code (read-only, nothing written):
//   cd ml/python && python3 -c "import train as T, json; rows=T.load_complaints('../data/complaints.jsonl');
//     m=json.load(open('../models/v1.json')); ... T.score(m, text); T.tokenize(text) ..."
//   presets (lowest id per category, 2022, 200-600 characters):
//     5147718 Checking -> Credit reporting (0.2569, low confidence)   5061687 Credit card -> Credit card (0.6534)
//     5076151 Credit reporting -> Credit reporting (0.6014)            5122859 Debt collection -> Debt collection (0.7125)
//     5084895 Money transfer -> Money transfer (0.3386, low)           5072922 Mortgage -> Mortgage (0.2712, low)
//   5122859: top TF-IDF contract x2 idf 3.9852 w 0.378, litigate x1 7.3975 0.3509, this x4 1.4044 0.2664;
//            Debt collection's top contributions debt 0.673, this 0.4248, contract 0.3516; no unknown words
describe('feature explorer data', () => {
  const props = featureExplorerProps(data);
  const p = { ...props, model: unpackExplorer(props.model) };
  const preset = (id: string) => p.presets.find((x) => x.id === id)!;

  it('ships the rounded explorer model, packed into strings, and unpacks it unchanged', () => {
    expect(p.model).toEqual(forExplorer(data.explorer));
    expect(typeof props.model.vocabulary).toBe('string');
    expect(unpackExplorer(packExplorer(p.model))).toEqual(p.model);
    expect(() => packExplorer({ ...p.model, vocabulary: ['two words'] })).toThrow(/space/);
  });

  it('picks one 2022 complaint per category: the lowest id of 200 to 600 characters', () => {
    expect(p.presets.map((x) => [x.id, x.category])).toEqual([
      ['5147718', CHECKING],
      ['5061687', 'Credit card'],
      ['5076151', CR],
      ['5122859', DEBT],
      ['5084895', MONEY],
      ['5072922', 'Mortgage'],
    ]);
    expect(featurePresets(data.complaints, V1.classes, { from: '2022-01-01', to: '2022-12-31' })).toEqual(p.presets);
    for (const x of p.presets) expect(x.text.length).toBeGreaterThanOrEqual(200);
  });

  it('labels every preset as the full model (the server) does', () => {
    const expected = [CR, 'Credit card', CR, DEBT, MONEY, 'Mortgage'];
    expect(p.presets.map((x) => explain(p.model, x.text).prediction.label)).toEqual(expected);
    expect(p.presets.map((x) => scoreText(V1, x.text).label)).toEqual(expected);
    expect(p.presets.map((x) => explain(p.model, x.text).prediction.low_confidence)).toEqual([true, false, false, false, true, true]);
  });

  it('explains a text: exact TF-IDF weights, then each class with its top contributing words', () => {
    const e = explain(p.model, preset('5122859').text);
    expect(e.tokens).toHaveLength(46);
    expect(e.unknown).toEqual([]);
    expect(e.terms.slice(0, 3).map((t) => [t.term, t.count])).toEqual([['contract', 2], ['litigate', 1], ['this', 4]]);
    expect(e.terms[0].idf).toBeCloseTo(3.9852, 3);
    expect(e.terms[0].weight).toBeCloseTo(0.378, 3);
    expect(e.terms[1].weight).toBeCloseTo(0.3509, 3);
    expect(e.classes[0].label).toBe(DEBT);
    expect(e.classes[0].p).toBeCloseTo(0.7125, 1);
    expect(e.classes[0].top.map((c) => c.term)).toEqual(['debt', 'this', 'contract']);
    expect(e.classes[0].top[0].value).toBeCloseTo(0.673, 2);
    expect(e.classes.map((c) => c.p).reduce((a, b) => a + b, 0)).toBeCloseTo(1, 12);
    // Every known word's weight is the exact model's, to the rounding of idf.
    const exact = tfidf(V1, preset('5122859').text);
    e.terms.forEach((t) => expect(t.weight).toBeCloseTo(exact.terms.find((x) => x.term === t.term)!.weight, 3));
  });

  it('says which known words carry no class weights in the explorer', () => {
    const e = explain(p.model, 'zzqx blorptag escrow');
    expect(e.unknown).toEqual(['zzqx', 'blorptag']);
    expect(e.terms.map((t) => [t.term, t.hasWeights])).toEqual([['escrow', true]]);
    expect(e.prediction.label).toBe('Mortgage'); // predictions.json: "one known word among unknown"
    const rare = p.model.vocabulary.find((_, j) => !p.model.coef_terms.includes(j))!;
    expect(explain(p.model, rare).terms[0].hasWeights).toBe(false);
  });

  it('an empty text has no tokens and falls back to the majority class', () => {
    const e = explain(p.model, '   ');
    expect(e.tokens).toEqual([]);
    expect(e.prediction).toMatchObject({ label: CR, low_confidence: true });
    expect(e.classes.every((c) => c.top.length === 0)).toBe(true);
  });
});

describe('FeatureExplorer', () => {
  const p = featureExplorerProps(data);
  const tokens = () => within(screen.getByRole('list', { name: 'Tokens' })).queryAllByRole('listitem').map((l) => l.textContent);
  const type = (text: string) => {
    fireEvent.input(screen.getByLabelText('Complaint text'), { target: { value: text } });
    act(() => void vi.advanceTimersByTime(DEBOUNCE_MS));
  };

  it('announces only the one-line prediction, not the token list and tables (M9)', () => {
    const { container } = render(<FeatureExplorer {...p} />);
    const live = container.querySelectorAll('[aria-live]');
    expect(live).toHaveLength(1);
    expect(live[0].textContent).toMatch(/^Predicted: /);
    expect(live[0].querySelector('table, ul, ol')).toBeNull();
  });

  it('shows the slice disclosure up front', () => {
    render(<FeatureExplorer {...p} />);
    expect(screen.getByRole('note').textContent).toContain(p.model.note);
    // idf is rounded to 4 significant figures, so "exact" is qualified.
    expect(screen.getByRole('note').textContent).toContain('exact at the precision shown');
    expect(p.model.note).toMatch(/^Weights for the 1,200 most influential of v1's 5,539 words; the label matches the full model on \d+\.\d% of 1,800 complaints/);
  });

  it('waits for typing to pause before it re-tokenizes', () => {
    vi.useFakeTimers();
    render(<FeatureExplorer {...p} />);
    fireEvent.input(screen.getByLabelText('Complaint text'), { target: { value: 'my mortgage escrow' } });
    act(() => void vi.advanceTimersByTime(DEBOUNCE_MS - 1));
    expect(tokens()).not.toEqual(['my', 'mortgage', 'escrow']);
    act(() => void vi.advanceTimersByTime(1));
    expect(tokens()).toEqual(['my', 'mortgage', 'escrow']);
    expect(screen.getByText(/^Predicted: Mortgage/)).toBeTruthy();
  });

  it('tokenizes in the browser exactly like Python (ml/data/fixtures/tokenize.json)', () => {
    vi.useFakeTimers();
    render(<FeatureExplorer {...p} />);
    expect(TOKENIZE.length).toBeGreaterThanOrEqual(20);
    for (const c of TOKENIZE) {
      type(c.input);
      // An unknown word is shown as "word (unknown)"; the token itself is the same.
      expect(tokens().map((t) => t!.replace(/ \(unknown\)$/, '')), c.name).toEqual(c.tokens);
    }
  });

  it('marks unknown words in text, not only by colour', () => {
    vi.useFakeTimers();
    render(<FeatureExplorer {...p} />);
    type('zzqx blorptag escrow');
    expect(tokens()).toEqual(['zzqx (unknown)', 'blorptag (unknown)', 'escrow']);
    expect(screen.getByText(/^Unknown words \(2\)/)).toBeTruthy();
    expect(screen.getByText(/^Predicted: Mortgage/)).toBeTruthy();
  });

  it('a preset fills the box at once and shows the true category next to the label', () => {
    render(<FeatureExplorer {...p} />);
    click(/5122859/);
    expect((screen.getByLabelText('Complaint text') as HTMLTextAreaElement).value).toBe(p.presets[3].text);
    expect(screen.getByText(/^Predicted: Debt collection/)).toBeTruthy();
    expect(screen.getByText(/true category: Debt collection \(match\)/)).toBeTruthy();
    const terms = within(screen.getByRole('table', { name: /TF-IDF/ }));
    const contract = terms.getByRole('row', { name: /^contract/ });
    expect(contract.textContent).toContain('3.985');
    expect(contract.textContent).toContain('0.378');
    click(/5147718/);
    expect(screen.getByText(/true category: Checking or savings account \(mismatch\)/)).toBeTruthy();
    expect(screen.getByText(/low confidence/)).toBeTruthy();
  });

  it('lists every class with its probability and top words', () => {
    render(<FeatureExplorer {...p} />);
    click(/5122859/);
    const classes = within(screen.getByRole('list', { name: 'Classes, most likely first' }));
    const items = classes.getAllByRole('listitem');
    expect(items).toHaveLength(6);
    expect(items[0].textContent).toContain('Debt collection');
    expect(items[0].textContent).toMatch(/debt \+0\.67/);
  });
});

// ── TrainingStepper ─────────────────────────────────────────────────────────
// Literals from ml/reports/training_log.json (python train.py --model logreg --version v1 --write-reports):
// iteration 0 loss 1.7917594692280547 (ln 6) penalty 0; 44: loss 0.6026532537535301, penalty 0.26995429824656253;
// top words at 1, 8, 21, 44; at 44 Credit card: card 5.1591, credit 2.332, capital 1.8636.
describe('training stepper data', () => {
  const p = trainingStepperProps(data.trainingLog);

  it('keeps every checkpoint with loss + penalty, which falls at every step', () => {
    expect(p.checkpoints.map((c) => c.iteration)).toEqual([0, 1, 2, 3, 5, 8, 13, 21, 34, 44]);
    expect(p.checkpoints[0].objective).toBeCloseTo(Math.log(6), 12);
    expect(p.checkpoints[9].objective).toBeCloseTo(0.6026532537535301 + 0.26995429824656253, 12);
    p.checkpoints.slice(1).forEach((c, i) => expect(c.objective).toBeLessThan(p.checkpoints[i].objective));
    expect(p.convergedAt).toBe(44);
    expect(p.note).toBe(data.trainingLog.note);
  });

  it('shows the top words recorded at or before the current step', () => {
    expect(recordedTopWords(p.checkpoints, 0)).toBeNull();
    expect(recordedTopWords(p.checkpoints, 1)!.iteration).toBe(1);
    expect(recordedTopWords(p.checkpoints, 4)!.iteration).toBe(1); // iteration 5: latest recorded is 1
    expect(recordedTopWords(p.checkpoints, 5)!.iteration).toBe(8);
    const last = recordedTopWords(p.checkpoints, 9)!;
    expect(last.iteration).toBe(44);
    expect(last.words[p.classes.indexOf('Credit card')].slice(0, 3)).toEqual([
      ['card', 5.1591],
      ['credit', 2.332],
      ['capital', 1.8636],
    ]);
    expect(last.words.every((w) => w.length === 8)).toBe(true);
  });
});

describe('loss chart geometry', () => {
  const p = trainingStepperProps(data.trainingLog);
  const box = { width: 560, height: 220, left: 40, right: 140, top: 12, bottom: 44 };

  it('spaces checkpoints evenly and puts lower values lower on the chart', () => {
    const { lines, ticks } = lossChart(p.checkpoints, box);
    const obj = lines.find((l) => l.key === 'objective')!.points;
    expect(obj[0].x).toBe(40);
    expect(obj[9].x).toBe(420);
    obj.slice(1).forEach((pt, i) => expect(pt.y).toBeGreaterThan(obj[i].y)); // falling = moving down in SVG
    expect(ticks.map((t) => t.value)).toEqual([0, 0.5, 1, 1.5, 2]);
    expect(ticks[0].y).toBe(176);
  });

  it('keeps line labels apart when the lines meet (loss = loss + penalty at iteration 0)', () => {
    expect(spreadLabels([100, 100, 186], 13)).toEqual([100, 113, 186]);
    expect(spreadLabels([50, 20, 30], 13)).toEqual([50, 20, 33]);
  });
});

describe('TrainingStepper', () => {
  const p = trainingStepperProps(data.trainingLog);
  const state = () => screen.getByRole('group', { name: 'Training state' }).textContent!;

  it('labels the objective as the report does and starts at iteration 0', () => {
    const { container } = render(<TrainingStepper {...p} />);
    expect(container.textContent).toContain(data.trainingLog.note);
    expect(state()).toContain('Iteration0');
    expect(state()).toContain('loss + penalty1.7918');
    expect(container.textContent).toContain('No words yet: every weight starts at 0');
  });

  it('Step moves one checkpoint and shows the latest recorded top words', () => {
    const { container } = render(<TrainingStepper {...p} />);
    click('Step');
    expect(state()).toContain('Iteration1');
    expect(container.textContent).toContain('Top 8 words per class at iteration 1');
    for (let i = 0; i < 4; i++) click('Step');
    expect(state()).toContain('Iteration8');
    expect(container.textContent).toContain('Top 8 words per class at iteration 8');
    click('Reset');
    expect(state()).toContain('Iteration0');
  });

  it('Play runs to convergence, then focus moves to Reset', () => {
    vi.useFakeTimers();
    const { container } = render(<TrainingStepper {...p} />);
    const play = screen.getByRole('button', { name: 'Play' });
    play.focus();
    fireEvent.click(play);
    for (let i = 0; i < 20; i++) act(() => void vi.advanceTimersByTime(1_000));
    expect(state()).toContain('Iteration44');
    expect(state()).toContain('loss + penalty0.8726');
    expect(state()).toContain('converged');
    expect(container.textContent).toContain('Top 8 words per class at iteration 44');
    const card = within(screen.getByRole('list', { name: 'Credit card: top words' }));
    expect(card.getAllByRole('listitem')[0].textContent).toContain('card');
    expect(card.getAllByRole('listitem')[0].textContent).toContain('5.159');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Reset' }));
    expect((screen.getByRole('button', { name: 'Step' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('Step focus moves to Reset at the end too', () => {
    render(<TrainingStepper {...p} />);
    const step = screen.getByRole('button', { name: 'Step' });
    step.focus();
    for (let i = 0; i < 9; i++) fireEvent.click(step);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Reset' }));
  });
});

// ── MetricsView ─────────────────────────────────────────────────────────────
// Literals recomputed from the Python code, read-only (matches ml/reports/metrics.json):
//   cd ml/python && python3 -c "import train as T; r=T.fit_and_evaluate(T.load_complaints('../data/complaints.jsonl')); ..."
//   majority train 0.3706 test 0.4333 (macro F1 0.1008, weighted 0.262); nb train 0.9267 test 0.8183 (macro F1 0.7742)
//   logreg train 0.9017 test 0.8083, band 0.0408 (n 600) / 0.0236 (n 1800), macro F1 0.7692, weighted F1 0.8004
//   logreg test confusion row 0 [48, 4, 8, 1, 8, 1]; Debt collection P 0.8542 R 0.5395 F1 0.6613 (76)
describe('metrics view data', () => {
  const p = metricsViewProps(data.metrics);

  it('has the three models, v1 named from the report', () => {
    expect(p.models.map((m) => [m.id, m.label])).toEqual([
      ['majority', 'Majority baseline'],
      ['nb', 'Naive Bayes'],
      ['logreg', 'Logistic regression (v1)'],
    ]);
    expect(versionOf('python train.py --model logreg --version v1 --write-reports')).toBe('v1');
    expect(p.note).toBe(data.metrics.note);
  });

  it('keeps the real numbers (to 6 significant figures)', () => {
    const lr = p.models[2];
    expect(lr.test.accuracy).toBeCloseTo(0.8083, 4);
    expect(lr.test.band).toBeCloseTo(0.04082, 5);
    expect(lr.test.macro.f1).toBeCloseTo(0.7692, 4);
    expect(lr.test.weighted.f1).toBeCloseTo(0.8004, 4);
    expect(lr.test.confusion[0]).toEqual([48, 4, 8, 1, 8, 1]);
    const debt = lr.test.per_class[DEBT];
    expect([debt.precision, debt.recall, debt.f1, debt.support]).toEqual([0.854167, 0.539474, 0.66129, 76]);
    expect(p.models[1].test.macro.f1).toBeCloseTo(0.7742, 4);
    expect(p.models[0].test.accuracy).toBeCloseTo(0.4333, 4);
  });

  it('measures the train-to-test drop against the test band', () => {
    expect(overfit(p.models[2])).toMatchObject({ beyond: true });
    expect(overfit(p.models[2]).drop).toBeCloseTo(0.9017 - 0.8083, 3);
    expect(overfit(p.models[0]).drop).toBeLessThan(0); // the majority class is commoner in 2022
    expect(overfit(p.models[0]).beyond).toBe(false);
  });
});

describe('MetricsView', () => {
  const p = metricsViewProps(data.metrics);
  const summary = () => screen.getByRole('status').textContent!;

  it('opens on v1 on the test split, with accuracy and its band, and the note', () => {
    const { container } = render(<MetricsView {...p} />);
    expect(summary()).toContain('Logistic regression (v1) on test (2022, 600 complaints): accuracy 80.8% ± 4.1 points');
    expect(container.textContent).toContain(data.metrics.note);
    const per = within(screen.getByRole('table', { name: /Per category/ }));
    expect(per.getByRole('row', { name: /^Macro average/ }).textContent).toContain('0.769');
    expect(per.getByRole('row', { name: /^Weighted average/ }).textContent).toContain('0.800');
    expect(per.getByRole('row', { name: /^Debt collection/ }).textContent).toMatch(/0\.854.*0\.539.*0\.661.*76/);
  });

  it('the split toggle shows the overfitting', () => {
    const { container } = render(<MetricsView {...p} />);
    click('Train (2019–2021)');
    expect(screen.getByRole('button', { name: 'Train (2019–2021)' }).getAttribute('aria-pressed')).toBe('true');
    expect(summary()).toContain('accuracy 90.2% ± 2.4 points');
    expect(container.textContent).toContain('9.3 points lower on test');
    expect(container.textContent).toContain('more than the ±4.1-point test band');
  });

  it('switches model and compares all three', () => {
    render(<MetricsView {...p} />);
    click('Naive Bayes');
    expect(summary()).toContain('Naive Bayes on test (2022, 600 complaints): accuracy 81.8% ± 4.1 points');
    click('Majority baseline');
    expect(summary()).toContain('accuracy 43.3%');
    const cmp = within(screen.getByRole('table', { name: /All three models/ }));
    expect(cmp.getByRole('row', { name: /^Naive Bayes/ }).textContent).toMatch(/81\.8%.*0\.774.*0\.809/);
  });

  it('draws the confusion matrix in class order, saying correct or mistake without colour', () => {
    const { container } = render(<MetricsView {...p} />);
    const table = container.querySelector('.mv-cm table')!;
    expect(table.querySelectorAll('tbody tr')).toHaveLength(6);
    expect(table.querySelectorAll('td.mv-diag .mv-mark')).toHaveLength(6);
    expect([...table.querySelectorAll('tbody th')].map((t) => t.textContent)).toEqual(p.classes.map(shortName));
    click(`True ${CHECKING}, predicted ${CHECKING}: 48 complaints (correct)`);
    const detail = screen.getByText(/^48 of the 70 Checking or savings account complaints/);
    expect(detail.closest('[aria-live]')).toBeTruthy();
    click(`True ${DEBT}, predicted ${CR}: 31 complaints (mistake)`);
    expect(screen.getByText(/^31 of the 76 Debt collection complaints were predicted Credit reporting/)).toBeTruthy();
  });
});

// ── Props stay small, and islands import only browser-safe modules ─────────
describe('props and imports', () => {
  it('each aid gets small derived props', () => {
    expect(size(complaintExplorerProps(data))).toBeLessThan(8_000); // 7.3 KB
    expect(size(featureExplorerProps(data))).toBeLessThan(146_000); // 142.3 KB packed, 2.8 KB of it presets
    expect(size(trainingStepperProps(data.trainingLog))).toBeLessThan(6_000); // 5.0 KB
    expect(size(metricsViewProps(data.metrics))).toBeLessThan(8_000); // 6.8 KB
  });

  it('mlAids.ts and the islands never import mlData.ts or llmsKey.ts at run time', () => {
    const src = (p: string) => readFileSync(join(here, '../src', p), 'utf8');
    const imports = (text: string) => [...text.matchAll(/^import\s+(type\s+)?[^;]*?from\s+['"]([^'"]+)['"]/gms)].map((m) => ({ type: !!m[1], from: m[2] }));
    const lib = ML_LIB.map((f) => `lib/${f}`);
    for (const f of [...lib, ...['ComplaintExplorer', 'FeatureExplorer', 'TrainingStepper', 'MetricsView'].map((n) => `components/ml/${n}.tsx`)]) {
      for (const i of imports(src(f))) {
        if (/mlData|llmsKey|llmsData/.test(i.from)) expect(i.type, `${f} imports ${i.from}`).toBe(true);
      }
      expect(src(f)).not.toMatch(/import\.meta|\brequire\(|import\(/);
    }
  });
});

// The aid logic, one module per aid group, re-exported by mlAids.ts (as mcpAids.ts and agentsAids.ts do).
describe('ml aid modules', () => {
  it('are split per aid group, each under 400 lines, and mlAids.ts only re-exports them', () => {
    const read = (f: string) => readFileSync(join(here, '../src/lib', f), 'utf8');
    for (const f of ML_LIB) expect(read(f).split('\n').length, f).toBeLessThan(400);
    expect(read('mlAids.ts').match(/^export \* from '\.\/mlAids\w+';$/gm)).toHaveLength(4);
    expect(read('mlAids.ts').replace(/^\/\/.*$/gm, '').replace(/^export \* from .*$/gm, '').trim()).toBe('');
  });
});
