// SubRip (.srt) caption output for the demo video: cue times from the recording's own clock.

/** Milliseconds → `HH:MM:SS,mmm`. */
export function formatTime(ms) {
  const total = Math.max(0, Math.round(ms));
  const h = Math.floor(total / 3600000);
  const m = Math.floor((total % 3600000) / 60000);
  const s = Math.floor((total % 60000) / 1000);
  const f = total % 1000;
  const two = (n) => String(n).padStart(2, '0');
  return `${two(h)}:${two(m)}:${two(s)},${String(f).padStart(3, '0')}`;
}

/** The recorder's timings `[{beat, caption, start, end}]` → cues, one per caption, in order. */
export function timingsToCues(timings) {
  return timings.map((t) => ({ start: t.start, end: t.end, text: t.caption }));
}

/** Cues `{start, end, text}` (ms) → the .srt text. A cue that starts before the previous one ends is a bug. */
export function toSrt(cues) {
  let out = '';
  cues.forEach((cue, i) => {
    if (i > 0 && cue.start < cues[i - 1].end) throw new Error(`overlapping cues at ${i + 1}`);
    out += `${i + 1}\n${formatTime(cue.start)} --> ${formatTime(cue.end)}\n${cue.text}\n\n`;
  });
  return out.replace(/\n\n$/, '\n');
}
