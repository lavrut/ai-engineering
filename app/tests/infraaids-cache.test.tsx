// @vitest-environment jsdom
// The AI Infrastructure track's cache aid: CacheReport (lesson 4). Every value it shows is pinned here as a literal
// taken from infra/data/derived/capstone.json (.venv-infra/bin/python infra/python/replay.py --start writes it).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadInfraData } from '../src/lib/infraData';
import { cacheReportProps } from '../src/lib/infraAids';
import CacheReport from '../src/components/infra/CacheReport';
import { pct } from '../src/components/infra/infraFormat';

const here = dirname(fileURLToPath(import.meta.url));
const cap = loadInfraData().capstone;
const props = cacheReportProps(cap, { initial: 'all' });
const live = () => screen.getByRole('status').textContent;
const grid = () => screen.getByRole('grid', { name: 'Cache figures' });
const bodyRows = () => within(grid()).getAllByRole('row').slice(1);
const fact = (key: string) => within(screen.getByRole('list', { name: 'Picked row' })).getByText(key).nextSibling!.textContent;

afterEach(() => cleanup());

describe('CacheReport', () => {
  it('opens on all: 957 calls, 20 hits, 2.1% (0.0209), and the prompt-cache tokens', () => {
    render(<CacheReport {...props} />);
    expect(fact('calls')).toBe('957');
    expect(fact('hits')).toBe('✓ hit 20');
    expect(fact('miss or skip')).toBe('○ miss or – skip 937');
    expect(fact('hit rate')).toBe('2.1% (0.0209)');
    expect(fact('prompt-cache read tokens')).toBe('502680');
    expect(fact('prompt-cache write tokens')).toBe('149144');
    expect(screen.getByText(cap.about.cache)).toBeTruthy();
    expect(screen.getByText(/ttl_s 3600; skip_tools true/)).toBeTruthy();
    expect(live()).toBe('all: 957 calls, 20 hits, hit rate 2.1% (0.0209); prompt cache 502680 read and 149144 write tokens.');
  });

  it('lists all, every set and every team, each with its kind and its hits in words', () => {
    render(<CacheReport {...props} />);
    const rows = within(grid()).getAllByRole('row');
    expect(rows[0].textContent).toBe('RowsKindCallsHitsMiss or skipHit ratePrompt-cache readPrompt-cache write');
    expect(rows).toHaveLength(12);
    expect(bodyRows()[0].textContent).toBe('allall957✓ hit 20○ 9372.1% (0.0209)502680149144');
    expect(bodyRows()[6].textContent).toBe('patterns-goset56✓ hit 19○ 3733.9% (0.3393)00');
    expect(bodyRows()[9].textContent).toBe('patternsteam116✓ hit 19○ 9716.4% (0.1638)00');
    for (const r of bodyRows()) expect(within(r).getAllByRole('gridcell')[2].textContent).toMatch(/^✓ hit \d+$/);
  });

  it('picks a row, the one interaction, and shows its figures', () => {
    render(<CacheReport {...props} />);
    const select = screen.getByRole('combobox', { name: 'Rows' }) as HTMLSelectElement;
    expect([...select.querySelectorAll('optgroup')].map((g) => g.label)).toEqual(['Sets', 'Teams']);
    expect(select.options).toHaveLength(11);
    fireEvent.change(select, { target: { value: 'patterns-go' } });
    expect(fact('calls')).toBe('56');
    expect(fact('hits')).toBe('✓ hit 19');
    expect(fact('hit rate')).toBe('33.9% (0.3393)');
    expect(bodyRows()[6].getAttribute('aria-selected')).toBe('true');
    expect(live()).toBe('patterns-go, set: 56 calls, 19 hits, hit rate 33.9% (0.3393); prompt cache 0 read and 0 write tokens.');
    fireEvent.change(select, { target: { value: 'team:desk' } });
    expect(fact('calls')).toBe('740');
    expect(fact('hits')).toBe('✓ hit 1');
  });

  it('moves the focused row with the arrow keys, picks it and announces it', () => {
    render(<CacheReport {...props} />);
    expect(bodyRows()[0].getAttribute('tabindex')).toBe('0');
    fireEvent.keyDown(bodyRows()[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(bodyRows()[1]);
    expect(live()).toBe('desk-hand, set: 183 calls, 0 hits, hit rate 0.0% (0); prompt cache 0 read and 0 write tokens.');
    expect((screen.getByRole('combobox', { name: 'Rows' }) as HTMLSelectElement).value).toBe('desk-hand');
    fireEvent.keyDown(document.activeElement!, { key: 'End' });
    expect(document.activeElement).toBe(bodyRows()[10]);
    expect(live()).toMatch(/^judge, team: 101 calls, 0 hits/);
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(fact('calls')).toBe('957');
  });

  it('shows a hit rate as a percentage with one decimal', () => {
    expect(pct(0.0209)).toBe('2.1%');
    expect(pct(0.3393)).toBe('33.9%');
    expect(pct(0)).toBe('0.0%');
  });

  it('has no hex colour in its source', () => {
    const src = readFileSync(join(here, '../src/components/infra/CacheReport.tsx'), 'utf8');
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
