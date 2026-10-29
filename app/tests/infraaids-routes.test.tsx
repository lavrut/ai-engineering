// @vitest-environment jsdom
// The AI Infrastructure track's routing aid: RouteTable (lesson 2). Every value it shows is pinned here as a literal
// taken from infra/data/derived/capstone.json's config (.venv-infra/bin/python infra/python/replay.py --start writes
// it from infra/gateway.yaml).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadInfraData } from '../src/lib/infraData';
import { REPLAY_KEY_NOTE, routeTableProps } from '../src/lib/infraAids';
import RouteTable from '../src/components/infra/RouteTable';
import { routeText } from '../src/components/infra/infraFormat';

const here = dirname(fileURLToPath(import.meta.url));
const props = routeTableProps(loadInfraData().capstone.config);
const live = () => screen.getByRole('status').textContent;
const grid = () => screen.getByRole('grid', { name: 'Routes' });
const bodyRows = () => within(grid()).getAllByRole('row').slice(1);

afterEach(() => cleanup());

describe('RouteTable', () => {
  it('renders one row per route and the * row, with the upstream and the replay-key note', () => {
    render(<RouteTable {...props} />);
    const rows = within(grid()).getAllByRole('row');
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toBe('Model in the requestRuleToFallbackrpmtpm');
    expect(rows[1].textContent).toBe('claude-haiku-5-5→ routeclaude-haiku-5-5none60200000');
    expect(rows[2].textContent).toBe('*✗ denynonenonenonenone');
    expect(screen.getByText(REPLAY_KEY_NOTE)).toBeTruthy();
    expect(screen.getByText(/http:\/\/127\.0\.0\.1:8791/)).toBeTruthy();
    expect(live()).toBe('claude-haiku-5-5: route to claude-haiku-5-5, fallback none, 60 rpm, 200000 tpm.');
  });

  it('says every rule in a word beside its symbol', () => {
    render(<RouteTable {...props} />);
    const words = bodyRows().map((r) => within(r).getAllByRole('gridcell')[0].textContent);
    expect(words).toEqual(['→ route', '✗ deny']);
  });

  it('picks a model, the one interaction, and shows the row it meets', () => {
    render(<RouteTable {...props} />);
    const select = screen.getByRole('combobox', { name: 'Model in the request' }) as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(['claude-haiku-5-5', 'another model']);
    const pick = () => screen.getByTestId('route-pick').textContent;
    expect(pick()).toBe('A request for claude-haiku-5-5 meets the claude-haiku-5-5 row: → route to claude-haiku-5-5, fallback none, 60 rpm, 200000 tpm.');
    expect(bodyRows()[0].getAttribute('aria-selected')).toBe('true');
    fireEvent.change(select, { target: { value: '*' } });
    expect(pick()).toBe('A request for another model meets the * row: ✗ deny, refused at the route stage before any upstream call.');
    expect(bodyRows()[1].getAttribute('aria-selected')).toBe('true');
    expect(bodyRows()[0].getAttribute('aria-selected')).toBe('false');
    expect(live()).toBe('*: deny, every model the routes do not name is refused.');
  });

  it('moves the focused row with the arrow keys and announces it', () => {
    render(<RouteTable {...props} />);
    expect(bodyRows()[0].getAttribute('tabindex')).toBe('0');
    expect(bodyRows()[1].getAttribute('tabindex')).toBe('-1');
    fireEvent.keyDown(bodyRows()[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(bodyRows()[1]);
    expect(live()).toBe('*: deny, every model the routes do not name is refused.');
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(bodyRows()[1]);
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(document.activeElement).toBe(bodyRows()[0]);
    expect(live()).toBe(routeText(props.rows[0]));
    fireEvent.keyDown(document.activeElement!, { key: 'End' });
    expect(document.activeElement).toBe(bodyRows()[1]);
  });

  it('without deny_unknown, the * row is a route that sends the model as itself', () => {
    const q = routeTableProps({ ...loadInfraData().capstone.config, deny_unknown: false });
    expect(routeText(q.rows[1])).toBe('*: route, every other model is sent upstream as itself.');
  });

  it('has no hex colour in its source', () => {
    const src = readFileSync(join(here, '../src/components/infra/RouteTable.tsx'), 'utf8');
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
