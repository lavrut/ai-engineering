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
  | 'mcp-client-connect'
  | 'mcp-client-calls'
  | 'mcp-auth'
  | 'mcp-auth-server'
  | 'mcp-app'
  | 'mcp-agent'
  | 'mcp-agent-config'
  | 'mcp-agent-host'
  | 'mcp-agent-model'
  | 'mcp-simulate'
  | 'agents-proxy'
  | 'agents-proxy-cost'
  | 'agents-proxy-handler'
  | 'agents-proxy-server'
  | 'agents-patterns'
  | 'agents-supervisor'
  | 'agents-langgraph'
  | 'agents-sdk'
  | 'agents-evals'
  | 'agents-run-evals'
  | 'agents-judge-set'
  | 'data-contract'
  | 'data-catalog'
  | 'data-quality'
  | 'data-gate'
  | 'data-migrate'
  | 'data-contract-tickets'
  | 'data-contract-tickets-v2'
  | 'data-contract-complaints'
  | 'data-contract-traces';

export type CodeSourceInfo = {
  /** Repo-relative path of the Python file (or, with lang: 'yaml', of the YAML file quoted as text). */
  python: string;
  /** The first file's language when it is not Python: a data contract is YAML, quoted by its `# ── N.`
   *  comment sections like any source. CodeTabs names the tab and highlights the code by it. */
  lang?: 'yaml';
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
  // The client is three files in each language, each numbering its own sections alike in Python and Go.
  'mcp-client': { python: 'mcp/python/client.py', go: 'mcp/go/client/main.go' }, // configuration, discovery, entry point: 1-3
  'mcp-client-connect': { python: 'mcp/python/client_connect.py', go: 'mcp/go/client/connect.go' }, // connecting (stdio / HTTP): 1
  'mcp-client-calls': { python: 'mcp/python/client_calls.py', go: 'mcp/go/client/calls.go' }, // calls (1), answering input-required rounds (2)
  'mcp-auth': { python: 'mcp/python/resource_auth.py', go: 'mcp/go/auth/auth.go' }, // the resource-server half of OAuth, sections 1-3
  'mcp-auth-server': { python: 'mcp/python/auth_server.py' }, // the test authorization server, sections 1-5
  'mcp-app': { python: 'mcp/python/ops_server.py' }, // section 7, the labelling App's resource (Python only)
  // The agent (Python only) is four files, each numbering its own sections, so each is its own source.
  'mcp-agent': { python: 'mcp/python/agent.py' }, // running a scenario (1), the entry point (2)
  'mcp-agent-config': { python: 'mcp/python/agent_config.py' }, // configuration: 1
  'mcp-agent-host': { python: 'mcp/python/agent_host.py' }, // the harness import, the bridge, MCP tools as harness tools, the guard: 1-4
  'mcp-agent-model': { python: 'mcp/python/agent_model.py' }, // model calls: replay and record (1), cost (2)
  'mcp-simulate': { python: 'mcp/python/simulate.py' }, // the SIMULATED compromised model, sections 1-4 (Python only)
  // AI Agents track. The hand-built patterns and supervisor number sections 1-6 alike in Python and Go;
  // the proxy, the two framework desks and the evals are Python only. The proxy is four files, each numbering its
  // own sections, so each is its own source.
  'agents-proxy': { python: 'agents/proxy/proxy.py' }, // configuration, the key, the store, responses, command line: 1-5
  'agents-proxy-cost': { python: 'agents/proxy/proxy_cost.py' }, // the worst case, usage, the ledger, the y/N: 1
  'agents-proxy-handler': { python: 'agents/proxy/proxy_handler.py' }, // one request: the guard (1), replay and record (2)
  'agents-proxy-server': { python: 'agents/proxy/proxy_server.py' }, // the server, make_server, serve: 1
  'agents-patterns': { python: 'agents/desk/python/patterns.py', go: 'agents/desk/go/patterns.go' }, // the five patterns, 1-6
  'agents-supervisor': { python: 'agents/desk/python/supervisor.py', go: 'agents/desk/go/supervisor.go' }, // single agent and supervisor, 1-6
  'agents-langgraph': { python: 'agents/frameworks/langgraph_desk.py' }, // the desk in LangGraph, 1-6
  'agents-sdk': { python: 'agents/frameworks/sdk_desk.py' }, // the desk in the Claude Agent SDK, 1-9
  // The eval suite is three files whose section numbers overlap (scorers.py 1-7, run_evals.py 1-7, judge_set.py 1),
  // so they cannot read as one file: agents-evals is the scorers, agents-run-evals the runner, judge and gate,
  // agents-judge-set the judge set's estimate and recording.
  'agents-evals': { python: 'agents/evals/scorers.py' }, // the scorers, 1-7
  'agents-run-evals': { python: 'agents/evals/run_evals.py' }, // scoring every engine, the judge, the gate (6), 1-7
  'agents-judge-set': { python: 'agents/evals/judge_set.py' }, // the judge set: estimate and record, 1
  // Data Fabric & Data Contracts track. The validator is contract.py (1-5) and its Go twin, three files that number
  // 1-9 between them (contract.go 1-5, rules.go 6-8, main.go 9), so a quote gives goSection: the rules are Python 3
  // and Go 7. The Go files join in section order (ruling D14), so a whole quote reads 1-9. gate.py and
  // migrate_tickets.py both number 1-4, so each is its own source. The contracts are YAML.
  'data-contract': {
    python: 'data/python/contract.py',
    go: 'data/go/validate/contract.go',
    goMore: ['data/go/validate/rules.go', 'data/go/validate/main.go'],
    goSections: 'own',
  }, // contract files, rows, rules, verdicts, command line: 1-5 (Go 1-9)
  'data-catalog': { python: 'data/python/catalog.py' }, // the datasets, files and git, the catalogue, lineage, the JSON, command line: 1-6
  'data-quality': { python: 'data/python/quality.py' }, // the datasets, freshness, trace sets, verdicts.json, command line: 1-5
  'data-gate': { python: 'data/python/gate.py' }, // paths, the gate, gate.json, command line: 1-4
  'data-migrate': { python: 'data/python/migrate_tickets.py' }, // paths, one row, the file, command line: 1-4
  'data-contract-tickets': { python: 'data/contracts/tickets.v1.yaml', lang: 'yaml' }, // info, schema, quality, servicing, consumers: 1-5
  'data-contract-tickets-v2': { python: 'data/contracts/tickets.v2.yaml', lang: 'yaml' },
  'data-contract-complaints': { python: 'data/contracts/complaints.v1.yaml', lang: 'yaml' },
  'data-contract-traces': { python: 'data/contracts/agent-traces.v1.yaml', lang: 'yaml' },
};

export const isSource = (id: string): id is CodeSourceId => Object.hasOwn(CODE_SOURCES, id);

/** The file name a tab or code panel shows ("server.py", "drift.go"); undefined for a missing Go file. */
export function fileName(source: CodeSourceId, lang: 'python' | 'go'): string | undefined {
  return CODE_SOURCES[source][lang]?.split('/').pop();
}

/** The first tab's language: what CodeTabs highlights it as, and the tab's name. */
export function sourceLang(source: CodeSourceId): { lang: 'python' | 'yaml'; label: 'Python' | 'YAML' } {
  return CODE_SOURCES[source].lang === 'yaml' ? { lang: 'yaml', label: 'YAML' } : { lang: 'python', label: 'Python' };
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
