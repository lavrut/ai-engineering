// The MCP track's teaching aids: types for mcp/data/derived/*.json and the pure logic the five
// aids use (message steps, tool budget bars, the elicitation paths, the App preview, the
// injection table). Browser-safe: no Node APIs, no file reads (mcpData.ts reads the files at build
// time and passes the islands only what they show).

// ── Types (the derived files, as written by mcp/python/derive.py) ───────────
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Obj = { [key: string]: Json };

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

/** mcp/data/derived/injection.json: recordings exist only after the paid runs were approved. */
export type InjectionJson =
  | { recorded: true; recordings: Record<string, InjectionRecording> }
  | { recorded: false; command?: string[] };

export type McpData = {
  messages: MessagesJson;
  toolBudget: ToolBudgetJson;
  elicitation: ElicitationJson;
  appSnapshot: AppSnapshotJson;
  injection: InjectionJson;
};

const pretty = (v: Json) => JSON.stringify(v, null, 2);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

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

// ── ElicitationStepper ──────────────────────────────────────────────────────
export type ElicitationStep = {
  n: number;
  from: 'client' | 'server';
  title: string;
  summary: string;
  json: string;
};

export type ElicitationPathView = {
  id: 'accepted' | 'declined';
  label: string;
  action: string;
  rowsWritten: number;
  /** The final line the tool returned. */
  outcome: string;
  steps: ElicitationStep[];
};

export type ElicitationStepperProps = {
  tool: string;
  arguments: { id: string; label: string };
  note: string;
  paths: ElicitationPathView[];
};

const asObj = (v: Json | undefined): Obj => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});

/** The question the server asks, from the first inputRequest of the input_required result. */
export function questionOf(response: Obj): string {
  const requests = asObj(asObj(response.result).inputRequests);
  const first = Object.values(requests)[0];
  const message = asObj(asObj(first).params).message;
  return typeof message === 'string' ? message : '';
}

/** The tool's final text: the structured result when it wrote, else the text content. */
export function finalLine(response: Obj): string {
  const result = asObj(response.result);
  const first = asObj((Array.isArray(result.content) ? result.content[0] : null) as Json);
  return typeof first.text === 'string' ? first.text : '';
}

function pathView(id: 'accepted' | 'declined', p: ElicitationPath, tool: string): ElicitationPathView {
  const [first, retry] = p.exchanges;
  const outcome = finalLine(retry.response);
  const question = questionOf(first.response);
  const failed = asObj(retry.response.result).isError === true;
  return {
    id,
    label: id === 'accepted' ? 'Accept' : 'Decline',
    action: p.answer.action,
    rowsWritten: p.rows_written,
    outcome,
    steps: [
      {
        n: 1,
        from: 'client',
        title: `Call ${tool}`,
        summary: 'The client calls the tool. The server has not written anything yet.',
        json: pretty(first.request),
      },
      {
        n: 2,
        from: 'server',
        title: 'Ask for input',
        summary: `The server does not run the tool. It returns resultType "input_required" with a question: ${question} It also returns a sealed requestState.`,
        json: pretty(first.response),
      },
      {
        n: 3,
        from: 'client',
        title: `Answer: ${p.answer.action}`,
        summary: `The client sends the same call again, with the answer (${p.answer.action}) and the requestState it was given.`,
        json: pretty(retry.request),
      },
      {
        n: 4,
        from: 'server',
        title: failed ? 'Refuse' : 'Write',
        summary: `${failed ? 'The server returns an error and writes nothing' : 'The server checks the state, writes one row'}: ${outcome} Rows written: ${p.rows_written}.`,
        json: pretty(retry.response),
      },
    ],
  };
}

export function elicitationStepperProps(e: ElicitationJson): ElicitationStepperProps {
  return {
    tool: e.tool,
    arguments: e.arguments,
    note: e.note,
    paths: [pathView('accepted', e.accepted, e.tool), pathView('declined', e.declined, e.tool)],
  };
}

// ── AppPreview ──────────────────────────────────────────────────────────────
/** The sandbox the preview runs in: scripts, and nothing else. In particular no allow-same-origin
 *  (the page gets an opaque origin, with no access to this site's storage or cookies), no
 *  allow-forms, no allow-popups. */
export const APP_SANDBOX = 'allow-scripts';

/** A Content-Security-Policy for the preview: the page may run its own inline script and style and
 *  load nothing else, and it cannot connect anywhere. */
export const APP_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'";

/** The snapshot with a CSP meta tag as the first thing in <head>. The HTML itself is unchanged. */
export function previewDoc(html: string): string {
  const meta = `<meta http-equiv="Content-Security-Policy" content="${APP_CSP}">`;
  if (!/<head>/i.test(html)) throw new Error('previewDoc: the snapshot has no <head>');
  return html.replace(/<head>/i, `<head>\n${meta}`);
}

/** The values of the page's <option> elements, in order. */
export function appCategories(html: string): string[] {
  return [...html.matchAll(/<option value="([^"]*)">/g)].map((m) => m[1]);
}

export type AppPreviewProps = {
  uri: string;
  mimeType: string;
  /** The srcdoc: the snapshot plus the CSP. */
  doc: string;
  bytes: number;
  categories: string[];
  /** What a screen reader gets in place of the frame. */
  description: string;
  sample: { name: string; id: string; label: string };
};

export function appPreviewProps(s: AppSnapshotJson): AppPreviewProps {
  const categories = appCategories(s.html);
  return {
    uri: s.uri,
    mimeType: s.mimeType,
    doc: previewDoc(s.html),
    bytes: new TextEncoder().encode(s.html).length,
    categories,
    description:
      `A small form headed "Label a ticket". It lists the ticket and the proposed label, which read "(waiting for the host)" and "(none)" until the host sends them. ` +
      `A Category menu offers ${categories.length} choices: ${categories.join(', ')}. ` +
      `Two buttons, Confirm and Cancel, stay disabled until the ticket arrives. A status line under them reports what happens. ` +
      `Confirm does not write anything: it asks the host to call label_ticket, and the server then asks you to confirm again.`,
    sample: { name: s.sample_tool_input.name, id: s.sample_tool_input.arguments.id, label: s.sample_tool_input.arguments.label },
  };
}

/** What the page sends the host, narrowed. Everything else is ignored. */
export type AppMessage = { jsonrpc: '2.0'; id?: number | string; method: string; params?: Obj };

export function isAppMessage(v: unknown): v is AppMessage {
  if (!v || typeof v !== 'object') return false;
  const m = v as Record<string, unknown>;
  return m.jsonrpc === '2.0' && typeof m.method === 'string' && (m.id === undefined || typeof m.id === 'number' || typeof m.id === 'string');
}

/** The host message that tells the App which tool call it belongs to. */
export function toolInputMessage(sample: { id: string; label: string }) {
  return { jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: { arguments: { id: sample.id, label: sample.label } } };
}

export const PREVIEW_REFUSAL = 'Preview only: no server is connected, so nothing was written.';

/** The stand-in host's answer to a request from the App; null for a notification. A tools/call is
 *  always answered with an error: this page has no server behind it. */
export function hostReply(m: AppMessage) {
  if (m.id === undefined) return null;
  if (m.method === 'tools/call') {
    return { jsonrpc: '2.0', id: m.id, result: { isError: true, content: [{ type: 'text', text: PREVIEW_REFUSAL }] } };
  }
  if (m.method === 'ui/initialize') return { jsonrpc: '2.0', id: m.id, result: {} };
  return { jsonrpc: '2.0', id: m.id, error: { code: -32601, message: 'Method not found' } };
}

export const LOG_LIMIT = 50;
/** Append to the preview log, keeping the last LOG_LIMIT entries. */
export function appendLog(log: string[], entry: string, limit = LOG_LIMIT): string[] {
  return [...log, entry].slice(-limit);
}

/** One sentence about a message from the App, for the log and the live region. Fields are read as
 *  plain strings and shown as text, never as markup. */
export function describeAppMessage(m: AppMessage): string {
  const str = (v: Json | undefined) => (typeof v === 'string' ? v : '?');
  if (m.method === 'tools/call') {
    const args = asObj(m.params?.arguments);
    return `The App asked the host to run ${str(m.params?.name)} for ${str(args.id)} with label ${str(args.label)}. The preview host refused: it has no server.`;
  }
  if (m.method === 'ui/initialize') return 'The App said hello to the host (ui/initialize). The preview host answered.';
  if (m.method === 'ui/notifications/initialized') return 'The App finished starting up.';
  return `The App sent ${m.method}.`;
}

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
};

export type InjectionLabProps = {
  recorded: boolean;
  rows: InjectionRow[];
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

const callText = (c: { name: string; input: Record<string, Json> }) => `${c.name}(${Object.values(c.input).map(String).join(', ')})`;

export function injectionLabProps(inj: InjectionJson): InjectionLabProps {
  if (!inj.recorded) {
    // The file's record commands win; derive.py (which turns recordings into this file) always comes last.
    const record = inj.command?.length ? inj.command : INJECTION_COMMANDS.slice(0, -1);
    return { recorded: false, rows: [], commands: [...record, DERIVE_COMMAND] };
  }
  const rows = Object.entries(inj.recordings)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, r]): InjectionRow => ({
      name,
      model: r.model,
      framing: r.framing,
      framingNote: framingNote(r.framing),
      guard: r.guard,
      toolCalls: r.tool_calls.map(callText),
      attempted: r.label_ticket_attempted,
      outcome: r.outcome,
      rowsWritten: r.rows_written,
      reading: readingOf(r),
      finalAnswer: r.final_answer,
    }));
  return { recorded: true, rows, commands: INJECTION_COMMANDS };
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
