// The demo video's pure parts: the nine beats (captions and actions), the viewport constants and the
// SRT formatter. The recorder and encoder are exercised by running them (see docs/superpowers/specs/
// 2026-10-29-demo-video-design.md §6); this file pins what can be checked without a browser.
import { describe, expect, it } from 'vitest';
import { BEATS, PHONE, TEST_COUNT_NOTE, VIEWPORT } from '../scripts/demo/beats.mjs';
import { formatTime, timingsToCues, toSrt } from '../scripts/demo/srt.mjs';
import { CAPTION_CSS } from '../scripts/demo/caption.mjs';

const KINDS = new Set(['goto', 'scrollTo', 'click', 'clickUntilDisabled', 'select', 'wait', 'resize', 'caption']);
const STEPPING = /\.mc-btn-primary|Next/;
type Action = Record<string, any> & { kind: string };
const actionsOf = (b: { actions: unknown[] }) => b.actions as Action[];

describe('the beats', () => {
  it('has nine beats with short captions and known actions', () => {
    expect(BEATS.map((b) => b.id)).toEqual(['home', 'anatomy', 'replay', 'code', 'aid', 'quiz', 'gate', 'phone', 'close']);
    for (const b of BEATS) {
      expect(b.captions.length, b.id).toBeGreaterThan(0);
      for (const c of b.captions) {
        expect(c.length, `${b.id}: ${c}`).toBeLessThan(90);
        expect(c.split('\n').length, `${b.id}: ${c}`).toBeLessThanOrEqual(2);
      }
      for (const a of actionsOf(b)) expect(KINDS.has(a.kind), `${b.id}: ${a.kind}`).toBe(true);
      for (const a of actionsOf(b)) if (a.kind === 'caption') expect(a.index, `${b.id}: caption index`).toBeLessThan(b.captions.length);
    }
  });

  it('holds every stepping click for at least 1200 ms', () => {
    for (const b of BEATS) {
      actionsOf(b).forEach((a, i) => {
        if (a.kind === 'clickUntilDisabled') expect(a.waitMs, `${b.id}: clickUntilDisabled`).toBeGreaterThanOrEqual(1200);
        if (a.kind === 'click' && STEPPING.test(a.selector)) {
          const next = actionsOf(b)[i + 1];
          expect(next && next.kind === 'wait' && next.ms >= 1200, `${b.id}: click ${a.selector} needs a wait >= 1200`).toBe(true);
        }
      });
    }
  });

  it('closes with a figure note and restores the viewport after the phone beat', () => {
    expect(TEST_COUNT_NOTE).toContain('2026-10-29');
    expect(VIEWPORT).toEqual({ width: 1920, height: 1080 });
    expect(PHONE.width).toBeLessThan(VIEWPORT.width);
    const phone = BEATS.find((b) => b.id === 'phone')!;
    const resizes = actionsOf(phone).filter((a) => a.kind === 'resize');
    expect(resizes.length).toBe(2);
    expect(resizes[0]).toMatchObject(PHONE);
    expect(resizes[1]).toMatchObject(VIEWPORT);
  });
});

describe('the srt formatter', () => {
  it('formats srt times and refuses overlaps', () => {
    expect(formatTime(3723456)).toBe('01:02:03,456');
    expect(formatTime(0)).toBe('00:00:00,000');
    expect(toSrt([{ start: 0, end: 1000, text: 'a' }, { start: 1000, end: 2500, text: 'b\nc' }])).toBe(
      '1\n00:00:00,000 --> 00:00:01,000\na\n\n2\n00:00:01,000 --> 00:00:02,500\nb\nc\n',
    );
    expect(() => toSrt([{ start: 0, end: 1000, text: 'a' }, { start: 900, end: 2000, text: 'b' }])).toThrow('overlapping cues at 2');
  });

  it('turns the recorder timings into abutting cues', () => {
    const timings = [
      { beat: 'home', caption: 'a', start: 0, end: 4000 },
      { beat: 'home', caption: 'b', start: 4000, end: 10000 },
      { beat: 'anatomy', caption: 'c', start: 10000, end: 30000 },
    ];
    expect(timingsToCues(timings)).toEqual([
      { start: 0, end: 4000, text: 'a' },
      { start: 4000, end: 10000, text: 'b' },
      { start: 10000, end: 30000, text: 'c' },
    ]);
    expect(() => toSrt(timingsToCues(timings))).not.toThrow();
  });
});

describe('the recorder', () => {
  it('rejects a beat whose selector is missing, naming the beat and the selector', async () => {
    const { runBeat } = await import('../scripts/demo/record.mjs');
    const page = {
      evaluate: async () => undefined,
      waitForTimeout: async () => undefined,
      locator: () => ({ first: () => ({ waitFor: async () => { throw new Error('timeout'); } }) }),
    };
    const beat = { id: 'bogus', captions: ['x'], actions: [{ kind: 'scrollTo', selector: '#nothing-here', ms: 10 }] };
    await expect(runBeat(page as any, beat as any, { dry: true, t0: Date.now(), timings: [] })).rejects.toThrow('beat bogus: missing #nothing-here');
  });
});

describe('the caption strip', () => {
  it('is a fixed strip that keeps caption line breaks', () => {
    expect(CAPTION_CSS).toContain('#demo-caption');
    expect(CAPTION_CSS).toContain('position: fixed');
    expect(CAPTION_CSS).toContain('white-space: pre-line');
  });
});
