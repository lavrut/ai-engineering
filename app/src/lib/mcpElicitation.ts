// The MCP track's aids: ElicitationStepper. Pure and browser-safe (no Node APIs, no file reads); mcpData.ts
// reads the derived files at build time. Re-exported by mcpAids.ts.
import { asObj, pretty, type ElicitationJson, type ElicitationPath, type Json, type Obj } from './mcpTypes';

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
