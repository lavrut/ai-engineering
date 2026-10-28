// Loop simulator engine. Pure and deterministic: no model is called. The
// transcript it builds follows the real Messages API shape so the lesson
// teaches the real protocol.

export type TextBlock = { type: 'text'; text: string };
export type ToolUse = {
  type: 'tool_use';
  id: string;
  name: 'list_files' | 'read_file' | 'write_file';
  input: Record<string, string>;
};
export type ToolResult = { type: 'tool_result'; tool_use_id: string; content: string; is_error: boolean };
export type Turn = {
  blocks: (TextBlock | ToolUse)[];
  stop_reason: 'tool_use' | 'end_turn';
  branches?: { denied?: Turn[] };
};
export type Scenario = {
  id: string;
  title: string;
  task: string;
  workspace: Record<string, string>;
  turns: Turn[];
};
export type ApiMessage = { role: 'user' | 'assistant'; content: string | (TextBlock | ToolUse | ToolResult)[] };
export type Policy = 'ask-writes' | 'allow-all';
export type SimState = {
  messages: ApiMessage[];
  workspace: Record<string, string>;
  pending: ToolUse | null;
  done: boolean;
  stopReason: Turn['stop_reason'] | null;
  turn: number;
  tokens: number;
};

const DENIED = 'The user denied this action.';

export function estimateTokens(messages: ApiMessage[]): number {
  return Math.ceil(JSON.stringify(messages).length / 4);
}

const isOutside = (p: string) => p.startsWith('/') || p.split(/[\\/]/).includes('..') || p.includes('..');

export function runFakeTool(ws: Record<string, string>, call: ToolUse): { content: string; is_error: boolean } {
  const path = call.input.path ?? '';
  const fail = (content: string) => ({ content, is_error: true });
  if (call.name !== 'list_files' && call.name !== 'read_file' && call.name !== 'write_file') {
    return fail(`Unknown tool: ${String(call.name)}`);
  }
  if (isOutside(path)) return fail(`PermissionError: ${path} is outside the workspace`);

  if (call.name === 'list_files') {
    const dir = path
      .replace(/^(\.\/)+/, '')
      .replace(/\/+$/, '')
      .replace(/^\.$/, '');
    const prefix = dir === '' ? '' : `${dir}/`;
    const names = new Set<string>();
    for (const file of Object.keys(ws)) {
      if (!file.startsWith(prefix)) continue;
      const rest = file.slice(prefix.length);
      const slash = rest.indexOf('/');
      names.add(slash === -1 ? rest : `${rest.slice(0, slash)}/`);
    }
    if (names.size === 0 && dir !== '') return fail(`FileNotFoundError: ${path}`);
    return { content: [...names].sort().join('\n') || '(empty directory)', is_error: false };
  }
  if (call.name === 'read_file') {
    if (!(path in ws)) return fail(`FileNotFoundError: ${path}`);
    return { content: ws[path].slice(0, 20_000), is_error: false };
  }
  const content = call.input.content ?? '';
  ws[path] = content;
  return { content: `Wrote ${content.length} characters to ${path}`, is_error: false };
}

export function validateTranscript(messages: ApiMessage[]): string[] {
  const problems: string[] = [];
  if (messages.length > 0 && messages[0].role !== 'user') problems.push('First message must have role user.');
  messages.forEach((m, i) => {
    if (i > 0 && m.role === messages[i - 1].role) {
      problems.push(`Message ${i} repeats role ${m.role}; roles must alternate.`);
    }
  });
  messages.forEach((m, i) => {
    if (m.role !== 'assistant' || typeof m.content === 'string') return;
    const ids = m.content.filter((b): b is ToolUse => b.type === 'tool_use').map((b) => b.id);
    if (ids.length === 0) return;
    const next = messages[i + 1];
    if (!next || next.role !== 'user' || typeof next.content === 'string') {
      problems.push(`Message ${i} has tool_use but is not followed by a user message of tool_result blocks.`);
      return;
    }
    if (next.content.some((b) => b.type !== 'tool_result')) {
      problems.push(`Message ${i + 1} must contain only tool_result blocks.`);
    }
    const got = next.content.filter((b): b is ToolResult => b.type === 'tool_result').map((b) => b.tool_use_id);
    for (const id of ids) {
      if (!got.includes(id)) problems.push(`Message ${i + 1} is missing a tool_result for tool_use ${id}.`);
    }
    if (ids.every((id) => got.includes(id)) && got.filter((id) => ids.includes(id)).join() !== ids.join()) {
      problems.push(`Message ${i + 1} tool_results are not in the order of the tool_use calls.`);
    }
  });
  messages.forEach((m, i) => {
    if (m.role !== 'user' || typeof m.content === 'string') return;
    const prev = messages[i - 1];
    const known =
      prev && prev.role === 'assistant' && typeof prev.content !== 'string'
        ? prev.content.filter((b): b is ToolUse => b.type === 'tool_use').map((b) => b.id)
        : [];
    for (const b of m.content) {
      if (b.type === 'tool_result' && !known.includes(b.tool_use_id)) {
        problems.push(`Message ${i} has tool_result ${b.tool_use_id} with no matching tool_use in the previous message.`);
      }
    }
  });
  return problems;
}

export function createSimulator(s: Scenario, initialPolicy: Policy) {
  let policy = initialPolicy;
  let messages: ApiMessage[];
  let workspace: Record<string, string>;
  let turns: Turn[];
  let next: number; // index into turns of the next assistant turn
  let turnCount: number;
  let awaitingResults: Turn | null; // assistant tool_use turn whose results are not yet sent
  let pending: ToolUse | null;
  let done: boolean;
  let stopReason: Turn['stop_reason'] | null;

  function init() {
    messages = [{ role: 'user', content: s.task }];
    workspace = { ...s.workspace };
    turns = s.turns;
    next = 0;
    turnCount = 0;
    awaitingResults = null;
    pending = null;
    done = false;
    stopReason = null;
  }

  function snapshot(): SimState {
    return {
      messages: structuredClone(messages),
      workspace: { ...workspace },
      pending: pending ? structuredClone(pending) : null,
      done,
      stopReason,
      turn: turnCount,
      tokens: estimateTokens(messages),
    };
  }

  const callsOf = (t: Turn) => t.blocks.filter((b): b is ToolUse => b.type === 'tool_use');

  function sendResults(denyId: string | null) {
    const turn = awaitingResults!;
    const results: ToolResult[] = callsOf(turn).map((c) => {
      const r = c.id === denyId ? { content: DENIED, is_error: true } : runFakeTool(workspace, c);
      return { type: 'tool_result', tool_use_id: c.id, ...r };
    });
    messages = [...messages, { role: 'user', content: results }];
    awaitingResults = null;
    pending = null;
  }

  function step() {
    if (done || pending) return;
    if (awaitingResults) {
      // Holds the turn's first write_file; approving it runs every write in the turn (no scenario has two).
      const gated = policy === 'ask-writes' ? callsOf(awaitingResults).find((c) => c.name === 'write_file') : undefined;
      if (gated) pending = gated;
      else sendResults(null);
      return;
    }
    const turn = turns[next++];
    if (!turn) {
      done = true;
      return;
    }
    messages = [...messages, { role: 'assistant', content: structuredClone(turn.blocks) }];
    turnCount++;
    stopReason = turn.stop_reason;
    if (turn.stop_reason === 'end_turn') done = true;
    else awaitingResults = turn;
  }

  init();
  return {
    state: snapshot,
    step() {
      step();
      return snapshot();
    },
    approve() {
      if (pending && awaitingResults) sendResults(null);
      return snapshot();
    },
    deny() {
      if (pending && awaitingResults) {
        const branch = awaitingResults.branches?.denied;
        sendResults(pending.id);
        if (branch) {
          turns = branch;
          next = 0;
        }
      }
      return snapshot();
    },
    runToEnd() {
      // Stops at a pending approval; it never approves on the user's behalf.
      while (!done && !pending) step();
      return snapshot();
    },
    reset(p?: Policy) {
      if (p) policy = p;
      init();
      return snapshot();
    },
  };
}

/** `read_file(path="README.md")`, with long argument values shortened. */
export function callLabel(call: ToolUse): string {
  const args = Object.entries(call.input).map(([k, v]) => `${k}=${JSON.stringify(v.length > 22 ? `${v.slice(0, 18)}…` : v)}`);
  return `${call.name}(${args.join(', ')})`;
}

const short = (text: string) => {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > 60 ? `${flat.slice(0, 57)}…` : flat;
};
const sentence = (text: string) => (/[.…]$/.test(text) ? text : `${text}.`);

/** The live line a screen reader hears after each step: what the newest message says. */
export function announce(state: SimState): string {
  const { messages, pending, done, stopReason, turn } = state;
  if (pending) return `Approval needed: the model wants to run ${callLabel(pending)}.`;
  const last = messages[messages.length - 1];
  if (messages.length === 1 || typeof last.content === 'string') return 'Ready. Press Step to send the task.';
  if (last.role === 'user') {
    const results = last.content.filter((b): b is ToolResult => b.type === 'tool_result');
    return sentence(
      `Tool results: ${results.map((r) => `${r.tool_use_id} ${r.is_error ? 'error' : 'ok'}: ${short(r.content)}`).join('; ')}`,
    );
  }
  const text = last.content
    .filter((b): b is TextBlock => b.type === 'text')
    .map((b) => b.text)
    .join(' ');
  const calls = last.content.filter((b): b is ToolUse => b.type === 'tool_use').map(callLabel);
  const says = [text && `says "${short(text)}"`, calls.length > 0 && `asks for ${calls.join(', ')}`]
    .filter(Boolean)
    .join(' and ');
  return `Turn ${turn}: the assistant ${says}; stop_reason ${stopReason}.${done ? ' Done.' : ''}`;
}
