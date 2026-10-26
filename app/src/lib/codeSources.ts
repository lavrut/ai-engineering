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
  | 'lora'
  | 'ml-train'
  | 'ml-mlops'
  | 'ml-mlflow'
  | 'ml-serve'
  | 'ml-monitor'
  | 'ml-fetch'
  | 'ml-llm';

export type CodeSourceInfo = {
  /** Repo-relative path of the Python file. */
  python: string;
  /** Repo-relative path of the Go file; none means Python only. */
  go?: string;
  /** 'own': the Go file numbers its sections differently, so a quote must give goSection. */
  goSections?: 'own';
};

export const CODE_SOURCES: Record<CodeSourceId, CodeSourceInfo> = {
  harness: { python: 'harness/python/harness.py', go: 'harness/go/main.go' }, // the reference harness
  llms: { python: 'llms/python/triage_lab.py', go: 'llms/go/main.go' }, // the eval lab
  lora: { python: 'llms/finetune/lora.py' }, // the LoRA script
  'ml-train': { python: 'ml/python/train.py' }, // data, features, models, metrics, export
  'ml-mlops': { python: 'ml/python/mlops.py' }, // run tracker, registry, gate, retrain
  'ml-mlflow': { python: 'ml/python/with_mlflow.py' }, // the same with MLflow
  'ml-serve': { python: 'ml/serve/server.py', go: 'ml/go/main.go', goSections: 'own' }, // the two model servers
  'ml-monitor': { python: 'ml/python/monitor.py', go: 'ml/go/drift.go', goSections: 'own' }, // drift
  'ml-fetch': { python: 'ml/data/fetch_sample.py' }, // the one-time CFPB fetch
  'ml-llm': { python: 'ml/python/compare_llm.py' }, // the Claude comparison
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
