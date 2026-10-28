// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/preact';
import ContextMeter from '../src/components/ContextMeter';
import { addTurn, initialMeter, total, type TurnKind } from '../src/lib/compaction';

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
    const kinds: TurnKind[] = Array.from({ length: 20 }, (_, i) => (['short', 'file', 'tools'] as const)[i % 3]);
    const end = kinds.reduce((s, k) => addTurn(s, k).state, initialMeter());
    expect(end.compactions).toBeGreaterThan(0);
    expect(container.querySelector('.cm-turns')?.textContent).toBe(`Turns since last compaction: ${end.history.length}`);
    expect(screen.getByText(`Compactions: ${end.compactions}`)).toBeTruthy();
    expect(container.querySelector('[aria-live="polite"]')?.textContent).toBe(
      `Total ${total(end).toLocaleString('en-US')} tokens; compactions ${end.compactions}; turns since last compaction ${end.history.length}.`,
    );
  });

  it('keeps one live line mounted from the start', () => {
    const { container } = render(<ContextMeter />);
    expect(container.querySelector('[aria-live="polite"]')?.textContent).toBe('Total 1,900 tokens; compactions 0; turns since last compaction 0.');
  });
});
