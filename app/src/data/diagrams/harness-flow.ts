import type { Diagram } from '../../lib/diagram';

// Layout (default node 150x56). Row 1 runs left to right: You, Entry, Context
// builder, Model. Row 2: Router under Model, then Permission gate. Row 3 snakes
// back right to left: Tool executor, Tools, Context manager. The loop edge
// climbs from Context manager straight up to Context builder. The Trace log is
// a tall rail on the right edge.
export const harnessFlow: Diagram = {
  id: 'harness-flow',
  title: 'The harness, step by step',
  width: 1200,
  height: 460,
  nodes: [
    { id: 'you', label: 'You', role: 'user', x: 30, y: 40 },
    { id: 'entry', label: 'Entry / REPL', role: 'harness', x: 220, y: 40 },
    { id: 'context-builder', label: 'Context builder', role: 'harness', x: 410, y: 40 },
    { id: 'model', label: 'Model (Claude API)', role: 'model', x: 620, y: 40, w: 190 },
    { id: 'router', label: 'Router (stop_reason)', role: 'harness', x: 620, y: 200, w: 190 },
    { id: 'permission-gate', label: 'Permission gate', role: 'harness', x: 850, y: 200 },
    { id: 'tool-executor', label: 'Tool executor', role: 'harness', x: 850, y: 360 },
    { id: 'tools', label: 'Tools (workspace)', role: 'tool', x: 620, y: 360, w: 190 },
    { id: 'context-manager', label: 'Context manager', role: 'memory', x: 410, y: 360 },
    { id: 'trace-log', label: 'Trace log', role: 'logs', x: 1040, y: 40, w: 130, h: 376 },
  ],
  edges: [
    { id: 'e-you-entry', from: 'you', to: 'entry', label: 'task' },
    { id: 'e-entry-builder', from: 'entry', to: 'context-builder' },
    { id: 'e-builder-model', from: 'context-builder', to: 'model', label: 'request' },
    { id: 'e-model-router', from: 'model', to: 'router', label: 'response' },
    { id: 'e-router-gate', from: 'router', to: 'permission-gate', label: 'tool_use' },
    { id: 'e-gate-executor', from: 'permission-gate', to: 'tool-executor', label: 'approved' },
    { id: 'e-executor-tools', from: 'tool-executor', to: 'tools' },
    { id: 'e-tools-manager', from: 'tools', to: 'context-manager', label: 'tool_result' },
    { id: 'e-loop', from: 'context-manager', to: 'context-builder', label: 'next turn', loop: true },
    { id: 'e-router-entry', from: 'router', to: 'entry', label: 'end_turn' },
    { id: 'e-model-log', from: 'model', to: 'trace-log' },
    { id: 'e-gate-log', from: 'permission-gate', to: 'trace-log' },
    { id: 'e-executor-log', from: 'tool-executor', to: 'trace-log' },
  ],
  steps: [
    { n: 1, highlight: ['you', 'e-you-entry', 'entry'], caption: 'You type a task. The entry point reads it and hands it to the harness.' },
    {
      n: 2,
      highlight: ['entry', 'e-entry-builder', 'context-builder'],
      caption: 'The harness builds the request: the system prompt, the message history so far, and the tool schemas.',
      code: { file: 'python', section: 6 },
    },
    {
      n: 3,
      highlight: ['context-builder', 'e-builder-model', 'model', 'e-model-log', 'trace-log'],
      caption: 'The request is sent to the model. Each call is logged to the trace.',
    },
    {
      n: 4,
      highlight: ['model', 'e-model-router', 'router'],
      caption: 'The model replies with text and/or tool_use blocks, plus a stop_reason that says why it stopped.',
      code: { file: 'python', section: 6 },
    },
    {
      n: 5,
      highlight: ['router', 'e-router-gate', 'permission-gate'],
      caption: 'The router reads stop_reason. "tool_use" means keep going. "end_turn" means jump to step 10.',
      code: { file: 'python', section: 8 },
    },
    {
      n: 6,
      highlight: ['permission-gate', 'e-gate-log', 'trace-log'],
      caption: 'The permission gate checks risky tools, such as file writes, before anything runs.',
      code: { file: 'python', section: 4 },
    },
    {
      n: 7,
      highlight: ['e-gate-executor', 'tool-executor', 'e-executor-tools', 'tools', 'e-executor-log', 'trace-log'],
      caption: 'The executor runs the approved tools, in parallel, inside the workspace sandbox.',
      code: { file: 'python', section: 5 },
    },
    {
      n: 8,
      highlight: ['tools', 'e-tools-manager', 'context-manager'],
      caption: 'Every result is appended as a tool_result block, all together in one new user message.',
      code: { file: 'python', section: 8 },
    },
    {
      n: 9,
      highlight: ['context-manager', 'e-loop', 'context-builder'],
      caption: 'The context manager checks the token budget and compacts the history if it is too big. Then the loop goes back to step 3.',
      code: { file: 'python', section: 7 },
    },
    {
      n: 10,
      highlight: ['router', 'e-router-entry', 'entry', 'you'],
      caption: 'On end_turn the loop stops. The final answer is shown to you.',
      code: { file: 'python', section: 9 },
    },
  ],
};
