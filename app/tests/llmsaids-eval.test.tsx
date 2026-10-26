// @vitest-environment jsdom
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { buildLlmsData, callCost, gradeRun, loadLlmsData, noiseBand, runName, type LlmsData } from '../src/lib/llmsData';
import {
  CATEGORIES,
  NONE,
  RECORD_COMMAND,
  cellTickets,
  evalSimProps,
  filterOptions,
  filterTickets,
  matrixCounts,
  matrixProps,
  runLabel,
  simFrames,
  snippet,
  ticketRows,
  type SimCall,
} from '../src/lib/llmsAids';
import DatasetBrowser from '../src/components/llms/DatasetBrowser';
import ConfusionMatrix from '../src/components/llms/ConfusionMatrix';
import EvalRunSim from '../src/components/llms/EvalRunSim';

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

const V1 = 'triage__claude-haiku-5-5__v1_basic__default';
const V2 = 'triage__claude-haiku-5-5__v2_rules__default';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ── llmsAids.ts ─────────────────────────────────────────────────────────────
describe('ticket rows and filters', () => {
  const rows = ticketRows(fixtureData().tickets);

  it('cuts text to the first 140 characters', () => {
    expect(snippet('short')).toBe('short');
    const long = 'x'.repeat(200);
    expect(snippet(long)).toBe(`${'x'.repeat(140)}…`);
    expect(rows[0]).toEqual({
      id: 'f001',
      text: '[fixture] I was charged twice this month, please check.',
      category: 'billing',
      priority: 'high',
      tags: ['angry_minor'],
      split: 'dev',
    });
  });

  it('filterTickets keeps tickets matching every set filter', () => {
    expect(filterTickets(rows, {})).toHaveLength(8);
    expect(filterTickets(rows, { category: 'billing' }).map((r) => r.id)).toEqual(['f001', 'f005']);
    expect(filterTickets(rows, { category: 'billing', split: 'test' }).map((r) => r.id)).toEqual(['f005']);
    expect(filterTickets(rows, { priority: 'urgent' }).map((r) => r.id)).toEqual(['f004', 'f008']);
    expect(filterTickets(rows, { tag: 'non_english' }).map((r) => r.id)).toEqual(['f003']);
    expect(filterTickets(rows, { tag: 'very_short', split: 'dev' })).toEqual([]);
    expect(filterTickets(rows, { category: null, tag: null }).length).toBe(8);
  });

  it('filterOptions lists categories and priorities in lab order, splits and tags sorted', () => {
    const o = filterOptions(rows);
    expect(o.category).toEqual(['billing', 'bug', 'account', 'outage']);
    expect(o.priority).toEqual(['low', 'normal', 'high', 'urgent']);
    expect(o.split).toEqual(['dev', 'test']);
    expect(o.tag).toEqual(['angry_minor', 'multi_issue', 'non_english', 'very_short']);
  });
});

describe('confusion matrix data', () => {
  const data = fixtureData();

  it('runLabel names the model, prompt and effort', () => {
    expect(runLabel({ kind: 'triage', model: 'claude-haiku-5-5', effort: null, prompt_version: 'v1_basic' })).toBe('Haiku 5.5 · v1_basic');
    expect(runLabel({ kind: 'triage', model: 'claude-opus-5-5', effort: 'high', prompt_version: 'v2_rules' })).toBe(
      'Opus 5.5 · v2_rules · high effort',
    );
  });

  it('matrixProps passes ticket ids, labels and one answer per ticket per run, nothing else', () => {
    const p = matrixProps(data, { run: V1 });
    expect(p.recorded).toBe(true);
    expect(p.initial).toBe(V1);
    expect(p.tickets.map((t) => t.id)).toEqual(['f001', 'f002', 'f003', 'f004']);
    expect(Object.keys(p.tickets[0]).sort()).toEqual(['category', 'id', 'text']);
    expect(p.runs.map((r) => r.name)).toEqual([V1, V2]);
    expect(p.runs[0].answers).toEqual(['billing', 'bug', 'account', 'bug']);
    expect(Object.keys(p.runs[0]).sort()).toEqual(['answers', 'label', 'name']);
  });

  it('a refusal or an error has no answer and lands in (none)', () => {
    const p = matrixProps(data, { run: V1, split: 'all' });
    expect(p.runs[0].answers.slice(6)).toEqual([null, null]); // f007 refusal, f008 error
    const counts = matrixCounts(p.tickets, p.runs[0].answers);
    expect(counts.cols).toEqual([...CATEGORIES, NONE]);
    expect(counts.rows).toEqual([...CATEGORIES]);
    const at = (e: string, pr: string) => counts.counts[counts.rows.indexOf(e)][counts.cols.indexOf(pr)];
    expect(at('account', NONE)).toBe(1);
    expect(at('outage', NONE)).toBe(1);
    expect(at('outage', 'bug')).toBe(1);
    expect(at('billing', 'billing')).toBe(2);
    expect(counts.max).toBe(2);
  });

  it('cellTickets lists the ids in one cell', () => {
    const p = matrixProps(data, { run: V1, split: 'all' });
    const a = p.runs[0].answers;
    expect(cellTickets(p.tickets, a, 'outage', 'bug')).toEqual(['f004']);
    expect(cellTickets(p.tickets, a, 'outage', NONE)).toEqual(['f008']);
    expect(cellTickets(p.tickets, a, 'bug', 'bug')).toEqual(['f002', 'f006']);
    expect(cellTickets(p.tickets, a, 'how_to', 'bug')).toEqual([]);
  });

  it('is not recorded with no responses, or when the run is missing', () => {
    const empty = matrixProps(fixtureData(false), { run: V1 });
    expect(empty).toMatchObject({ recorded: false, runs: [], tickets: [] });
    expect(matrixProps(data, { run: 'triage__claude-haiku-5-5__v9__default' }).recorded).toBe(false);
  });
});

describe('eval run sim data', () => {
  const call = (id: string, ms: number, ok = true): SimCall => ({ id, ms, in: 1000, out: 100, ok });

  it('evalSimProps passes per-call latency, tokens and correctness plus one score per rep', () => {
    const p = evalSimProps(fixtureData(), { run: V2 });
    expect(p.recorded).toBe(true);
    expect(p.model).toBe('claude-haiku-5-5');
    expect(p.label).toBe('Haiku 5.5 · v2_rules');
    expect(p.calls).toHaveLength(4); // dev, rep 0
    expect(p.calls[0]).toEqual({ id: 'f001', ms: 600, in: 90, out: 40, ok: true });
    expect(p.reps).toEqual([
      { rep: 0, both: 1, scored: 4 },
      { rep: 1, both: 0.75, scored: 4 }, // f002 answered low, expected normal
    ]);
    expect(p.band).toBeCloseTo(noiseBand(4));
  });

  it('evalSimProps is not recorded with no responses', () => {
    expect(evalSimProps(fixtureData(false), { run: V2 })).toMatchObject({ recorded: false, calls: [], reps: [] });
  });

  it('simFrames finishes calls in latency order over a worker pool, with running tokens and cost', () => {
    const calls = [call('a', 500), call('b', 100, false), call('c', 300), call('d', 50)];
    const reps = [
      { rep: 0, both: 0.75, scored: 4 },
      { rep: 1, both: 0.5, scored: 4 },
    ];
    const frames = simFrames(calls, 'claude-haiku-5-5', reps, 2);
    // Two workers: a (0-500) and b (0-100) start; c starts at 100 (ends 400); d at 400 (ends 450).
    expect(frames).toHaveLength(1 + 4 + 2);
    expect(frames[0]).toMatchObject({ done: 0, total: 4, last: null, elapsedMs: 0, cost: 0, correct: 0, repsShown: 0 });
    expect(frames.slice(1, 5).map((f) => f.last!.id)).toEqual(['b', 'c', 'd', 'a']);
    expect(frames.slice(1, 5).map((f) => f.elapsedMs)).toEqual([100, 400, 450, 500]);
    expect(frames[2]).toMatchObject({ done: 2, inputTokens: 2000, outputTokens: 200, correct: 1 });
    expect(frames[2].cost).toBeCloseTo(2 * callCost('claude-haiku-5-5', 1000, 100));
    expect(frames[4]).toMatchObject({ done: 4, correct: 3, repsShown: 0 });
    expect(frames[5]).toMatchObject({ done: 4, repsShown: 1 });
    expect(frames[6]).toMatchObject({ repsShown: 2 });
  });

  it('simFrames with one worker runs calls one after another', () => {
    const frames = simFrames([call('a', 500), call('b', 100)], 'claude-haiku-5-5', [], 1);
    expect(frames.map((f) => f.elapsedMs)).toEqual([0, 500, 600]);
  });
});

// ── Islands ─────────────────────────────────────────────────────────────────
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }));

describe('DatasetBrowser', () => {
  const rows = ticketRows(fixtureData().tickets);

  it('shows every ticket and the count line', () => {
    const { container } = render(<DatasetBrowser tickets={rows} />);
    expect(screen.getByText('8 of 8 tickets')).toBeTruthy();
    expect(container.querySelectorAll('.db-item')).toHaveLength(8);
    expect(container.textContent).toContain('[fixture] I was charged twice this month');
  });

  it('filters change the count; pressing a filter again clears it', () => {
    const { container } = render(<DatasetBrowser tickets={rows} />);
    const billing = screen.getByRole('button', { name: 'billing' });
    fireEvent.click(billing);
    expect(billing.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText('2 of 8 tickets')).toBeTruthy();
    click('test');
    expect(screen.getByText('1 of 8 tickets')).toBeTruthy();
    expect([...container.querySelectorAll('.db-id')].map((e) => e.textContent)).toEqual(['f005']);
    click('test');
    expect(screen.getByText('2 of 8 tickets')).toBeTruthy();
    click('urgent');
    expect(screen.getByText('0 of 8 tickets')).toBeTruthy();
    expect(screen.getByText('No ticket matches these filters.')).toBeTruthy();
    click('Clear filters');
    expect(screen.getByText('8 of 8 tickets')).toBeTruthy();
  });

  it('a tag filter keeps tickets with that tag', () => {
    render(<DatasetBrowser tickets={rows} />);
    click('non_english');
    expect(screen.getByText('1 of 8 tickets')).toBeTruthy();
  });
});

describe('ConfusionMatrix', () => {
  it('draws 6 x 7 cells with the diagonal marked', () => {
    const { container } = render(<ConfusionMatrix {...matrixProps(fixtureData(), { run: V1, split: 'all' })} />);
    expect(container.querySelectorAll('tbody tr')).toHaveLength(6);
    expect(container.querySelectorAll('tbody td')).toHaveLength(42);
    expect(container.querySelectorAll('td.cm2-diag')).toHaveLength(6);
    expect(container.textContent).toContain(NONE);
  });

  it('says right or wrong without colour: labels, a visible mark and hidden text for empty cells', () => {
    const { container } = render(<ConfusionMatrix {...matrixProps(fixtureData(), { run: V1, split: 'all' })} />);
    expect(screen.getByRole('button', { name: 'Expected billing, answered billing: 2 tickets (correct)' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Expected outage, answered bug: 1 ticket (mistake)' })).toBeTruthy();
    expect(container.querySelectorAll('td.cm2-diag.has-count .cm2-mark')).toHaveLength(3); // billing, bug, account
    expect(container.querySelector('.cm2-legend')!.getAttribute('aria-hidden')).toBeNull();
    expect(container.querySelector('.cm2-legend')!.textContent).toContain('✓');
    const empty = container.querySelector('td:not(.has-count)')!;
    expect(empty.querySelector('[aria-label]')).toBeNull();
    expect(empty.querySelector('.visually-hidden')!.textContent).toBe('Expected billing, answered bug: none');
  });

  it('clicking a cell lists its tickets with the model answer', () => {
    const { container } = render(<ConfusionMatrix {...matrixProps(fixtureData(), { run: V1, split: 'all' })} />);
    click('Expected outage, answered bug: 1 ticket (mistake)');
    const list = container.querySelector('.cm2-cases')!;
    expect([...list.querySelectorAll('.cm2-case-id')].map((e) => e.textContent)).toEqual(['f004']);
    expect(list.textContent).toContain('answered bug');
    expect(list.textContent).toContain('Everything is down');
    click('Expected outage, answered (none): 1 ticket (mistake)');
    expect([...container.querySelectorAll('.cm2-case-id')].map((e) => e.textContent)).toEqual(['f008']);
    expect(container.querySelector('.cm2-cases')!.textContent).toContain('no answer');
  });

  it('cells are buttons only when they hold tickets', () => {
    render(<ConfusionMatrix {...matrixProps(fixtureData(), { run: V1, split: 'all' })} />);
    const buttons = screen.getAllByRole('button').filter((b) => b.closest('table'));
    expect(buttons).toHaveLength(6); // billing, bug, account, outage->bug, account->(none), outage->(none)
  });

  it('the picker switches the recorded config', () => {
    const { container } = render(<ConfusionMatrix {...matrixProps(fixtureData(), { run: V1 })} />);
    expect(container.textContent).toContain('3 of 4 right on category');
    fireEvent.change(screen.getByLabelText('Recorded run'), { target: { value: V2 } });
    expect(container.textContent).toContain('4 of 4 right on category');
  });

  it('shows the record command with no recorded run', () => {
    const { container } = render(<ConfusionMatrix {...matrixProps(fixtureData(false), { run: V1 })} />);
    expect(screen.getByText('No recorded run yet')).toBeTruthy();
    expect(container.textContent).toContain(RECORD_COMMAND);
    expect(container.querySelector('table')).toBeNull();
  });
});

describe('EvalRunSim', () => {
  const props = () => evalSimProps(fixtureData(), { run: V2 });

  it('starts with no calls finished', () => {
    const { container } = render(<EvalRunSim {...props()} />);
    expect(container.querySelector('.ers-state')!.textContent).toContain('0 of 4');
    expect(container.querySelectorAll('.ers-call.is-done')).toHaveLength(0);
  });

  it('Step finishes one call and adds its tokens and cost', () => {
    const { container } = render(<EvalRunSim {...props()} />);
    click('Step');
    const state = container.querySelector('.ers-state')!.textContent!;
    expect(state).toContain('1 of 4');
    expect(state).toContain('90');
    expect(state).toContain(`$${callCost('claude-haiku-5-5', 90, 40).toFixed(5)}`);
    expect(container.querySelectorAll('.ers-call.is-done')).toHaveLength(1);
  });

  it('after the calls come the repeats with each score and the band; Reset starts over', () => {
    const { container } = render(<EvalRunSim {...props()} />);
    for (let i = 0; i < 6; i++) click('Step');
    const reps = container.querySelector('.ers-reps')!;
    expect(reps.textContent).toContain('100.0%');
    expect(reps.textContent).toContain('75.0%');
    expect(reps.textContent).toContain(`±${(noiseBand(4) * 100).toFixed(1)}%`);
    expect((screen.getByRole('button', { name: 'Step' }) as HTMLButtonElement).disabled).toBe(true);
    click('Reset');
    expect(container.querySelector('.ers-state')!.textContent).toContain('0 of 4');
  });

  it('Play runs to the end on a timer', () => {
    vi.useFakeTimers();
    const { container } = render(<EvalRunSim {...props()} />);
    click('Play');
    expect(screen.getByRole('button', { name: 'Pause' })).toBeTruthy();
    // One timer per frame: 4 calls + 2 repeats, then Play turns back off.
    for (let i = 0; i < 10; i++) act(() => void vi.advanceTimersByTime(1_000));
    expect(container.querySelectorAll('.ers-call.is-done')).toHaveLength(4);
    // The final frame: every repeat's score and the band.
    expect([...container.querySelectorAll('.ers-rep-score')].map((e) => e.textContent)).toEqual(['100.0%', '75.0%']);
    expect(container.querySelector('.ers-band')!.textContent).toContain(`±${(noiseBand(4) * 100).toFixed(1)}%`);
    expect(container.querySelector('.ers-state')!.textContent).toContain('done');
    expect(screen.getByRole('button', { name: 'Play' })).toBeTruthy();
  });

  it('moves focus to Reset when Step or Play is disabled at the end', () => {
    vi.useFakeTimers();
    render(<EvalRunSim {...props()} />);
    const step = screen.getByRole('button', { name: 'Step' });
    step.focus();
    for (let i = 0; i < 6; i++) fireEvent.click(step);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Reset' }));
    click('Reset');
    const play = screen.getByRole('button', { name: 'Play' });
    play.focus();
    fireEvent.click(play);
    for (let i = 0; i < 10; i++) act(() => void vi.advanceTimersByTime(1_000));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Reset' }));
  });

  it('shows the record command with no recorded run', () => {
    const { container } = render(<EvalRunSim {...evalSimProps(fixtureData(false), { run: V2 })} />);
    expect(screen.getByText('No recorded run yet')).toBeTruthy();
    expect(container.textContent).toContain(RECORD_COMMAND);
    expect(screen.queryByRole('button', { name: 'Step' })).toBeNull();
  });
});

// ── Real data: defaults exist and the props stay small (ruling R30) ─────────
describe('real data props', () => {
  const real = loadLlmsData();
  const size = (v: unknown) => JSON.stringify(v).length;

  it('the default configs are recorded and the props are small', () => {
    const dataset = { tickets: ticketRows(real.tickets) };
    const matrix = matrixProps(real, { run: V1 });
    const sim = evalSimProps(real, { run: V2 });
    expect(dataset.tickets).toHaveLength(60);
    expect(matrix.recorded).toBe(true);
    expect(matrix.tickets).toHaveLength(40);
    expect(matrix.runs.length).toBeGreaterThanOrEqual(11);
    expect(sim.recorded).toBe(true);
    expect(sim.calls).toHaveLength(40);
    expect(sim.reps.map((r) => r.rep)).toEqual([0, 1, 2]);
    // Tiles follow ticket order; only the frames follow finish order.
    expect(sim.calls.map((c) => c.id)).toEqual(real.tickets.filter((t) => t.split === 'dev').map((t) => t.id));
    expect(size(dataset)).toBeLessThan(16_000);
    expect(size(matrix)).toBeLessThan(13_000);
    expect(size(sim)).toBeLessThan(3_000);
  });

  // Expected values come from the Python lab, not from the code under test (replay, no API calls):
  //   python3 llms/python/triage_lab.py run --prompt v1_basic --split dev
  //     -> category 92.5%, priority 77.5%, both 70.0%, band +/-15.8%
  //   python3 llms/python/triage_lab.py run --prompt v2_rules --split dev --reps 3 -> both 96.7% (mean of the reps)
  //   per rep and the matrix, from triage_lab's own functions in llms/python:
  //     python3 -c "import triage_lab as T; tk=T.split(T.load_tickets(),'dev')
  //       print([T.accuracy([T.grade(t, T.triage(t,'v2_rules',rep=r)) for t in tk],'both_ok') for r in range(3)])
  //       print(T.confusion_matrix([(t['category'], (T.triage(t,'v1_basic').get('output') or {}).get('category')) for t in tk]))"
  //     -> [0.975, 0.975, 0.95]
  //     -> billing {billing 7}, bug {bug 10, how_to 1}, account {account 5, how_to 2}, how_to {how_to 6},
  //        feature_request {feature_request 6}, outage {outage 3}
  it('matches the Python lab on the real recorded runs', () => {
    const sim = evalSimProps(real, { run: V2 });
    expect(sim.reps.map((r) => r.both)).toEqual([0.975, 0.975, 0.95]);
    expect(sim.band).toBeCloseTo(noiseBand(40));
    expect(sim.band).toBeCloseTo(0.158, 3);

    const matrix = matrixProps(real, { run: V1 });
    const v1 = matrix.runs.find((r) => r.name === V1)!;
    const right = matrix.tickets.filter((t, i) => v1.answers[i] === t.category).length;
    expect(right).toBe(37); // 92.5% of 40
    const { rows, cols, counts } = matrixCounts(matrix.tickets, v1.answers);
    const at = (e: string, a: string) => counts[rows.indexOf(e)][cols.indexOf(a)];
    expect(at('billing', 'billing')).toBe(7);
    expect(at('bug', 'bug')).toBe(10);
    expect(at('bug', 'how_to')).toBe(1);
    expect(at('account', 'account')).toBe(5);
    expect(at('account', 'how_to')).toBe(2);
    expect(at('outage', 'outage')).toBe(3);
    expect(counts.flat().reduce((a, b) => a + b, 0)).toBe(40);
    expect(cols.map((c) => at('how_to', c)).reduce((a, b) => a + b, 0)).toBe(6);

    const v1Run = real.runs.find((r) => runName(r.config) === V1)!;
    const dev = new Set(real.tickets.filter((t) => t.split === 'dev').map((t) => t.id));
    const both = gradeRun(v1Run, real.tickets, 0).filter((g) => dev.has(g.case_id) && g.both_ok).length;
    expect(both / 40).toBe(0.7);
  });
});
