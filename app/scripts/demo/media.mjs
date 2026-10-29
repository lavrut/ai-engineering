#!/usr/bin/env node
// The README's media from the encoded demo video: a poster frame and a short GIF teaser, written to
// docs/media/ so GitHub renders them inline. Run after `npm run demo`.
//
//   node scripts/demo/media.mjs [--out DIR]      (from app/; needs ffmpeg)
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const APP = resolve(fileURLToPath(new URL('../../', import.meta.url)));
const ROOT = resolve(APP, '..');
const MEDIA = join(ROOT, 'docs', 'media');
export const POSTER_AT = 42;        // seconds: the agent replay beat
export const TEASER = { start: 12, seconds: 8, width: 960, fps: 12 };   // the diagram stepping
export const GIF_MAX_BYTES = 4 * 1024 * 1024;

export function main(argv = process.argv.slice(2)) {
  const outArg = argv[argv.indexOf('--out') + 1];
  const demo = argv.includes('--out') && outArg ? resolve(outArg) : join(APP, 'demo');
  const mp4 = join(demo, 'ai-engineering-demo.mp4');
  if (spawnSync('ffmpeg', ['-version'], { stdio: 'ignore' }).status !== 0) { console.error('ffmpeg is not on the PATH'); return 2; }
  if (!existsSync(mp4)) { console.error(`missing ${mp4}: run npm run demo first`); return 2; }
  mkdirSync(MEDIA, { recursive: true });
  const poster = join(MEDIA, 'demo-poster.png');
  const gif = join(MEDIA, 'demo-teaser.gif');
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-ss', String(POSTER_AT), '-i', mp4, '-frames:v', '1', '-vf', 'scale=1280:-2', poster], { stdio: 'inherit' });
  const filter = `fps=${TEASER.fps},scale=${TEASER.width}:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4`;
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-ss', String(TEASER.start), '-t', String(TEASER.seconds), '-i', mp4, '-vf', filter, gif], { stdio: 'inherit' });
  const size = statSync(gif).size;
  if (size > GIF_MAX_BYTES) { console.error(`the teaser is ${(size / 1e6).toFixed(1)} MB (want under 4 MB)`); return 2; }
  console.log(`${poster} (${(statSync(poster).size / 1e3).toFixed(0)} KB)\n${gif} (${(size / 1e6).toFixed(1)} MB)`);
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(main());
}
