import type { Diagram } from '../../lib/diagram';

// The record/replay proxy every engine talks to (agents/proxy/: proxy.py, proxy_cost.py, proxy_handler.py and
// proxy_server.py, Python only).
// Layout: row 1 runs engine -> proxy -> the record-mode gate -> api.anthropic.com; the store sits
// under the proxy. Replay is the store loop on the left; recording runs right through the gate and
// comes back down into the store. Each caption ends with the section it links to (the test pins the
// number against the step's code link).
export const replayProxy: Diagram = {
  id: 'replay-proxy',
  title: 'Record once, replay for free',
  width: 1170,
  height: 300,
  nodes: [
    { id: 'engine', label: 'Engine (any desk)', role: 'harness', x: 30, y: 40, w: 180 },
    { id: 'proxy', label: 'Proxy on 127.0.0.1', role: 'harness', x: 290, y: 40, w: 190 },
    { id: 'gate', label: 'y/N, cap, key', role: 'user', x: 560, y: 40, w: 190 },
    { id: 'api', label: 'api.anthropic.com', role: 'model', x: 830, y: 40, w: 200 },
    { id: 'store', label: 'Recordings', role: 'memory', x: 290, y: 210, w: 190 },
  ],
  edges: [
    { id: 'e-engine-proxy', from: 'engine', to: 'proxy', label: 'request' },
    { id: 'e-proxy-engine', from: 'proxy', to: 'engine', label: 'response' },
    { id: 'e-proxy-store', from: 'proxy', to: 'store', label: 'key' },
    { id: 'e-store-proxy', from: 'store', to: 'proxy', label: 'recording' },
    { id: 'e-proxy-gate', from: 'proxy', to: 'gate', label: 'record mode only' },
    { id: 'e-gate-api', from: 'gate', to: 'api', label: 'paid call' },
    { id: 'e-api-store', from: 'api', to: 'store', label: 'save' },
  ],
  steps: [
    {
      n: 1,
      highlight: ['engine', 'e-engine-proxy', 'proxy'],
      caption:
        'Every engine sets ANTHROPIC_BASE_URL to http://127.0.0.1:8790, so its Claude calls go to the proxy and it never knows the difference. The proxy listens on the loopback address only. In the code: section 1.',
      code: { file: 'python', section: 1, source: 'agents-proxy' },
    },
    {
      n: 2,
      highlight: ['proxy', 'e-proxy-store', 'store'],
      caption:
        'The proxy turns each request into a key: the SHA-256 of canonical JSON over the method, the path and the body, with volatile fields removed. Headers are never part of it. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'agents-proxy' },
    },
    {
      n: 3,
      highlight: ['store', 'e-store-proxy', 'proxy', 'e-proxy-engine', 'engine'],
      caption:
        'Replay is the default. A key that is in the store is answered from disk: free, offline and the same bytes every time, streamed replies included. In the code: section 4.',
      code: { file: 'python', section: 4, source: 'agents-proxy' },
    },
    {
      n: 4,
      highlight: ['proxy', 'e-proxy-engine', 'engine'],
      caption:
        'A key that is not in the store gets a 404 replay_miss that names the command to record it. A replay miss is never forwarded to the API. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'agents-proxy-handler' },
    },
    {
      n: 5,
      highlight: ['proxy', 'e-proxy-gate', 'gate'],
      caption:
        'Record mode is the only way out. It prints a cost estimate first and asks y/N; input that is not a terminal counts as no, and nothing is called. In the code: section 1.',
      code: { file: 'python', section: 1, source: 'agents-proxy-cost' },
    },
    {
      n: 6,
      highlight: ['gate', 'e-gate-api', 'api'],
      caption:
        'Before every paid call the proxy checks the cap: it refuses when what it spent plus the worst case would pass --max-cost (10 dollars by default). The API key comes from the environment only and is never logged or stored. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'agents-proxy-handler' },
    },
    {
      n: 7,
      highlight: ['api', 'e-api-store', 'store'],
      caption:
        'The reply is saved under its key with no auth headers, and replay serves it from then on. Recording the same key again replaces it: the latest wins. In the code: section 3.',
      code: { file: 'python', section: 3, source: 'agents-proxy' },
    },
  ],
};
