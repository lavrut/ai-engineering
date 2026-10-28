// The text of the files in CODE_SOURCES, and what a CodeTabs or a diagram step shows from them.
// Build time (CodeTabs.astro, FlowDiagram.astro) and tests only: sourceTexts bundles every
// quoted file, so an island must never import this module (use codeSources.ts for metadata).
import { CODE_SOURCES, isSource, type CodeRequest, type CodeSourceId } from './codeSources';
import { extractSection, listSections } from './regions';

export type ResolvedCode = { python: string; go?: string };

let texts: Record<string, string> | undefined;

const FIRST_MARKER = /^[ \t]*(?:#|\/\/) ── \d+\./m;
function fromFirstSection(text: string, path: string): string {
  const at = text.search(FIRST_MARKER);
  if (at === -1) throw new Error(`codeSourceText: ${path} has no numbered section`);
  return text.slice(at);
}

/** The source files' text, read at build time (and in tests). */
export function sourceTexts(id: CodeSourceId): { python: string; go?: string } {
  if (!texts) {
    // Keep this list in step with CODE_SOURCES (the test checks both against the files on disk).
    const raw = import.meta.glob(
      [
        '../../../harness/python/harness.py',
        '../../../harness/go/main.go',
        '../../../llms/python/triage_lab.py',
        '../../../llms/go/main.go',
        '../../../llms/finetune/lora.py',
        '../../../llms/inspect/triage_inspect.py',
        '../../../ml/python/train.py',
        '../../../ml/python/mlops.py',
        '../../../ml/python/with_mlflow.py',
        '../../../ml/serve/server.py',
        '../../../ml/go/main.go',
        '../../../ml/python/monitor.py',
        '../../../ml/go/drift.go',
        '../../../ml/data/fetch_sample.py',
        '../../../ml/python/compare_llm.py',
        '../../../mcp/python/read_server.py',
        '../../../mcp/go/readserver/server.go',
        '../../../mcp/go/readserver/main.go',
        '../../../mcp/python/ops_server.py',
        '../../../mcp/go/opsserver/server.go',
        '../../../mcp/go/opsserver/main.go',
        '../../../mcp/python/client.py',
        '../../../mcp/go/client/main.go',
        '../../../mcp/python/resource_auth.py',
        '../../../mcp/go/auth/auth.go',
        '../../../mcp/python/auth_server.py',
        '../../../mcp/python/agent.py',
        '../../../mcp/python/simulate.py',
        '../../../agents/proxy/proxy.py',
        '../../../agents/desk/python/patterns.py',
        '../../../agents/desk/go/patterns.go',
        '../../../agents/desk/python/supervisor.py',
        '../../../agents/desk/go/supervisor.go',
        '../../../agents/frameworks/langgraph_desk.py',
        '../../../agents/frameworks/sdk_desk.py',
        '../../../agents/evals/scorers.py',
        '../../../agents/evals/run_evals.py',
      ],
      { query: '?raw', import: 'default', eager: true },
    ) as Record<string, string>;
    texts = Object.fromEntries(Object.entries(raw).map(([k, v]) => [k.replace(/^(\.\.\/)+/, ''), v]));
  }
  const read = (path: string) => {
    const t = texts![path];
    if (t === undefined) throw new Error(`codeSourceText: ${path} is not in sourceTexts' file list`);
    return t;
  };
  const info = CODE_SOURCES[id];
  // Extra Go files follow the first, so section 6 (main.go) sits after section 5 (server.go). Each
  // extra file starts at its first section marker: its package clause and imports would otherwise
  // end up inside the last section of the file before it.
  const go = info.go ? [read(info.go), ...(info.goMore ?? []).map((f) => fromFirstSection(read(f), f))].join('\n') : undefined;
  return { python: read(info.python), go };
}

/** Problems with a request: an unknown source, a section missing from a file it shows, or a
 *  goSection that is missing (goSections: 'own') or has no Go tab to apply to. [] means valid. */
export function codeProblems(req: CodeRequest): string[] {
  const id = req.source ?? 'harness';
  if (!isSource(id)) return [`unknown source "${id}"`];
  if (!req.whole && req.section === undefined) return ['needs section={n} or whole'];
  const info = CODE_SOURCES[id];
  const problems: string[] = [];
  if (req.goSection !== undefined && !info.go) problems.push(`goSection given, but ${id} has no Go file`);
  else if (req.goSection !== undefined && req.pythonOnly) problems.push('goSection given, but pythonOnly hides the Go tab');
  if (req.whole) return problems;
  const src = sourceTexts(id);
  const has = (text: string, n: number) => listSections(text).some((s) => s.n === n);
  if (!has(src.python, req.section!)) problems.push(`no section ${req.section} in ${info.python}`);
  if (src.go === undefined || req.pythonOnly) return problems;
  if (info.goSections === 'own' && req.goSection === undefined) {
    problems.push(`${id}: ${info.go} numbers its own sections; add goSection={n} (or pythonOnly)`);
    return problems;
  }
  const goSection = req.goSection ?? req.section!;
  if (!has(src.go, goSection)) problems.push(`no section ${goSection} in ${info.go}`);
  return problems;
}

/** The code a CodeTabs shows: Python, and Go unless the source has none or pythonOnly is set. */
export function resolveCode(req: CodeRequest): ResolvedCode {
  const problems = codeProblems(req);
  if (problems.length) throw new Error(`CodeTabs: ${problems.join('; ')}`);
  const src = sourceTexts((req.source ?? 'harness') as CodeSourceId);
  if (req.whole) return { python: src.python, go: req.pythonOnly ? undefined : src.go };
  const python = extractSection(src.python, req.section!);
  if (src.go === undefined || req.pythonOnly) return { python };
  return { python, go: extractSection(src.go, req.goSection ?? req.section!) };
}

