// The code a lesson or a diagram step can quote, by name. CodeTabs and FlowDiagram read the
// files through codeSourceText.ts; lessons.test.ts and diagrams.test.ts check every quoted
// section against the same registry.
//
// Each source has a Python file and an optional Go file. A source without Go shows only the
// Python tab. Most pairs number their sections alike (harness, llms), so `section={n}` picks
// section n in both. ml-serve and ml-monitor do not: the Go server keeps its own numbering, and
// Go's drift code is section 6 of drift.go. For those (goSections: 'own') a lesson passes the Go
// section explicitly: `<CodeTabs source="ml-monitor" section={2} goSection={6} />`. A missing
// goSection there is an error, so a Go tab can never show the wrong section by accident.
//
// This file is metadata only and browser-safe (the FlowDiagram island imports fileName). The
// file text lives in codeSourceText.ts, which only build-time code and tests import.

export type CodeSourceId =
  | 'harness'
  | 'llms'
  | 'llms-inspect'
  | 'lora'
  | 'ml-train'
  | 'ml-mlops'
  | 'ml-mlflow'
  | 'ml-serve'
  | 'ml-monitor'
  | 'ml-fetch'
  | 'ml-llm'
  | 'mcp-read'
  | 'mcp-ops'
  | 'mcp-client'
  | 'mcp-auth'
  | 'mcp-auth-server'
  | 'mcp-app'
  | 'mcp-agent'
  | 'mcp-simulate'
  | 'agents-proxy'
  | 'agents-patterns'
  | 'agents-supervisor'
  | 'agents-langgraph'
  | 'agents-sdk'
  | 'agents-evals'
  | 'agents-run-evals';

export type CodeSourceInfo = {
  /** Repo-relative path of the Python file. */
  python: string;
  /** Repo-relative path of the Go file; none means Python only. */
  go?: string;
  /** More Go files whose text follows `go`, so one source reads as one file (lumen-read keeps sections 1-5 in
   *  server.go and the entry point, section 6, in main.go). */
  goMore?: string[];
  /** 'own': the Go file numbers its sections differently, so a quote must give goSection. */
  goSections?: 'own';
};

export const CODE_SOURCES: Record<CodeSourceId, CodeSourceInfo> = {
  harness: { python: 'harness/python/harness.py', go: 'harness/go/main.go' }, // the reference harness
  llms: { python: 'llms/python/triage_lab.py', go: 'llms/go/main.go' }, // the eval lab
  'llms-inspect': { python: 'llms/inspect/triage_inspect.py' }, // the lab rebuilt in Inspect
  lora: { python: 'llms/finetune/lora.py' }, // the LoRA script
  'ml-train': { python: 'ml/python/train.py' }, // data, features, models, metrics, export
  'ml-mlops': { python: 'ml/python/mlops.py' }, // run tracker, registry, gate, retrain
  'ml-mlflow': { python: 'ml/python/with_mlflow.py' }, // the same with MLflow
  'ml-serve': { python: 'ml/serve/server.py', go: 'ml/go/main.go', goSections: 'own' }, // the two model servers
  'ml-monitor': { python: 'ml/python/monitor.py', go: 'ml/go/drift.go', goSections: 'own' }, // drift
  'ml-fetch': { python: 'ml/data/fetch_sample.py' }, // the one-time CFPB fetch
  'ml-llm': { python: 'ml/python/compare_llm.py' }, // the Claude comparison
  // MCP track. Python and Go number sections 1-6 alike, so none needs goSections: 'own'.
  'mcp-read': { python: 'mcp/python/read_server.py', go: 'mcp/go/readserver/server.go', goMore: ['mcp/go/readserver/main.go'] }, // lumen-read
  // ops_server.py also has section 7 (the App); Go has 1-6, so a quote of 7 uses mcp-app.
  'mcp-ops': { python: 'mcp/python/ops_server.py', go: 'mcp/go/opsserver/server.go', goMore: ['mcp/go/opsserver/main.go'] }, // lumen-ops
  'mcp-client': { python: 'mcp/python/client.py', go: 'mcp/go/client/main.go' }, // the client
  'mcp-auth': { python: 'mcp/python/resource_auth.py', go: 'mcp/go/auth/auth.go' }, // the resource-server half of OAuth, sections 1-3
  'mcp-auth-server': { python: 'mcp/python/auth_server.py' }, // the test authorization server, sections 1-5
  'mcp-app': { python: 'mcp/python/ops_server.py' }, // section 7, the labelling App's resource (Python only)
  'mcp-agent': { python: 'mcp/python/agent.py' }, // the harness driving the servers (Python only)
  'mcp-simulate': { python: 'mcp/python/simulate.py' }, // the SIMULATED compromised model, sections 1-4 (Python only)
  // AI Agents track. The hand-built patterns and supervisor number sections 1-6 alike in Python and Go;
  // the proxy, the two framework desks and the evals are Python only.
  'agents-proxy': { python: 'agents/proxy/proxy.py' }, // the record/replay proxy, sections 1-7
  'agents-patterns': { python: 'agents/desk/python/patterns.py', go: 'agents/desk/go/patterns.go' }, // the five patterns, 1-6
  'agents-supervisor': { python: 'agents/desk/python/supervisor.py', go: 'agents/desk/go/supervisor.go' }, // single agent and supervisor, 1-6
  'agents-langgraph': { python: 'agents/frameworks/langgraph_desk.py' }, // the desk in LangGraph, 1-6
  'agents-sdk': { python: 'agents/frameworks/sdk_desk.py' }, // the desk in the Claude Agent SDK, 1-9
  // The eval suite is two files whose section numbers overlap (scorers.py 1-7, run_evals.py 1-8), so they
  // cannot read as one file: agents-evals is the scorers, agents-run-evals the runner, judge and gate.
  'agents-evals': { python: 'agents/evals/scorers.py' }, // the scorers, 1-7
  'agents-run-evals': { python: 'agents/evals/run_evals.py' }, // scoring every engine, the judge, the gate (6), 1-8
};

export const isSource = (id: string): id is CodeSourceId => Object.hasOwn(CODE_SOURCES, id);

/** The file name a tab or code panel shows ("server.py", "drift.go"); undefined for a missing Go file. */
export function fileName(source: CodeSourceId, lang: 'python' | 'go'): string | undefined {
  return CODE_SOURCES[source][lang]?.split('/').pop();
}

/** What a CodeTabs tag (or a diagram step's code link) asks for. */
export type CodeRequest = {
  /** Defaults to 'harness'. */
  source?: string;
  /** The Python section; also the Go section unless goSection is given. */
  section?: number;
  /** The Go section, for sources whose Go file numbers its own sections. */
  goSection?: number;
  whole?: boolean;
  pythonOnly?: boolean;
};

/** The props of a `<CodeTabs ... />` tag in lesson source (for the tests). Quoted values such
 *  as goNote are blanked before looking for the bare flags, so their text cannot match. */
export function parseCodeTabsTag(tag: string): CodeRequest {
  const bare = tag.replace(/"[^"]*"/g, '""');
  const num = (name: string) => {
    const m = new RegExp(`\\b${name}=\\{(\\d+)\\}`).exec(bare);
    return m ? Number(m[1]) : undefined;
  };
  const req: CodeRequest = {};
  const source = /\bsource="([^"]*)"/.exec(tag)?.[1];
  if (source !== undefined) req.source = source;
  const section = num('section');
  if (section !== undefined) req.section = section;
  const goSection = num('goSection');
  if (goSection !== undefined) req.goSection = goSection;
  if (/\swhole\b(?!=)/.test(bare)) req.whole = true;
  if (/\spythonOnly\b(?!=)/.test(bare)) req.pythonOnly = true;
  return req;
}
