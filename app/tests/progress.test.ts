import { describe, expect, it, vi } from 'vitest';
import { createProgressStore } from '../src/lib/progress';

function fakeStorage(initial?: string) {
  const data = new Map<string, string>();
  if (initial !== undefined) data.set('aie:progress:v1', initial);
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
  };
}

describe('progress store', () => {
  it('round-trips done state through storage', () => {
    const s = fakeStorage();
    createProgressStore(s).markDone('a');
    const again = createProgressStore(s);
    expect(again.isDone('a')).toBe(true);
    expect(again.isDone('b')).toBe(false);
    expect(JSON.parse(s.data.get('aie:progress:v1')!)).toEqual({ done: ['a'], scores: {} });
  });

  it('keeps the best score', () => {
    const p = createProgressStore(fakeStorage());
    expect(p.bestScore('q')).toBeNull();
    p.recordScore('q', 1, 2);
    p.recordScore('q', 2, 2);
    p.recordScore('q', 1, 2);
    expect(p.bestScore('q')).toBe(1);
  });

  it('a full score marks done; a partial one does not', () => {
    const p = createProgressStore(fakeStorage());
    p.recordScore('q', 1, 2);
    expect(p.isDone('q')).toBe(false);
    p.recordScore('q', 2, 2);
    expect(p.isDone('q')).toBe(true);
  });

  it('computes rounded percent', () => {
    const p = createProgressStore(fakeStorage());
    p.markDone('a');
    expect(p.percent(['a', 'b', 'c'])).toBe(33);
    expect(p.percent([])).toBe(0);
  });

  it('survives corrupt JSON', () => {
    const p = createProgressStore(fakeStorage('{not json'));
    expect(p.isDone('a')).toBe(false);
    expect(p.bestScore('a')).toBeNull();
    p.markDone('a');
    expect(p.isDone('a')).toBe(true);
  });

  it('works in memory when storage throws', () => {
    const bad = {
      getItem: () => {
        throw new Error('no');
      },
      setItem: () => {
        throw new Error('no');
      },
    };
    const p = createProgressStore(bad);
    expect(() => p.markDone('a')).not.toThrow();
    expect(p.isDone('a')).toBe(true);
  });

  it('works with null storage', () => {
    const p = createProgressStore(null);
    p.markDone('a');
    expect(p.isDone('a')).toBe(true);
  });

  it('notifies subscribers and supports unsubscribe', () => {
    const p = createProgressStore(fakeStorage());
    const fn = vi.fn();
    const off = p.subscribe(fn);
    p.markDone('a');
    expect(fn).toHaveBeenCalledTimes(1);
    off();
    p.markDone('b');
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
