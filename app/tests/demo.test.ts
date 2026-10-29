// The demo video's pure parts: the nine beats (captions and actions), the viewport constants and the
// SRT formatter. The recorder and encoder are exercised by running them (see docs/superpowers/specs/
// 2026-10-29-demo-video-design.md §6); this file pins what can be checked without a browser.
import { describe, expect, it } from 'vitest';
import { BEATS, PHONE, TEST_COUNT_NOTE, VIEWPORT, plannedMs } from '../scripts/demo/beats.mjs';
import { formatTime, timingsToCues, toSrt } from '../scripts/demo/srt.mjs';
import { CAPTION_CSS } from '../scripts/demo/caption.mjs';
import { CURSOR_CSS, CURSOR_MS, OVERLAY_CSS, TRANSITION_MS } from '../scripts/demo/overlay.mjs';

const KINDS = new Set(['goto', 'scrollTo', 'click', 'clickUntilDisabled', 'select', 'wait', 'resize', 'caption', 'phoneFrame']);
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

  it('closes with a figure note and shows the phone beat in a phone-sized frame', () => {
    expect(TEST_COUNT_NOTE).toContain('2026-10-29');
    expect(VIEWPORT).toEqual({ width: 1920, height: 1080 });
    expect(PHONE.width).toBeLessThan(VIEWPORT.width);
    const phone = BEATS.find((b) => b.id === 'phone')!;
    const frames = actionsOf(phone).filter((a) => a.kind === 'phoneFrame');
    expect(frames.length).toBe(1);
    expect(frames[0]).toMatchObject(PHONE);
    expect(frames[0].ms).toBeGreaterThanOrEqual(5000);
    expect(actionsOf(phone).some((a) => a.kind === 'resize')).toBe(false);
  });

  it('plans enough fixed waiting to clear the 140 s minimum with a margin', () => {
    // The recording's length is the fixed waits plus the replay beat's stepping; the fixed part alone must
    // leave a margin above the encoder's 140 s floor so a faster machine cannot fail the encode.
    // Fixed waits of about 82 s plus roughly 65 s of cursor glides, presses, transitions, eased scrolls and the
    // replay beat's stepping land near 150 s, inside 140–170.
    expect(plannedMs(BEATS)).toBeGreaterThanOrEqual(80000);
    expect(plannedMs(BEATS)).toBeLessThanOrEqual(92000);
  });

  it('counts the tracks the way the home page does', () => {
    for (const c of BEATS.flatMap((b) => b.captions)) expect(c, c).not.toMatch(/Nine tracks/);
    expect(BEATS[0].captions[1]).toMatch(/^Eight tracks/);
    expect(BEATS[BEATS.length - 1].captions[0]).toMatch(/^Eight tracks/);
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

describe('the encoder checks', () => {
  it('picks one still time per beat, at the middle of the beat', async () => {
    const { stillTimes } = await import('../scripts/demo/encode.mjs');
    const timings = [
      { beat: 'home', caption: 'a', start: 0, end: 4000 },
      { beat: 'home', caption: 'b', start: 4000, end: 10000 },
      { beat: 'anatomy', caption: 'c', start: 10000, end: 30000 },
    ];
    expect(stillTimes(timings)).toEqual([{ beat: 'home', seconds: 5 }, { beat: 'anatomy', seconds: 20 }]);
  });

  it('lists every violated bound of a probe, and none for a good one', async () => {
    const { ffprobeOk } = await import('../scripts/demo/encode.mjs');
    const good = { streams: [{ codec_name: 'h264', width: 1920, height: 1080, r_frame_rate: '30/1', pix_fmt: 'yuv420p' }], format: { duration: '150.2', size: '52000000' } };
    expect(ffprobeOk(good)).toEqual([]);
    const bad = { streams: [{ codec_name: 'vp9', width: 1280, height: 720, r_frame_rate: '25/1', pix_fmt: 'yuv444p' }], format: { duration: '99.0', size: '250000000' } };
    expect(ffprobeOk(bad)).toEqual([
      'codec vp9 (want h264)', 'size 1280x720 (want 1920x1080)', 'frame rate 25/1 (want 30/1)',
      'pixel format yuv444p (want yuv420p)', 'duration 99.0 s (want 140-170)', 'file 250000000 bytes (want under 200 MB)',
    ]);
    expect(ffprobeOk({ streams: [], format: {} })).toEqual(['no video stream']);
  });
});

describe('the caption strip', () => {
  it('is a fixed strip that keeps caption line breaks', () => {
    expect(CAPTION_CSS).toContain('#demo-caption');
    expect(CAPTION_CSS).toContain('position: fixed');
    expect(CAPTION_CSS).toContain('white-space: pre-line');
  });

  it('is centred by its margins so it can grow to 72% of the width', () => {
    expect(CAPTION_CSS).toContain('width: fit-content');
    expect(CAPTION_CSS).toContain('max-width: 72%');
    expect(CAPTION_CSS).toContain('margin-inline: auto');
    expect(CAPTION_CSS).not.toContain('left: 50%');
  });
});

describe('the cursor and the view transitions', () => {
  it('draws a fixed cursor that glides and presses, and an overlay that fades', () => {
    expect(CURSOR_CSS).toContain('#demo-cursor');
    expect(CURSOR_CSS).toContain('pointer-events: none');
    expect(CURSOR_CSS).toContain(`transform ${CURSOR_MS}ms`);
    expect(CURSOR_CSS).toContain('#demo-cursor.is-press');
    expect(OVERLAY_CSS).toContain('#demo-overlay');
    expect(OVERLAY_CSS).toContain(`opacity ${TRANSITION_MS}ms`);
    expect(CURSOR_MS).toBeGreaterThanOrEqual(400);
    expect(TRANSITION_MS).toBeGreaterThanOrEqual(250);
  });

  it('fades between views only when the view changes', async () => {
    const { needsTransition } = await import('../scripts/demo/record.mjs');
    expect(needsTransition('#home', '#home')).toBe(false);
    expect(needsTransition('', '#home')).toBe(false);
    expect(needsTransition('#home', '#ontology-what')).toBe(true);
    expect(needsTransition('#infra-capstone', '#home')).toBe(true);
  });

  it('records in the light theme unless told otherwise', async () => {
    const { themeOf } = await import('../scripts/demo/record.mjs');
    expect(themeOf([])).toBe('light');
    expect(themeOf(['--theme', 'dark'])).toBe('dark');
    expect(() => themeOf(['--theme', 'sepia'])).toThrow('light or dark');
  });
});

describe('the demo command', () => {
  it('runs record then encode, and only record for a dry run, with the same arguments', async () => {
    const { plan } = await import('../scripts/demo/run.mjs');
    expect(plan(['--out', 'x'])).toEqual([['record', ['--out', 'x']], ['encode', ['--out', 'x']]]);
    expect(plan(['--dry'])).toEqual([['record', ['--dry']]]);
  });

  it('is what npm run demo calls', async () => {
    const pkg = JSON.parse((await import('node:fs')).readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
    expect(pkg.scripts.demo).toBe('node scripts/demo/run.mjs');
  });
});
