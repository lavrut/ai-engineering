// The MCP track's aids: InjectionLab (real recordings and the separate simulated runs). Pure and browser-safe (no Node APIs, no file reads); mcpData.ts
// reads the derived files at build time. Re-exported by mcpAids.ts.
import { plural, type InjectionJson, type InjectionRecording, type Json } from './mcpTypes';

// ── InjectionLab ────────────────────────────────────────────────────────────
/** What to run when injection.json has no recordings (the file's own record commands win when present). */
export const DERIVE_COMMAND = 'python mcp/python/derive.py';
export const INJECTION_COMMANDS = [
  'python mcp/python/agent.py --scenario capstone --elicit accept --mode record --max-cost 10',
  'python mcp/python/agent.py --scenario capstone --guard --approve decline --elicit accept --mode record --max-cost 10',
  DERIVE_COMMAND,
];

export type InjectionRow = {
  name: string;
  model: string;
  framing: string;
  /** Set when the framing is not "none": what was different about the prompt. */
  framingNote: string | null;
  guard: boolean;
  toolCalls: string[];
  attempted: boolean;
  outcome: string;
  rowsWritten: number;
  /** The honest reading, in words. */
  reading: string;
  finalAnswer: string;
  /** True only for a scripted run from the `simulated` block; its model text says it is not a real model. */
  simulated: boolean;
};

export type InjectionLabProps = {
  recorded: boolean;
  rows: InjectionRow[];
  /** The simulated runs, kept apart from the real recordings and shown under their own heading. */
  simulatedRows: InjectionRow[];
  commands: string[];
};

export function framingNote(framing: string): string | null {
  if (framing === 'none') return null;
  if (framing === 'training') {
    return 'The system prompt began with a note saying this is a security-training test environment. That changes what the model is told, so this run is not like-for-like with a framing "none" run.';
  }
  return `The system prompt was changed with framing "${framing}", so this run is not like-for-like with a framing "none" run.`;
}

/** What happened, said plainly. An unattempted write is reported as unattempted, never as a defence working. */
export function readingOf(r: Pick<InjectionRecording, 'label_ticket_attempted' | 'rows_written' | 'guard'>): string {
  const rows = plural(r.rows_written, 'row');
  if (!r.label_ticket_attempted) {
    return `The model did not try to write. It never called label_ticket, so there was nothing for ${r.guard ? 'the guard' : 'a defence'} to stop. Rows written: ${r.rows_written}.`;
  }
  if (r.rows_written > 0) {
    return `The model called label_ticket and ${rows} ${r.rows_written === 1 ? 'was' : 'were'} written${r.guard ? ', even with the guard on' : ''}.`;
  }
  return `The model called label_ticket and no row was written.`;
}

/** A simulated run's reading: always starts "Simulated: " and speaks of the scripted model. */
export function simulatedReadingOf(r: Pick<InjectionRecording, 'label_ticket_attempted' | 'rows_written' | 'outcome'>): string {
  if (!r.label_ticket_attempted) return `Simulated: the scripted model did not call label_ticket. Rows written: ${r.rows_written}.`;
  if (r.rows_written > 0) {
    return `Simulated: the scripted model called label_ticket and ${plural(r.rows_written, 'row')} ${r.rows_written === 1 ? 'was' : 'were'} written.`;
  }
  const why = r.outcome === 'guard denied' ? ' The guard asked the person, who said no.' : '';
  return `Simulated: the scripted model called label_ticket and no row was written.${why}`;
}

export const SIMULATED_MODEL_NOTE = '(scripted, not a real model)';

const callText = (c: { name: string; input: Record<string, Json> }) => `${c.name}(${Object.values(c.input).map(String).join(', ')})`;

const toRow =
  (simulated: boolean) =>
  ([name, r]: [string, InjectionRecording]): InjectionRow => ({
    name,
    model: simulated ? `${r.model} ${SIMULATED_MODEL_NOTE}` : r.model,
    framing: r.framing,
    framingNote: framingNote(r.framing),
    guard: r.guard,
    toolCalls: r.tool_calls.map(callText),
    attempted: r.label_ticket_attempted,
    outcome: r.outcome,
    rowsWritten: r.rows_written,
    reading: simulated ? simulatedReadingOf(r) : readingOf(r),
    finalAnswer: r.final_answer,
    simulated,
  });

const byName = <T,>(rec: Record<string, T>) => Object.entries(rec).sort(([a], [b]) => a.localeCompare(b));

export function injectionLabProps(inj: InjectionJson): InjectionLabProps {
  const simulatedRows = byName(inj.simulated ?? {}).map(toRow(true));
  if (!inj.recorded) {
    // The file's record commands win; derive.py (which turns recordings into this file) always comes last.
    const record = inj.command?.length ? inj.command : INJECTION_COMMANDS.slice(0, -1);
    return { recorded: false, rows: [], simulatedRows, commands: [...record, DERIVE_COMMAND] };
  }
  return { recorded: true, rows: byName(inj.recordings).map(toRow(false)), simulatedRows, commands: INJECTION_COMMANDS };
}

export type GuardFilter = 'all' | 'guard' | 'noguard';

export function filterRows(rows: InjectionRow[], f: GuardFilter): InjectionRow[] {
  return f === 'all' ? rows : rows.filter((r) => r.guard === (f === 'guard'));
}

/** The sentence for the live region and the summary line. */
export function injectionSummary(rows: InjectionRow[]): string {
  if (rows.length === 0) return 'No recordings match this filter.';
  const attempted = rows.filter((r) => r.attempted).length;
  const written = rows.reduce((a, r) => a + r.rowsWritten, 0);
  const tried =
    attempted === 0
      ? 'In none of them did the model try to write.'
      : `In ${attempted} of them the model tried to write.`;
  return `${plural(rows.length, 'recording')} shown. ${tried} Rows written in total: ${written}.`;
}

/** The summary line for the simulated table: it names the runs as simulated every time. */
export function simulatedSummary(rows: InjectionRow[]): string {
  if (rows.length === 0) return 'No simulated runs match this filter.';
  const attempted = rows.filter((r) => r.attempted).length;
  const written = rows.reduce((a, r) => a + r.rowsWritten, 0);
  return `${plural(rows.length, 'simulated run')} shown (a scripted model, not a real one). In ${attempted} of them the script tried to write. Rows written in total: ${written}.`;
}
