// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/preact';
import ContextMeter from '../src/components/ContextMeter';

afterEach(cleanup);

const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));

describe('ContextMeter', () => {
  it('starts empty, labelled as estimates, with the window note', () => {
    const { container } = render(<ContextMeter />);
    expect(screen.getByText('Illustrative token estimates')).toBeTruthy();
    expect(screen.getByText('Compactions: 0')).toBeTruthy();
    expect(container.textContent).toContain('1,000,000');
    expect(container.querySelector('.cm-explain')).toBeNull();
  });

  it('six large file reads compact once and explain why history is never edited', () => {
    const { container } = render(<ContextMeter />);
    for (let i = 0; i < 6; i++) click('Add large file read');
    expect(screen.getByText('Compactions: 1')).toBeTruthy();
    const msg = container.querySelector('.cm-explain')?.textContent ?? '';
    expect(msg).toContain('summary');
    expect(msg).toContain('append-only');
  });

  it('short replies grow history without compacting; Reset clears everything', () => {
    render(<ContextMeter />);
    click('Add short reply');
    expect(screen.getByText('Compactions: 0')).toBeTruthy();
    click('Reset');
    expect(screen.getByText('Compactions: 0')).toBeTruthy();
  });

  it('Auto-run 20 turns adds twenty turns', () => {
    const { container } = render(<ContextMeter />);
    click('Auto-run 20 turns');
    expect(container.querySelector('.cm-turns')?.textContent).toContain('Turns since last compaction');
    expect(screen.getByText(/Compactions: [1-9]/)).toBeTruthy();
  });
});
