// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadMlData } from '../src/lib/mlData';
import {
  LLM_COMMANDS,
  alertCounts,
  chanceReading,
  diffRuns,
  driftChartProps,
  driftScale,
  llmGap,
  modelVsLlmProps,
  registryTimelineProps,
  runTableProps,
  simPromote,
  simRollback,
  simStart,
  sortRuns,
} from '../src/lib/mlAids';
import ModelVsLlm from '../src/components/ml/ModelVsLlm';
import RunTable from '../src/components/ml/RunTable';
import RegistryTimeline from '../src/components/ml/RegistryTimeline';
import DriftChart from '../src/components/ml/DriftChart';

const here = dirname(fileURLToPath(import.meta.url));
const data = loadMlData();
const CR = 'Credit reporting or other personal consumer reports';
const size = (v: unknown) => JSON.stringify(v).length;
const button = (name: string | RegExp) => screen.getByRole('button', { name });

afterEach(() => cleanup());

// ── ModelVsLlm ──────────────────────────────────────────────────────────────
// Literals from ml/reports/llm_compare.json (python compare_llm.py compare, replaying the 200
// answers recorded in Task 11) and ml/reports/latency.json (python bench.py --write-reports).
describe('model vs LLM data', () => {
  const p = modelVsLlmProps(data);

  it('keeps the headline numbers over all 200 complaints, no-answers counted as wrong', () => {
    const c = p.compare!;
    expect(c.n).toBe(200);
    expect(c.no_answer).toBe(0);
    expect(c.v1.accuracy).toBe(0.81);
    expect(c.claude.accuracy).toBe(0.865);
    expect(c.claude.name).toBe('claude-haiku-5-5');
    expect(c.v1.name).toBe('v1 (logistic regression)');
    expect(c.v1.macro_f1).toBeCloseTo(0.764525, 6);
    expect(c.claude.macro_f1).toBeCloseTo(0.835169, 6);
    expect(c.answered_only).toEqual({ n: 200, v1_accuracy: 0.81, claude_accuracy: 0.865 });
    // Per-class F1 in class order: Mortgage is the last class.
    expect(c.classes[5]).toBe('Mortgage');
    expect(c.v1.per_class_f1[5]).toBe(0.9);
    expect(c.claude.per_class_f1[5]).toBeCloseTo(0.97561, 5);
    expect(c.v1.cost_per_1000).toBe(0);
    expect(c.claude.cost_per_1000).toBeCloseTo(0.1021975, 7);
  });

  it('says the accuracy gap sits inside the ±7.1-point band', () => {
    const g = llmGap(p.compare!);
    expect(g.gap).toBeCloseTo(0.055, 12);
    expect(g.band).toBeCloseTo(1 / Math.sqrt(200), 12);
    expect(g.inside).toBe(true);
    expect(g.leader).toBe('claude');
  });

  it('carries both latency notes, the caveat and the Go server as a third reference', () => {
    const c = p.compare!;
    expect(c.v1.latency_note).toMatch(/^local CPU inference/);
    expect(c.claude.latency_note).toMatch(/^remote API round trip/);
    expect(c.claude.latency_p50_ms).toBe(1151.5);
    expect(c.latency_caveat).toBe('The two latencies measure different things (local CPU inference vs a remote API call).');
    expect(p.servers.python.p50_ms).toBeCloseTo(0.252395, 6);
    expect(p.servers.go.p50_ms).toBeCloseTo(0.1195, 6);
    expect(p.servers.go.p95_ms).toBeCloseTo(0.202937, 6);
    expect(p.servers.ratio_p50).toBeCloseTo(2.1121, 4);
    expect(p.servers.machine).toBe('Apple M5');
  });

  it('is null without llm_compare.json, with the commands to record one', () => {
    const empty = modelVsLlmProps({ ...data, llmCompare: null });
    expect(empty.compare).toBeNull();
    expect(empty.commands).toEqual(LLM_COMMANDS);
    expect(LLM_COMMANDS).toEqual(['python compare_llm.py record', 'python compare_llm.py compare']);
  });
});

describe('ModelVsLlm', () => {
  const p = modelVsLlmProps(data);

  it('shows both models side by side and the gap inside the band, in words', () => {
    const { container } = render(<ModelVsLlm {...p} />);
    const table = within(screen.getByRole('table', { name: /Side by side/ }));
    expect(table.getByRole('row', { name: /^Accuracy/ }).textContent).toContain('81.0%');
    expect(table.getByRole('row', { name: /^Accuracy/ }).textContent).toContain('86.5%');
    expect(table.getByRole('row', { name: /^Macro F1/ }).textContent).toContain('0.765');
    expect(table.getByRole('row', { name: /^Macro F1/ }).textContent).toContain('0.835');
    expect(table.getByRole('row', { name: /^Cost per 1,000/ }).textContent).toContain('$0.102');
    expect(container.textContent).toContain('a 5.5-point gap');
    expect(container.textContent).toContain('inside the ±7.1-point band');
    expect(container.textContent).toMatch(/no answer: 0/i);
  });

  it('never shows the latencies as like-for-like: notes, caveat and the Go server', () => {
    render(<ModelVsLlm {...p} />);
    const lat = within(screen.getByRole('table', { name: /Latency/ }));
    expect(lat.getByRole('row', { name: /Python server/ }).textContent).toContain('0.252 ms');
    expect(lat.getByRole('row', { name: /Go server/ }).textContent).toContain('0.120 ms');
    expect(lat.getByRole('row', { name: /claude-haiku-5-5/ }).textContent).toContain('1,151.5 ms');
    expect(screen.getByText(p.compare!.v1.latency_note, { exact: false })).toBeTruthy();
    expect(screen.getByText(p.compare!.claude.latency_note, { exact: false })).toBeTruthy();
    expect(screen.getByRole('note').textContent).toContain(p.compare!.latency_caveat);
  });

  it('lists per-category F1 for both, with the difference as a signed number', () => {
    render(<ModelVsLlm {...p} />);
    const t = within(screen.getByRole('table', { name: /F1 per category/ }));
    const mortgage = t.getByRole('row', { name: /^Mortgage/ });
    expect(mortgage.textContent).toContain('0.900');
    expect(mortgage.textContent).toContain('0.976');
    expect(mortgage.textContent).toContain('+0.076');
    expect(t.getByRole('row', { name: /^Debt collection/ }).textContent).toContain('0.000');
  });

  it('says "No recorded run yet" and how to record one when the report is missing', () => {
    const { container } = render(<ModelVsLlm {...modelVsLlmProps({ ...data, llmCompare: null })} />);
    expect(container.textContent).toContain('No recorded run yet');
    expect(container.textContent).toContain('python compare_llm.py record');
    expect(container.textContent).toContain('python compare_llm.py compare');
    expect(screen.queryByRole('table', { name: /Side by side/ })).toBeNull();
    // The local servers' latency is still real data.
    expect(screen.getByRole('table', { name: /Latency/ }).textContent).toContain('0.120 ms');
  });
});

// ── RunTable ────────────────────────────────────────────────────────────────
// Literals from ml/runs/*.json and ml/registry.json (python mlops.py experiments / retrain,
// committed in Task 4; python mlops.py runs prints the same table).
describe('run table data', () => {
  const p = runTableProps(data);
  const byId = (id: string) => p.runs.find((r) => r.id === id)!;

  it('has the five runs, oldest first, with params, data version and hash prefixes', () => {
    expect(p.runs.map((r) => r.id)).toEqual([
      'logreg-C0.3-2019-2021',
      'logreg-C1-2019-2021',
      'logreg-C3-2019-2021',
      'nb-alpha0.1-2019-2021',
      'logreg-C1-2019-2022',
    ]);
    const v1 = byId('logreg-C1-2019-2021');
    expect(v1.params).toMatchObject({ model: 'logreg', C: 1, max_iter: 1000, min_df: 2, max_features: 20000, seed: 7 });
    expect(byId('nb-alpha0.1-2019-2021').params).toMatchObject({ model: 'nb', alpha: 0.1 });
    expect(v1.dataVersion).toBe('sha256:5c2b05a03e9dfee3a9fc28e25bc9c619cc420deda0cb663525c0a29ae3a3f8b6');
    expect(v1.modelHash.slice(0, 8)).toBe('a804df41');
    expect(v1.gitCommit!.slice(0, 7)).toBe('256e694');
    expect(v1.gitDirty).toBe(false);
  });

  it('reads each run on its own eval split', () => {
    expect(byId('logreg-C1-2019-2021').eval).toEqual({ name: 'test', from: '2022-01-01', to: '2022-12-31' });
    expect(byId('logreg-C1-2019-2021').metrics.macro_f1).toBeCloseTo(0.769202, 6);
    expect(byId('logreg-C1-2019-2022').eval.name).toBe('holdout');
    expect(byId('logreg-C1-2019-2022').metrics).toMatchObject({ n: 300 });
    expect(byId('logreg-C1-2019-2022').metrics.accuracy).toBeCloseTo(0.813333, 6);
  });

  it('names the registered versions and checks their hashes', () => {
    expect(p.runs.map((r) => [r.id, r.version, r.stage, r.hashMatches])).toEqual([
      ['logreg-C0.3-2019-2021', null, null, null],
      ['logreg-C1-2019-2021', 'v1', 'production', true],
      ['logreg-C3-2019-2021', null, null, null],
      ['nb-alpha0.1-2019-2021', null, null, null],
      ['logreg-C1-2019-2022', 'v2', 'staging', true],
    ]);
  });

  it('sorts by a metric, best first, ties by run id', () => {
    expect(sortRuns(p.runs, 'macro_f1').map((r) => r.id)).toEqual([
      'nb-alpha0.1-2019-2021', // 0.7742
      'logreg-C1-2019-2021', // 0.7692
      'logreg-C3-2019-2021', // 0.7673
      'logreg-C1-2019-2022', // 0.7642 (holdout)
      'logreg-C0.3-2019-2021', // 0.6859
    ]);
    // C=1 and C=3 tie on test accuracy (0.8083): run id breaks the tie.
    expect(sortRuns(p.runs, 'accuracy').map((r) => r.id).slice(0, 4)).toEqual([
      'nb-alpha0.1-2019-2021',
      'logreg-C1-2019-2022',
      'logreg-C1-2019-2021',
      'logreg-C3-2019-2021',
    ]);
    expect(sortRuns(p.runs, 'time').map((r) => r.id)).toEqual(p.runs.map((r) => r.id).reverse());
  });

  it('diffs two runs like python mlops.py compare: only what changed', () => {
    const d = diffRuns(byId('logreg-C1-2019-2021'), byId('logreg-C3-2019-2021'));
    expect(d.params).toEqual([{ key: 'C', a: 1, b: 3 }]);
    expect(d.sameEval).toBe(true);
    expect(d.sameData).toBe(true);
    expect(d.metrics.find((m) => m.key === 'test.accuracy')).toBeUndefined(); // both 0.8083
    const macro = d.metrics.find((m) => m.key === 'test.macro_f1')!;
    expect(macro.delta).toBeCloseTo(0.767334 - 0.769202, 6); // C=3 0.7673 vs C=1 0.7692
  });

  it('flags a diff across different eval windows as not comparable', () => {
    const d = diffRuns(byId('logreg-C1-2019-2021'), byId('logreg-C1-2019-2022'));
    expect(d.params).toEqual([]);
    expect(d.windows).toEqual([
      { key: 'train', a: '2019-01-01 to 2021-12-31', b: '2019-01-01 to 2022-12-31' },
      { key: 'eval', a: 'test 2022-01-01 to 2022-12-31', b: 'holdout 2023-01-01 to 2023-06-30' },
    ]);
    expect(d.sameEval).toBe(false);
    expect(d.metrics.find((m) => m.key === 'test.accuracy')).toMatchObject({ b: null, delta: null });
  });
});

describe('RunTable', () => {
  const p = runTableProps(data);

  it('shows every run with its version, data version and hash prefixes', () => {
    render(<RunTable {...p} />);
    const table = within(screen.getByRole('table', { name: /Tracked runs/ }));
    const v1 = table.getByRole('row', { name: /logreg-C1-2019-2021/ });
    expect(v1.textContent).toContain('v1');
    expect(v1.textContent).toContain('production');
    expect(v1.textContent).toContain('a804df41');
    expect(v1.textContent).toContain('256e694');
    expect(v1.textContent).toContain('5c2b05a0');
    expect(v1.textContent).toContain('C = 1');
    expect(table.getByRole('row', { name: /logreg-C1-2019-2022/ }).textContent).toContain('holdout');
    expect(table.getAllByRole('row')).toHaveLength(6); // header + 5
  });

  it('sorts by a metric from a toggle, and says so', () => {
    render(<RunTable {...p} />);
    fireEvent.click(button('Macro F1'));
    expect(button('Macro F1').getAttribute('aria-pressed')).toBe('true');
    const rows = within(screen.getByRole('table', { name: /Tracked runs/ })).getAllByRole('row').slice(1);
    expect(rows[0].textContent).toContain('nb-alpha0.1-2019-2021');
    expect(screen.getByRole('status').textContent).toMatch(/sorted by macro F1, highest first/i);
  });

  it('diffs two picked runs and warns when they were evaluated on different data', () => {
    render(<RunTable {...p} />);
    fireEvent.change(screen.getByLabelText('Run A'), { target: { value: 'logreg-C1-2019-2021' } });
    fireEvent.change(screen.getByLabelText('Run B'), { target: { value: 'logreg-C3-2019-2021' } });
    const diff = within(screen.getByRole('region', { name: 'Run diff' }));
    expect(diff.getByRole('row', { name: /^C/ }).textContent).toContain('3');
    expect(diff.queryByText(/not comparable/)).toBeNull();
    fireEvent.change(screen.getByLabelText('Run B'), { target: { value: 'logreg-C1-2019-2022' } });
    expect(diff.getByText(/not comparable/)).toBeTruthy();
    expect(diff.getByText(/No parameter changed/)).toBeTruthy();
  });
});

// ── RegistryTimeline ────────────────────────────────────────────────────────
// Literals from ml/registry.json and ml/runs/logreg-C1-2019-2022.json (python mlops.py retrain).
describe('registry timeline data', () => {
  const p = registryTimelineProps(data);

  it('keeps the real history and the current stage of each version', () => {
    expect(p.history.map((h) => [h.action, h.version, h.from_stage, h.to_stage])).toEqual([
      ['register', 'v1', null, 'staging'],
      ['promote', 'v1', 'staging', 'production'],
      ['register', 'v2', null, 'staging'],
      ['gate', 'v2', 'staging', 'staging'],
    ]);
    expect(p.versions.map((v) => [v.version, v.stage])).toEqual([
      ['v1', 'production'],
      ['v2', 'staging'],
    ]);
  });

  it('carries the gate numbers from the v2 run', () => {
    expect(p.gate).toMatchObject({ version: 'v2', production_version: 'v1', passed: false, n: 300, floor: 0.5 });
    expect(p.gate!.candidate_macro_f1).toBeCloseTo(0.764231, 6);
    expect(p.gate!.production_macro_f1).toBeCloseTo(0.759003, 6);
    expect(p.gate!.gain).toBeCloseTo(0.005228, 6);
    expect(p.gate!.band).toBeCloseTo(1 / Math.sqrt(300), 6);
    expect(p.gate!.reasons[0]).toBe('macro-F1 0.764 vs 0.759: gain +0.005 <= band 0.058 (n=300)');
  });

  it('simulates "promote v2 anyway" as mlops.promote does: v1 archived first', () => {
    const start = simStart(p);
    const r = simPromote(start, 'v2');
    expect(r.error).toBeUndefined();
    expect(r.state.stages).toEqual({ v1: 'archived', v2: 'production' });
    expect(r.state.history.slice(4).map((h) => [h.action, h.version, h.from_stage, h.to_stage, h.note, h.simulated])).toEqual([
      ['archive', 'v1', 'production', 'archived', 'replaced by v2', true],
      ['promote', 'v2', 'staging', 'production', 'promoted although the gate failed', true],
    ]);
    // The real history is untouched.
    expect(p.history).toHaveLength(4);
    expect(start.history).toHaveLength(4);
    expect(simPromote(r.state, 'v2').error).toBe('v2 is already in production');
  });

  it('simulates a rollback: the last other version promoted to production comes back', () => {
    const start = simStart(p);
    expect(simRollback(start).error).toBe('no previous production version before v1');
    const after = simRollback(simPromote(start, 'v2').state);
    expect(after.restored).toBe('v1');
    expect(after.state.stages).toEqual({ v1: 'production', v2: 'archived' });
    expect(after.state.history.slice(6).map((h) => [h.action, h.version, h.to_stage, h.note])).toEqual([
      ['rollback', 'v2', 'archived', 'rolled back to v1'],
      ['rollback', 'v1', 'production', 'replaces v2'],
    ]);
  });
});

describe('RegistryTimeline', () => {
  const p = registryTimelineProps(data);

  it('shows the real history with the failed gate and its numbers', () => {
    render(<RegistryTimeline {...p} />);
    const real = within(screen.getByRole('list', { name: 'Real history (registry.json)' }));
    const items = real.getAllByRole('listitem');
    expect(items).toHaveLength(4);
    expect(items[3].textContent).toMatch(/gate.*v2.*FAILED/);
    expect(items[3].textContent).toContain('0.764 vs 0.759');
    expect(items[3].textContent).toContain('+0.005');
    expect(items[3].textContent).toContain('0.058');
    const stages = within(screen.getByRole('table', { name: 'Current stage per version' }));
    expect(stages.getByRole('row', { name: /^v1/ }).textContent).toContain('production');
    expect(stages.getByRole('row', { name: /^v2/ }).textContent).toContain('staging');
  });

  it('runs a clearly labelled simulation and never changes the real history', () => {
    render(<RegistryTimeline {...p} />);
    const sim = within(screen.getByRole('region', { name: /Simulation/ }));
    expect(sim.getAllByText(/SIMULATION/).length).toBeGreaterThan(0);
    fireEvent.click(sim.getByRole('button', { name: 'Roll back' }));
    expect(sim.getByRole('status').textContent).toContain('no previous production version before v1');
    fireEvent.click(sim.getByRole('button', { name: 'Promote v2 anyway' }));
    expect(sim.getByRole('status').textContent).toMatch(/v2 is now in production; v1 was archived/);
    const simList = within(sim.getByRole('list', { name: /Simulated history/ }));
    expect(simList.getAllByRole('listitem')).toHaveLength(6);
    expect(simList.getAllByText(/simulated/i).length).toBe(2);
    fireEvent.click(sim.getByRole('button', { name: 'Roll back' }));
    expect(sim.getByRole('status').textContent).toMatch(/v1 is back in production/);
    expect(within(screen.getByRole('list', { name: 'Real history (registry.json)' })).getAllByRole('listitem')).toHaveLength(4);
    fireEvent.click(sim.getByRole('button', { name: 'Reset simulation' }));
    expect(within(sim.getByRole('list', { name: /Simulated history/ })).getAllByRole('listitem')).toHaveLength(4);
  });
});

// ── DriftChart ──────────────────────────────────────────────────────────────
// Literals from ml/reports/drift.json (python monitor.py --write-reports; the Task 7 report's
// fix-round-2 tables print the same numbers).
describe('drift chart data', () => {
  const p = driftChartProps(data.drift);
  const month = (label: string) => p.units.month.periods.find((x) => x.label === label)!;

  it('has 24 months and 8 quarters with both signals and the calibrated thresholds', () => {
    expect(p.units.month.periods).toHaveLength(24);
    expect(p.units.quarter.periods).toHaveLength(8);
    expect(p.units.month.periods[0].label).toBe('2023-01');
    expect(p.units.quarter.periods[7].label).toBe('2024-Q4');
    expect(p.units.month.null.predictions.threshold).toBeCloseTo(0.30592, 5);
    expect(p.units.month.null.words.p50).toBeCloseTo(0.032913, 6);
    expect(p.units.month.null.words.p95).toBeCloseTo(0.052334, 6);
    expect(p.units.month.null.words.threshold).toBeCloseTo(0.061879, 6); // the within-2022 max wins
    expect(p.units.quarter.null.words.threshold).toBeCloseTo(0.026036, 6);
    expect(p.alertAt).toBe(0.2);
  });

  it('marks alerts per signal, matching the report', () => {
    expect(month('2024-11')).toMatchObject({ n: 50, words_calibrated: true, predictions_calibrated: false, words_convention: false });
    expect(month('2024-11').words).toBeCloseTo(0.082338, 5); // vs threshold 0.0619
    expect(alertCounts(p.units.month.periods)).toEqual({ predictions: { calibrated: 0, convention: 0 }, words: { calibrated: 1, convention: 0 } });
    expect(alertCounts(p.units.quarter.periods)).toEqual({ predictions: { calibrated: 0, convention: 0 }, words: { calibrated: 2, convention: 0 } });
    expect(p.units.quarter.periods.filter((q) => q.words_calibrated).map((q) => q.label)).toEqual(['2024-Q1', '2024-Q4']);
  });

  it('reads the alert counts against the chance expectation', () => {
    expect(p.units.month.expected).toEqual({ per_signal: 1.2, either_signal_max: 2.4 });
    expect(p.units.quarter.expected).toEqual({ per_signal: 0.4, either_signal_max: 0.8 });
    expect(chanceReading(p, 'month')).toBe(
      'By chance alone, expect at most about 1.2 calibrated alerts per signal over 24 months (2.4 on either). Seen: predictions 0, words 1: within what chance gives.',
    );
    expect(chanceReading(p, 'quarter')).toBe(
      'By chance alone, expect at most about 0.4 calibrated alerts per signal over 8 quarters (0.8 on either). Seen: predictions 0, words 2: more than chance gives, weak but real evidence.',
    );
  });

  it('keeps the in-sample reference (months only), which raises 9 convention alerts', () => {
    expect(p.inSample).toHaveLength(24);
    expect(p.inSample.filter((m) => m.predictions_convention).map((m) => m.label)).toEqual([
      '2023-09',
      '2024-01',
      '2024-02',
      '2024-06',
      '2024-07',
      '2024-09',
      '2024-10',
      '2024-11',
      '2024-12',
    ]);
    expect(p.inSampleNote).toContain('in-sample');
  });

  it('keeps the top shifted words per period and the class shares per year', () => {
    expect(month('2024-11').top.map((w) => `${w.word}${w.direction === 'up' ? '+' : '-'}`)).toEqual([
      'accounts+',
      'report+',
      'these+',
      'told-',
      'balance+',
    ]);
    const cr = p.classes.indexOf(CR);
    const y = p.years.indexOf('2024');
    expect(p.real[cr][y]).toBeCloseTo(0.8, 3);
    expect(p.sample[cr][y]).toBeCloseTo(0.4667, 4);
  });

  it('puts every value, threshold and the 0.2 convention on one axis from 0 that never moves', () => {
    expect(driftScale(0.3451)).toEqual({ max: 0.4, ticks: [0, 0.1, 0.2, 0.3, 0.4] });
    expect(driftScale(0.2)).toEqual({ max: 0.2, ticks: [0, 0.05, 0.1, 0.15, 0.2] });
    expect(p.units.month.scale.predictions.max).toBe(0.4); // in-sample 0.345 included even when hidden
    expect(p.units.month.scale.words.max).toBe(0.2); // the convention line is always on the chart
  });
});

describe('DriftChart', () => {
  const p = driftChartProps(data.drift);

  it('draws both signals with the threshold, the null band and the labelled convention', () => {
    const { container } = render(<DriftChart {...p} />);
    expect(screen.getAllByRole('img')).toHaveLength(2);
    expect(container.textContent).toContain('calibrated threshold 0.306');
    expect(container.textContent).toContain('0.2 convention (rule of thumb)');
    expect(container.textContent).toContain('null p50–p95');
    expect(container.textContent).toContain('within what chance gives');
  });

  it('marks alerts with a shape and text, not colour only, and has a table view', () => {
    render(<DriftChart {...p} />);
    const table = within(screen.getByRole('table', { name: /PSI by month/ }));
    expect(table.getAllByRole('row')).toHaveLength(26); // header, 24 months, thresholds
    const nov = table.getByRole('row', { name: /2024-11/ });
    expect(nov.textContent).toContain('0.082');
    expect(nov.textContent).toContain('▲ calibrated alert');
    expect(screen.getByText(/▲ calibrated alert: above the calibrated threshold/)).toBeTruthy();
  });

  it('switches to quarters', () => {
    render(<DriftChart {...p} />);
    fireEvent.click(button('Quarterly'));
    expect(button('Quarterly').getAttribute('aria-pressed')).toBe('true');
    expect(within(screen.getByRole('table', { name: /PSI by quarter/ })).getAllByRole('row')).toHaveLength(10);
    expect(screen.getByText(/more than chance gives/)).toBeTruthy();
    expect(button(/in-sample/).hasAttribute('disabled')).toBe(true);
  });

  it('overlays the in-sample reference, labelled as misleading', () => {
    const { container } = render(<DriftChart {...p} />);
    expect(container.textContent).not.toContain('in-sample (misleading)');
    fireEvent.click(button(/in-sample/));
    expect(button(/in-sample/).getAttribute('aria-pressed')).toBe('true');
    expect(container.textContent).toContain('in-sample (misleading)');
    expect(container.textContent).toContain('9 of 24 months');
  });

  it('picking a period shows its top shifted words', () => {
    render(<DriftChart {...p} />);
    fireEvent.change(screen.getByLabelText('Period'), { target: { value: '2024-11' } });
    const detail = screen.getByRole('region', { name: 'Period detail' });
    expect(detail.textContent).toContain('2024-11');
    expect(detail.textContent).toContain('accounts');
    expect(detail.textContent).toContain('more often');
    expect(detail.textContent).toContain('▲ calibrated alert');
  });

  it('compares real and sample class shares per year', () => {
    render(<DriftChart {...p} />);
    fireEvent.click(button('2024'));
    const shares = within(screen.getByRole('table', { name: /Class shares in 2024/ }));
    const row = shares.getByRole('row', { name: /^Credit reporting/ });
    expect(row.textContent).toContain('80.0%');
    expect(row.textContent).toContain('46.7%');
  });
});

// ── Props stay small, and islands import only browser-safe modules ─────────
describe('ops props and imports', () => {
  it('each aid gets small derived props', () => {
    expect(size(modelVsLlmProps(data))).toBeLessThan(2_500); // 1.8 KB
    expect(size(runTableProps(data))).toBeLessThan(10_000); // 8.7 KB
    expect(size(registryTimelineProps(data))).toBeLessThan(2_500); // 1.7 KB
    expect(size(driftChartProps(data.drift))).toBeLessThan(22_000); // 20.8 KB, 7.2 KB of it the months' top words
  });

  it('the ops islands never import mlData.ts at run time', () => {
    const src = (p: string) => readFileSync(join(here, '../src', p), 'utf8');
    const imports = (text: string) => [...text.matchAll(/^import\s+(type\s+)?[^;]*?from\s+'([^']+)'/gms)].map((m) => ({ type: !!m[1], from: m[2] }));
    for (const f of ['ModelVsLlm', 'RunTable', 'RegistryTimeline', 'DriftChart'].map((n) => `components/ml/${n}.tsx`)) {
      for (const i of imports(src(f))) {
        if (/mlData|llmsKey|llmsData/.test(i.from)) expect(i.type, `${f} imports ${i.from}`).toBe(true);
      }
      expect(src(f)).not.toMatch(/import\.meta|\brequire\(|import\(/);
    }
  });
});
