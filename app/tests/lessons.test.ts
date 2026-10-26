import { readdirSync, readFileSync } from 'node:fs';
import { basename, dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';
import { lessonSchema } from '../src/content/schemas';
import { glossary } from '../src/data/glossary';
import { listSections } from '../src/lib/regions';
import { viewId } from '../src/lib/router';

const here = dirname(fileURLToPath(import.meta.url));
const lessonsDir = join(here, '../src/content/lessons');
const diagramsDir = join(here, '../src/data/diagrams');
const repo = join(here, '../..');

// The code a lesson can quote, by CodeTabs `source` (see CodeTabs.astro).
const SOURCES: Record<string, { python: string; go?: string }> = {
  harness: { python: 'harness/python/harness.py', go: 'harness/go/main.go' },
  llms: { python: 'llms/python/triage_lab.py', go: 'llms/go/main.go' },
  lora: { python: 'llms/finetune/lora.py' },
};
const sectionsOf = (file: string) => new Set(listSections(readFileSync(join(repo, file), 'utf8')).map((s) => s.n));
const SECTIONS = Object.fromEntries(
  Object.entries(SOURCES).map(([k, f]) => [k, { python: sectionsOf(f.python), go: f.go ? sectionsOf(f.go) : undefined }]),
);

/** Problems with the CodeTabs tags in a lesson: an unknown source, or a section missing from a file it shows. */
function codeTabsProblems(content: string): string[] {
  const problems: string[] = [];
  for (const [tag] of content.matchAll(/<CodeTabs\b[^>]*>/g)) {
    const section = /\bsection=\{(\d+)\}/.exec(tag)?.[1];
    if (section === undefined) continue;
    const source = /\bsource="([^"]*)"/.exec(tag)?.[1] ?? 'harness';
    const secs = SECTIONS[source];
    if (!secs) {
      problems.push(`${tag}: unknown source "${source}"`);
      continue;
    }
    const n = Number(section);
    const pythonOnly = /\bpythonOnly\b/.test(tag) || !secs.go;
    if (!secs.python.has(n)) problems.push(`${tag}: no section ${n} in ${SOURCES[source].python}`);
    if (!pythonOnly && !secs.go!.has(n)) problems.push(`${tag}: no section ${n} in ${SOURCES[source].go}`);
  }
  return problems;
}

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

  it.each(lessons.map((l) => [l.name, l] as const))('%s: every CodeTabs section exists in its source', (_n, l) => {
    expect(codeTabsProblems(l.content)).toEqual([]);
  });

  it('checks CodeTabs sections against the named source', () => {
    expect(codeTabsProblems('<CodeTabs section={9} />')).toEqual([]);
    expect(codeTabsProblems('<CodeTabs section={10} />')).toHaveLength(2);
    expect(codeTabsProblems('<CodeTabs source="llms" section={10} />')).toEqual([]);
    expect(codeTabsProblems('<CodeTabs source="llms" section={12} />')).toHaveLength(2);
    expect(codeTabsProblems('<CodeTabs source="lora" section={5} />')).toEqual([]);
    expect(codeTabsProblems('<CodeTabs source="lora" section={6} />')).toHaveLength(1);
    expect(codeTabsProblems('<CodeTabs source="nope" section={1} />')).toHaveLength(1);
    expect(codeTabsProblems('<CodeTabs whole source="llms" />')).toEqual([]);
  });

  it.each(lessons.map((l) => [l.name, l] as const))('%s: every FlowDiagram names an existing diagram', (_n, l) => {
    const names = matches(l.content, /<FlowDiagram\b[^>]*\bdiagram="([^"]*)"/g);
    expect(names.filter((d) => !diagramKeys.has(d))).toEqual([]);
  });

  it('gives every lesson file its own view id', () => {
    const ids = lessons.map((l) => viewId(l.data.track, l.data.slug));
    expect(ids.filter((id, i) => ids.indexOf(id) !== i)).toEqual([]);
  });

  it('ids content entries by path, so a slug repeated across tracks does not replace a lesson', () => {
    // Without generateId the glob loader keys entries by the slug field, and
    // llms/01-what would silently replace harness/01-what (both slug "what").
    const config = readFileSync(join(here, '../src/content.config.ts'), 'utf8');
    const lessonsLoader = config.slice(config.indexOf('const lessons'), config.indexOf('export const collections'));
    expect(lessonsLoader).toMatch(/generateId:\s*\(\{\s*entry\s*\}\)\s*=>\s*entry\.replace\(/);
    expect(lessons.filter((l) => l.data.slug === 'what').length).toBeGreaterThan(1);
  });

  it.each(lessons.map((l) => [l.name, l] as const))('%s: every Term names a glossary key', (_n, l) => {
    const terms = matches(l.content, /<Term\b[^>]*\bt="([^"]*)"/g);
    expect(terms.filter((t) => !(t in glossary))).toEqual([]);
  });
});
