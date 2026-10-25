import { describe, expect, it } from 'vitest';
import { resolveView, viewId } from '../src/lib/router';

const known = new Set(['home', 'harness-loop']);

describe('resolveView', () => {
  it('returns a known lesson id from its hash', () => {
    expect(resolveView('#harness-loop', known)).toBe('harness-loop');
  });

  it.each(['#harness-nope', '#settings', '', '#'])('falls back to home for %j', (hash) => {
    expect(resolveView(hash, known)).toBe('home');
  });
});

describe('viewId', () => {
  it('joins track and slug', () => {
    expect(viewId('harness', 'loop')).toBe('harness-loop');
  });
});
