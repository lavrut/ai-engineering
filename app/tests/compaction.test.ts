import { describe, expect, it } from 'vitest';
import { BUDGET, SUMMARY_TOKENS, addTurn, initialMeter, total, type MeterState } from '../src/lib/compaction';

describe('compaction math', () => {
  it('starts at system + tool schema tokens', () => {
    expect(total(initialMeter())).toBe(1_900);
  });

  it('five file turns stay under budget', () => {
    let s = initialMeter();
    for (let i = 0; i < 5; i++) {
      const r = addTurn(s, 'file');
      expect(r.compacted).toBe(false);
      s = r.state;
    }
    expect(total(s)).toBe(126_900);
    expect(total(s)).toBeLessThan(BUDGET);
  });

  it('the sixth file turn compacts to a summary', () => {
    let s = initialMeter();
    for (let i = 0; i < 5; i++) s = addTurn(s, 'file').state;
    const r = addTurn(s, 'file');
    expect(r.compacted).toBe(true);
    expect(r.state.history).toEqual([]);
    expect(r.state.summary).toBe(SUMMARY_TOKENS);
    expect(r.state.compactions).toBe(1);
    expect(total(r.state)).toBe(4_900);
  });

  it('does not mutate its input', () => {
    const s: MeterState = initialMeter();
    const snapshot = JSON.stringify(s);
    addTurn(s, 'tools');
    expect(JSON.stringify(s)).toBe(snapshot);
  });
});
