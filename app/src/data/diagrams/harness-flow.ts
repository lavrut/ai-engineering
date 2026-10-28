import type { Diagram } from '../../lib/diagram';

// Layout (default node 150x56). Row 1 runs left to right: You, Entry, Context
// builder, Model. Row 2: Router under Model, then Permission gate. Row 3 snakes
// back right to left: Tool executor, Tools, Context manager. The Trace log is
// a tall rail on the right edge. The loop edge leaves Context manager to the
// left and runs round the outside of the drawing (left edge, then a lane
// above row 1) into the top of Context builder, so the end_turn path from
// Router back to Entry never has to cross it.
export const harnessFlow: Diagram = {
  id: 'harness-flow',
  title: 'The harness, step by step',
  width: 1200,
  height: 480,
  nodes: [
    { id: 'you', label: 'You', role: 'user', x: 30, y: 72, w: 110 },
    { id: 'entry', label: 'Entry / REPL', role: 'harness', x: 220, y: 72 },
    { id: 'context-builder', label: 'Context builder', role: 'harness', x: 410, y: 72 },
    { id: 'model', label: 'Model (Claude API)', role: 'model', x: 620, y: 72, w: 190 },
    { id: 'router', label: 'Router (stop_reason)', role: 'harness', x: 620, y: 232, w: 190 },
    { id: 'permission-gate', label: 'Permission gate', role: 'harness', x: 880, y: 232 },
    { id: 'tool-executor', label: 'Tool executor', role: 'harness', x: 880, y: 392 },
    { id: 'tools', label: 'Tools (workspace)', role: 'tool', x: 620, y: 392, w: 190 },
    { id: 'context-manager', label: 'Context manager', role: 'memory', x: 370, y: 392 },
    { id: 'trace-log', label: 'Trace log', role: 'logs', x: 1060, y: 72, w: 120, h: 376 },
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
    {
      id: 'e-loop',
      from: 'context-manager',
      to: 'context-builder',
      label: 'next turn',
      loop: true,
      via: [
        [14, 420],
        [14, 32],
        [485, 32],
      ],
    },
    { id: 'e-router-entry', from: 'router', to: 'entry', label: 'end_turn' },
    { id: 'e-entry-you', from: 'entry', to: 'you', label: 'answer' },
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
      caption: 'The context manager checks the token budget and compacts the history if it is too big. Then the loop goes back to step 2, to build the next request, and step 3 sends it.',
      code: { file: 'python', section: 7 },
    },
    {
      n: 10,
      highlight: ['router', 'e-router-entry', 'entry', 'e-entry-you', 'you'],
      caption: 'On end_turn the loop stops. The final answer is shown to you.',
      code: { file: 'python', section: 9 },
    },
  ],
};
