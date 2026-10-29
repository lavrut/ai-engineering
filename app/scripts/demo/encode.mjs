#!/usr/bin/env node
// Encodes the demo video: raw.webm → ai-engineering-demo.mp4 (H.264, 30 fps, fades), writes the .srt from
// the recorder's timings, verifies the file with ffprobe against the spec's bounds, and extracts one still
// per beat. Needs ffmpeg and ffprobe on the PATH.
//
//   node scripts/demo/encode.mjs [--out DIR]      (after record.mjs; from app/)
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { timingsToCues, toSrt } from './srt.mjs';

const APP = resolve(fileURLToPath(new URL('../../', import.meta.url)));
const NAME = 'ai-engineering-demo';
const BOUNDS = { codec: 'h264', width: 1920, height: 1080, fps: '30/1', pix: 'yuv420p', minS: 140, maxS: 170, maxBytes: 200 * 1024 * 1024 };

// ── 1. Pure checks ───────────────────────────────────────────────────────────
/** One still per beat, at the middle of the beat's span, in seconds. */
export function stillTimes(timings) {
  const spans = new Map();
  for (const t of timings) {
    const s = spans.get(t.beat) || { beat: t.beat, start: t.start, end: t.end };
    s.start = Math.min(s.start, t.start);
    s.end = Math.max(s.end, t.end);
    spans.set(t.beat, s);
  }
  return [...spans.values()].map((s) => ({ beat: s.beat, seconds: (s.start + s.end) / 2000 }));
}

/** The spec's bounds a parsed ffprobe result violates; empty when it passes. */
export function ffprobeOk(probe) {
  const v = (probe.streams || []).find((s) => s.codec_name !== undefined);
  if (!v) return ['no video stream'];
  const out = [];
  if (v.codec_name !== BOUNDS.codec) out.push(`codec ${v.codec_name} (want ${BOUNDS.codec})`);
  if (v.width !== BOUNDS.width || v.height !== BOUNDS.height) out.push(`size ${v.width}x${v.height} (want ${BOUNDS.width}x${BOUNDS.height})`);
  if (v.r_frame_rate !== BOUNDS.fps) out.push(`frame rate ${v.r_frame_rate} (want ${BOUNDS.fps})`);
  if (v.pix_fmt !== BOUNDS.pix) out.push(`pixel format ${v.pix_fmt} (want ${BOUNDS.pix})`);
  const dur = Number(probe.format?.duration);
  if (!(dur >= BOUNDS.minS && dur <= BOUNDS.maxS)) out.push(`duration ${Number.isFinite(dur) ? dur.toFixed(1) : '?'} s (want ${BOUNDS.minS}-${BOUNDS.maxS})`);
  const size = Number(probe.format?.size);
  if (!(size > 0 && size < BOUNDS.maxBytes)) out.push(`file ${probe.format?.size ?? '?'} bytes (want under 200 MB)`);
  return out;
}

// ── 2. ffmpeg ────────────────────────────────────────────────────────────────
function have(tool) {
  return spawnSync(tool, ['-version'], { stdio: 'ignore' }).status === 0;
}

function probe(file) {
  const json = execFileSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'stream=codec_name,width,height,r_frame_rate,pix_fmt', '-show_entries', 'format=duration,size',
    '-of', 'json', file], { encoding: 'utf8' });
  return JSON.parse(json);
}

function encode(input, output, durationS) {
  const fadeOut = Math.max(0, durationS - 1).toFixed(2);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', input,
    '-vf', `fade=t=in:st=0:d=0.5,fade=t=out:st=${fadeOut}:d=1,format=yuv420p`,
    '-r', '30', '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-movflags', '+faststart', '-an', output], { stdio: 'inherit' });
}

function still(input, seconds, output) {
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-ss', seconds.toFixed(2), '-i', input, '-frames:v', '1', output], { stdio: 'inherit' });
}

// ── 3. The run ───────────────────────────────────────────────────────────────
export function main(argv = process.argv.slice(2)) {
  const outArg = argv[argv.indexOf('--out') + 1];
  const out = argv.includes('--out') && outArg ? resolve(outArg) : join(APP, 'demo');
  const raw = join(out, 'raw.webm');
  const timingsPath = join(out, 'timings.json');
  for (const tool of ['ffmpeg', 'ffprobe']) if (!have(tool)) { console.error(`${tool} is not on the PATH; the WebM is kept at ${raw}`); return 2; }
  if (!existsSync(raw) || !existsSync(timingsPath)) { console.error(`missing ${raw} or ${timingsPath}: run record.mjs first`); return 2; }
  const timings = JSON.parse(readFileSync(timingsPath, 'utf8'));
  const mp4 = join(out, `${NAME}.mp4`);
  const srt = join(out, `${NAME}.srt`);
  const rawDur = Number(probe(raw).format?.duration) || timings[timings.length - 1].end / 1000;
  encode(raw, mp4, rawDur);
  writeFileSync(srt, toSrt(timingsToCues(timings)));
  const result = probe(mp4);
  const problems = ffprobeOk(result);
  if (problems.length) { console.error(`the encode is outside the bounds: ${problems.join('; ')}`); return 2; }
  const stills = join(out, 'stills');
  mkdirSync(stills, { recursive: true });
  const times = stillTimes(timings);
  times.forEach((t, i) => still(mp4, t.seconds, join(stills, `beat-${i + 1}-${t.beat}.png`)));
  const v = result.streams[0];
  console.log(`duration ${Number(result.format.duration).toFixed(1)} s | size ${(Number(result.format.size) / 1e6).toFixed(1)} MB | ${v.width}x${v.height} ${v.r_frame_rate} ${v.codec_name} ${v.pix_fmt} | cues ${timings.length} | stills ${times.length}`);
  console.log(`${mp4}\n${srt}`);
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(main());
}
