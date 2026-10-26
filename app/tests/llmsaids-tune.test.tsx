// @vitest-environment jsdom
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { buildLlmsData, callCost, compare, gradeRun, loadLlmsData, noiseBand, runName, type Comparison, type LlmsData } from '../src/lib/llmsData';
import {
  DEFAULT_AB,
  LORA_D,
  abCompare,
  abGrades,
  abVerdict,
  cheapestAbove,
  costChartProps,
  loraParams,
  promptAbProps,
  type CostPoint,
} from '../src/lib/llmsAids';
import PromptAB from '../src/components/llms/PromptAB';
import CostQualityChart from '../src/components/llms/CostQualityChart';
import LoraDiagram from '../src/components/llms/LoraDiagram';

// Fixtures are read with node:fs (ruling R6); the app loader never sees them.
const here = dirname(fileURLToPath(import.meta.url));
const fixtures = join(here, '../../llms/data/fixtures');
const read = (p: string) => readFileSync(join(fixtures, p), 'utf8');
function fixtureData(withResponses = true): LlmsData {
  const dir = join(fixtures, 'responses');
  const responses = withResponses
    ? Object.fromEntries(readdirSync(dir).map((f) => [`responses/${f}`, readFileSync(join(dir, f), 'utf8')]))
    : {};
  return buildLlmsData({ tickets: read('tickets.jsonl'), labels: read('reply_labels.jsonl'), prompts: read('prompts.json'), responses });
}
const expected = JSON.parse(read('expected_scores.json'));

afterEach(cleanup);

const point = (label: string, cost: number, both: number): CostPoint => ({
  name: label,
  model: 'm',
  effort: null,
  prompt: 'p',
  label,
  short: label,
  cost,
  both,
  scored: 40,
  band: noiseBand(40),
});

// ── LoraDiagram logic ───────────────────────────────────────────────────────
describe('loraParams', () => {
  it('counts 2·d·r trainable parameters against d² for the full matrix', () => {
    expect(LORA_D).toBe(896);
    const p = loraParams(896, 8);
    expect(p.trainable).toBe(14_336);
    expect(p.full).toBe(802_816);
    expect(p.share).toBeCloseTo(14_336 / 802_816, 12);
    expect(loraParams(896, 1).trainable).toBe(1_792);
    expect(loraParams(896, 64)).toEqual({ trainable: 114_688, full: 802_816, share: 1 / 7 }); // 2r/d = 128/896
  });

  it('rejects a rank outside 1..d', () => {
    expect(() => loraParams(896, 0)).toThrow();
    expect(() => loraParams(896, 897)).toThrow();
    expect(() => loraParams(896, 2.5)).toThrow();
  });
});

// ── CostQualityChart logic ──────────────────────────────────────────────────
describe('cheapestAbove', () => {
  const pts = [point('a', 5, 0.95), point('b', 0.2, 0.7), point('c', 1, 0.9), point('d', 1, 0.925)];

  it('picks the cheapest point at or above the target', () => {
    expect(cheapestAbove(pts, 0.5)?.label).toBe('b');
    expect(cheapestAbove(pts, 0.7)?.label).toBe('b'); // at the target counts
    expect(cheapestAbove(pts, 0.8)?.label).toBe('d'); // same cost as c: the more accurate wins
    expect(cheapestAbove(pts, 0.93)?.label).toBe('a');
  });

  it('returns null when nothing reaches the target, or there are no points', () => {
    expect(cheapestAbove(pts, 0.96)).toBeNull();
    expect(cheapestAbove([], 0.5)).toBeNull();
  });

  it('treats a target written as a decimal the same as n/40', () => {
    expect(cheapestAbove([point('x', 1, 38 / 40)], 0.95)?.label).toBe('x');
    expect(cheapestAbove([point('x', 1, 35 / 40)], 0.875)?.label).toBe('x');
  });
});

describe('costChartProps (fixtures)', () => {
  it('has one point per recorded config, cost per 1,000 tickets from the saved tokens', () => {
    const p = costChartProps(fixtureData(), { split: 'all', prompt: 'v1_basic' });
    expect(p.recorded).toBe(true);
    expect(p.prompts).toEqual(['v1_basic', 'v2_rules']);
    expect(p.points.map((x) => x.name)).toEqual(['triage__claude-haiku-5-5__v1_basic__default', 'triage__claude-haiku-5-5__v2_rules__default']);
    const v1 = p.points[0];
    // 6 answered lines of 90 in / 40 out, a refusal of 90 in / 0 out, and an error with no tokens, over 8 tickets.
    expect(v1.cost).toBeCloseTo(((6 * callCost('claude-haiku-5-5', 90, 40) + callCost('claude-haiku-5-5', 90, 0)) * 1000) / 8, 12);
    expect(v1.both).toBeCloseTo(expected.compare_v1_v2.a, 12);
    expect(v1.scored).toBe(7);
    expect(v1.band).toBeCloseTo(noiseBand(7), 12);
    expect(v1.short).toBe('Haiku 5.5');
    // Only rep 0 counts, so the v2 noise repeat does not double the cost.
    expect(p.points[1].cost).toBeCloseTo(callCost('claude-haiku-5-5', 90, 40) * 1000, 12);
    expect(p.points[1].both).toBe(expected.compare_v1_v2.b);
  });

  it('is not recorded without responses or when the prompt is missing', () => {
    expect(costChartProps(fixtureData(false)).recorded).toBe(false);
    expect(costChartProps(fixtureData(), { split: 'all', prompt: 'v9' }).recorded).toBe(false);
  });
});

// ── PromptAB logic ──────────────────────────────────────────────────────────
describe('promptAbProps and the verdict (fixtures)', () => {
  const data = fixtureData();
  const props = promptAbProps(data, { split: 'all' });

  it('lists the recorded prompt versions of the task model with one answer per ticket', () => {
    expect(props.recorded).toBe(true);
    expect(props.prompts.map((p) => p.version)).toEqual(['v1_basic', 'v2_rules']);
    expect(props.a).toBe(DEFAULT_AB.a);
    expect(props.b).toBe(DEFAULT_AB.b);
    expect(props.tickets.map((t) => t.id)).toEqual(['f001', 'f002', 'f003', 'f004', 'f005', 'f006', 'f007', 'f008']);
    const v1 = props.prompts[0].answers;
    expect(v1[0]).toEqual(['billing', 'normal']);
    expect(v1[6]).toBe('refusal');
    expect(v1[7]).toBe('error');
  });

  it('re-grades with grade() and matches compare() on rep 0', () => {
    const v1 = data.runs.find((r) => runName(r.config) === 'triage__claude-haiku-5-5__v1_basic__default')!;
    expect(abGrades(props.tickets, props.prompts[0].answers)).toEqual(gradeRun(v1, data.tickets, 0));
    const c = abCompare(props, 'v1_basic', 'v2_rules');
    const e = expected.compare_v1_v2;
    expect(c.a).toBeCloseTo(e.a, 12);
    expect(c.b).toBe(e.b);
    expect(c.diff).toBeCloseTo(e.diff, 12);
    expect(c.band).toBeCloseTo(e.band, 12);
    expect(c.fixed).toEqual(e.fixed);
    expect(c.broken).toEqual(e.broken);
    expect(c.significant).toBe(false);
  });

  it('the verdict follows compare().significant, not a recomputation', () => {
    const base: Comparison = { a: 0.5, b: 0.51, diff: 0.01, band: 0.5, fixed: [], broken: [], significant: true };
    expect(abVerdict(base).larger).toBe(true);
    expect(abVerdict(base).text).toMatch(/is larger than the noise band/);
    const no = abVerdict({ ...base, diff: 0.9, significant: false });
    expect(no.larger).toBe(false);
    expect(no.text).toMatch(/is not larger than the noise band/);
  });

  it('is not recorded without responses or when a default prompt is missing', () => {
    expect(promptAbProps(fixtureData(false)).recorded).toBe(false);
    expect(promptAbProps(data, { a: 'v9' }).recorded).toBe(false);
  });
});

// ── Components ──────────────────────────────────────────────────────────────
describe('PromptAB', () => {
  it('shows both scores with the band, the verdict and the fixed tickets', () => {
    render(<PromptAB {...promptAbProps(fixtureData(), { split: 'all' })} />);
    const scores = screen.getByRole('list', { name: 'Scores' });
    expect(within(scores).getByText(/42\.9%/)).toBeTruthy();
    expect(within(scores).getByText(/75\.0%/)).toBeTruthy();
    expect(within(scores).getAllByText(/± 37\.8%/)).toHaveLength(2);
    expect(screen.getByText(/is not larger than the noise band/)).toBeTruthy();
    const fixed = screen.getByRole('list', { name: /Fixed by B/ });
    expect(within(fixed).getAllByRole('listitem').map((li) => li.dataset.id)).toEqual(['f001', 'f004', 'f007']);
    expect(within(fixed).getByText(/A: no answer \(refusal\)/)).toBeTruthy();
    expect(screen.getByText(/Broken by B: none/)).toBeTruthy();
  });

  it('switching B to the same prompt as A shows no difference', () => {
    render(<PromptAB {...promptAbProps(fixtureData(), { split: 'all' })} />);
    fireEvent.change(screen.getByLabelText('Prompt B'), { target: { value: 'v1_basic' } });
    expect(screen.getByText(/A and B are the same prompt/)).toBeTruthy();
    expect(screen.getByText(/Fixed by B: none/)).toBeTruthy();
  });

  it('shows the record command when nothing is recorded', () => {
    render(<PromptAB {...promptAbProps(fixtureData(false))} />);
    expect(screen.getByText('No recorded run yet')).toBeTruthy();
  });
});

describe('CostQualityChart', () => {
  const props = costChartProps(fixtureData(), { split: 'all', prompt: 'v1_basic' });

  it('names the cheapest config at or above the target, and none above every point', () => {
    render(<CostQualityChart {...props} />);
    const slider = screen.getByLabelText(/Target accuracy/);
    fireEvent.input(slider, { target: { value: '400' } });
    expect(screen.getByRole('status').textContent).toMatch(/Haiku 5\.5 is the cheapest/);
    fireEvent.input(slider, { target: { value: '500' } });
    expect(screen.getByRole('status').textContent).toMatch(/No recorded config reaches 50\.0%/);
  });

  it('switches prompt and keeps a data table', () => {
    render(<CostQualityChart {...props} />);
    const table = screen.getByRole('table');
    expect(within(table).getByText('42.9%')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'v2_rules' } });
    expect(within(screen.getByRole('table')).getByText('75.0%')).toBeTruthy();
    expect(screen.getByRole('img').getAttribute('aria-label')).toMatch(/log scale/);
  });

  it('shows the record command when nothing is recorded', () => {
    render(<CostQualityChart {...costChartProps(fixtureData(false))} />);
    expect(screen.getByText('No recorded run yet')).toBeTruthy();
  });
});

describe('LoraDiagram', () => {
  it('shows the counts at rank 8 and updates with the slider', () => {
    render(<LoraDiagram />);
    const out = screen.getByRole('status');
    expect(out.textContent).toMatch(/14,336/);
    expect(out.textContent).toMatch(/802,816/);
    expect(out.textContent).toMatch(/1\.79%/);
    fireEvent.input(screen.getByLabelText(/Rank r/), { target: { value: '64' } });
    expect(out.textContent).toMatch(/114,688/);
    expect(out.textContent).toMatch(/14\.29%/);
  });
});

// ── Real data ───────────────────────────────────────────────────────────────
// Expected values come from the Python lab in replay mode (no API calls), run from the repo root:
//   python3 llms/python/triage_lab.py compare --prompt v1_basic --vs v2_rules --split dev
//     -> v1_basic 70.0%  v2_rules 97.5%  diff +27.5%  band +/-15.8%  a real difference
//        fixed t001, t003, t007, t012, t015, t028, t029, t030, t033, t038, t039  broken -
//   python3 llms/python/triage_lab.py compare --prompt v2_rules --vs v3_examples --split dev
//     -> 97.5% vs 97.5%, diff +0.0%, within the noise
//   python3 llms/python/triage_lab.py run --prompt <p> --model <m> --effort <e> --split dev  (both accuracy)
//     haiku (no effort): v1_basic 70.0, v2_rules 97.5, v3_examples 97.5
//     sonnet low/high:   v1_basic 62.5 / 65.0, v2_rules 85.0 / 87.5
//     opus low/high:     v1_basic 65.0 / 72.5, v2_rules 95.0 / 95.0
//   cost per 1,000 tickets, from llms/python with triage_lab's own call_cost over rep 0's dev lines:
//     python3 -c "import triage_lab as T; dev=T.split(T.load_tickets(),'dev'); ls=[T.triage(t,P,model=M,effort=E,rep=0) for t in dev]
//       print(sum(T.call_cost(M,l['input_tokens'],l['output_tokens']) for l in ls)/len(dev)*1000)"
describe('real recorded data', () => {
  const real = loadLlmsData();
  const size = (v: unknown) => JSON.stringify(v).length;
  const PY: [string, number, number][] = [
    ['triage__claude-haiku-5-5__v1_basic__default', 0.12559, 0.7],
    ['triage__claude-haiku-5-5__v2_rules__default', 0.254015, 0.975],
    ['triage__claude-haiku-5-5__v3_examples__default', 0.2878525, 0.975],
    ['triage__claude-sonnet-5-5__v1_basic__low', 2.80355, 0.625],
    ['triage__claude-sonnet-5-5__v2_rules__low', 4.25655, 0.85],
    ['triage__claude-sonnet-5-5__v1_basic__high', 2.97555, 0.65],
    ['triage__claude-sonnet-5-5__v2_rules__high', 5.3778, 0.875],
    ['triage__claude-opus-5-5__v1_basic__low', 6.1026, 0.65],
    ['triage__claude-opus-5-5__v2_rules__low', 9.3246, 0.95],
    ['triage__claude-opus-5-5__v1_basic__high', 8.3821, 0.725],
    ['triage__claude-opus-5-5__v2_rules__high', 10.7266, 0.95],
  ];

  it('PromptAB matches triage_lab compare', () => {
    const p = promptAbProps(real);
    expect(p.recorded).toBe(true);
    expect(p.prompts.map((x) => x.version)).toEqual(['v1_basic', 'v2_rules', 'v3_examples']);
    const c = abCompare(p, 'v1_basic', 'v2_rules');
    expect([c.a, c.b]).toEqual([0.7, 0.975]);
    expect(c.diff).toBeCloseTo(0.275, 12);
    expect(c.band).toBeCloseTo(0.158, 3);
    expect(c.fixed).toEqual(['t001', 't003', 't007', 't012', 't015', 't028', 't029', 't030', 't033', 't038', 't039']);
    expect(c.broken).toEqual([]);
    expect(c.significant).toBe(true);
    const same = abCompare(p, 'v2_rules', 'v3_examples');
    expect([same.a, same.b, same.significant]).toEqual([0.975, 0.975, false]);
    // compare() on the loader's own rep-0 grades gives the same answer.
    const dev = new Set(real.tickets.filter((t) => t.split === 'dev').map((t) => t.id));
    const g = (v: string) =>
      gradeRun(real.runs.find((r) => runName(r.config) === `triage__claude-haiku-5-5__${v}__default`)!, real.tickets, 0).filter((x) => dev.has(x.case_id));
    expect(compare(g('v1_basic'), g('v2_rules'))).toEqual(c);
    expect(size(p)).toBeLessThan(12_000);
  });

  it('CostQualityChart has one point per recorded config, matching the Python cost and accuracy', () => {
    const p = costChartProps(real);
    expect(p.recorded).toBe(true);
    expect(p.prompts).toEqual(['v1_basic', 'v2_rules']); // v3_examples is Haiku only, so it is not comparable
    expect(p.initial).toBe('v2_rules');
    expect(p.points).toHaveLength(10);
    for (const [name, cost, both] of PY.filter(([n]) => !n.includes('v3_examples'))) {
      const pt = p.points.find((x) => x.name === name)!;
      expect(pt, name).toBeTruthy();
      expect(pt.cost, name).toBeCloseTo(cost, 9);
      expect(pt.both, name).toBe(both);
      expect(pt.band, name).toBeCloseTo(noiseBand(40), 12);
    }
    const of = (prompt: string) => p.points.filter((x) => x.prompt === prompt);
    // With v2_rules the cheapest model also scores highest: Haiku at $0.25 per 1,000 tickets.
    expect(cheapestAbove(of('v2_rules'), 0.9)?.name).toBe('triage__claude-haiku-5-5__v2_rules__default');
    expect(cheapestAbove(of('v2_rules'), 0.975)?.name).toBe('triage__claude-haiku-5-5__v2_rules__default');
    expect(cheapestAbove(of('v2_rules'), 1)).toBeNull();
    expect(cheapestAbove(of('v1_basic'), 0.7)?.name).toBe('triage__claude-haiku-5-5__v1_basic__default');
    expect(cheapestAbove(of('v1_basic'), 0.725)?.name).toBe('triage__claude-opus-5-5__v1_basic__high');
    expect(cheapestAbove(of('v1_basic'), 0.75)).toBeNull();
    expect(size(p)).toBeLessThan(3_000);
  });
});
