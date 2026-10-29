// TraceExplorer (lesson 6): the three labelled traces of traces.sample.json (a cache hit from the capstone run,
// the flaky injector's retried call, the demo run's first budget refusal), each with every stage decision, and
// the capstone gate's lines verbatim. Filter by team, tag and outcome; open one. The sample's id is its label and
// its ts is 0 (about.stable), so nothing here changes between runs. Pure and browser-safe; infraData.ts reads the
// files at build time. Re-exported by infraAids.ts.
import { pickId, type GateJson, type SampleTraceJson, type TracesSampleJson } from './infraAidsTypes';

export type GateLineView = { name: string; ok: boolean; detail: string };
export type TraceExplorerProps = {
  /** traces.sample.json's traces, unchanged. */
  traces: SampleTraceJson[];
  /** The filter values the traces carry, sorted. */
  teams: string[];
  tags: string[];
  outcomes: string[];
  gate: { ok: boolean; lines: GateLineView[] };
  about: Record<string, string>;
  /** The label of the trace the aid opens. */
  initial: string;
};
export type TraceFilter = { team?: string; tag?: string; outcome?: string };

const values = (xs: string[]) => [...new Set(xs)].sort();

export function traceExplorerProps(sample: TracesSampleJson, gate: GateJson, opts: { initial?: string } = {}): TraceExplorerProps {
  const traces = sample.traces;
  if (traces.length === 0) throw new Error('TraceExplorer: traces.sample.json has no traces (rerun replay.py)');
  return {
    traces,
    teams: values(traces.map((t) => t.trace.team)),
    tags: values(traces.map((t) => t.trace.tag)),
    outcomes: values(traces.map((t) => t.trace.outcome)),
    gate: { ok: gate.ok, lines: gate.lines.map(([name, ok, detail]) => ({ name, ok, detail })) },
    about: sample.about,
    initial: pickId('TraceExplorer', 'trace', traces.map((t) => t.label), opts.initial),
  };
}

/** The traces every given filter matches, in the sample's order. */
export function filterTraces(p: TraceExplorerProps, f: TraceFilter): SampleTraceJson[] {
  return p.traces.filter(
    ({ trace: t }) => (f.team === undefined || t.team === f.team) && (f.tag === undefined || t.tag === f.tag) && (f.outcome === undefined || t.outcome === f.outcome),
  );
}
