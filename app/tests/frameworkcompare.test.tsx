// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { allMatch, compareRows, runOptions, type InspectSummary } from '../src/lib/frameworkCompare';
import FrameworkCompare from '../src/components/llms/FrameworkCompare';

const here = dirname(fileURLToPath(import.meta.url));
const real = (): InspectSummary =>
  JSON.parse(readFileSync(join(here, '../../llms/data/inspect_summary.json'), 'utf8'));

afterEach(cleanup);

describe('frameworkCompare', () => {
  it('formats v1_basic rows from the real summary', () => {
    const rows = compareRows(real(), 'v1_basic');
    expect(rows).toEqual([
      { metric: 'category', lab: '90.0%', inspect: '90.0%', stderr: '± 3.9 pts', n: 60, match: true, mark: '✓ match' },
      { metric: 'priority', lab: '73.3%', inspect: '73.3%', stderr: '± 5.8 pts', n: 60, match: true, mark: '✓ match' },
    ]);
  });

  it('formats the judge rows by rubric id', () => {
    const rows = compareRows(real(), 'judge');
    expect(rows.map((r) => [r.metric, r.lab, r.n])).toEqual([
      ['addresses_issue', '70.0%', 20],
      ['tone', '95.0%', 20],
      ['no_false_promises', '95.0%', 20],
    ]);
  });

  it('returns no rows for an unknown run', () => {
    expect(compareRows(real(), 'nope')).toEqual([]);
  });

  it('lists the four runs', () => {
    expect(runOptions(real()).map((o) => o.id)).toEqual(['v1_basic', 'v2_rules', 'v3_examples', 'judge']);
  });

  it('allMatch is true for the real file and false after one number changes', () => {
    expect(allMatch(real())).toBe(true);
    const s = real();
    s.runs[0].rows[0].inspect = 0.85;
    expect(allMatch(s)).toBe(false);
    const rows = compareRows(s, 'v1_basic');
    expect(rows[0]).toMatchObject({ inspect: '85.0%', match: false, mark: '✗ differs' });
    expect(rows[1].mark).toBe('✓ match');
  });
});

describe('FrameworkCompare', () => {
  it('renders the radio group, table and both lists', () => {
    render(<FrameworkCompare summary={real()} />);
    const group = screen.getByRole('radiogroup', { name: /run/i });
    expect(within(group).getAllByRole('radio')).toHaveLength(4);
    expect(screen.getByRole('table', { name: 'Lab vs Inspect' })).toBeTruthy();
    expect(screen.getByText('90.0%', { selector: 'td.fc-lab' })).toBeTruthy();
    expect(screen.getAllByText('✓ match').length).toBe(2);
    expect(screen.getByRole('list', { name: 'Inspect gives you' }).querySelectorAll('li')).toHaveLength(4);
    expect(screen.getByRole('list', { name: 'You still own' }).querySelectorAll('li')).toHaveLength(4);
  });

  it('switching run updates the table and the live region', () => {
    render(<FrameworkCompare summary={real()} />);
    fireEvent.click(screen.getByRole('radio', { name: /judge/ }));
    expect(screen.getByText('addresses_issue')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toMatch(/judge/);
    expect(screen.getByRole('status').textContent).toMatch(/3 of 3/);
  });

  it('shows the empty state with the summarize command when there is no summary', () => {
    render(<FrameworkCompare summary={null} />);
    expect(screen.getByText('No recorded run yet')).toBeTruthy();
    expect(screen.getByText('python llms/inspect/summarize.py')).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });
});
