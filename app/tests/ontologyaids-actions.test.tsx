// @vitest-environment jsdom
// The Ontology track's action aid: ActionMatrix (lesson 4). Every number it shows is pinned here as a literal taken
// from ontology/data/derived/governance.json (.venv-ontology/bin/python ontology/python/governance.py writes it from
// ontology/lumen.yaml section 6 and the agents traces).
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadOntologyData } from '../src/lib/ontologyData';
import {
  ALL_ENGINES,
  actionMatrixProps,
  cellText,
  countsText,
  governanceGateMark,
  gridMove,
  matrixLine,
  ruleMark,
  type GovernanceJson,
} from '../src/lib/ontologyAids';
import ActionMatrix from '../src/components/ontology/ActionMatrix';

const data = loadOntologyData();
const props = actionMatrixProps(data.governance);
const live = () => document.querySelector('[aria-live="polite"]')?.textContent;
const cell = (role: string, action: string) => props.cells.find((c) => c.role === role && c.action === action)!;

afterEach(() => cleanup());

describe('action matrix data', () => {
  it('is every role by every action, in the ontology order, with the engines the traces hold', () => {
    expect(props.roles).toEqual(['single', 'triage', 'research', 'drafter', 'reviewer', 'supervisor', 'human']);
    expect(props.actions).toEqual(['read', 'write', 'approve', 'escalate']);
    expect(props.cells).toHaveLength(28);
    expect(props.engines).toEqual(['hand', 'hand-go', 'langgraph', 'sdk']);
    expect(props.tools).toEqual([
      ['classify_complaint', 'read'], ['confirmation', 'approve'], ['get_ticket', 'read'], ['handoff', 'escalate'], ['label_ticket', 'write'], ['search_tickets', 'read'],
    ]);
    expect(props.conditions.map((c) => c.id)).toEqual(['human_confirmed', 'review_passed']);
    expect(props.gate).toEqual({ ok: true, reason: 'green: 0 not permitted; 60 conditional, 561 permitted, of 621 events' });
  });

  it('carries the rule, the conditions and the counts per engine', () => {
    expect(cell('supervisor', 'write')).toMatchObject({ rule: 'conditional', conditions: ['review_passed', 'human_confirmed'], unmet: { human_confirmed: 42 } });
    expect(cell('supervisor', 'write').counts[ALL_ENGINES]).toEqual({ events: 42, permitted: 0, conditional: 42, not_permitted: 0 });
    expect(cell('supervisor', 'write').counts.sdk).toEqual({ events: 9, permitted: 0, conditional: 9, not_permitted: 0 });
    expect(cell('research', 'read').counts.hand.events).toBe(39);
    expect(cell('research', 'write').rule).toBe('no');
    expect(cell('drafter', 'read')).toMatchObject({ rule: 'permitted', conditions: [] });
    expect(cell('drafter', 'read').counts[ALL_ENGINES].events).toBe(0);
    expect(props.totals[ALL_ENGINES]).toEqual({ events: 621, permitted: 561, conditional: 60, not_permitted: 0 });
    expect(props.totals.sdk.events).toBe(150);
  });

  it('fails the build on a matrix with a missing cell', () => {
    const broken: GovernanceJson = { ...data.governance, matrix: data.governance.matrix.slice(1) };
    expect(() => actionMatrixProps(broken)).toThrow(/ActionMatrix: governance.json has no cell for single x read/);
  });

  it('says each cell and the whole in words', () => {
    expect(countsText(cell('single', 'read').counts[ALL_ENGINES])).toBe('76 events: 76 permitted');
    expect(countsText(cell('triage', 'read').counts[ALL_ENGINES])).toBe('0 events');
    expect(cellText(cell('supervisor', 'write'), ALL_ENGINES)).toBe(
      'supervisor write: conditional, if review_passed and human_confirmed. all engines: 42 events: 42 conditional (human_confirmed unmet on 42).',
    );
    expect(cellText(cell('research', 'write'), 'hand')).toBe('research write: no row permits it. hand: 0 events.');
    expect(matrixLine(props, ALL_ENGINES)).toBe('621 events in 104 desk runs, all engines: 561 permitted, 60 conditional, 0 not permitted.');
    expect(matrixLine(props, 'langgraph')).toBe('152 events, langgraph: 139 permitted, 13 conditional, 0 not permitted.');
  });

  it('marks a rule and the gate by a symbol and a word, never colour alone', () => {
    expect(ruleMark('permitted')).toEqual({ icon: '✓', word: 'PERMITTED', tone: 'ok' });
    expect(ruleMark('conditional')).toEqual({ icon: '◐', word: 'CONDITIONAL', tone: 'warn' });
    expect(ruleMark('no')).toEqual({ icon: '✗', word: 'NO', tone: 'fail' });
    expect(governanceGateMark({ ok: false, reason: 'x' }).word).toBe('RED');
  });

  it('moves by arrow keys, clamped at the edges, Home and End along the row', () => {
    expect(gridMove(0, 0, 'ArrowRight', 7, 4)).toEqual([0, 1]);
    expect(gridMove(0, 3, 'ArrowRight', 7, 4)).toEqual([0, 3]);
    expect(gridMove(0, 0, 'ArrowUp', 7, 4)).toEqual([0, 0]);
    expect(gridMove(6, 2, 'ArrowDown', 7, 4)).toEqual([6, 2]);
    expect(gridMove(3, 2, 'Home', 7, 4)).toEqual([3, 0]);
    expect(gridMove(3, 0, 'End', 7, 4)).toEqual([3, 3]);
    expect(gridMove(3, 0, 'Enter', 7, 4)).toBeNull();
  });
});

describe('ActionMatrix', () => {
  it('renders the grid, the gate as written, the conditions and the tool table', () => {
    render(<ActionMatrix {...props} />);
    const grid = screen.getByRole('grid', { name: 'Roles and actions, all engines' });
    const rows = within(grid).getAllByRole('row');
    expect(rows).toHaveLength(8);
    expect(rows[0].textContent).toBe('Rolereadwriteapproveescalate');
    expect(within(grid).getAllByRole('gridcell')).toHaveLength(28);
    expect(screen.getByRole('status').textContent).toContain('✓ GREEN');
    expect(screen.getByRole('status').textContent).toContain('green: 0 not permitted; 60 conditional, 561 permitted, of 621 events');
    expect(screen.getByText('human_confirmed').closest('div')!.textContent).toContain('A person said yes to the write.');
    expect(within(screen.getByRole('table', { name: 'Tools and actions' })).getAllByRole('row')).toHaveLength(7);
    expect(live()).toBe('single read: permitted. all engines: 76 events: 76 permitted.');
  });

  it('says every rule in text, not colour alone', () => {
    render(<ActionMatrix {...props} />);
    const cells = within(screen.getByRole('grid')).getAllByRole('gridcell');
    for (const c of cells) expect(c.textContent).toMatch(/^(✓ PERMITTED|◐ CONDITIONAL|✗ NO)/);
    const words = cells.map((c) => c.textContent!.split(' ')[1].replace(/[^A-Z]/g, ''));
    expect(words.filter((w) => w === 'PERMITTED')).toHaveLength(9);
    expect(words.filter((w) => w === 'CONDITIONAL')).toHaveLength(2);
    expect(words.filter((w) => w === 'NO')).toHaveLength(17);
    expect(cells[1].textContent).toBe('◐ CONDITIONALif human_confirmed18 events: 18 conditional');
  });

  it('filters by engine, the one interaction, and announces the cell under it', () => {
    render(<ActionMatrix {...props} />);
    const buttons = within(screen.getByRole('group', { name: 'Engine' })).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual(['all', 'hand', 'hand-go', 'langgraph', 'sdk']);
    fireEvent.click(screen.getByRole('button', { name: 'sdk' }));
    expect(screen.getByRole('button', { name: 'sdk' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('grid', { name: 'Roles and actions, sdk' })).toBeTruthy();
    expect(screen.getByText('150 events, sdk: 136 permitted, 14 conditional, 0 not permitted.')).toBeTruthy();
    expect(live()).toBe('single read: permitted. sdk: 10 events: 10 permitted.');
    const cells = within(screen.getByRole('grid')).getAllByRole('gridcell');
    expect(cells[5 * 4 + 1].textContent).toBe('◐ CONDITIONALif review_passed and human_confirmed9 events: 9 conditional');
  });

  it('moves the focused cell with the arrow keys and announces it', () => {
    render(<ActionMatrix {...props} />);
    const cells = () => within(screen.getByRole('grid')).getAllByRole('gridcell');
    expect(cells()[0].getAttribute('tabindex')).toBe('0');
    expect(cells()[1].getAttribute('tabindex')).toBe('-1');
    fireEvent.keyDown(cells()[0], { key: 'ArrowRight' });
    expect(document.activeElement).toBe(cells()[1]);
    expect(cells()[1].getAttribute('aria-selected')).toBe('true');
    expect(live()).toBe('single write: conditional, if human_confirmed. all engines: 18 events: 18 conditional (human_confirmed unmet on 18).');
    for (let i = 0; i < 5; i++) fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' });
    expect(live()).toMatch(/^supervisor write: conditional, if review_passed and human_confirmed/);
    fireEvent.keyDown(document.activeElement!, { key: 'End' });
    expect(live()).toBe('supervisor escalate: permitted. all engines: 240 events: 240 permitted.');
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' });
    expect(live()).toBe('human escalate: no row permits it. all engines: 0 events.');
  });
});
