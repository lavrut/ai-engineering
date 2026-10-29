import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { codeProblems, resolveCode, sourceTexts } from '../src/lib/codeSourceText';
import { CODE_SOURCES, fileName, parseCodeTabsTag, sourceLang, type CodeSourceId } from '../src/lib/codeSources';
import { listSections } from '../src/lib/regions';
import { PATTERNS } from '../src/lib/agentsAids';

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
        'mcp-agent', 'mcp-agent-config', 'mcp-agent-host', 'mcp-agent-model', 'mcp-app', 'mcp-auth', 'mcp-auth-server', 'mcp-client', 'mcp-client-calls', 'mcp-client-connect', 'mcp-ops', 'mcp-read', 'mcp-simulate',
        'agents-proxy', 'agents-proxy-cost', 'agents-proxy-handler', 'agents-proxy-server', 'agents-patterns', 'agents-supervisor', 'agents-langgraph', 'agents-sdk', 'agents-evals', 'agents-run-evals', 'agents-judge-set',
        'data-contract', 'data-catalog', 'data-quality', 'data-gate', 'data-migrate',
        'data-contract-tickets', 'data-contract-tickets-v2', 'data-contract-complaints', 'data-contract-traces',
        'ontology-core', 'ontology-generate', 'ontology-grounding', 'ontology-audit', 'ontology-lumen',
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
    expect(texts.go).toBe(goFiles.length ? goFiles.map((f, i) => { const t = readFileSync(join(repo, f), 'utf8'); return i === 0 ? t : t.slice(t.search(/^\/\/ ── \d+\./m)); }).join('\n') : undefined);
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
      ['mcp-client', [1, 2, 3], [1, 2, 3]],
      ['mcp-client-connect', [1], [1]],
      ['mcp-client-calls', [1, 2], [1, 2]],
      ['mcp-auth', [1, 2, 3], [1, 2, 3]],
      ['mcp-auth-server', [1, 2, 3, 4, 5], undefined],
      ['mcp-app', [1, 2, 3, 4, 5, 6, 7], undefined],
      ['mcp-agent', [1, 2], undefined],
      ['mcp-agent-config', [1], undefined],
      ['mcp-agent-host', [1, 2, 3, 4], undefined],
      ['mcp-agent-model', [1, 2], undefined],
      ['mcp-simulate', [1, 2, 3, 4], undefined],
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

    it("does not leak main.go's package clause or imports into Go section 5", () => {
      for (const source of ['mcp-read', 'mcp-ops'] as const) {
        const go = resolveCode({ source, section: 5 }).go!;
        expect(go, source).not.toContain('package main');
        expect(go, source).not.toContain('import (');
        expect(go.split('\n')[0]).toMatch(/^\/\/ ── 5\. HTTP handler and security/);
        expect(resolveCode({ source, section: 6 }).go).toMatch(/^\/\/ ── 6\. Entry point/);
      }
    });

    it('ends read section 4 with the factory return (the prompts section runs to the next marker)', () => {
      const py = resolveCode({ source: 'mcp-read', section: 4 }).python.split('\n');
      expect(py.at(-1)).toBe('return mcp');
      const go = resolveCode({ source: 'mcp-read', section: 4 }).go!.split('\n');
      expect(go.at(-1)).toBe('}');
      expect(go.at(-2)).toBe('return s');
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

  // The AI Agents track: which sections each source has (listed from the real files), so a lesson
  // can quote any of them by number. Python and Go number the patterns and the supervisor alike.
  describe('agents sources', () => {
    const nums = (t: string | undefined) => listSections(t ?? '').map((x) => x.n);
    it.each([
      ['agents-proxy', 'agents/proxy/proxy.py', [1, 2, 3, 4, 5], undefined],
      ['agents-proxy-cost', 'agents/proxy/proxy_cost.py', [1], undefined],
      ['agents-proxy-handler', 'agents/proxy/proxy_handler.py', [1, 2], undefined],
      ['agents-proxy-server', 'agents/proxy/proxy_server.py', [1], undefined],
      ['agents-patterns', 'agents/desk/python/patterns.py', [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6]],
      ['agents-supervisor', 'agents/desk/python/supervisor.py', [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6]],
      ['agents-langgraph', 'agents/frameworks/langgraph_desk.py', [1, 2, 3, 4, 5, 6], undefined],
      ['agents-sdk', 'agents/frameworks/sdk_desk.py', [1, 2, 3, 4, 5, 6, 7, 8, 9], undefined],
      ['agents-evals', 'agents/evals/scorers.py', [1, 2, 3, 4, 5, 6, 7], undefined],
      ['agents-run-evals', 'agents/evals/run_evals.py', [1, 2, 3, 4, 5, 6, 7], undefined],
      ['agents-judge-set', 'agents/evals/judge_set.py', [1], undefined],
    ] as const)('%s lists its numbered sections', (id, py, sections, go) => {
      expect(CODE_SOURCES[id].python).toBe(py);
      const t = sourceTexts(id);
      expect(nums(t.python)).toEqual(sections);
      expect(go ? nums(t.go) : t.go).toEqual(go);
      expect(CODE_SOURCES[id].goSections).toBeUndefined();
    });

    // PatternStepper's captions name the section of patterns.py and patterns.go that holds each pattern:
    // pinned here against the files' own section titles, so a renumbered file fails, not a lesson.
    it("names each pattern's section in PatternStepper's caption", () => {
      const src = sourceTexts('agents-patterns');
      const title = (t: string, n: number) => listSections(t).find((s) => s.n === n)?.title;
      for (const p of PATTERNS) {
        expect(title(src.python, p.section), p.id).toBe(p.name);
        expect(title(src.go!, p.section), p.id).toBe(p.name);
        expect(p.caption).toBe(`In the code: patterns.py and patterns.go, section ${p.section}.`);
      }
    });

    it('pairs the hand-built desk with its Go files, section for section', () => {
      expect(CODE_SOURCES['agents-patterns'].go).toBe('agents/desk/go/patterns.go');
      expect(CODE_SOURCES['agents-supervisor'].go).toBe('agents/desk/go/supervisor.go');
      const p = resolveCode({ source: 'agents-patterns', section: 6 });
      expect(p.python.split('\n')[0]).toMatch(/^# ── 6\. Evaluator-optimizer/);
      expect(p.go!.split('\n')[0]).toMatch(/^\/\/ ── 6\. Evaluator-optimizer/);
      const s = resolveCode({ source: 'agents-supervisor', section: 5 });
      expect(s.python.split('\n')[0]).toMatch(/^# ── 5\. The supervisor/);
      expect(s.go!.split('\n')[0]).toMatch(/^\/\/ ── 5\. The supervisor/);
      expect(fileName('agents-evals', 'python')).toBe('scorers.py');
      expect(resolveCode({ source: 'agents-run-evals', section: 6 }).python.split('\n')[0]).toMatch(/^# ── 6\. The gate/);
    });
  });

  // The Data Fabric & Data Contracts track: the validator in two languages (Python numbers 1-5, Go 1-9 over
  // three files, so goSections: 'own'), the catalogue, quality, gate and migration scripts, and the contracts
  // themselves, quoted as YAML by their `# ── N.` comment sections.
  describe('data sources', () => {
    const nums = (t: string | undefined) => listSections(t ?? '').map((x) => x.n);
    const title = (t: string | undefined, n: number) => listSections(t ?? '').find((s) => s.n === n)?.title;

    it.each([
      ['data-contract', 'data/python/contract.py', [1, 2, 3, 4, 5], [1, 2, 3, 4, 5, 6, 7, 8, 9]],
      ['data-catalog', 'data/python/catalog.py', [1, 2, 3, 4, 5, 6], undefined],
      ['data-quality', 'data/python/quality.py', [1, 2, 3, 4, 5], undefined],
      ['data-gate', 'data/python/gate.py', [1, 2, 3, 4], undefined],
      ['data-migrate', 'data/python/migrate_tickets.py', [1, 2, 3, 4], undefined],
      ['data-contract-tickets', 'data/contracts/tickets.v1.yaml', [1, 2, 3, 4, 5], undefined],
      ['data-contract-tickets-v2', 'data/contracts/tickets.v2.yaml', [1, 2, 3, 4, 5], undefined],
      ['data-contract-complaints', 'data/contracts/complaints.v1.yaml', [1, 2, 3, 4, 5], undefined],
      ['data-contract-traces', 'data/contracts/agent-traces.v1.yaml', [1, 2, 3, 4, 5], undefined],
    ] as const)('%s lists its numbered sections', (id, first, sections, go) => {
      expect(CODE_SOURCES[id].python).toBe(first);
      const t = sourceTexts(id);
      expect(nums(t.python)).toEqual(sections);
      expect(go ? nums(t.go) : t.go).toEqual(go);
    });

    it('pairs the Python validator with its Go twin, whose files number their own sections', () => {
      expect(CODE_SOURCES['data-contract']).toEqual({
        python: 'data/python/contract.py',
        go: 'data/go/validate/contract.go',
        goMore: ['data/go/validate/rules.go', 'data/go/validate/main.go'],
        goSections: 'own',
      });
      expect(fileName('data-contract', 'go')).toBe('contract.go');
      // The rules: Python section 3, Go section 7 (rules.go); the contract file loader: Python 1, Go 3 (contract.go).
      const rules = resolveCode({ source: 'data-contract', section: 3, goSection: 7 });
      expect(rules.python.split('\n')[0]).toMatch(/^# ── 3\. Rules/);
      expect(rules.go!.split('\n')[0]).toMatch(/^\/\/ ── 7\. Rules/);
      expect(rules.go).not.toContain('package main');
      expect(resolveCode({ source: 'data-contract', section: 1, goSection: 3 }).go!.split('\n')[0]).toMatch(/^\/\/ ── 3\. Contracts/);
      expect(resolveCode({ source: 'data-contract', section: 5, goSection: 9 }).go).toContain('func main()');
      expect(codeProblems({ source: 'data-contract', section: 3 })).toEqual([
        'data-contract: data/go/validate/contract.go numbers its own sections; add goSection={n} (or pythonOnly)',
      ]);
    });

    it('quotes the gate and the migration as two sources, since both number 1-4', () => {
      expect(title(sourceTexts('data-gate').python, 2)).toBe('The gate');
      expect(title(sourceTexts('data-migrate').python, 2)).toBe('One row');
      expect(resolveCode({ source: 'data-migrate', section: 2 }).python).toContain('def migrate(row: dict) -> dict:');
      expect(fileName('data-migrate', 'python')).toBe('migrate_tickets.py');
    });

    it('quotes a contract by its five sections, in YAML', () => {
      for (const id of ['data-contract-tickets', 'data-contract-tickets-v2', 'data-contract-complaints', 'data-contract-traces'] as const) {
        expect(CODE_SOURCES[id].lang, id).toBe('yaml');
        expect(CODE_SOURCES[id].go, id).toBeUndefined();
        expect(listSections(sourceTexts(id).python).map((s) => s.title), id).toEqual(['info', 'schema', 'quality', 'servicing', 'consumers']);
      }
      const info = resolveCode({ source: 'data-contract-tickets', section: 1 }).python;
      expect(info.split('\n')[0]).toMatch(/^# ── 1\. info ─+$/);
      expect(info).toContain('  sunset: 2027-01-31');
      expect(info).not.toContain('schema:');
      expect(resolveCode({ source: 'data-contract-tickets-v2', section: 2 }).python).toContain('- name: urgency');
      expect(resolveCode({ source: 'data-contract-complaints', section: 3 }).python).toContain('row_count: {min: 3600, max: 3600}');
      expect(resolveCode({ source: 'data-contract-traces', section: 5 }).python).toContain('- name: agents-eval');
      expect(fileName('data-contract-tickets', 'python')).toBe('tickets.v1.yaml');
    });

    it('labels a source by its language: Python unless the registry says YAML', () => {
      expect(sourceLang('data-contract-tickets')).toEqual({ lang: 'yaml', label: 'YAML' });
      expect(sourceLang('data-contract')).toEqual({ lang: 'python', label: 'Python' });
      expect(sourceLang('harness')).toEqual({ lang: 'python', label: 'Python' });
      // CodeTabs names its first tab and highlights it by the same call.
      const tabs = readFileSync(join(here, '../src/components/CodeTabs.astro'), 'utf8');
      expect(tabs).toMatch(/sourceLang\(source\)/);
      expect(tabs).not.toMatch(/>Python<\/button>/);
    });
  });

  // The Ontology track: Python only. ontology.py (1-6), generate.py (1-5), grounding.py (1-3) and audit.py (1-7)
  // each number their own sections, so each is its own source; Lumen's ontology is YAML, quoted by its five
  // `# ── N.` sections as the data contracts are.
  describe('ontology sources', () => {
    const titles = (t: string | undefined) => listSections(t ?? '').map((x) => `${x.n}. ${x.title}`);

    it.each([
      ['ontology-core', 'ontology/python/ontology.py', ['1. File', '2. Checks', '3. Constraint expressions', '4. Validation', '5. Export', '6. Command line']],
      ['ontology-generate', 'ontology/python/generate.py', ['1. Definitions', '2. Schema', '3. Tool', '4. Compare', '5. Entry point']],
      ['ontology-grounding', 'ontology/python/grounding.py', ['1. Reading the saved outputs', '2. Validation', '3. Build, write, check']],
      [
        'ontology-audit',
        'ontology/python/audit.py',
        ['1. Rows and helpers', '2. The labelling guide', '3. The prompts', '4. The ML category map', '5. The data contracts', '6. The audit and the gate', '7. Write, check, command line'],
      ],
      ['ontology-lumen', 'ontology/lumen.yaml', ['1. Classes', '2. Properties', '3. Relations', '4. Individuals', '5. Constraints']],
    ] as const)('%s quotes %s by its numbered sections, Python only', (id, file, sections) => {
      expect(CODE_SOURCES[id].python).toBe(file);
      expect(CODE_SOURCES[id].go).toBeUndefined();
      const t = sourceTexts(id);
      expect(titles(t.python)).toEqual(sections);
      expect(t.go).toBeUndefined();
    });

    it('quotes the ontology as YAML, one section per part of the file', () => {
      expect(CODE_SOURCES['ontology-lumen'].lang).toBe('yaml');
      expect(sourceLang('ontology-lumen')).toEqual({ lang: 'yaml', label: 'YAML' });
      for (const id of ['ontology-core', 'ontology-generate', 'ontology-grounding', 'ontology-audit'] as const) {
        expect(sourceLang(id), id).toEqual({ lang: 'python', label: 'Python' });
      }
      const classes = resolveCode({ source: 'ontology-lumen', section: 1 }).python;
      expect(classes.split('\n')[0]).toMatch(/^# ── 1\. Classes ─+$/);
      expect(classes).toContain('Ticket');
      expect(classes).not.toContain('# ── 2.');
      expect(resolveCode({ source: 'ontology-lumen', section: 5 }).python).toContain('feature_request_never_above_normal');
      expect(fileName('ontology-lumen', 'python')).toBe('lumen.yaml');
      expect(fileName('ontology-core', 'python')).toBe('ontology.py');
    });

    it('links each script section to the code it names', () => {
      expect(resolveCode({ source: 'ontology-core', section: 4 }).python).toContain('def validate(');
      expect(resolveCode({ source: 'ontology-generate', section: 4 }).python).toMatch(/^# ── 4\. Compare/);
      expect(resolveCode({ source: 'ontology-audit', section: 6 }).python).toContain('def gate(');
      expect(codeProblems({ source: 'ontology-grounding', section: 4 })).toEqual(['no section 4 in ontology/python/grounding.py']);
      expect(codeProblems({ source: 'ontology-audit', section: 2, goSection: 2 })).toEqual(['goSection given, but ontology-audit has no Go file']);
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
