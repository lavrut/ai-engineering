// @vitest-environment jsdom
// The AI Infrastructure track's trace aid: TraceExplorer (lesson 6), plus the module rules every infra aid keeps.
// Every value it shows is pinned here as a literal taken from infra/data/derived/traces.sample.json and capstone.json's
// gate (.venv-infra/bin/python infra/python/replay.py --start writes both).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadInfraData } from '../src/lib/infraData';
import { traceExplorerProps } from '../src/lib/infraAids';
import TraceExplorer from '../src/components/infra/TraceExplorer';
import { gateMark, outcomeMark } from '../src/components/infra/infraFormat';

const here = dirname(fileURLToPath(import.meta.url));
const data = loadInfraData();
const props = traceExplorerProps(data.traces, data.capstone.gate, { initial: 'cached' });
const live = () => screen.getByRole('status').textContent;
const grid = () => screen.getByRole('grid', { name: 'Matching traces' });
const bodyRows = () => within(grid()).getAllByRole('row').slice(1);
const select = (name: string) => screen.getByRole('combobox', { name }) as HTMLSelectElement;
const opened = () => screen.getByRole('region', { name: /^Open trace/ });

afterEach(() => cleanup());

describe('TraceExplorer', () => {
  it('lists the three labelled traces and opens cached, its decisions as stage: verdict (detail)', () => {
    render(<TraceExplorer {...props} />);
    expect(within(grid()).getAllByRole('row')[0].textContent).toBe('TraceTeamTagStatusOutcomemsCost');
    expect(bodyRows().map((r) => r.textContent)).toEqual([
      'cacheddeskt001200✓ cached1057$0.0000000',
      'retrieddeskt008200✓ ok0$0.0000035',
      'refusedpatternst007402✗ refused0none',
    ]);
    expect(screen.getByRole('region', { name: 'Open trace cached' })).toBeTruthy();
    const lines = within(within(opened()).getByRole('list', { name: 'Decisions' })).getAllByRole('listitem');
    expect(lines.map((l) => l.textContent)).toEqual([
      'tag: • desk (team from x-team; tag t001 from x-request-tag)',
      'budget: ✓ allow ($0.054740 spent + $0.002112 worst case within $1.00)',
      'cache: ✓ hit (stored 333s ago)',
      'ledger: ✓ booked ($0.000000 to team desk)',
      'trace: ✓ written (capstone.jsonl)',
    ]);
    expect(within(opened()).getByText('No upstream attempts.')).toBeTruthy();
    expect(live()).toBe('cached: desk, tag t001, status 200, cached, 1057 ms, $0.0000000.');
  });

  it('shows the gate lines verbatim, ok or red as a word beside the symbol', () => {
    render(<TraceExplorer {...props} />);
    expect(screen.getByTestId('gate-verdict').textContent).toBe('✓ green');
    const rows = within(screen.getByRole('table', { name: 'Gate lines' })).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.firstChild!.textContent)).toEqual(['ledger', 'budgets', 'stages', 'figures']);
    for (const r of rows) expect(r.children[1].textContent).toBe('✓ ok');
    expect(rows[3].children[2].textContent).toBe('hit rate 0.0209 (20 of 957) and p95 3471 ms; from the traces and the recordings: 0.0209 and 3471 ms');
    expect(gateMark(false)).toEqual({ icon: '✗', word: 'red', tone: 'fail' });
  });

  it('filters by team, tag and outcome, and opens one', () => {
    render(<TraceExplorer {...props} />);
    expect([...select('Outcome').options].map((o) => o.textContent)).toEqual(['any', 'cached', 'ok', 'refused']);
    expect([...select('Team').options].map((o) => o.textContent)).toEqual(['any', 'desk', 'patterns']);
    expect([...select('Tag').options].map((o) => o.textContent)).toEqual(['any', 't001', 't007', 't008']);
    fireEvent.change(select('Outcome'), { target: { value: 'cached' } });
    expect(bodyRows()).toHaveLength(1);
    expect(bodyRows()[0].textContent).toMatch(/^cached/);
    fireEvent.change(select('Outcome'), { target: { value: '' } });
    fireEvent.change(select('Team'), { target: { value: 'desk' } });
    expect(bodyRows().map((r) => r.firstChild!.textContent)).toEqual(['cached', 'retried']);
    fireEvent.change(select('Tag'), { target: { value: 't007' } });
    expect(screen.queryByRole('grid')).toBeNull();
    expect(screen.getByText('No trace matches these filters.')).toBeTruthy();
    fireEvent.change(select('Team'), { target: { value: '' } });
    fireEvent.click(bodyRows()[0]);
    const lines = within(within(screen.getByRole('region', { name: 'Open trace refused' })).getByRole('list', { name: 'Decisions' })).getAllByRole('listitem');
    expect(lines.map((l) => l.textContent)).toEqual([
      'tag: • patterns (team from x-team; tag t007 from x-request-tag)',
      'budget: ✗ refuse ($0.008291 spent + $0.002143 worst case > $0.01 limit)',
    ]);
  });

  it('moves the focused row with the arrow keys, announces it, and Enter opens it with its attempts', () => {
    render(<TraceExplorer {...props} />);
    expect(bodyRows()[0].getAttribute('tabindex')).toBe('0');
    fireEvent.keyDown(bodyRows()[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(bodyRows()[1]);
    expect(live()).toBe('retried: desk, tag t008, status 200, ok, 0 ms, $0.0000035.');
    fireEvent.keyDown(document.activeElement!, { key: 'Enter' });
    expect(screen.getByRole('region', { name: 'Open trace retried' })).toBeTruthy();
    const attempts = within(within(opened()).getByRole('list', { name: 'Attempts' })).getAllByRole('listitem');
    expect(attempts.map((a) => a.textContent)).toEqual([
      'Attempt 1: claude-haiku-5-5, ✗ 429 rate limited, 1 ms of backoff',
      'Attempt 2: claude-haiku-5-5, ✗ 500 server error, 2 ms of backoff',
      'Attempt 3: claude-haiku-5-5, ✓ 200 ok, 0 ms of backoff',
    ]);
    fireEvent.keyDown(document.activeElement!, { key: 'End' });
    expect(live()).toBe('refused: patterns, tag t007, status 402, refused, 0 ms, no cost.');
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(document.activeElement).toBe(bodyRows()[0]);
  });

  it('marks every outcome by a symbol and a word', () => {
    expect(outcomeMark('cached')).toEqual({ icon: '✓', word: 'cached', tone: 'ok' });
    expect(outcomeMark('ok')).toEqual({ icon: '✓', word: 'ok', tone: 'ok' });
    expect(outcomeMark('refused')).toEqual({ icon: '✗', word: 'refused', tone: 'fail' });
    expect(outcomeMark('failed')).toEqual({ icon: '✗', word: 'failed', tone: 'fail' });
  });
});

// ── The modules ─────────────────────────────────────────────────────────────
const AIDS = ['RouteTable', 'RetryTimeline', 'CacheReport', 'LedgerView', 'TraceExplorer'];
const comp = (f: string) => readFileSync(join(here, '../src/components/infra', f), 'utf8');

describe('infra aid modules', () => {
  it('islands import only the browser-safe barrel, never the build-time loader, and each has a status live region', () => {
    for (const a of AIDS) {
      const src = comp(`${a}.tsx`);
      expect(src, a).not.toMatch(/infraData/);
      expect(src, a).toMatch(/from '\.\.\/\.\.\/lib\/infraAids'/);
      expect(src, a).toMatch(/role="status" aria-live="polite"/);
      expect(src.split('\n').length, a).toBeLessThan(250);
    }
  });

  it('wrappers read the data at build time and hand the island its props', () => {
    for (const a of AIDS) {
      const src = comp(`${a}.astro`);
      expect(src, a).toMatch(/loadInfraData\(\)/);
      expect(src, a).toMatch(/client:visible/);
    }
  });

  it('puts every table in its own scroll box, so 375 px never scrolls the page; tokens only', () => {
    for (const a of AIDS) {
      const src = comp(`${a}.tsx`);
      const tables = src.match(/<table\b/g) ?? [];
      const boxes = src.match(/<div class="mc-scroll mc-focus" role="region" aria-label="[^"]+" tabIndex=\{0\}>\s*<table\b/g) ?? [];
      expect(tables.length, a).toBeGreaterThan(0);
      expect(boxes.length, a).toBe(tables.length);
      expect(src, a).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    }
    for (const f of ['infraStyle.ts', 'infraFormat.ts', 'InfraMark.tsx']) {
      const src = comp(f);
      expect(src, f).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(src, f).not.toMatch(/rgb\(|hsl\(/);
    }
    expect(comp('infraStyle.ts')).toMatch(/\.in-table \{ min-width: \d+rem; \}/);
  });
});
