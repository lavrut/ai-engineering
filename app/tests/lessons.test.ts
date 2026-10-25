import { readdirSync, readFileSync } from 'node:fs';
import { basename, dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';
import { lessonSchema } from '../src/content/schemas';
import { glossary } from '../src/data/glossary';

const here = dirname(fileURLToPath(import.meta.url));
const lessonsDir = join(here, '../src/content/lessons');
const diagramsDir = join(here, '../src/data/diagrams');

function mdxFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return mdxFiles(p);
    return e.name.endsWith('.mdx') ? [p] : [];
  });
}

const lessons = mdxFiles(lessonsDir).map((file) => {
  const { data, content } = matter(readFileSync(file, 'utf8'));
  return { file, name: relative(lessonsDir, file), data, content };
});

// FlowDiagram.astro keys its diagrams by the data file name (harness-flow.ts -> 'harness-flow').
const diagramKeys = new Set(
  readdirSync(diagramsDir)
    .filter((f) => f.endsWith('.ts'))
    .map((f) => f.replace(/\.ts$/, '')),
);

const matches = (content: string, re: RegExp) => [...content.matchAll(re)].map((m) => m[1]);

describe('lesson content', () => {
  it('has lessons for the harness track', () => {
    expect(lessons.filter((l) => l.name.startsWith('harness/')).length).toBeGreaterThan(0);
  });

  it.each(lessons.map((l) => [l.name, l] as const))('%s frontmatter matches lessonSchema', (_n, l) => {
    const result = lessonSchema.safeParse(l.data);
    expect(result.success ? [] : result.error.issues).toEqual([]);
  });

  it.each(lessons.map((l) => [l.name, l] as const))('%s: track matches its folder', (_n, l) => {
    expect(l.data.track).toBe(basename(dirname(l.file)));
  });

  it.each(lessons.map((l) => [l.name, l] as const))('%s: slug and order match the file name', (_n, l) => {
    const m = basename(l.file).match(/^(\d{2})-([a-z0-9-]+)\.mdx$/);
    expect(m, 'file name must be NN-slug.mdx').not.toBeNull();
    expect(l.data.slug).toBe(m![2]);
    expect(l.data.order).toBe(Number(m![1]));
  });

  it('order values are unique and consecutive from 1 within each track', () => {
    const byTrack = new Map<string, number[]>();
    for (const l of lessons) byTrack.set(l.data.track, [...(byTrack.get(l.data.track) ?? []), l.data.order]);
    for (const [track, orders] of byTrack) {
      const sorted = [...orders].sort((a, b) => a - b);
      expect({ track, orders: sorted }).toEqual({ track, orders: sorted.map((_, i) => i + 1) });
    }
  });

  it.each(lessons.map((l) => [l.name, l] as const))('%s: every CodeTabs section is 1..9', (_n, l) => {
    const sections = matches(l.content, /<CodeTabs\b[^>]*\bsection=\{(\d+)\}/g).map(Number);
    expect(sections.filter((n) => n < 1 || n > 9)).toEqual([]);
  });

  it.each(lessons.map((l) => [l.name, l] as const))('%s: every FlowDiagram names an existing diagram', (_n, l) => {
    const names = matches(l.content, /<FlowDiagram\b[^>]*\bdiagram="([^"]*)"/g);
    expect(names.filter((d) => !diagramKeys.has(d))).toEqual([]);
  });

  it.each(lessons.map((l) => [l.name, l] as const))('%s: every Term names a glossary key', (_n, l) => {
    const terms = matches(l.content, /<Term\b[^>]*\bt="([^"]*)"/g);
    expect(terms.filter((t) => !(t in glossary))).toEqual([]);
  });
});
