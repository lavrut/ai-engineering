// QualityReport (lesson 3): the rules and verdicts for one dataset from verdicts.json, pass and fail marked by
// text as well as colour. The agent traces are checked once per trace set, so `agent-traces` shows the six sets
// as a table and one set's verdicts at a time. Ages are as of verdicts.json's pinned as_of (ruling D10). Pure and
// browser-safe; dataData.ts reads verdicts.json at build time. Re-exported by dataAids.ts.
import { entrySummary, entryView, type QualityEntryView, type VerdictsJson } from './dataAidsTypes';

const TRACE_GROUP = 'agent-traces';

export type QualityReportProps = {
  /** What the lesson asked for: a verdicts.json key, or "agent-traces" for every trace set. */
  dataset: string;
  asOf: string;
  /** One entry, or one per trace set. */
  entries: QualityEntryView[];
  grouped: boolean;
};

/** The names a lesson can pass: verdicts.json's keys, with the trace sets folded into "agent-traces". */
export function qualityDatasets(verdicts: VerdictsJson): string[] {
  const names = Object.keys(verdicts.datasets).map((k) => (k.startsWith(`${TRACE_GROUP}/`) ? TRACE_GROUP : k));
  return [...new Set(names)].sort();
}

export function qualityReportProps(verdicts: VerdictsJson, opts: { dataset: string }): QualityReportProps {
  const grouped = opts.dataset === TRACE_GROUP;
  const keys = Object.keys(verdicts.datasets).filter((k) => (grouped ? k.startsWith(`${TRACE_GROUP}/`) : k === opts.dataset));
  if (keys.length === 0) {
    throw new Error(`QualityReport: no verdicts for "${opts.dataset}" (known: ${qualityDatasets(verdicts).join(', ')})`);
  }
  return { dataset: opts.dataset, asOf: verdicts.as_of, entries: keys.map((k) => entryView(k, verdicts.datasets[k])), grouped };
}

/** The live line: the entry's summary, then what the table shows. */
export function reportLine(e: QualityEntryView, failuresOnly: boolean): string {
  const shown = failuresOnly ? `Showing the ${e.verdicts.filter((v) => !v.ok).length} that failed.` : `Showing all ${e.verdicts.length}.`;
  return `${entrySummary(e)} ${shown}`;
}
