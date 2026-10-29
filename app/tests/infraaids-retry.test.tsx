// @vitest-environment jsdom
// The AI Infrastructure track's retry aid: RetryTimeline (lesson 3). Every value it shows is pinned here as a literal
// taken from infra/data/derived/capstone.json's injector (.venv-infra/bin/python infra/python/replay.py --start runs
// each script in infra/data/fixtures/injector through the gateway with retry.base_ms 1).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadInfraData } from '../src/lib/infraData';
import { retryTimelineProps } from '../src/lib/infraAids';
import RetryTimeline from '../src/components/infra/RetryTimeline';
import { statusMark } from '../src/components/infra/infraFormat';

const here = dirname(fileURLToPath(import.meta.url));
const cap = loadInfraData().capstone;
const props = retryTimelineProps(cap.injector, { initial: 'flaky', about: cap.about.injector });
const live = () => screen.getByRole('status').textContent;
const grid = () => screen.getByRole('grid', { name: /^Attempts/ });
const bodyRows = () => within(grid()).getAllByRole('row').slice(1);
const answers = () => bodyRows().map((r) => within(r).getAllByRole('gridcell')[1].textContent);

afterEach(() => cleanup());

describe('RetryTimeline', () => {
  it('renders the flaky script: three attempts answered 429, 500, 200, the outcome, the decisions and the about text', () => {
    render(<RetryTimeline {...props} />);
    expect(screen.getByRole('grid', { name: 'Attempts, flaky' })).toBeTruthy();
    expect(within(grid()).getAllByRole('row')[0].textContent).toBe('AttemptModelAnswerBackoff before the next');
    expect(bodyRows().map((r) => r.textContent)).toEqual([
      '1claude-haiku-5-5✗ 429 rate limited1 ms',
      '2claude-haiku-5-5✗ 500 server error2 ms',
      '3claude-haiku-5-5✓ 200 ok0 ms',
    ]);
    expect(screen.getByText('Scripted answers, in order (starting again from the top when it runs out): 429, 500, 200')).toBeTruthy();
    expect(screen.getByTestId('retry-outcome').textContent).toBe('✓ ok: the caller got 200 after 3 attempts and 3 ms of backoff.');
    const decisions = within(screen.getByRole('list', { name: 'Stage decisions, flaky' })).getAllByRole('listitem');
    expect(decisions.map((d) => d.textContent)).toEqual([
      'tag: • desk', 'budget: ✓ allow', 'cache: ○ miss', 'route: → route', 'limit: ✓ allow', 'retry: ↻ retried', 'ledger: ✓ booked', 'trace: ✓ written',
    ]);
    expect(screen.getByText(cap.about.injector)).toBeTruthy();
    expect(live()).toBe('Attempt 1 of 3: claude-haiku-5-5 answered 429 rate limited, then 1 ms of backoff.');
  });

  it('picks a script, the one interaction, and shows its timeline', () => {
    render(<RetryTimeline {...props} />);
    const buttons = within(screen.getByRole('group', { name: 'Script' })).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual(['flaky', 'dead', 'ratelimited']);
    expect(buttons[0].getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'dead' }));
    expect(screen.getByRole('button', { name: 'dead' }).getAttribute('aria-pressed')).toBe('true');
    expect(answers()).toEqual(['✗ 500 server error', '✗ 500 server error', '✗ 500 server error']);
    expect(screen.getByTestId('retry-outcome').textContent).toBe('✗ failed: the caller got 502 after 3 attempts and 3 ms of backoff.');
    expect(within(screen.getByRole('list', { name: 'Stage decisions, dead' })).getAllByRole('listitem').at(-1)!.textContent).toBe('retry: ✗ fail');
    fireEvent.click(screen.getByRole('button', { name: 'ratelimited' }));
    expect(answers()).toEqual(['✗ 429 rate limited', '✗ 429 rate limited', '✓ 200 ok']);
    expect(live()).toBe('Attempt 1 of 3: claude-haiku-5-5 answered 429 rate limited, then 1 ms of backoff.');
  });

  it('moves the focused attempt with the arrow keys and announces it', () => {
    render(<RetryTimeline {...props} />);
    expect(bodyRows()[0].getAttribute('tabindex')).toBe('0');
    fireEvent.keyDown(bodyRows()[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(bodyRows()[1]);
    expect(bodyRows()[1].getAttribute('aria-selected')).toBe('true');
    expect(live()).toBe('Attempt 2 of 3: claude-haiku-5-5 answered 500 server error, then 2 ms of backoff.');
    fireEvent.keyDown(document.activeElement!, { key: 'End' });
    expect(live()).toBe('Attempt 3 of 3: claude-haiku-5-5 answered 200 ok, the last try.');
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(document.activeElement).toBe(bodyRows()[0]);
  });

  it('says every answer in words, a dropped connection included', () => {
    expect(statusMark(null)).toEqual({ icon: '✗', word: 'dropped', tone: 'fail' });
    expect(statusMark(200).word).toBe('200 ok');
    expect(statusMark(429).word).toBe('429 rate limited');
    expect(statusMark(503).word).toBe('503 server error');
    expect(statusMark(402).word).toBe('402 refused');
  });

  it('has no hex colour in its source', () => {
    const src = readFileSync(join(here, '../src/components/infra/RetryTimeline.tsx'), 'utf8');
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
