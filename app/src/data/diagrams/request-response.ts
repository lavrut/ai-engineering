import type { Diagram } from '../../lib/diagram';

// Layout (default node 150x56). One row left to right: harness, request, API,
// response. The three parts of the response fan out on a second row below it.
export const requestResponse: Diagram = {
  id: 'request-response',
  title: 'One request, one response',
  width: 940,
  height: 320,
  nodes: [
    { id: 'harness', label: 'Your harness', role: 'harness', x: 30, y: 60, w: 140 },
    { id: 'request', label: 'Request', role: 'memory', x: 220, y: 60 },
    { id: 'api', label: 'Model (Claude API)', role: 'model', x: 420, y: 60, w: 190 },
    { id: 'response', label: 'Response', role: 'memory', x: 660, y: 60 },
    { id: 'content', label: 'content blocks', role: 'memory', x: 480, y: 220, w: 150 },
    { id: 'stop-reason', label: 'stop_reason', role: 'memory', x: 660, y: 220, w: 130 },
    { id: 'usage', label: 'usage', role: 'memory', x: 820, y: 220, w: 90 },
  ],
  edges: [
    { id: 'e-harness-request', from: 'harness', to: 'request', label: 'builds' },
    { id: 'e-request-api', from: 'request', to: 'api', label: 'POST' },
    { id: 'e-api-response', from: 'api', to: 'response' },
    { id: 'e-response-content', from: 'response', to: 'content' },
    { id: 'e-response-stop', from: 'response', to: 'stop-reason' },
    { id: 'e-response-usage', from: 'response', to: 'usage' },
  ],
  steps: [
    { n: 1, highlight: ['harness', 'e-harness-request', 'request'], caption: 'Your code builds a request. It is just a JSON body.' },
    {
      n: 2,
      highlight: ['request', 'e-request-api', 'api'],
      caption: 'The request carries five things: model, system (the instructions), messages (the history), tools (the schemas), and max_tokens (the reply size limit).',
      code: { file: 'python', section: 6 },
    },
    { n: 3, highlight: ['api', 'e-api-response', 'response'], caption: 'The model reads the request and writes one reply. It remembers nothing between calls, so you send the history every time.' },
    { n: 4, highlight: ['response', 'e-response-content', 'content'], caption: 'The reply holds a list of content blocks. A block is either text or a tool_use request.' },
    {
      n: 5,
      highlight: ['response', 'e-response-stop', 'stop-reason', 'e-response-usage', 'usage'],
      caption: 'stop_reason says why it stopped, such as end_turn or tool_use. usage counts the input and output tokens you were billed for.',
    },
  ],
};
