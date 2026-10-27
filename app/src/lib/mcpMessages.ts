// The MCP track's aids: McpMessageView and ToolBudget. Pure and browser-safe (no Node APIs, no file reads); mcpData.ts
// reads the derived files at build time. Re-exported by mcpAids.ts.
import { pretty, type Json, type MessagesJson, type Obj, type ToolBudgetJson } from './mcpTypes';

// ── McpMessageView ──────────────────────────────────────────────────────────
// What each method does, in words. The wire messages next to it come from the file.
const METHOD_NOTES: Record<string, string> = {
  'server/discover': 'The first call. The client asks what the server is and supports; there is no initialize handshake before it.',
  'tools/list': 'The client asks for the tools: names, descriptions, input schemas and annotations such as readOnlyHint.',
  'tools/call': 'The client runs one tool with arguments.',
  'resources/read': 'The client reads one resource by its URI.',
  'prompts/get': 'The client fetches a prompt, filled in with the arguments it gives.',
};

export type MessageStep = {
  n: number;
  method: string;
  /** JSON-RPC id of the request. */
  id: number | string | null;
  note: string;
  request: string;
  result: string;
  /** One sentence for the live region. */
  announce: string;
};

export type McpMessageViewProps = { server: string; protocolVersion: string; capturedWith: string; steps: MessageStep[] };

const idOf = (m: Obj): number | string | null => (typeof m.id === 'number' || typeof m.id === 'string' ? m.id : null);

export function messageViewProps(m: MessagesJson): McpMessageViewProps {
  const steps = m.exchanges.map((e, i): MessageStep => {
    const result = (e.response.result ?? e.response.error ?? null) as Json;
    return {
      n: i + 1,
      method: e.method,
      id: idOf(e.request),
      note: METHOD_NOTES[e.method] ?? 'A request and its response.',
      request: pretty(e.request),
      result: pretty(result),
      announce: `Step ${i + 1} of ${m.exchanges.length}: ${e.method}. The request and its ${e.response.error ? 'error' : 'result'} are shown.`,
    };
  });
  return { server: m.server, protocolVersion: m.protocolVersion, capturedWith: m.captured_with, steps };
}

// ── ToolBudget ──────────────────────────────────────────────────────────────
export type BudgetRow = {
  server: string;
  tools: number;
  chars: number;
  tokens: number;
  /** Bar length as a percentage of the largest server (0–100). */
  percent: number;
  /** Estimated tokens per tool, rounded. */
  perTool: number;
};

export type ToolBudgetProps = { note: string; rows: BudgetRow[]; totalTools: number; totalTokens: number };

export function toolBudgetProps(b: ToolBudgetJson): ToolBudgetProps {
  const entries = Object.entries(b.servers).sort(([a], [z]) => a.localeCompare(z));
  const max = Math.max(1, ...entries.map(([, s]) => s.tokens_estimate));
  const rows = entries.map(([server, s]) => ({
    server,
    tools: s.tools,
    chars: s.chars,
    tokens: s.tokens_estimate,
    percent: Math.round((s.tokens_estimate / max) * 1000) / 10,
    perTool: s.tools ? Math.round(s.tokens_estimate / s.tools) : 0,
  }));
  return {
    note: b.note,
    rows,
    totalTools: rows.reduce((a, r) => a + r.tools, 0),
    totalTokens: rows.reduce((a, r) => a + r.tokens, 0),
  };
}
