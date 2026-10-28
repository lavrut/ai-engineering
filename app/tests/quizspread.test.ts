import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';

// A quiz whose right answer sits mostly in one position can be passed by always picking that
// position. No track may have more than half of its correct answers at one option index.
const lessons = join(dirname(fileURLToPath(import.meta.url)), '../src/content/lessons');
// Tracks not yet rebalanced. Every track is rebalanced now (ruling P13), so this list must stay empty: a test
// below fails if a track is ever added back. A listed track is also asserted to still be unbalanced, so fixing one
// fails here until it is removed from the list.
const NOT_YET_REBALANCED = new Set<string>([]);
const tracks = readdirSync(lessons, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);

describe('quiz answers are spread over the option positions', () => {
  it('finds the tracks', () => expect(tracks.length).toBeGreaterThan(0));
  it('leaves no track waiting to be rebalanced (P13: NOT_YET_REBALANCED stays empty)', () => {
    expect([...NOT_YET_REBALANCED]).toEqual([]);
  });
  for (const track of tracks) {
    const pending = NOT_YET_REBALANCED.has(track);
    const name = pending ? 'still has more than 50% of its correct answers at one index (rebalance, then drop it from the list)'
      : 'no option index holds more than 50% of the correct answers';
    it(`${track}: ${name}`, () => {
      const counts = [0, 0, 0, 0];
      for (const f of readdirSync(join(lessons, track)).filter((n) => n.endsWith('.mdx'))) {
        const quiz = (matter(readFileSync(join(lessons, track, f), 'utf8')).data.quiz ?? []) as { answer: number }[];
        for (const q of quiz) counts[q.answer]++;
      }
      const total = counts.reduce((a, b) => a + b, 0);
      expect(total, `${track} has no quiz answers`).toBeGreaterThan(0);
      const share = Math.max(...counts) / total;
      if (pending) expect(share, `${track} is balanced now: remove it from NOT_YET_REBALANCED`).toBeGreaterThan(0.5);
      else expect(share, `${track} answers by index: ${counts.join(' / ')}`).toBeLessThanOrEqual(0.5);
    });
  }
});
