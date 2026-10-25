// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/preact';
import LoopSimulator from '../src/components/LoopSimulator';

afterEach(cleanup);

function setup(scenario: 'parallel-read' | 'approve-write' | 'recover-error' = 'approve-write', showPolicy = true) {
  const view = render(<LoopSimulator scenario={scenario} showPolicy={showPolicy} />);
  const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
  const json = () => view.container.querySelector('.ls-json pre')?.textContent ?? '';
  const messageCount = () => (JSON.parse(json()) as unknown[]).length;
  const card = () => view.container.querySelector('.ls-approval');
  const strip = () => view.container.querySelector('.ls-state')?.textContent ?? '';
  return { ...view, click, json, messageCount, card, strip };
}

describe('LoopSimulator', () => {
  it('starts with the user task as the only message and shows the simulated label', () => {
    const { messageCount, card, container } = setup();
    expect(messageCount()).toBe(1);
    expect(card()).toBeNull();
    expect(screen.getByText('Simulated — no real model is called')).toBeTruthy();
    expect(container.querySelector('.ls-conversation')?.textContent).toContain('Fix the spelling mistake in README.md.');
  });

  it('approve-write with ask policy: Run to end stops at the approval card', () => {
    const { click, card } = setup();
    click('Run to end');
    expect(card()).not.toBeNull();
    expect(card()!.textContent).toContain('write_file');
    expect(card()!.textContent).toContain('README.md');
    expect(screen.getByRole('button', { name: 'Approve' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Deny' })).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Step' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('Deny then Run to end: the denial result and end_turn appear', () => {
    const { click, card, json, strip, container } = setup();
    click('Run to end');
    click('Deny');
    click('Run to end');
    expect(card()).toBeNull();
    expect(json()).toContain('The user denied this action.');
    expect(container.querySelector('.ls-conversation')?.textContent).toContain('The user denied this action.');
    expect(strip()).toContain('end_turn');
  });

  it('Approve then Run to end finishes with the fix', () => {
    const { click, card, strip, container } = setup();
    click('Run to end');
    click('Approve');
    click('Run to end');
    expect(card()).toBeNull();
    expect(container.querySelector('.ls-conversation')?.textContent).toContain('Wrote ');
    expect(strip()).toContain('end_turn');
  });

  it('Reset returns to one message and no card', () => {
    const { click, card, messageCount } = setup();
    click('Run to end');
    expect(card()).not.toBeNull();
    click('Reset');
    expect(messageCount()).toBe(1);
    expect(card()).toBeNull();
  });

  it('Step adds one event at a time and updates the state strip', () => {
    const { click, messageCount, strip } = setup();
    click('Step');
    expect(messageCount()).toBe(2);
    expect(strip()).toContain('tool_use');
    click('Step');
    expect(messageCount()).toBe(3);
  });

  it('Allow everything runs the write without asking', () => {
    const { click, card, strip } = setup();
    fireEvent.click(screen.getByRole('radio', { name: 'Allow everything' }));
    click('Run to end');
    expect(card()).toBeNull();
    expect(strip()).toContain('end_turn');
  });

  it('hides the policy switch without showPolicy', () => {
    setup('approve-write', false);
    expect(screen.queryByRole('radio', { name: 'Allow everything' })).toBeNull();
  });

  it('marks error results in recover-error', () => {
    const { click, container } = setup('recover-error');
    click('Step');
    click('Step');
    const err = container.querySelector('.ls-result.is-error');
    expect(err?.textContent).toContain('FileNotFoundError: config.yaml');
    expect(err?.textContent).toContain('error');
  });

  it('the scenario picker switches scenario and resets', () => {
    const { container, messageCount } = setup();
    fireEvent.change(screen.getByLabelText('Scenario'), { target: { value: 'parallel-read' } });
    expect(messageCount()).toBe(1);
    expect(container.querySelector('.ls-conversation')?.textContent).toContain('Summarize notes.md and todo.md.');
  });
});
