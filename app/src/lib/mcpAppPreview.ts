// The MCP track's aids: AppPreview. Pure and browser-safe (no Node APIs, no file reads); mcpData.ts
// reads the derived files at build time. Re-exported by mcpAids.ts.
import { asObj, type AppSnapshotJson, type Json, type Obj } from './mcpTypes';

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
