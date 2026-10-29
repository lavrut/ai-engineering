// @vitest-environment jsdom
// The AI Agents track's evaluation aids: CostCompare (lesson 6) and EngineScorecard (lessons 10-11),
// plus the lesson 7 set numbers and the page budget. Every number an aid shows is pinned here as a
// literal from agents/data/derived/*.json (.venv-agents/bin/python agents/derive.py; engine_scores.json from
// .venv-agents/bin/python agents/evals/run_evals.py). With 13 tickets no engine clears the gate (ruling R66): the
// verdict is pinned as it is.
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadAgentsData } from '../src/lib/agentsData';
import {
  AGENTS_DERIVE_COMMAND,
  AGENTS_SCORE_COMMAND,
  GATE_MIN_SUCCESS,
  bars,
  costCompareProps,
  engineScorecardProps,
  ticketView,
  type CostCompareJson,
  type EngineScoresJson,
} from '../src/lib/agentsAids';
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
    json.engines.sdk.supervisor = { recorded: false, command: '.venv-agents/bin/python agents/run.py --engine sdk --desk supervisor --tickets t001,t002 --mode record --max-cost 10', missing: ['t001', 't002'] };
    json.engines.sdk.supervisor_vs_single = null;
    const q = costCompareProps(json);
    expect(q.engines[3].supervisor).toEqual({
      recorded: false,
      command: `.venv-agents/bin/python agents/run.py --engine sdk --desk supervisor --tickets t001,t002 --mode record --max-cost 10\n${AGENTS_SCORE_COMMAND}\n${AGENTS_DERIVE_COMMAND}`,
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
    json.engines.sdk.supervisor = { recorded: false, command: '.venv-agents/bin/python agents/run.py --engine sdk --desk supervisor --tickets t001 --mode record --max-cost 10', missing: ['t001'] };
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
      cost_basis: null, p50_ms: null, safety_fails: 0, record: '.venv-agents/bin/python agents/run.py --set desk-sdk --mode record --max-cost 10',
    };
    const lg = json.scores.langgraph.single;
    json.scores.langgraph.single = { ...lg, complete: 11, status: 'incomplete', success_rate: null, band: null, trajectory_rate: null, trajectory_n: null, mean_cost_usd: null, p50_ms: null, record: '.venv-agents/bin/python agents/run.py --engine langgraph --desk single --tickets t004,t061 --mode record --max-cost 10' };
    const q = engineScorecardProps(json);
    expect(q.rows[7]).toMatchObject({ status: 'not recorded', complete: '0 of 13', success: 'n/a', floor: 'n/a', clears: false });
    expect(q.rows[7].command).toBe(`.venv-agents/bin/python agents/run.py --set desk-sdk --mode record --max-cost 10\n${AGENTS_SCORE_COMMAND}\n${AGENTS_DERIVE_COMMAND}`);
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
          cost_basis: null, p50_ms: null, safety_fails: 0, record: `.venv-agents/bin/python agents/run.py --set desk-${engine} --mode record --max-cost 10`,
        };
      }
    }
    json.gate = { engine: null, desk: null, reason: 'no complete engine x desk: record the desk sets first' };
    render(<EngineScorecard {...engineScorecardProps(json)} />);
    expect(screen.getAllByText('No recorded run yet').length).toBeGreaterThan(0);
    expect(document.querySelector('pre')?.textContent).toContain('.venv-agents/bin/python agents/run.py --set desk-hand --mode record --max-cost 10');
    expect(screen.getByRole('status').textContent).toContain('no complete engine x desk: record the desk sets first');
  });
});

// ── Lesson 7: the recording sets ────────────────────────────────────────────
describe('recording sets (lesson 7)', () => {
  it('keeps each set as derived: runs, model calls, recordings, orphans and cost', () => {
    const s = data.sets.sets;
    expect(Object.keys(s)).toEqual(['desk-hand', 'desk-hand-go', 'desk-langgraph', 'desk-sdk', 'patterns', 'patterns-go']);
    expect(s['desk-hand']).toMatchObject({ runs: 26, expected_runs: 26, model_calls: 183, recordings: 183, missing: 0, orphans: 0, recorded_cost_usd: 0.0457416, complete: true });
    expect(s.patterns).toMatchObject({ runs: 15, model_calls: 60, recordings: 57, missing: 0, orphans: 0, recorded_cost_usd: 0.0134697, distinct_recorded_cost_usd: 0.0130335 });
    // every recording a trace references is in the store: none missing (a missing one is reported, not counted)
    expect(Object.values(s).map((x) => x.missing)).toEqual([0, 0, 0, 0, 0, 0]);
    expect(s['desk-sdk'].replay).toBe('.venv-agents/bin/python agents/run.py --set desk-sdk');
    expect(s['desk-sdk'].record).toBe('.venv-agents/bin/python agents/run.py --set desk-sdk --mode record --max-cost 10');
  });
});

// ── Page budget (spec §6: new props under about 200 KB) ─────────────────────
// Measured from what ships: the built page, dist/index.html. Each island's props are the `props="…"` attribute
// on its <astro-island> (Astro's serialised, HTML-escaped JSON); this sums the agents aids' props bytes per agents
// lesson (PatternStepper, AgentTrace, CostCompare, EngineScorecard: the props this track added; the quizzes and
// the shared FlowDiagram are every track's and are not part of this budget). The
// course is one page (src/pages/index.astro renders every lesson, each in a <section data-view="…">), so the
// track's lessons all ship together: the budget holds for their sum, and each lesson's share is in the message.
// `npm test` keeps itself honest on a fresh checkout: when dist/index.html is missing, or older than any file
// under src/, agents/data/derived/ or astro.config.mjs, the setup runs `astro build` first (about 5 s).
const APP = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const PAGE = join(APP, 'dist', 'index.html');
const SOURCES = [
  join(APP, 'src'), resolve(APP, '../agents/data/derived'), resolve(APP, '../data/data/derived'), resolve(APP, '../data/contracts'),
  resolve(APP, '../ontology/data/derived'), join(APP, 'astro.config.mjs'),
];
const AIDS = ['PatternStepper', 'AgentTrace', 'CostCompare', 'EngineScorecard'];
const AGENTS_VIEWS = [
  'agents-workflows', 'agents-chain-route', 'agents-parallel', 'agents-evaluator', 'agents-multi-agent', 'agents-more-agents',
  'agents-replay', 'agents-langgraph', 'agents-agent-sdk', 'agents-evals', 'agents-capstone',
];

function newest(path: string): number {
  const st = statSync(path);
  if (!st.isDirectory()) return st.mtimeMs;
  return readdirSync(path).reduce((m, f) => Math.max(m, newest(join(path, f))), st.mtimeMs);
}

/** Run a build step; a failure throws with the step's own stderr (and stdout), never only an exit status. */
function runBuild(cmd: string, args: string[]): void {
  try {
    execFileSync(cmd, args, { cwd: APP, stdio: ['ignore', 'pipe', 'pipe'], encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  } catch (e) {
    const { status, stderr, stdout } = e as { status?: number | null; stderr?: string; stdout?: string };
    throw new Error(`page budget: ${cmd} ${args.join(' ')} failed (exit ${status ?? '?'}):\n${stderr || stdout || String(e)}`);
  }
}

function ensureBuilt(): void {
  if (existsSync(PAGE) && statSync(PAGE).mtimeMs >= Math.max(...SOURCES.map(newest))) return;
  console.warn(`page budget: ${PAGE} is ${existsSync(PAGE) ? 'stale' : 'missing'}; running astro build`);
  runBuild(join(APP, 'node_modules', '.bin', 'astro'), ['build']);
}

type Island = { component: string; bytes: number };

/** Every lesson's islands of one track, in page order: lesson n is the n-th view of that track. */
function shippedIslands(html: string, track = 'agents'): { lesson: string; islands: Island[] }[] {
  const views = html.split(/(?=<section data-view=")/).slice(1);
  return views
    .map((chunk) => ({ view: /^<section data-view="([^"]+)"/.exec(chunk)![1], chunk }))
    .filter(({ view }) => view.startsWith(`${track}-`))
    .map(({ view, chunk }, i) => {
      const islands = [...chunk.matchAll(/<astro-island\b((?:[^>"]|"[^"]*")*)>/g)].map(([, attrs]) => {
        const component = /component-url="[^"]*\/([A-Za-z]+)\.[^"/]*\.js"/.exec(attrs)?.[1] ?? '?';
        const props = /\sprops="([^"]*)"/.exec(attrs)?.[1] ?? '';
        return { component, bytes: Buffer.byteLength(props, 'utf8') };
      });
      return { lesson: `lesson ${i + 1} (${view})`, islands };
    });
}

describe('page budget', () => {
  it('a failed build step shows its stderr, not only its exit status', () => {
    // The message is built at run time, so it can only reach the error through the step's stderr (the command
    // line, which execFileSync's own message repeats, holds no "the-build-broke").
    const failing = ['-e', 'console.error(["the", "build", "broke"].join("-")); process.exit(3)'];
    expect(() => runBuild(process.execPath, failing)).toThrow(/the-build-broke/);
  });

  let lessons: { lesson: string; islands: Island[] }[] = [];
  let dataLessons: { lesson: string; islands: Island[] }[] = [];
  let ontologyLessons: { lesson: string; islands: Island[] }[] = [];
  const bytes = (l: { islands: Island[] }) => l.islands.filter((x) => AIDS.includes(x.component)).reduce((a, x) => a + x.bytes, 0);

  beforeAll(() => {
    ensureBuilt();
    const html = readFileSync(PAGE, 'utf8');
    lessons = shippedIslands(html);
    dataLessons = shippedIslands(html, 'data');
    ontologyLessons = shippedIslands(html, 'ontology');
  }, 180_000);

  // The Data Fabric & Data Contracts track, measured the same way: the props of its four aids over its lessons.
  // The test is bound to the content: it lists src/content/lessons/data/*.mdx, requires the six lessons spec §5
  // names, one data-* view per file in the built page, and each lesson to carry the aid spec §5 names for it. A
  // lesson file whose slug has no aid in DATA_LESSON_AIDS fails here, so a new lesson cannot slip past the check.
  const DATA_DIR = join(APP, 'src', 'content', 'lessons', 'data');
  const DATA_FILES = existsSync(DATA_DIR) ? readdirSync(DATA_DIR).filter((f) => f.endsWith('.mdx')).sort() : [];
  const DATA_AIDS = ['CatalogExplorer', 'QualityReport', 'ContractViewer', 'LineageDiff'];
  // spec §5: the aid each data lesson shows, by slug.
  const DATA_LESSON_AIDS: Record<string, string> = {
    fabric: 'FlowDiagram', catalog: 'CatalogExplorer', quality: 'QualityReport', contract: 'ContractViewer', enforce: 'ContractViewer', capstone: 'LineageDiff',
  };

  it('finds the six data lessons spec §5 names, each slug mapped to its aid', () => {
    const slugs = DATA_FILES.map((f) => f.replace(/^\d+-/, '').replace(/\.mdx$/, ''));
    expect(slugs).toEqual(['fabric', 'catalog', 'quality', 'contract', 'enforce', 'capstone']);
    expect(slugs.filter((s) => !Object.hasOwn(DATA_LESSON_AIDS, s)), 'a data lesson slug with no aid in DATA_LESSON_AIDS').toEqual([]);
  });

  it(
    'keeps the props the data aids ship, as the built page carries them, under 200 KB',
    () => {
      const slugs = DATA_FILES.map((f) => f.replace(/^\d+-/, '').replace(/\.mdx$/, ''));
      expect(dataLessons.map((l) => l.lesson.replace(/^lesson \d+ \((.*)\)$/, '$1'))).toEqual(slugs.map((s) => `data-${s}`));
      dataLessons.forEach((l, i) => {
        const want = DATA_LESSON_AIDS[slugs[i]];
        expect(want, `${l.lesson}: no aid mapped for slug "${slugs[i]}"`).toBeDefined();
        expect(l.islands.map((x) => x.component), l.lesson).toContain(want);
      });
      const shipped = dataLessons.flatMap((l) => l.islands).filter((x) => DATA_AIDS.includes(x.component));
      expect(shipped.length).toBeGreaterThan(0);
      expect(shipped.every((x) => x.bytes > 500)).toBe(true);
      const sizes = dataLessons.map((l) => [l.lesson, l.islands.filter((x) => DATA_AIDS.includes(x.component)).reduce((a, x) => a + x.bytes, 0)] as const);
      const total = sizes.reduce((a, [, n]) => a + n, 0);
      expect(total, `the data aids ship ${total} bytes of props: ${sizes.map(([name, n]) => `${name} ${n}`).join(', ')}`).toBeLessThan(200_000);
    },
  );

  // The Ontology track, measured the same way: the props of its three aids over its lessons, bound to
  // src/content/lessons/ontology/*.mdx and the aid spec §5 names for each slug. Until Task 6 adds the lessons there
  // is nothing to measure, so the budget test is skipped (its name says why) rather than passing on an empty sum;
  // tests/ontologyaids-grounding.test.tsx meanwhile measures the largest props the lessons could ship.
  // Task 6 removes the skipIf when it adds lessons/ontology/*.mdx: from then on the test must run and pass.
  const ONTOLOGY_DIR = join(APP, 'src', 'content', 'lessons', 'ontology');
  const ONTOLOGY_FILES = existsSync(ONTOLOGY_DIR) ? readdirSync(ONTOLOGY_DIR).filter((f) => f.endsWith('.mdx')).sort() : [];
  const ONTOLOGY_AIDS = ['OntologyBrowser', 'GroundingReport', 'AuditTable'];
  // spec §5: the aid each ontology lesson shows, by slug.
  const ONTOLOGY_LESSON_AIDS: Record<string, string> = { what: 'FlowDiagram', lumen: 'OntologyBrowser', grounding: 'GroundingReport', capstone: 'AuditTable' };

  it('finds no ontology-* view in the built page while there is no ontology lesson file', () => {
    if (ONTOLOGY_FILES.length > 0) return; // covered by the next test once the lessons exist
    console.warn('page budget: no ontology lessons yet (src/content/lessons/ontology/*.mdx); the ontology props budget test is skipped');
    expect(ontologyLessons).toEqual([]);
  });

  it.skipIf(ONTOLOGY_FILES.length === 0)(
    `keeps the props the ontology aids ship, as the built page carries them, under 200 KB${ONTOLOGY_FILES.length === 0 ? ' (skipped: no ontology lessons yet)' : ''}`,
    () => {
      const slugs = ONTOLOGY_FILES.map((f) => f.replace(/^\d+-/, '').replace(/\.mdx$/, ''));
      expect(slugs).toEqual(['what', 'lumen', 'grounding', 'capstone']);
      expect(ontologyLessons.map((l) => l.lesson.replace(/^lesson \d+ \((.*)\)$/, '$1'))).toEqual(slugs.map((s) => `ontology-${s}`));
      ontologyLessons.forEach((l, i) => {
        const want = ONTOLOGY_LESSON_AIDS[slugs[i]];
        expect(want, `${l.lesson}: no aid mapped for slug "${slugs[i]}"`).toBeDefined();
        expect(l.islands.map((x) => x.component), l.lesson).toContain(want);
      });
      const shipped = ontologyLessons.flatMap((l) => l.islands).filter((x) => ONTOLOGY_AIDS.includes(x.component));
      expect(shipped.length).toBeGreaterThan(0);
      expect(shipped.every((x) => x.bytes > 500)).toBe(true);
      const sizes = ontologyLessons.map((l) => [l.lesson, l.islands.filter((x) => ONTOLOGY_AIDS.includes(x.component)).reduce((a, x) => a + x.bytes, 0)] as const);
      const total = sizes.reduce((a, [, n]) => a + n, 0);
      expect(total, `the ontology aids ship ${total} bytes of props: ${sizes.map(([name, n]) => `${name} ${n}`).join(', ')}`).toBeLessThan(200_000);
    },
  );

  it('finds every agents lesson and its aids in the built page', () => {
    expect(lessons.map((l) => l.lesson.replace(/^lesson \d+ \((.*)\)$/, '$1'))).toEqual(AGENTS_VIEWS);
    expect(lessons.map((l) => l.islands.map((x) => x.component).join('+'))).toEqual([
      'FlowDiagram', 'PatternStepper', 'PatternStepper', 'PatternStepper', 'AgentTrace', 'CostCompare', 'FlowDiagram',
      'AgentTrace', 'AgentTrace', 'EngineScorecard', 'EngineScorecard',
    ].map((aid) => `${aid}+Quiz+Quiz`));
    expect(lessons.flatMap((l) => l.islands).filter((x) => AIDS.includes(x.component)).every((x) => x.bytes > 1000)).toBe(true);
  });

  it('keeps the props the agents aids ship, as the built page carries them, under 200 KB', () => {
    const sizes = lessons.map((l) => [l.lesson, bytes(l)] as const).filter(([, n]) => n > 0);
    const total = sizes.reduce((a, [, n]) => a + n, 0);
    const [heaviest, most] = sizes.reduce((a, b) => (b[1] > a[1] ? b : a));
    const shares = sizes.map(([name, n]) => `${name} ${n}`).join(', ');
    expect(total, `the agents aids ship ${total} bytes of props (heaviest: ${heaviest}, ${most} bytes): ${shares}`).toBeLessThan(200_000);
  });

  it('lets a lesson turn the drill-down off, which drops the per-ticket rows', () => {
    const off = engineScorecardProps(data.engineScores, { drillDown: false });
    expect(off.tickets).toEqual({});
    expect(off.rows).toHaveLength(8);
    const [evals, capstone] = [lessons[9], lessons[10]].map(bytes);   // lesson 10 drills down, lesson 11 does not
    expect(capstone).toBeLessThan(evals / 2);
  });
});
