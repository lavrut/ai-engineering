import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { sourceTexts } from '../src/lib/codeSourceText';
import { parseCodeTabsTag, type CodeSourceId } from '../src/lib/codeSources';

// A lesson's walkthrough bullets quote code. The quotes drifted from the source once
// (`app = build_http_app()` after the code became `build_http_app(port=args.port)`), so this
// checks them: in the MCP, AI Agents and Data Fabric lessons, every backticked span in a bullet that follows a <CodeTabs>
// block and looks like code (contains "(" or "=") must appear in the source file that block quotes
// (the whole file, since bullets often point just outside the shown section), in the Python or the Go text.
//
// Scope, kept narrow on purpose to avoid false positives:
//  - only the bullet list right after a CodeTabs tag (stops at the first non-bullet paragraph);
//  - only spans containing "(" or "=" (identifiers, flags and prose-like spans are not checked);
//  - a bullet that quotes code from a file the CodeTabs block does not show is out of scope: write the
//    name without parentheses there (e.g. `local_hosts`), as lesson 7 does;
//  - signature spans (starting with def, async def or func) are skipped: lessons shorten parameter
//    lists and drop type hints there on purpose, and the same name is checked where it is called;
//  - whitespace is collapsed, and a literal "..." in a span means "anything here": each piece
//    between the dots must appear, in order.
const here = dirname(fileURLToPath(import.meta.url));
const lessonsDir = join(here, '../src/content/lessons');

const squash = (t: string) => t.replace(/\s+/g, ' ').replace(/\( /g, '(').replace(/ \)/g, ')');

export function bulletsAfterCodeTabs(mdx: string): { tag: string; bullets: string[] }[] {
  const lines = mdx.split('\n');
  const out: { tag: string; bullets: string[] }[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].startsWith('<CodeTabs')) continue;
    let tag = lines[i];
    while (!tag.includes('/>')) tag += ' ' + lines[++i];
    const bullets: string[] = [];
    let j = i + 1;
    while (j < lines.length && lines[j].trim() === '') j++;
    while (j < lines.length && lines[j].startsWith('- ')) {
      bullets.push(lines[j]);
      j++;
      if (lines[j]?.trim() === '' && lines[j + 1]?.startsWith('- ')) j++;
    }
    out.push({ tag, bullets });
  }
  return out;
}

export function codeSpans(bullet: string): string[] {
  return [...bullet.matchAll(/`([^`]+)`/g)].map((m) => m[1]).filter((s) => /[(=]/.test(s) && !/^(async def|def|func) /.test(s));
}

export function appearsIn(span: string, haystack: string): boolean {
  const h = squash(haystack);
  // `name(a, b)` with bare identifiers is shorthand for a signature (the source has type hints and
  // defaults), so only the name and its opening parenthesis are checked. Any `=` or string is exact.
  const bare = /^([\w.]+\()[\w, ]*\)$/.exec(span.trim());
  if (bare) return h.includes(bare[1]);
  let from = 0;
  for (const piece of span.split('...').map((p) => squash(p).trim()).filter(Boolean)) {
    const at = h.indexOf(piece, from);
    if (at < 0) return false;
    from = at + piece.length;
  }
  return true;
}

describe('walkthrough quote helpers', () => {
  it('matches the helper on known cases', () => {
    expect(appearsIn('build_http_app(port=args.port)', 'app = build_http_app(port=args.port)')).toBe(true);
    expect(appearsIn('app = build_http_app()', 'app = build_http_app(port=args.port)')).toBe(false);
    expect(appearsIn('f(a, ...)', 'f(a,\n   b)')).toBe(true);
    expect(appearsIn('write_label(a, k="x")', 'write_label(\n  a, k="x")')).toBe(true);
    expect(appearsIn('http_target(url, token)', 'def http_target(url: str, token: str | None = None)')).toBe(true);
    expect(appearsIn('http_target2(url)', 'def http_target(url: str)')).toBe(false);
    expect(appearsIn('f(x="b")', 'f(x="a")')).toBe(false);
    expect(codeSpans('- `x = 1` and `name` and `f()` and `def g(a)`')).toEqual(['x = 1', 'f()']);
  });
});

// The AI Agents lessons quote their sources the same way (agents-patterns, agents-supervisor, ...), so the
// same check runs on them, with thresholds for their own size.
describe.each([
  { track: 'mcp', title: 'MCP', minFiles: 11, minSpans: 40 },
  { track: 'agents', title: 'AI Agents', minFiles: 6, minSpans: 30 },
  { track: 'data', title: 'Data Fabric & Data Contracts', minFiles: 6, minSpans: 15 },
])('$title lesson walkthrough quotes', ({ track, minFiles, minSpans }) => {
  const dir = join(lessonsDir, track);
  const files = readdirSync(dir).filter((f) => f.endsWith('.mdx')).sort();

  it('finds the lessons and a useful number of checked spans', () => {
    expect(files.length).toBeGreaterThanOrEqual(minFiles);
    let n = 0;
    for (const f of files) for (const b of bulletsAfterCodeTabs(readFileSync(join(dir, f), 'utf8'))) for (const x of b.bullets) n += codeSpans(x).length;
    expect(n).toBeGreaterThan(minSpans);
  });

  it.each(files)('%s: quoted code appears in the quoted source file', (f) => {
    const missing: string[] = [];
    for (const { tag, bullets } of bulletsAfterCodeTabs(readFileSync(join(dir, f), 'utf8'))) {
      const source = parseCodeTabsTag(tag).source;
      if (!source) throw new Error(`CodeTabs without a source: ${tag}`);
      const texts = sourceTexts(source as CodeSourceId);
      const text = texts.python + '\n' + (texts.go ?? '');
      for (const b of bullets) for (const s of codeSpans(b)) if (!appearsIn(s, text)) missing.push(`${s}   [${tag.slice(0, 60)}]`);
    }
    expect(missing).toEqual([]);
  });
});
