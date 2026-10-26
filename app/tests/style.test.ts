import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';
import { lessonSchema, trackSchema } from '../src/content/schemas';
import { lessonFileName, nextOrder, renderLesson } from '../scripts/new-lesson-lib.mjs';

// The rules in docs/STYLE.md that a test can check.

const here = dirname(fileURLToPath(import.meta.url));
const app = join(here, '..');
const lessonsDir = join(app, 'src/content/lessons');

function files(dir: string, ext: RegExp): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    // Skip tool caches (.pytest_cache, __pycache__): they are not source.
    if (e.isDirectory()) return e.name.startsWith('.') || e.name === '__pycache__' ? [] : files(p, ext);
    return ext.test(e.name) ? [p] : [];
  });
}

const lessons = files(lessonsDir, /\.mdx$/).map((file) => ({
  name: relative(lessonsDir, file),
  text: readFileSync(file, 'utf8'),
}));

const REQUIRED = ['Concept', 'See it', 'The code', 'Try it'];
const OPTIONAL_LANG = /^How .+ does it$/;

/** The lesson's H2 headings, skipping fenced code blocks. */
function h2s(mdx: string): string[] {
  let fenced = false;
  const out: string[] = [];
  for (const line of mdx.split('\n')) {
    if (/^\s*```/.test(line)) fenced = !fenced;
    else if (!fenced && line.startsWith('## ')) out.push(line.slice(3).trim());
  }
  return out;
}

/** Problems with a heading list, or [] when it follows the lesson anatomy. */
function anatomyProblems(headings: string[]): string[] {
  const core = headings.filter((h) => !OPTIONAL_LANG.test(h));
  const problems: string[] = [];
  if (core.join('|') !== REQUIRED.join('|')) problems.push(`H2s must be ${REQUIRED.join(', ')} in order; got ${core.join(', ')}`);
  headings.forEach((h, i) => {
    if (OPTIONAL_LANG.test(h) && (headings[i - 1] !== 'The code' || headings[i + 1] !== 'Try it')) {
      problems.push(`"${h}" must sit between "The code" and "Try it"`);
    }
  });
  return problems;
}

describe('lesson anatomy', () => {
  it.each(lessons.map((l) => [l.name, l] as const))('%s has the standard sections', (_n, l) => {
    expect(anatomyProblems(h2s(l.text))).toEqual([]);
  });

  it.each(lessons.map((l) => [l.name, l] as const))('%s has no TODO left from the template', (_n, l) => {
    expect(l.text.match(/.*\bTODO\b.*/g) ?? []).toEqual([]);
  });

  it('flags missing, reordered and misplaced sections', () => {
    expect(anatomyProblems(['Concept', 'See it', 'The code', 'Try it'])).toEqual([]);
    expect(anatomyProblems(['Concept', 'See it', 'The code', 'How Go does it', 'Try it'])).toEqual([]);
    expect(anatomyProblems(['Concept', 'The code', 'Try it'])).toHaveLength(1);
    expect(anatomyProblems(['See it', 'Concept', 'The code', 'Try it'])).toHaveLength(1);
    expect(anatomyProblems(['Concept', 'How Go does it', 'See it', 'The code', 'Try it'])).toHaveLength(1);
  });
});

describe('lesson template', () => {
  const template = readFileSync(join(app, 'templates/lesson.mdx'), 'utf8');
  const rendered = renderLesson(template, { track: 'harness', order: 10, slug: 'new-thing', title: 'A new thing' });
  const { data, content } = matter(rendered);

  it('renders frontmatter that matches lessonSchema', () => {
    const result = lessonSchema.safeParse(data);
    expect(result.success ? [] : result.error.issues).toEqual([]);
    expect(data).toMatchObject({ track: 'harness', order: 10, slug: 'new-thing', title: 'A new thing' });
  });

  it('follows the lesson anatomy', () => {
    expect(anatomyProblems(h2s(content))).toEqual([]);
  });

  it('leaves no placeholder unfilled', () => {
    expect(rendered).not.toMatch(/\{\{\w+\}\}/);
  });
});

describe('new-lesson helpers', () => {
  it('numbers a new lesson one past the highest existing one', () => {
    expect(nextOrder([])).toBe(1);
    expect(nextOrder(['01-what.mdx', '03-tools.mdx', 'notes.txt'])).toBe(4);
  });
  it('pads the order to two digits', () => {
    expect(lessonFileName(4, 'loop')).toBe('04-loop.mdx');
    expect(lessonFileName(12, 'evals')).toBe('12-evals.mdx');
  });
});

describe('track template', () => {
  it('has every field trackSchema requires', () => {
    // gray-matter parses YAML front matter, so wrap the file in --- fences.
    const { data } = matter(`---\n${readFileSync(join(app, 'templates/track.yaml'), 'utf8')}\n---\n`);
    const result = trackSchema.safeParse(data);
    expect(result.success ? [] : result.error.issues).toEqual([]);
  });
});

describe('design tokens', () => {
  it('keeps every hex colour in src/styles/tokens.css', () => {
    const sources = files(join(app, 'src'), /\.(astro|tsx|css|mdx)$/).filter((f) => !f.endsWith('tokens.css'));
    const hits = sources.flatMap((f) =>
      [...readFileSync(f, 'utf8').matchAll(/(?<![\w&%])#[0-9a-fA-F]{3,8}\b/g)].map((m) => `${relative(app, f)}: ${m[0]}`),
    );
    expect(hits).toEqual([]);
  });
});

describe('role tokens', () => {
  const css = readFileSync(join(app, 'src/styles/tokens.css'), 'utf8');
  // The light block is the bare :root; the two dark blocks must stay identical.
  const blocks = {
    light: css.slice(css.indexOf(':root {'), css.indexOf('@media')),
    'dark (media)': css.slice(css.indexOf('@media'), css.indexOf(':root[data-theme="dark"]')),
    'dark (forced)': css.slice(css.indexOf(':root[data-theme="dark"]')),
  };
  const vars = (block: string) =>
    Object.fromEntries([...block.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map((m) => [m[1], m[2]]));
  const luminance = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a: string, b: string) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const ROLES = ['user', 'harness', 'model', 'tool', 'memory', 'logs', 'dataset', 'grader'];

  it.each(Object.entries(blocks))('%s defines every role with at least 3:1 contrast on --bg', (_name, block) => {
    const v = vars(block);
    const problems = ROLES.flatMap((r) => {
      const c = v[`--role-${r}`];
      if (!c) return [`--role-${r} missing`];
      const ratio = contrast(c, v['--bg']);
      return ratio >= 3 ? [] : [`--role-${r} ${c} is ${ratio.toFixed(2)}:1 on ${v['--bg']}`];
    });
    expect(problems).toEqual([]);
  });

  it('defines the same dark colours in both dark blocks', () => {
    expect(vars(blocks['dark (media)'])).toEqual(vars(blocks['dark (forced)']));
  });
});

describe('text integrity', () => {
  // UTF-8 bytes read as Latin-1 turn a middle dot or curly apostrophe into two or
  // three junk characters (A-circumflex, a-circumflex + euro sign, ...); U+FFFD marks
  // bytes that failed to decode.
  const MOJIBAKE = /Â[ -¿]|â€|â†|Ã[\u0080-¿]|�/;
  const repo = join(app, '..');
  const roots = [
    'app/src',
    'app/templates',
    'app/scripts',
    'app/tests',
    'docs',
    'harness/python',
    'harness/go',
    'llms/python',
    'llms/go',
    'llms/finetune',
    'llms/data',
    'ml/python',
    'ml/go',
    'ml/serve',
    'ml/data',
    'ml/reports',
  ];
  const sources = roots.flatMap((r) => files(join(repo, r), /\.(astro|tsx?|mjs|css|mdx?|ya?ml|jsonl?|py|go)$/));

  it('finds source files to check', () => {
    expect(sources.length).toBeGreaterThan(50);
    for (const r of [
      'llms/python',
      'llms/go',
      'llms/finetune',
      'llms/data',
      'llms/data/fixtures',
      'ml/python',
      'ml/go',
      'ml/serve',
      'ml/data',
      'ml/data/fixtures',
      'ml/reports',
    ]) {
      expect(sources.some((f) => relative(repo, f).startsWith(`${r}/`)), r).toBe(true);
    }
  });

  it('has no mis-decoded characters in any source file', () => {
    const decoder = new TextDecoder('utf-8', { fatal: true });
    const problems = sources.flatMap((f) => {
      const name = relative(repo, f);
      let text: string;
      try {
        text = decoder.decode(readFileSync(f));
      } catch {
        return [`${name}: not valid UTF-8`];
      }
      return text
        .split('\n')
        .flatMap((line, i) => (MOJIBAKE.test(line) && !line.includes('MOJIBAKE') ? [`${name}:${i + 1}`] : []));
    });
    expect(problems).toEqual([]);
  });
});
