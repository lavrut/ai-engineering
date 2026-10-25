export const BUDGET = 150_000;
export const WINDOW = 1_000_000;
export const SYSTEM_TOKENS = 800;
export const TOOL_SCHEMA_TOKENS = 1_100;
export const SUMMARY_TOKENS = 3_000;
export const TURN_SIZES = { short: 1_500, file: 25_000, tools: 8_000 } as const;

export type TurnKind = keyof typeof TURN_SIZES;
export type MeterState = { history: number[]; summary: number | null; compactions: number };

export function initialMeter(): MeterState {
  return { history: [], summary: null, compactions: 0 };
}

export function total(s: MeterState): number {
  return (
    SYSTEM_TOKENS +
    TOOL_SCHEMA_TOKENS +
    (s.summary ?? 0) +
    s.history.reduce((a, b) => a + b, 0)
  );
}

export function addTurn(s: MeterState, kind: TurnKind): { state: MeterState; compacted: boolean } {
  const next: MeterState = { ...s, history: [...s.history, TURN_SIZES[kind]] };
  if (total(next) > BUDGET) {
    return {
      state: { history: [], summary: SUMMARY_TOKENS, compactions: s.compactions + 1 },
      compacted: true,
    };
  }
  return { state: next, compacted: false };
}
