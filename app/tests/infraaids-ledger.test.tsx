// @vitest-environment jsdom
// The AI Infrastructure track's spend aid: LedgerView (lesson 5). Every value it shows is pinned here as a literal
// taken from infra/data/derived/ledger.json and capstone.json (.venv-infra/bin/python infra/python/replay.py --start
// writes both; the budget lines are gateway.yaml section 5).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadInfraData } from '../src/lib/infraData';
import { ledgerViewProps } from '../src/lib/infraAids';
import LedgerView from '../src/components/infra/LedgerView';
import { stateMark, usd } from '../src/components/infra/infraFormat';

const here = dirname(fileURLToPath(import.meta.url));
const data = loadInfraData();
const props = ledgerViewProps(data.ledger, data.capstone.config.budgets, { sets: data.capstone.sets, initial: 'all' });
const live = () => screen.getByRole('status').textContent;
const grid = () => screen.getByRole('grid', { name: /^Spend by team/ });
const bodyRows = () => within(grid()).getAllByRole('row').slice(1);

afterEach(() => cleanup());

describe('LedgerView', () => {
  it('renders spend per team against its lines, each state a word beside its symbol', () => {
    render(<LedgerView {...props} />);
    const rows = within(grid()).getAllByRole('row');
    expect(rows[0].textContent).toBe('TeamRequestsSpendLimitWarn lineStateCachedRefused');
    expect(bodyRows().map((r) => r.textContent)).toEqual([
      'desk740$0.1856$1.00$0.80✓ under10',
      'patterns116$0.0233$0.50none✓ under190',
      'judge101$0.0255$0.50none✓ under00',
    ]);
    expect(live()).toBe('desk: $0.1856 of $1.00, warn line $0.80: under. 740 requests, 1 cached, 0 refused.');
  });

  it('names the idle team, the top tags and the agents ledger beside the gateway', () => {
    render(<LedgerView {...props} />);
    expect(screen.getByText('lab has a budget and sent no traffic in this run.')).toBeTruthy();
    const tags = within(screen.getByRole('table', { name: 'Top tags by spend' })).getAllByRole('row').slice(1);
    expect(tags).toHaveLength(10);
    expect(tags[0].textContent).toBe('none$0.0286');
    expect(tags[1].textContent).toBe('t001$0.0257');
    const agents = within(screen.getByRole('table', { name: 'Agents ledger beside the gateway' })).getAllByRole('row');
    expect(agents[0].textContent).toBe('SetAgents ledgerGateway ledgerCache hitsChecked by the gate');
    expect(agents).toHaveLength(7);
    expect(agents[1].textContent).toBe('desk-hand$0.0457416$0.04574160yes, a desk set');
    expect(agents[2].textContent).toBe('desk-hand-go$0.0460793$0.04588741yes, a desk set');
    expect(agents[6].textContent).toBe('patterns-go$0.0121577$0.009784719no');
    expect(screen.getByText(data.ledger.about.agents)).toBeTruthy();
  });

  it('filters by team, the one interaction', () => {
    render(<LedgerView {...props} />);
    const buttons = within(screen.getByRole('group', { name: 'Team' })).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual(['all', 'desk', 'patterns', 'judge']);
    fireEvent.click(screen.getByRole('button', { name: 'patterns' }));
    expect(screen.getByRole('button', { name: 'patterns' }).getAttribute('aria-pressed')).toBe('true');
    expect(bodyRows().map((r) => r.textContent)).toEqual(['patterns116$0.0233$0.50none✓ under190']);
    expect(live()).toBe('patterns: $0.0233 of $0.50, no warn line: under. 116 requests, 19 cached, 0 refused.');
    fireEvent.click(screen.getByRole('button', { name: 'all' }));
    expect(bodyRows()).toHaveLength(3);
  });

  it('moves the focused row with the arrow keys and announces it', () => {
    render(<LedgerView {...props} />);
    expect(bodyRows()[0].getAttribute('tabindex')).toBe('0');
    fireEvent.keyDown(bodyRows()[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(bodyRows()[1]);
    expect(bodyRows()[1].getAttribute('aria-selected')).toBe('true');
    expect(live()).toMatch(/^patterns: /);
    fireEvent.keyDown(document.activeElement!, { key: 'End' });
    expect(live()).toBe('judge: $0.0255 of $0.50, no warn line: under. 101 requests, 0 cached, 0 refused.');
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(document.activeElement).toBe(bodyRows()[0]);
  });

  it('marks every state by a symbol and a word', () => {
    expect(stateMark('under')).toEqual({ icon: '✓', word: 'under', tone: 'ok' });
    expect(stateMark('warn')).toEqual({ icon: '◐', word: 'warn', tone: 'warn' });
    expect(stateMark('over')).toEqual({ icon: '✗', word: 'over', tone: 'fail' });
    expect(usd(0.18562630000000013)).toBe('$0.1856');
    expect(usd(1, 2)).toBe('$1.00');
  });

  it('has no hex colour in its source', () => {
    const src = readFileSync(join(here, '../src/components/infra/LedgerView.tsx'), 'utf8');
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
