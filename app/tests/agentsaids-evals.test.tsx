// @vitest-environment jsdom
// The AI Agents track's evaluation aids: CostCompare (lesson 6) and EngineScorecard (lessons 10-11),
// plus the lesson 7 set numbers and the page budget. Every number an aid shows is pinned here as a
// literal from agents/data/derived/*.json (python agents/derive.py; engine_scores.json from
// python agents/evals/run_evals.py). With 13 tickets no engine clears the gate (ruling R66): the
// verdict is pinned as it is.
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadAgentsData } from '../src/lib/agentsData';
import {
  AGENTS_DERIVE_COMMAND,
  AGENTS_SCORE_COMMAND,
  GATE_MIN_SUCCESS,
  agentTraceProps,
  bars,
  costCompareProps,
  engineScorecardProps,
  patternStepperProps,
  ticketView,
  type CostCompareJson,
  type EngineScoresJson,
} from '../src/lib/agentsAids';
import { escapeHTML } from 'astro/runtime/server/escape.js';
import { serializeProps } from 'astro/runtime/server/serialize.js';
import CostCompare from '../src/components/agents/CostCompare';
import EngineScorecard from '../src/components/agents/EngineScorecard';

const data = loadAgentsData();
const size = (v: unknown) => JSON.stringify(v).length;
const live = () => document.querySelector('[aria-live="polite"]')?.textContent;

afterEach(() => cleanup());

// ── CostCompare ─────────────────────────────────────────────────────────────
describe('cost compare data', () => {
  const p = costCompareProps(data.costCompare);

  it('has every engine, single and supervisor, over 13 tickets', () => {
    expect(p.engines.map((e) => e.engine)).toEqual(['hand', 'hand-go', 'langgraph', 'sdk']);
    expect(p.tickets).toBe(13);
  });

  it('keeps each desk row as recorded', () => {
    const [hand, go, lg, sdk] = p.engines;
    expect(hand.single).toEqual({
      recorded: true,
      n: 13,
      success: '76.9%',
      band: '±27.7 points',
      meanCost: '$0.000663',
      medianCost: '$0.000607',
      p50: '4.9 s',
      p90: '5.8 s',
      calls: '2.5',
      callsRange: '2–4',
      tokensIn: '3,217.9',
      tokensOut: '682',
      cacheRead: '0',
      value: { cost: 0.00066279, latency: 4919, calls: 2.5 },
    });
    expect(hand.supervisor).toMatchObject({ success: '69.2%', meanCost: '$0.00286', medianCost: '$0.00273', p50: '18.7 s', p90: '23.4 s', calls: '11.6', callsRange: '11–18' });
    expect(go.single).toMatchObject({ success: '76.9%', meanCost: '$0.000680', p50: '5.4 s', calls: '2.5', callsRange: '2–3' });
    expect(go.supervisor).toMatchObject({ success: '84.6%', meanCost: '$0.00286', p50: '18.9 s', calls: '12', callsRange: '11–17' });
    expect(lg.single).toMatchObject({ success: '69.2%', meanCost: '$0.000561', p50: '4.5 s', calls: '2.2' });
    expect(lg.supervisor).toMatchObject({ success: '76.9%', meanCost: '$0.00284', p50: '17.4 s', calls: '11.5' });
    expect(sdk.single).toMatchObject({ success: '61.5%', meanCost: '$0.000409', p50: '3.9 s', calls: '2.2', callsRange: '1–4', tokensIn: '4.3', cacheRead: '2,790.4' });
    expect(sdk.supervisor).toMatchObject({ success: '76.9%', meanCost: '$0.00342', p50: '20.9 s', p90: '33.1 s', calls: '12.5', callsRange: '8–17' });
  });

  it('states the supervisor-to-single ratios and reads the success difference against the band', () => {
    const [hand, go, lg, sdk] = p.engines;
    expect(hand.ratio).toEqual({ cost: '4.31×', latency: '3.80×', calls: '4.64×', successDelta: '-7.7 points', beyondBand: false });
    expect(go.ratio).toMatchObject({ cost: '4.22×', latency: '3.48×', calls: '4.80×', successDelta: '+7.7 points', beyondBand: false });
    expect(lg.ratio).toMatchObject({ cost: '5.07×', latency: '3.90×', calls: '5.23×', successDelta: '+7.7 points', beyondBand: false });
    expect(sdk.ratio).toMatchObject({ cost: '8.35×', latency: '5.34×', calls: '5.68×', successDelta: '+15.4 points', beyondBand: false });
    expect(hand.sentence).toBe(
      'Hand-built (Python): the supervisor desk costs 4.31× the single agent per ticket, takes 3.80× as long at the median and makes 4.64× the model calls. Its success rate is 7.7 points lower, inside the ±27.7-point noise band, so not evidence of a difference.',
    );
  });

  it('words a success difference inside the band as not evidence', () => {
    const json = structuredClone(data.costCompare) as CostCompareJson;
    const e = json.engines.hand;
    if (e.supervisor_vs_single === null) throw new Error('ratio');
    e.supervisor_vs_single.success_delta = -0.076923;
    expect(costCompareProps(json).engines[0].sentence).toMatch(/Its success rate is 7\.7 points lower, inside the ±27\.7-point noise band, so not evidence of a difference\.$/);
  });

  it('draws bars on one scale per metric, longest = 100%', () => {
    const cost = bars(p.engines, 'cost');
    expect(cost.map((b) => [b.engine, b.single.text, b.supervisor.text])).toEqual([
      ['hand', '$0.000663', '$0.00286'],
      ['hand-go', '$0.000680', '$0.00286'],
      ['langgraph', '$0.000561', '$0.00284'],
      ['sdk', '$0.000409', '$0.00342'],
    ]);
    expect(cost[3].supervisor.percent).toBe(100);
    expect(cost[0].single.percent).toBe(19.4);
    expect(bars(p.engines, 'latency')[3].supervisor).toEqual({ text: '20.9 s', percent: 100 });
    expect(bars(p.engines, 'calls')[0].single).toEqual({ text: '2.5', percent: 20 });
  });

  it('shows the record command for a desk with missing tickets', () => {
    const json = structuredClone(data.costCompare) as CostCompareJson;
    json.engines.sdk.supervisor = { recorded: false, command: 'python agents/run.py --engine sdk --desk supervisor --tickets t001,t002 --mode record --max-cost 10', missing: ['t001', 't002'] };
    json.engines.sdk.supervisor_vs_single = null;
    const q = costCompareProps(json);
    expect(q.engines[3].supervisor).toEqual({
      recorded: false,
      command: `python agents/run.py --engine sdk --desk supervisor --tickets t001,t002 --mode record --max-cost 10\n${AGENTS_SCORE_COMMAND}\n${AGENTS_DERIVE_COMMAND}`,
      missing: 't001, t002',
    });
    expect(q.engines[3].ratio).toBeNull();
    expect(bars(q.engines, 'cost').map((b) => b.engine)).toEqual(['hand', 'hand-go', 'langgraph']);
  });

  it('stays small', () => {
    expect(size(p)).toBeLessThan(10_000);
  });
});

describe('CostCompare', () => {
  const p = costCompareProps(data.costCompare);

  it('has a bar per engine and desk, labelled with text', () => {
    render(<CostCompare {...p} />);
    const chart = screen.getByRole('list', { name: /Mean cost per ticket/ });
    expect(within(chart).getAllByRole('img')).toHaveLength(8);
    expect(chart.textContent).toContain('Supervisor: $0.00342');
    expect(chart.textContent).toContain('Single agent: $0.000409');
  });

  it('switches the metric with the radios and announces the ratios', () => {
    render(<CostCompare {...p} />);
    fireEvent.click(screen.getByRole('radio', { name: 'Median time (p50)' }));
    expect(screen.getByRole('list', { name: /Median time/ }).textContent).toContain('Supervisor: 20.9 s');
    expect(live()).toBe(
      'Showing median time (p50). Supervisor ÷ single: Hand-built (Python) 3.80×, Hand-built (Go) 3.48×, LangGraph 3.90×, Claude Agent SDK 5.34×.',
    );
  });

  it('has a table with every number beside the chart', () => {
    render(<CostCompare {...p} />);
    const rows = within(screen.getByRole('table', { name: /single and supervisor desks/ })).getAllByRole('row');
    expect(rows).toHaveLength(9);
    for (const s of ['Hand-built (Python)', 'Single agent', '76.9% ±27.7 points', '$0.000663', '$0.000607', '4.9 s', '5.8 s', '2.5 (2–4)']) expect(rows[1].textContent).toContain(s);
    for (const s of ['Claude Agent SDK', 'Supervisor', '76.9%', '$0.00342', '33.1 s', '12.5 (8–17)', '35,877.3']) expect(rows[8].textContent).toContain(s);
    expect(screen.getByText(/the supervisor desk costs 8\.35× the single agent/)).toBeTruthy();
  });

  it('shows No recorded run yet for a desk with missing tickets', () => {
    const json = structuredClone(data.costCompare) as CostCompareJson;
    json.engines.sdk.supervisor = { recorded: false, command: 'python agents/run.py --engine sdk --desk supervisor --tickets t001 --mode record --max-cost 10', missing: ['t001'] };
    json.engines.sdk.supervisor_vs_single = null;
    render(<CostCompare {...costCompareProps(json)} />);
    expect(screen.getByText('No recorded run yet')).toBeTruthy();
    expect(document.querySelector('pre')?.textContent).toContain('--engine sdk --desk supervisor --tickets t001 --mode record');
  });
});

// ── EngineScorecard ─────────────────────────────────────────────────────────
describe('engine scorecard data', () => {
  const p = engineScorecardProps(data.engineScores);

  it('has a row per engine and desk with its success, band and the gate check', () => {
    expect(p.rows.map((r) => `${r.engine}/${r.desk}`)).toEqual([
      'hand/single', 'hand/supervisor', 'hand-go/single', 'hand-go/supervisor', 'langgraph/single', 'langgraph/supervisor', 'sdk/single', 'sdk/supervisor',
    ]);
    expect(p.rows[0]).toEqual({
      engine: 'hand',
      desk: 'single',
      engineLabel: 'Hand-built (Python)',
      deskLabel: 'Single agent',
      label: 'Hand-built (Python), single agent',
      status: 'complete',
      complete: '13 of 13',
      success: '76.9%',
      band: '±27.7 points',
      floor: '49.2%',
      clears: false,
      trajectory: '100% of 13',
      safety: '0',
      meanCost: '$0.000663',
      p50: '4.9 s',
      costBasis: 'recording_tokens',
      command: null,
      value: { success: 0.769231, band: 0.27735 },
    });
    // engine_scores.json rounds this mean to 0.002865, whose binary value lies just below the half: $0.00286,
    // the same as cost_compare.json's unrounded 0.00286498.
    expect(p.rows[3]).toMatchObject({ label: 'Hand-built (Go), supervisor', success: '84.6%', floor: '56.9%', clears: false, trajectory: '100% of 13', meanCost: '$0.00286', p50: '18.9 s' });
    expect(p.rows[1]).toMatchObject({ success: '69.2%', floor: '41.5%', trajectory: '92.3% of 13', meanCost: '$0.00286', p50: '18.7 s' });
    expect(p.rows[4]).toMatchObject({ success: '69.2%', floor: '41.5%', meanCost: '$0.000561', p50: '4.5 s' });
    expect(p.rows[5]).toMatchObject({ success: '76.9%', floor: '49.2%', meanCost: '$0.00284', p50: '17.4 s' });
    expect(p.rows[6]).toMatchObject({ success: '61.5%', floor: '33.8%', trajectory: '76.9% of 13', meanCost: '$0.000409', p50: '3.9 s' });
    expect(p.rows[7]).toMatchObject({ success: '76.9%', floor: '49.2%', trajectory: '100% of 4', meanCost: '$0.00342', p50: '20.9 s' });
    expect(p.rows.every((r) => !r.clears)).toBe(true);
  });

  it('states the gate verdict as the scorer wrote it: no engine clears it', () => {
    expect(GATE_MIN_SUCCESS).toBe(0.7);
    expect(data.engineScores.gate.reason).toContain(`against the minimum ${GATE_MIN_SUCCESS}`);
    expect(p.gate).toEqual({
      passed: false,
      headline: 'No engine clears the gate.',
      rule: 'The gate: success minus the noise band must reach 70%, with no safety failures.',
      reason:
        'none qualifies: the best, hand-go supervisor, has success 0.846 - band 0.277 = 0.569 against the minimum 0.7 (at most 0 safety failures; it has 0)',
      min: 0.7,
      minText: '70%',
    });
  });

  it('names the judge and how many replies it judged', () => {
    expect(p.judge).toBe("Replies judged by claude-haiku-5-5: 101. The LLMs lab's judge model was claude-opus-5-5. A reply with no judge recording has no judge verdict; it is never guessed.");
  });

  it('drills down to each ticket of a run', () => {
    const rows = p.tickets['hand-go/supervisor'].map(ticketView);
    expect(rows).toHaveLength(13);
    expect(rows.map((r) => r.ticket)).toEqual(['t001', 't002', 't003', 't004', 't005', 't006', 't007', 't008', 't009', 't010', 't011', 't018', 't061']);
    expect(rows[12]).toEqual({ ticket: 't061', success: 'no', category: 'yes', judge: 'no', trajectory: 'yes', safety: 'no', cost: '$0.00451', time: '34.2 s' });
    expect(p.tickets['hand-go/supervisor'][12]).toEqual(['t061', 0, 1, 0, 1, 0, 0.0045072, 34192, null]);
    expect(ticketView(p.tickets['hand/single'][0])).toEqual({ ticket: 't001', success: 'yes', category: 'yes', judge: 'yes', trajectory: 'yes', safety: 'no', cost: '$0.000654', time: '6.4 s' });
    const traj = p.tickets['sdk/supervisor'].map((r) => ticketView(r).trajectory);
    expect(traj.filter((t) => t === 'not evaluable')).toHaveLength(9);
    expect(p.initial).toBe('hand-go/supervisor');
  });

  it("shows the Claude Agent SDK's own estimate beside the ledger cost on SDK tickets only", () => {
    // engine_scores.json sdk/single t001: cost_usd 0.00059775 (ledger), sdk_cost_usd 0.00059775 (the CLI's
    // total_cost_usd); sdk/supervisor t004: 0.00461509 and 0.0046151.
    expect(ticketView(p.tickets['sdk/single'][0])).toMatchObject({ ticket: 't001', cost: '$0.000598', sdkCost: '$0.000598' });
    expect(ticketView(p.tickets['sdk/supervisor'][3])).toMatchObject({ ticket: 't004', cost: '$0.00462', sdkCost: '$0.00462' });
    expect(p.tickets['sdk/supervisor'].every((t) => t[8] !== null)).toBe(true);
    for (const key of ['hand/single', 'hand-go/supervisor', 'langgraph/single']) {
      expect(p.tickets[key].every((t) => ticketView(t).sdkCost === undefined), key).toBe(true);
    }
  });

  it('says every cost is from the ledger, because every row says so', () => {
    expect(data.engineScores.about.cost_basis).toMatch(/^per row: recording_tokens \(the proxy's ledger, R62\)/);
    expect(p.rows.map((r) => r.costBasis)).toEqual(Array(8).fill('recording_tokens'));
    expect(p.costNote).toBe("Every cost here is from the proxy's ledger: what the recorded calls cost, priced from their usage.");
    const json = structuredClone(data.engineScores) as EngineScoresJson;
    json.scores.sdk.single.cost_basis = 'sdk_estimate';
    expect(engineScorecardProps(json).costNote).toBe("Not every cost here is from the proxy's ledger: Claude Agent SDK, single agent (sdk_estimate).");
  });

  it('shows not recorded and incomplete rows with their record command and no rates', () => {
    const json = structuredClone(data.engineScores) as EngineScoresJson;
    json.scores.sdk.supervisor = {
      n: 13, complete: 0, status: 'not recorded', success_rate: null, band: null, trajectory_rate: null, trajectory_n: null, mean_cost_usd: null,
      cost_basis: null, p50_ms: null, safety_fails: 0, record: 'python agents/run.py --set desk-sdk --mode record --max-cost 10',
    };
    const lg = json.scores.langgraph.single;
    json.scores.langgraph.single = { ...lg, complete: 11, status: 'incomplete', success_rate: null, band: null, trajectory_rate: null, trajectory_n: null, mean_cost_usd: null, p50_ms: null, record: 'python agents/run.py --engine langgraph --desk single --tickets t004,t061 --mode record --max-cost 10' };
    const q = engineScorecardProps(json);
    expect(q.rows[7]).toMatchObject({ status: 'not recorded', complete: '0 of 13', success: 'n/a', floor: 'n/a', clears: false });
    expect(q.rows[7].command).toBe(`python agents/run.py --set desk-sdk --mode record --max-cost 10\n${AGENTS_SCORE_COMMAND}\n${AGENTS_DERIVE_COMMAND}`);
    expect(q.rows[4]).toMatchObject({ status: 'incomplete', complete: '11 of 13', success: 'n/a', meanCost: 'n/a' });
    expect(q.tickets['sdk/supervisor']).toEqual([]);
  });

  it('stays small', () => {
    expect(size(p)).toBeLessThan(30_000);
  });
});

describe('EngineScorecard', () => {
  const p = engineScorecardProps(data.engineScores);

  it('leads with the gate verdict, unsoftened', () => {
    render(<EngineScorecard {...p} />);
    const verdict = screen.getByRole('status');
    expect(verdict.textContent).toContain('No engine clears the gate.');
    expect(verdict.textContent).toContain('none qualifies: the best, hand-go supervisor, has success 0.846 - band 0.277 = 0.569 against the minimum 0.7');
  });

  it('draws each success with its band against the 70% line, and a table with the same numbers', () => {
    render(<EngineScorecard {...p} />);
    const chart = screen.getByRole('list', { name: /Success rate with its noise band/ });
    const marks = within(chart).getAllByRole('img');
    expect(marks).toHaveLength(8);
    expect(marks[3].getAttribute('aria-label')).toBe('Hand-built (Go), supervisor: success 84.6%, band ±27.7 points, success minus band 56.9%: below the 70% gate.');
    const rows = within(screen.getByRole('table', { name: 'Scores per engine and desk' })).getAllByRole('row');
    expect(rows).toHaveLength(9);
    for (const s of ['Hand-built (Go)', 'Supervisor', '84.6%', '±27.7 points', '56.9%', 'No', '100% of 13', '$0.00286', '18.9 s']) expect(rows[4].textContent).toContain(s);
  });

  it('drills down to the tickets of the chosen run, and announces the choice', () => {
    render(<EngineScorecard {...p} />);
    const pick = screen.getByLabelText('Show the tickets of');
    fireEvent.change(pick, { target: { value: 'sdk/single' } });
    expect(live()).toBe('Claude Agent SDK, single agent: 8 of 13 tickets succeeded (61.5%).');
    const rows = within(screen.getByRole('table', { name: /Tickets of Claude Agent SDK, single agent/ })).getAllByRole('row');
    expect(rows).toHaveLength(14);
    for (const s of ['t001', 'yes', '$0.000598', '4.9 s']) expect(rows[1].textContent).toContain(s);
  });

  it("adds the SDK's own estimate column beside the ledger cost when an SDK run is drilled into", () => {
    render(<EngineScorecard {...p} />);
    const table = () => screen.getByRole('table', { name: /^Tickets of / });
    expect(table().textContent).not.toContain("The SDK's own estimate");
    fireEvent.change(screen.getByLabelText('Show the tickets of'), { target: { value: 'sdk/supervisor' } });
    const rows = within(table()).getAllByRole('row');
    expect(rows[0].textContent).toContain('Cost (ledger)');
    expect(rows[0].textContent).toContain("The SDK's own estimate");
    expect(rows[4].textContent).toContain('t004');
    expect(rows[4].textContent!.split('$0.00462')).toHaveLength(3);
    expect(screen.getByText(/client-side estimate it reports itself/)).toBeTruthy();
  });

  it('hides the drill-down when a lesson turns it off, and keeps the verdict and the table', () => {
    render(<EngineScorecard {...engineScorecardProps(data.engineScores, { drillDown: false })} />);
    expect(screen.queryByLabelText('Show the tickets of')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('No engine clears the gate.');
    expect(screen.getByRole('table', { name: 'Scores per engine and desk' })).toBeTruthy();
  });

  it('shows No recorded run yet with the command for a desk not recorded', () => {
    const json = structuredClone(data.engineScores) as EngineScoresJson;
    for (const engine of Object.keys(json.scores)) {
      for (const desk of Object.keys(json.scores[engine])) {
        json.scores[engine][desk] = {
          n: 13, complete: 0, status: 'not recorded', success_rate: null, band: null, trajectory_rate: null, trajectory_n: null, mean_cost_usd: null,
          cost_basis: null, p50_ms: null, safety_fails: 0, record: `python agents/run.py --set desk-${engine} --mode record --max-cost 10`,
        };
      }
    }
    json.gate = { engine: null, desk: null, reason: 'no complete engine x desk: record the desk sets first' };
    render(<EngineScorecard {...engineScorecardProps(json)} />);
    expect(screen.getAllByText('No recorded run yet').length).toBeGreaterThan(0);
    expect(document.querySelector('pre')?.textContent).toContain('python agents/run.py --set desk-hand --mode record --max-cost 10');
    expect(screen.getByRole('status').textContent).toContain('no complete engine x desk: record the desk sets first');
  });
});

// ── Lesson 7: the recording sets ────────────────────────────────────────────
describe('recording sets (lesson 7)', () => {
  it('keeps each set as derived: runs, model calls, recordings, orphans and cost', () => {
    const s = data.sets.sets;
    expect(Object.keys(s)).toEqual(['desk-hand', 'desk-hand-go', 'desk-langgraph', 'desk-sdk', 'patterns', 'patterns-go']);
    expect(s['desk-hand']).toMatchObject({ runs: 26, expected_runs: 26, model_calls: 183, recordings: 183, orphans: 0, recorded_cost_usd: 0.0457416, complete: true });
    expect(s.patterns).toMatchObject({ runs: 15, model_calls: 60, recordings: 57, orphans: 0, recorded_cost_usd: 0.0134697, distinct_recorded_cost_usd: 0.0130335 });
    expect(s['desk-sdk'].replay).toBe('python agents/run.py --set desk-sdk');
    expect(s['desk-sdk'].record).toBe('python agents/run.py --set desk-sdk --mode record --max-cost 10');
  });
});

// ── Page budget (spec §6: new props under about 200 KB) ─────────────────────
// Measured as shipped: Astro writes each island's props into the page with serializeProps (every value
// wrapped as [0, value]) and escapeHTML (every quote as &quot;), so a page carries 1.7-2.4 times the raw
// JSON. The course is one page (src/pages/index.astro renders every lesson), so the track's lessons all
// ship together: the budget holds for their sum, and each lesson's share is named in the message.
describe('page budget', () => {
  // What the lessons are expected to pass: lessons 2-4 their patterns, 5/8/9 their engines, 6, 10 and 11
  // (lesson 11 shows the gate and the table; lesson 10 has the drill-down).
  const lessons: [string, object][] = [
    ['lesson 2 PatternStepper', patternStepperProps(data.patterns, { patterns: ['chain', 'route'] })],
    ['lesson 3 PatternStepper', patternStepperProps(data.patterns, { patterns: ['parallel', 'orchestrate'] })],
    ['lesson 4 PatternStepper', patternStepperProps(data.patterns, { patterns: ['evaluate_optimize'] })],
    ['lesson 5 AgentTrace', agentTraceProps(data.traces, { engines: ['hand', 'hand-go'] })],
    ['lesson 6 CostCompare', costCompareProps(data.costCompare)],
    ['lesson 8 AgentTrace', agentTraceProps(data.traces, { engines: ['langgraph'] })],
    ['lesson 9 AgentTrace', agentTraceProps(data.traces, { engines: ['sdk'] })],
    ['lesson 10 EngineScorecard', engineScorecardProps(data.engineScores)],
    ['lesson 11 EngineScorecard', engineScorecardProps(data.engineScores, { drillDown: false })],
  ];
  const shipped = (props: object) => escapeHTML(serializeProps(props)).length;

  it('keeps the props the agents lessons ship, as Astro writes them, under 200 KB', () => {
    const sizes = lessons.map(([name, props]) => [name, shipped(props)] as const);
    const total = sizes.reduce((a, [, n]) => a + n, 0);
    const [heaviest, most] = sizes.reduce((a, b) => (b[1] > a[1] ? b : a));
    const shares = sizes.map(([name, n]) => `${name} ${n}`).join(', ');
    expect(total, `agents lessons ship ${total} bytes of props (heaviest: ${heaviest}, ${most} bytes): ${shares}`).toBeLessThan(200_000);
  });

  it('lets a lesson turn the drill-down off, which drops the per-ticket rows', () => {
    const off = engineScorecardProps(data.engineScores, { drillDown: false });
    expect(off.tickets).toEqual({});
    expect(off.rows).toHaveLength(8);
    expect(shipped(off)).toBeLessThan(shipped(engineScorecardProps(data.engineScores)) / 2);
  });
});
