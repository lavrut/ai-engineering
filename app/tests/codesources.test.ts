import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { codeProblems, resolveCode, sourceTexts } from '../src/lib/codeSourceText';
import { CODE_SOURCES, fileName, parseCodeTabsTag, type CodeSourceId } from '../src/lib/codeSources';
import { listSections } from '../src/lib/regions';

const here = dirname(fileURLToPath(import.meta.url));
const lessonsDir = join(here, '../src/content/lessons');
const repo = join(here, '../..');

function mdxFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return mdxFiles(p);
    return e.name.endsWith('.mdx') ? [p] : [];
  });
}

const digest = (t: string | undefined) =>
  t === undefined ? null : { lines: t.split('\n').length, sha256: createHash('sha256').update(t, 'utf8').digest('hex') };

describe('existing lessons', () => {
  // Every CodeTabs in the harness and llms lessons, with what it shows. The snapshot was
  // recorded with CodeTabs' old inline source map, before the registry replaced it; the
  // registry must show exactly the same code. If you edit a quoted source on purpose,
  // check the lessons and update the snapshot with `npx vitest run -u tests/codesources.test.ts`.
  it('show the same CodeTabs code as before the source registry', () => {
    const shown: Record<string, unknown> = {};
    for (const file of mdxFiles(lessonsDir).sort()) {
      const name = relative(lessonsDir, file);
      if (!/^(harness|llms)\//.test(name)) continue;
      [...readFileSync(file, 'utf8').matchAll(/<CodeTabs\b[\s\S]*?\/>/g)].forEach(([tag], i) => {
        const r = resolveCode(parseCodeTabsTag(tag));
        shown[`${name} #${i + 1}`] = { python: digest(r.python), go: digest(r.go) };
      });
    }
    expect(Object.keys(shown).length).toBe(32);
    expect(shown).toMatchSnapshot();
  });
});

describe('code source registry', () => {
  const ids = Object.keys(CODE_SOURCES) as CodeSourceId[];

  it('registers the harness, the LLMs lab and the ML files', () => {
    expect([...ids].sort()).toEqual(
      [
        'harness', 'llms', 'llms-inspect', 'lora', 'ml-fetch', 'ml-llm', 'ml-mlflow', 'ml-mlops', 'ml-monitor', 'ml-serve', 'ml-train',
        'mcp-agent', 'mcp-app', 'mcp-auth', 'mcp-auth-server', 'mcp-client', 'mcp-ops', 'mcp-read',
      ].sort(),
    );
    expect(CODE_SOURCES['ml-serve']).toMatchObject({ python: 'ml/serve/server.py', go: 'ml/go/main.go' });
    expect(CODE_SOURCES['ml-monitor']).toMatchObject({ python: 'ml/python/monitor.py', go: 'ml/go/drift.go' });
    for (const id of ['llms-inspect', 'lora', 'ml-train', 'ml-mlops', 'ml-mlflow', 'ml-fetch', 'ml-llm'] as const) {
      expect(CODE_SOURCES[id].go, id).toBeUndefined();
    }
  });

  it.each(ids)('%s: loads the same text as the file on disk, with numbered sections', (id) => {
    const info = CODE_SOURCES[id];
    const texts = sourceTexts(id);
    expect(texts.python).toBe(readFileSync(join(repo, info.python), 'utf8'));
    const goFiles = info.go ? [info.go, ...(info.goMore ?? [])] : [];
    expect(texts.go).toBe(goFiles.length ? goFiles.map((f) => readFileSync(join(repo, f), 'utf8')).join('\n') : undefined);
    expect(listSections(texts.python).length).toBeGreaterThan(0);
    if (texts.go) expect(listSections(texts.go).length).toBeGreaterThan(0);
  });

  it('names the file per language', () => {
    expect(fileName('ml-serve', 'python')).toBe('server.py');
    expect(fileName('ml-serve', 'go')).toBe('main.go');
    expect(fileName('ml-monitor', 'go')).toBe('drift.go');
    expect(fileName('ml-train', 'go')).toBeUndefined();
  });

  it('resolves a new Python-only source with no Go code, so CodeTabs hides the Go tab', () => {
    const r = resolveCode({ source: 'ml-train', section: 3 });
    expect(r.python.split('\n')[0]).toMatch(/^# ── 3\. Features/);
    expect(r.python).toContain('def tokenize(');
    expect(r.go).toBeUndefined();
    expect(resolveCode({ source: 'ml-fetch', section: 2 }).go).toBeUndefined();
    expect(resolveCode({ source: 'ml-llm', whole: true }).python).toBe(sourceTexts('ml-llm').python);
  });

  it('pythonOnly hides the Go tab of a source that has Go', () => {
    expect(resolveCode({ source: 'llms', section: 3 }).go).toBeDefined();
    expect(resolveCode({ source: 'llms', section: 3, pythonOnly: true }).go).toBeUndefined();
  });

  it('takes the Go section from goSection where the Go file numbers its own sections', () => {
    const serve = resolveCode({ source: 'ml-serve', section: 4, goSection: 5 });
    expect(serve.python.split('\n')[0]).toMatch(/^# ── 4\. Endpoints/);
    expect(serve.go!.split('\n')[0]).toMatch(/^\/\/ ── 5\. HTTP server/);
    const drift = resolveCode({ source: 'ml-monitor', section: 2, goSection: 6 });
    expect(drift.python.split('\n')[0]).toMatch(/^# ── 2\. PSI/);
    expect(drift.go!.split('\n')[0]).toMatch(/^\/\/ ── 6\. Drift/);
    expect(resolveCode({ source: 'ml-monitor', whole: true }).go).toBe(sourceTexts('ml-monitor').go);
    // Showing only Python needs no Go section.
    expect(resolveCode({ source: 'ml-serve', section: 4, pythonOnly: true }).go).toBeUndefined();
  });

  // The MCP track: which sections each source has in Python and Go (listed from the real files).
  describe('MCP sources', () => {
    const nums = (t: string | undefined) => listSections(t ?? '').map((x) => x.n);
    it.each([
      ['mcp-read', [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6]],
      ['mcp-ops', [1, 2, 3, 4, 5, 6, 7], [1, 2, 3, 4, 5, 6]],
      ['mcp-client', [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6]],
      ['mcp-auth', [1, 2, 3], [1, 2, 3]],
      ['mcp-auth-server', [1, 2, 3, 4, 5], undefined],
      ['mcp-app', [1, 2, 3, 4, 5, 6, 7], undefined],
      ['mcp-agent', [1, 2, 3, 4, 5, 6, 7, 8, 9], undefined],
    ] as const)('%s lists its numbered sections', (id, py, go) => {
      const t = sourceTexts(id);
      expect(nums(t.python)).toEqual(py);
      expect(go ? nums(t.go) : t.go).toEqual(go);
      expect(CODE_SOURCES[id].goSections).toBeUndefined();
    });

    it('reads sections that sit inside a function, flush left', () => {
      const r = resolveCode({ source: 'mcp-read', section: 2 });
      expect(r.python.split('\n')[0]).toBe('# ── 2. Tools ────────────────────────────────────────────────────────────');
      expect(r.python).toContain('\n@mcp.tool(annotations=READ_ONLY)\ndef search_tickets(');
      expect(r.go!.split('\n')[0]).toMatch(/^\/\/ ── 2\. Tools/);
      expect(resolveCode({ source: 'mcp-ops', section: 4 }).go!.split('\n')[0]).toMatch(/^\/\/ ── 4\. Tools/);
    });

    it("joins lumen-read's main.go after server.go, so the entry point is Go section 6", () => {
      const r = resolveCode({ source: 'mcp-read', section: 6 });
      expect(r.python).toContain('def main()');
      expect(r.go!.split('\n')[0]).toMatch(/^\/\/ ── 6\. Entry point/);
      expect(r.go).toContain('func main()');
      expect(resolveCode({ source: 'mcp-ops', section: 6 }).go).toContain('func main()');
      expect(fileName('mcp-read', 'go')).toBe('server.go');
    });

    it('quotes the App (ops_server section 7) from mcp-app, Python only; mcp-ops needs pythonOnly for it', () => {
      const app = resolveCode({ source: 'mcp-app', section: 7 });
      expect(app.python).toContain('APP_URI = "ui://lumen/label.html"');
      expect(app.go).toBeUndefined();
      expect(codeProblems({ source: 'mcp-ops', section: 7 })).toEqual(['no section 7 in mcp/go/opsserver/server.go']);
      expect(codeProblems({ source: 'mcp-ops', section: 7, pythonOnly: true })).toEqual([]);
    });

    it('quotes the auth code in both languages', () => {
      const v = resolveCode({ source: 'mcp-auth', section: 2 });
      expect(v.python).toContain('class ');
      expect(v.go!.split('\n')[0]).toMatch(/^\/\/ ── 2\. Token verifier/);
      expect(resolveCode({ source: 'mcp-auth-server', section: 3 }).python.split('\n')[0]).toMatch(/^# ── 3\. The provider/);
    });
  });

  it('reports what is wrong with a request', () => {
    expect(codeProblems({ source: 'harness', section: 9 })).toEqual([]);
    expect(codeProblems({ section: 10 })).toHaveLength(2); // harness.py and main.go stop at 9
    expect(codeProblems({ source: 'nope', section: 1 })).toEqual(['unknown source "nope"']);
    expect(codeProblems({ source: 'llms' })).toEqual(['needs section={n} or whole']);
    // ml-serve and ml-monitor number their Go sections on their own: goSection is required.
    expect(codeProblems({ source: 'ml-serve', section: 4 })).toEqual([
      'ml-serve: ml/go/main.go numbers its own sections; add goSection={n} (or pythonOnly)',
    ]);
    expect(codeProblems({ source: 'ml-serve', section: 4, goSection: 6 })).toEqual(['no section 6 in ml/go/main.go']);
    expect(codeProblems({ source: 'ml-monitor', section: 4, goSection: 6 })).toEqual([]);
    expect(codeProblems({ source: 'ml-train', section: 3, goSection: 3 })).toEqual([
      'goSection given, but ml-train has no Go file',
    ]);
    expect(codeProblems({ source: 'llms', section: 3, goSection: 4, pythonOnly: true })).toEqual([
      'goSection given, but pythonOnly hides the Go tab',
    ]);
    expect(() => resolveCode({ source: 'ml-serve', section: 4 })).toThrow(/goSection/);
  });

  it('parses the props of a CodeTabs tag', () => {
    expect(parseCodeTabsTag('<CodeTabs section={9} goNote="a > b" />')).toEqual({ section: 9 });
    expect(parseCodeTabsTag('<CodeTabs source="ml-serve" section={4} goSection={5} />')).toEqual({
      source: 'ml-serve',
      section: 4,
      goSection: 5,
    });
    expect(parseCodeTabsTag('<CodeTabs whole source="llms" pythonOnly />')).toEqual({
      source: 'llms',
      whole: true,
      pythonOnly: true,
    });
  });
});
