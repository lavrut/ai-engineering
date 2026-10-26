// Pure, browser-safe logic for the FrameworkCompare aid. The data is llms/data/inspect_summary.json,
// written by llms/inspect/summarize.py; inspectData.ts loads it at build time.

export type InspectRow = { metric: string; lab: number; inspect: number; stderr: number; n: number; match: boolean };
export type InspectRun = {
  id: string;
  label: string;
  task: string;
  epochs: number;
  command: string;
  lab_command: string;
  rows: InspectRow[];
};
export type InspectSummary = {
  inspect_version: string;
  lab: string;
  split: string;
  runs: InspectRun[];
  framework_gives: string[];
  we_still_own: string[];
};

export type CompareRow = {
  metric: string;
  lab: string;
  inspect: string;
  stderr: string;
  n: number;
  match: boolean;
  mark: '✓ match' | '✗ differs';
};

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
// The file stores 4 decimals, so two numbers agree when they differ by less than half a unit there.
const same = (a: number, b: number) => Math.abs(a - b) < 5e-5;

export function compareRows(summary: InspectSummary, runId: string): CompareRow[] {
  const run = summary.runs.find((r) => r.id === runId);
  if (!run) return [];
  return run.rows.map((r) => {
    const match = r.match && same(r.lab, r.inspect);
    return {
      metric: r.metric,
      lab: pct(r.lab),
      inspect: pct(r.inspect),
      stderr: `± ${(r.stderr * 100).toFixed(1)} pts`,
      n: r.n,
      match,
      mark: match ? '✓ match' : '✗ differs',
    };
  });
}

export function allMatch(summary: InspectSummary): boolean {
  return summary.runs.every((run) => compareRows(summary, run.id).every((r) => r.match));
}

export function runOptions(summary: InspectSummary): { id: string; label: string }[] {
  return summary.runs.map((r) => ({ id: r.id, label: r.label }));
}
