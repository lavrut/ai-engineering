// The MCP track's aids: the types of mcp/data/derived/*.json (as written by mcp/python/derive.py)
// and small shared helpers. Browser-safe. Re-exported by mcpAids.ts.

// ── Types (the derived files, as written by mcp/python/derive.py) ───────────
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Obj = { [key: string]: Json };

/** One request and its response, as the SDK serialised them. */
export type Exchange = { method: string; request: Obj; response: Obj };

/** mcp/data/derived/messages.json */
export type MessagesJson = {
  server: string;
  protocolVersion: string;
  captured_with: string;
  exchanges: Exchange[];
};

/** mcp/data/derived/tool_budget.json */
export type ToolBudgetJson = {
  note: string;
  servers: Record<string, { tools: number; chars: number; tokens_estimate: number }>;
};

/** One elicitation run: the answer the client gave and the two tools/call exchanges. */
export type ElicitationPath = {
  answer: { action: string; content?: Record<string, Json> };
  rows_written: number;
  exchanges: Exchange[];
};

/** mcp/data/derived/elicitation.json */
export type ElicitationJson = {
  server: string;
  tool: string;
  arguments: { id: string; label: string };
  note: string;
  accepted: ElicitationPath;
  declined: ElicitationPath;
};

/** mcp/data/derived/app_snapshot.json */
export type AppSnapshotJson = {
  uri: string;
  mimeType: string;
  html: string;
  sample_tool_input: { name: string; arguments: { id: string; label: string } };
};

/** One recording in mcp/data/derived/injection.json. */
export type InjectionRecording = {
  model: string;
  framing: string;
  guard: boolean;
  tool_calls: { name: string; input: Record<string, Json> }[];
  label_ticket_attempted: boolean;
  outcome: string;
  rows_written: number;
  final_answer: string;
};

/** One SIMULATED run (simulate.py: a scripted model that obeys t061, through the real agent). Not a real model. */
export type SimulatedRun = InjectionRecording & { simulated: true };

/** mcp/data/derived/injection.json: recordings exist only after the paid runs were approved. The
 * `simulated` block is separate and never mixed into `recordings` (ruling R41). */
export type InjectionJson = (
  | { recorded: true; recordings: Record<string, InjectionRecording> }
  | { recorded: false; command?: string[] }
) & { simulated?: Record<string, SimulatedRun> };

export type McpData = {
  messages: MessagesJson;
  toolBudget: ToolBudgetJson;
  elicitation: ElicitationJson;
  appSnapshot: AppSnapshotJson;
  injection: InjectionJson;
};

export const pretty = (v: Json) => JSON.stringify(v, null, 2);
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
export const asObj = (v: Json | undefined): Obj => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
