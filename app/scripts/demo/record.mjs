#!/usr/bin/env node
// Records the demo video: serves app/publish on a loopback port, drives Chromium through the beats
// with Playwright's video recording on, draws the captions, a cursor and the view transitions in the
// page, and writes the caption timings. Output: <out>/raw.webm and <out>/timings.json. `--dry` runs
// the beats without recording (waits capped at 100 ms) and fails on the first missing element.
//
//   node scripts/demo/record.mjs [--dry] [--out DIR] [--theme light|dark]   (from app/; `npm run package` first)
import { createServer } from 'node:http';
import { createReadStream, existsSync, mkdirSync, statSync } from 'node:fs';
import { copyFile, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { BEATS, VIEWPORT } from './beats.mjs';
import { installCaption, showCaption } from './caption.mjs';
import { cursorPress, cursorShow, cursorTo, installOverlay, overlay, scrollToLocator } from './overlay.mjs';

const APP = resolve(fileURLToPath(new URL('../../', import.meta.url)));
const PUBLISH = join(APP, 'publish');
const DIST = join(APP, 'dist');
const SELECTOR_TIMEOUT = 5000;
const DRY_WAIT = 100;
const SCROLL_MS = 900;
const CLICK_KINDS = new Set(['click', 'clickUntilDisabled', 'select']);
const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2',
  '.woff': 'font/woff', '.ico': 'image/x-icon', '.txt': 'text/plain',
};

// ── 1. The static server ─────────────────────────────────────────────────────
/** Serve `dir` on a loopback free port. */
export function serve(dir) {
  return new Promise((ok) => {
    const server = createServer((req, res) => {
      const path = decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname);
      const file = join(dir, path === '/' ? 'index.html' : path);
      if (!file.startsWith(dir) || !existsSync(file) || statSync(file).isDirectory()) {
        res.writeHead(404); res.end('not found'); return;
      }
      res.writeHead(200, { 'content-type': TYPES[extname(file)] || 'application/octet-stream' });
      createReadStream(file).pipe(res);
    });
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      ok({ url: `http://127.0.0.1:${port}`, close: () => new Promise((done) => server.close(done)) });
    });
  });
}

// ── 2. Actions ───────────────────────────────────────────────────────────────
function locate(page, beat, selector) {
  return page.locator(selector).first().waitFor({ timeout: SELECTOR_TIMEOUT }).then(
    () => page.locator(selector).first(),
    () => { throw new Error(`beat ${beat.id}: missing ${selector}`); },
  );
}

const setTheme = (theme) => `document.documentElement.setAttribute('data-theme', ${JSON.stringify(theme)})`;

async function runAction(page, beat, action, ctx) {
  const wait = (ms) => page.waitForTimeout(ctx.dry ? Math.min(ms, DRY_WAIT) : ms);
  switch (action.kind) {
    case 'goto':
      await overlay(page, true, wait);
      await page.evaluate((h) => { location.hash = h; window.scrollTo(0, 0); }, action.hash);
      await wait(150);
      await overlay(page, false, wait);
      return;
    case 'scrollTo': {
      const el = await locate(page, beat, action.selector);
      await scrollToLocator(page, el, ctx.dry ? DRY_WAIT : SCROLL_MS);
      await wait(action.ms);
      return;
    }
    case 'click': {
      const el = await locate(page, beat, action.selector);
      await cursorTo(page, el, wait);
      await cursorPress(page, wait);
      await el.click();
      return;
    }
    case 'clickUntilDisabled': {
      const el = await locate(page, beat, action.selector);
      await cursorTo(page, el, wait);
      for (let i = 0; i < action.max; i++) {
        if (await el.isDisabled()) break;
        await cursorPress(page, wait);
        await el.click();
        await wait(action.waitMs);
      }
      return;
    }
    case 'select': {
      const el = await locate(page, beat, action.selector);
      await cursorTo(page, el, wait);
      await cursorPress(page, wait);
      await el.selectOption(action.value);
      return;
    }
    case 'wait':
      await wait(action.ms);
      return;
    case 'resize':
      await page.setViewportSize({ width: action.width, height: action.height });
      await wait(500);
      return;
    case 'caption':
      await caption(page, beat, beat.captions[action.index], ctx);
      return;
    case 'phoneFrame': {
      // The page again, at phone size, in a centred frame over a dimmed backdrop; media queries apply
      // inside the frame because its own viewport is the phone's.
      const url = `${ctx.pageUrl}${action.hash}`;
      await overlay(page, true, wait);
      await page.evaluate(({ url, width, height }) => {
        const back = document.createElement('div');
        back.id = 'demo-phone-back';
        back.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:9000;';
        const frame = document.createElement('iframe');
        frame.id = 'demo-phone';
        frame.src = url;
        frame.style.cssText = `position:fixed;left:50%;top:50%;width:${width}px;height:${height}px;transform:translate(-50%,-60%);border:10px solid #222;border-radius:28px;background:#000;z-index:9001;`;
        document.body.append(back, frame);
      }, { url, width: action.width, height: action.height });
      const frame = page.frameLocator('#demo-phone');
      await frame.locator('body').waitFor({ timeout: SELECTOR_TIMEOUT });
      const inner = page.frames().find((f) => f.url().endsWith(action.hash));
      if (inner) await inner.evaluate(setTheme(ctx.theme));
      await overlay(page, false, wait);
      await wait(1200);
      const target = frame.locator(action.selector).first();
      await target.waitFor({ timeout: SELECTOR_TIMEOUT }).catch(() => { throw new Error(`beat ${beat.id}: missing ${action.selector} in the phone frame`); });
      await target.evaluate((node) => node.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      await wait(action.ms);
      await overlay(page, true, wait);
      await page.evaluate(() => { document.getElementById('demo-phone')?.remove(); document.getElementById('demo-phone-back')?.remove(); });
      await overlay(page, false, wait);
      return;
    }
    default:
      throw new Error(`beat ${beat.id}: unknown action ${action.kind}`);
  }
}

async function caption(page, beat, text, ctx) {
  const now = Date.now() - ctx.t0;
  const last = ctx.timings[ctx.timings.length - 1];
  if (last && last.end === null) last.end = now;
  ctx.timings.push({ beat: beat.id, caption: text, start: now, end: null });
  await showCaption(page, text);
}

/** Run one beat: its first caption, then its actions in order. The cursor shows only in beats that click. */
export async function runBeat(page, beat, ctx) {
  await caption(page, beat, beat.captions[0], ctx);
  if (page.evaluate && ctx.overlay !== false) await cursorShow(page, beat.actions.some((a) => CLICK_KINDS.has(a.kind)));
  for (const action of beat.actions) await runAction(page, beat, action, ctx);
}

// ── 3. The run ───────────────────────────────────────────────────────────────
function checkPublish() {
  const index = join(PUBLISH, 'index.html');
  if (!existsSync(index)) return 'app/publish/index.html is missing: run `npm run package` first';
  const dist = join(DIST, 'index.html');
  if (existsSync(dist) && statSync(dist).mtimeMs > statSync(index).mtimeMs + 1000) {
    return 'app/publish is older than app/dist: run `npm run package` first';
  }
  return null;
}

/** The theme the recording uses: light unless `--theme dark`. */
export function themeOf(argv) {
  const i = argv.indexOf('--theme');
  const theme = i >= 0 ? argv[i + 1] : 'light';
  if (theme !== 'light' && theme !== 'dark') throw new Error('--theme must be light or dark');
  return theme;
}

export async function main(argv = process.argv.slice(2)) {
  const dry = argv.includes('--dry');
  const theme = themeOf(argv);
  const outArg = argv[argv.indexOf('--out') + 1];
  const out = argv.includes('--out') && outArg ? resolve(outArg) : join(APP, 'demo');
  const problem = checkPublish();
  if (problem) { console.error(problem); return 2; }
  mkdirSync(out, { recursive: true });
  const videoDir = dry ? null : await mkdtemp(join(tmpdir(), 'demo-video-'));
  const server = await serve(PUBLISH);
  const browser = await chromium.launch();
  const ctx = { dry, theme, t0: 0, timings: [], pageUrl: `${server.url}/index.html` };
  try {
    const context = await browser.newContext({
      viewport: VIEWPORT, colorScheme: theme, deviceScaleFactor: 1,
      ...(dry ? {} : { recordVideo: { dir: videoDir, size: VIEWPORT } }),
    });
    const page = await context.newPage();
    ctx.t0 = Date.now();
    await page.goto(`${server.url}/index.html#home`, { waitUntil: 'load' });
    await page.evaluate(setTheme(theme));
    await installCaption(page);
    await installOverlay(page);
    await page.waitForTimeout(dry ? DRY_WAIT : 800);
    for (const beat of BEATS) await runBeat(page, beat, ctx);
    await showCaption(page, '');
    await cursorShow(page, false);
    await page.waitForTimeout(dry ? DRY_WAIT : 1000);
    const end = Date.now() - ctx.t0;
    const last = ctx.timings[ctx.timings.length - 1];
    if (last && last.end === null) last.end = end;
    const video = dry ? null : page.video();
    await context.close();
    if (dry) { console.log(`dry run ok: ${BEATS.length} beats`); return 0; }
    await copyFile(await video.path(), join(out, 'raw.webm'));
    await writeFile(join(out, 'timings.json'), JSON.stringify(ctx.timings, null, 1) + '\n');
    console.log(`recorded ${BEATS.length} beats, ${ctx.timings.length} captions, ${(end / 1000).toFixed(1)} s, ${theme} theme → ${join(out, 'raw.webm')}`);
    return 0;
  } finally {
    await browser.close();
    await server.close();
    if (videoDir) await rm(videoDir, { recursive: true, force: true });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then((code) => process.exit(code), (err) => { console.error(err.message); process.exit(1); });
}
