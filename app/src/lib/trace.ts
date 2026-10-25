export type TraceEvent = { ts: number; event: string } & Record<string, unknown>;

/** Parse the harness's JSON Lines trace. Blank lines are skipped; a non-JSON line throws. */
export function parseTrace(text: string): TraceEvent[] {
  return text
    .split('\n')
    .map((line, i) => ({ line: line.trim(), n: i + 1 }))
    .filter(({ line }) => line !== '')
    .map(({ line, n }) => {
      try {
        return JSON.parse(line) as TraceEvent;
      } catch {
        throw new Error(`Trace line ${n} is not valid JSON: ${line.slice(0, 40)}`);
      }
    });
}
