# Captioned Demo Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `npm run demo` in `app/` records the packaged course page through nine scripted beats with captions drawn in the page and produces a 1920×1080 H.264 MP4 plus an `.srt` for a LinkedIn post.

**Architecture:** A Node script serves `app/publish` on the loopback, drives Chromium through Playwright with native video recording, injects a caption strip into the page before each beat, and records caption timings; a second script encodes the WebM with ffmpeg, writes the `.srt` from the timings, verifies the result with ffprobe and extracts one still per beat.

**Tech Stack:** Node 26, `playwright@1.64.1` (dev dependency; its Chromium build 1248 is already cached), ffmpeg 8 / ffprobe on the PATH, Vitest for the pure parts.

**Spec:** `docs/superpowers/specs/2026-10-29-demo-video-design.md`

## Global Constraints

- One new dev dependency only: `playwright@1.64.1` (exact pin; its Chromium is the cached build 1248, so `npm install` fetches the package, never a browser). Nothing else installed; no `npx playwright install`.
- No lesson, component or data file is changed; the only repo changes are `app/scripts/demo/*`, `app/tests/demo.test.ts`, `app/package.json`, `app/package-lock.json`, `.gitignore`.
- Output under `app/demo/` is git-ignored: `raw.webm`, `timings.json`, `ai-engineering-demo.mp4`, `ai-engineering-demo.srt`, `stills/beat-N.png`.
- Video: 1920×1080, H.264 yuv420p, 30 fps, 140–170 s, under 200 MB, fade in 0.5 s and out 1 s, `+faststart`. Captions: at most two lines, under 90 characters, drawn in the page in the site's font and colour tokens; the `.srt` cues come from the recording's clock and never overlap.
- The page is `app/publish` (run `npm run package` first) served on a loopback free port; dark theme via `document.documentElement.setAttribute('data-theme','dark')` and `prefers-color-scheme: dark` emulation.
- No paid calls, no network beyond the one `npm install`; no absolute local paths in committed files; commits with explicit paths, no amend, no attribution lines.

## Review Focus

1. A beat's selector missing because the page changed: expected the dry run and the recording both stop naming the beat and the selector, and nothing is encoded. Test in Task 2.
2. Two captions in one beat with the first still fading when the second shows: expected the `.srt` cues abut without overlap and the page shows one caption at a time. Test in Task 1 (formatter) and Task 2 (caption swap).
3. `app/publish` absent or stale: expected `record.mjs` exits 2 telling the user to run `npm run package`; it must not silently record an old build. Test in Task 2.
4. ffmpeg missing or an encode that silently produces a 0-byte or mis-sized file: expected `encode.mjs` exits 2 with the measured values and leaves the WebM. Test in Task 3.
5. The agents replay stepped too fast to read: expected each step holds at least 1.2 s; the beats file fixes the waits and the test checks every `click` on a stepping control is followed by a `wait` of at least 1200 ms. Test in Task 1.

---

### Task 1: Beats, captions and the SRT formatter (the pure parts)

**Files:**
- Create: `app/scripts/demo/beats.mjs`, `app/scripts/demo/srt.mjs`, `app/tests/demo.test.ts`
- Modify: `app/package.json` (devDependency `"playwright": "1.64.1"`; scripts `"demo": "node scripts/demo/record.mjs && node scripts/demo/encode.mjs"`), `app/package-lock.json` (from `npm install`), `.gitignore` (`app/demo/`)

**Interfaces:**
- Produces `beats.mjs`: `export const BEATS: Beat[]` with `Beat = {id: string, captions: string[], actions: Action[]}` and `Action = {kind: 'goto', hash} | {kind: 'scrollTo', selector, ms} | {kind: 'click', selector} | {kind: 'wait', ms} | {kind: 'resize', width, height} | {kind: 'caption', index}` (the caption action shows `captions[index]`; the first caption shows at the beat's start without an action). `export const TEST_COUNT_NOTE` (a comment-like string naming the 4,000+ figure's source and date, 2026-10-29, main 35ceb8c). `export const VIEWPORT = {width: 1920, height: 1080}`, `export const PHONE = {width: 390, height: 844}`.
- The nine beats and their selectors, decided here (the page's views are `#<track>-<slug>` containers; views switch by `location.hash`):
  1. `home`: goto `#home`; scrollTo `[data-view="home"] .track-list, #home` 6000 ms; captions per spec §3.
  2. `anatomy`: goto `#ontology-what`; scrollTo `#ontology-what svg` 1500; click `#ontology-what button:has-text("Next")` ×3 with wait 2000 between; caption index 1 after the second click.
  3. `replay`: goto `#agents-multi-agent`; scrollTo `#agents-multi-agent section[aria-label="Desk run step by step"]` 1500; click `… .mc-btn-primary` ×8 with wait 1500 between (the spec's "the human's decline" is the run's final step: step to the last step, so `click` until the button is disabled: use `kind: 'clickUntilDisabled', selector, waitMs: 1500, max: 20`, added to `Action`); caption index 1 after the third click.
  4. `code`: goto `#infra-routing`; scrollTo `#infra-routing .codetabs` 1500; click `#infra-routing .codetabs [role="tab"][data-lang="go"]`; wait 2500; scrollTo the same `.codetabs pre` 4000.
  5. `aid`: goto `#infra-capstone`; scrollTo `#infra-capstone select` 1500; the outcome select set to `cached` (`kind: 'select', selector, value`, added to `Action`); wait 1500; click the first row (`#infra-capstone table tbody tr`); wait 4000; scrollTo the opened decisions list 3000.
  6. `quiz`: goto `#ontology-what`; scrollTo `#ontology-what .quiz` 1500; click the third option of the first question (`#ontology-what .quiz-options:first-of-type input, label` nth 3: the implementer reads `Quiz.tsx` for the option markup); click `#ontology-what .quiz .btn-primary`; wait 5000 (the feedback `role="status"` is visible).
  7. `gate`: goto `#infra-capstone`; scrollTo `#infra-capstone :text("ledger")` (the gate lines in the TraceExplorer) 2000; wait 4000; goto `#infra-budgets`; scrollTo `#infra-budgets .codetabs` 3000 (a code block standing in for "a test file": the spec's "test file scrolling briefly" is realised with the lesson's pinned-number code since test files are not on the page; record this as a plan decision P1).
  8. `phone`: resize PHONE; goto `#infra-capstone`; scrollTo `#infra-capstone .mc-scroll` 3000; wait 3000; resize VIEWPORT.
  9. `close`: goto `#home`; scrollTo `#home` 2000; wait 8000.
  Each beat's planned length is in spec §3; the recorder does not enforce it, the encode check enforces the total.
- Produces `srt.mjs`: `formatTime(ms: number): string` → `HH:MM:SS,mmm`; `toSrt(cues: {start: number, end: number, text: string}[]): string` (1-based indices, blank-line separated, throws `Error('overlapping cues at N')` when a cue starts before the previous ends; a cue's `text` may hold one `\n`).

- [ ] **Step 1: Write the failing tests** in `app/tests/demo.test.ts`:

```ts
import { BEATS, PHONE, VIEWPORT } from '../scripts/demo/beats.mjs';
import { formatTime, toSrt } from '../scripts/demo/srt.mjs';
it('has nine beats with short captions and known actions', () => {
  expect(BEATS.map((b) => b.id)).toEqual(['home','anatomy','replay','code','aid','quiz','gate','phone','close']);
  for (const b of BEATS) for (const c of b.captions) { expect(c.length).toBeLessThan(90); expect(c.split('\n').length).toBeLessThanOrEqual(2); }
  const kinds = new Set(['goto','scrollTo','click','clickUntilDisabled','select','wait','resize','caption']);
  for (const b of BEATS) for (const a of b.actions) expect(kinds.has(a.kind), `${b.id}: ${a.kind}`).toBe(true);
});
it('holds every stepping click for at least 1200 ms', () => { /* every click/clickUntilDisabled on a .mc-btn-primary or Next is followed by wait >= 1200 or carries waitMs >= 1200 */ });
it('closes with a figure note and restores the viewport after the phone beat', () => { /* TEST_COUNT_NOTE mentions 2026-10-29; the phone beat's last resize equals VIEWPORT */ });
it('formats srt times and refuses overlaps', () => {
  expect(formatTime(3723456)).toBe('01:02:03,456');
  expect(toSrt([{ start: 0, end: 1000, text: 'a' }, { start: 1000, end: 2500, text: 'b\nc' }])).toBe('1\n00:00:00,000 --> 00:00:01,000\na\n\n2\n00:00:01,000 --> 00:00:02,500\nb\nc\n');
  expect(() => toSrt([{ start: 0, end: 1000, text: 'a' }, { start: 900, end: 2000, text: 'b' }])).toThrow('overlapping cues at 2');
});
```

- [ ] **Step 2: Run `cd app && npx vitest run tests/demo.test.ts`** → FAIL (modules missing).
- [ ] **Step 3: Install the dependency and scaffold**: `cd app && npm install --save-dev --save-exact playwright@1.64.1` (expect no browser download: `npx playwright --version` prints 1.64.1 and `ls ~/Library/Caches/ms-playwright/chromium-1248` exists); add the `demo` script; add `app/demo/` to `.gitignore`; write `beats.mjs` and `srt.mjs`.
- [ ] **Step 4: Run the test** → PASS. Also `npm test` stays green and `npm run build` passes (the `typecheck:tests` step must accept the `.mjs` imports: add a `declare module` or JSDoc types if `tsc -p tests` complains; say which in the report).
- [ ] **Step 5: Commit** `app/scripts/demo/beats.mjs app/scripts/demo/srt.mjs app/tests/demo.test.ts app/package.json app/package-lock.json .gitignore` as `feat(demo): beats, captions and the srt formatter`.

### Task 2: The caption strip and the recorder (with dry run)

**Files:**
- Create: `app/scripts/demo/caption.mjs`, `app/scripts/demo/record.mjs`

**Interfaces:**
- Consumes: `BEATS`, `VIEWPORT`, `PHONE` from `beats.mjs`.
- Produces `caption.mjs`: `export const CAPTION_CSS: string` (a fixed strip `#demo-caption` in the bottom third: `position: fixed; left: 50%; bottom: 9%; transform: translateX(-50%); max-width: 72%; padding: 0.6em 1.1em; border-radius: 12px; font: 600 40px/1.25 var(--font-sans, system-ui); color: var(--color-text, #fff); background: color-mix(in srgb, var(--color-bg, #000) 82%, transparent); box-shadow: …; opacity transition 250ms; z-index: 9999; white-space: pre-line; text-align: center`; the hex values above are fallbacks only when a token is absent, allowed because this is not site CSS), `export const CAPTION_JS: string` (installs `window.__demoCaption(text)`: sets the text, fades in; `''` fades out), and `export async function installCaption(page)`, `export async function showCaption(page, text)`.
- Produces `record.mjs`: `serve(dir) -> Promise<{url, close}>` (Node `http`, loopback, port 0, static files with content types for html/js/css/json/svg/png/woff2; 404 otherwise); `runBeat(page, beat, timings, dry)`; `main(argv)` with `--dry` and `--out DIR` (default `app/demo`); flow: check `app/publish/index.html` exists and is newer than `app/dist/index.html` if dist exists (else exit 2 with "run npm run package"); launch `chromium` (from the `playwright` package) with `recordVideo: {dir: tmp, size: VIEWPORT}` unless `--dry`, `viewport: VIEWPORT`, `colorScheme: 'dark'`; open `${url}/index.html#home`; set `data-theme="dark"`; install the caption; for each beat: show caption 0, record `{beat, caption, start}` with `performance.now()` relative to the first beat's start, run actions (a missing selector throws `Error('beat <id>: missing <selector>')` after a 5 s wait), `caption` actions swap the text and record a new timing; at the end hide the caption, wait 1 s, close the context (which finalises the WebM), move the WebM to `<out>/raw.webm`, write `<out>/timings.json` as `[{beat, caption, start, end}]` with `end` = the next caption's start or the recording end. In `--dry` mode the waits are capped at 100 ms and no video is recorded; exit 0 prints "dry run ok: 9 beats".
- Selector waits use `page.locator(selector).first().waitFor({timeout: 5000})`; `scrollTo` scrolls the element into view with `behavior: 'smooth'` and then waits `ms`; `clickUntilDisabled` clicks until the button is disabled or `max` is reached, waiting `waitMs` between clicks; `select` uses `selectOption`.

- [ ] **Step 1: Write a failing test** in `app/tests/demo.test.ts` for the pure helper `timingsToCues(timings) -> cues` (added to `srt.mjs`: consecutive captions become abutting cues; the last cue ends at `end`), and for `CAPTION_CSS` containing `#demo-caption` and `white-space: pre-line`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement `caption.mjs`, `record.mjs`, `timingsToCues`.** **Step 4: Run the test** → PASS; then `cd app && npm run package && node scripts/demo/record.mjs --dry` → "dry run ok: 9 beats" (fix any selector in `beats.mjs` that the real page does not match; report every change); then the real `node scripts/demo/record.mjs` → `app/demo/raw.webm` exists, `timings.json` has 13 entries (9 first captions + 4 second captions), total length between 140 and 170 s by the last `end`.
- [ ] **Step 5: Review Focus 1 and 3**: a test that `runBeat` with a bogus selector rejects with `missing`; and running `record.mjs` with `app/publish` renamed away exits 2 with the message (do it by hand in a temp copy, paste the output in the report; restore).
- [ ] **Step 6: Commit** `app/scripts/demo/caption.mjs app/scripts/demo/record.mjs app/scripts/demo/srt.mjs app/scripts/demo/beats.mjs app/tests/demo.test.ts` as `feat(demo): caption strip and the Playwright recorder with a dry run`.

### Task 3: Encode, verify, stills

**Files:**
- Create: `app/scripts/demo/encode.mjs`

**Interfaces:**
- Consumes: `app/demo/raw.webm`, `app/demo/timings.json`, `toSrt`, `timingsToCues`.
- Produces `encode.mjs`: `main(argv)` with `--out DIR`: checks `ffmpeg` and `ffprobe` on the PATH (else exit 2); runs `ffmpeg -y -i raw.webm -vf "fade=t=in:st=0:d=0.5,fade=t=out:st=<dur-1>:d=1,format=yuv420p" -r 30 -c:v libx264 -preset slow -crf 20 -movflags +faststart -an ai-engineering-demo.mp4` (the duration from ffprobe on the WebM); writes `ai-engineering-demo.srt` from the timings; runs `ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,pix_fmt -show_entries format=duration,size -of json` and asserts codec `h264`, 1920×1080, `30/1`, `yuv420p`, 140 ≤ duration ≤ 170, size < 200 MB, exiting 2 with the measured values otherwise; extracts stills with `ffmpeg -ss <mid of beat> -i mp4 -frames:v 1 stills/beat-N.png` for each of the nine beats (mid = (start of the beat's first caption + end of its last) / 2); prints a summary table (duration, size, fps, codec, cues, stills).

- [ ] **Step 1: Write a failing test** for the pure `stillTimes(timings) -> number[]` (nine midpoints, in seconds, one per beat) and `ffprobeOk(probe) -> string[]` (the list of violated bounds given a parsed ffprobe JSON; empty when all hold; a 0-byte or missing stream reports `no video stream`), both in `encode.mjs` exported.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run the test** → PASS; then `cd app && npm run demo` end to end → the summary table, exit 0; `ls app/demo/stills` shows nine PNGs; `git status --short` shows nothing under `app/demo/`.
- [ ] **Step 5: Review Focus 4**: rename `raw.webm` away and run `encode.mjs` → exit 2 naming the missing input; restore. Also `PATH=/usr/bin node scripts/demo/encode.mjs` (no ffmpeg) → exit 2 with the ffmpeg message.
- [ ] **Step 6: Commit** `app/scripts/demo/encode.mjs app/tests/demo.test.ts` as `feat(demo): encode, verify and stills`.

## Plan decisions

- P1: beat 7's "a test file scrolling briefly" shows the budgets lesson's code block instead, because test files are not on the page; the caption still states that every number is pinned by a test.
- P2: `playwright@1.64.1` is pinned exactly because its Chromium build (1248) is the one in the local cache; a different version would try to download a browser.
- P3: captions use the site's CSS variables with plain fallbacks so the strip renders even if a token is renamed; the strip is injected only during recording and never ships in the page.
