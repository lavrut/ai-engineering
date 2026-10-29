import type { Diagram } from '../../lib/diagram';

// The AI gateway's request path (infra/python/stages.py and gateway.py, with the Go twin of the chain).
// Layout: the programs on the left all call one URL; row 1 runs the first six stages left to right, out to the
// upstream (the replay proxy in the course, api.anthropic.com live); the answer comes back down into row 2, the
// ledger and trace stages, each with its store below it as a side output. A cache hit drops from the cache stage
// straight to the ledger. diagrams.test.ts pins every number a caption states against capstone.json; each caption
// ends with the section its step links to.
const STAGE_W = 96;
const ROW1 = 100;
const ROW2 = 260;
const stage = (id: string, label: string, x: number, y = ROW1) => ({ id, label, role: 'harness' as const, x, y, w: STAGE_W });
const program = (id: string, label: string, y: number) => ({ id, label, role: 'user' as const, x: 20, y, w: 120 });
const edge = (from: string, to: string, label?: string, via?: [number, number][]) => ({
  id: `e-${from}-${to}`,
  from,
  to,
  ...(label ? { label } : {}),
  ...(via ? { via } : {}),
});

export const infraGateway: Diagram = {
  id: 'infra-gateway',
  title: 'One gateway in front of the model API: eight stages, a ledger and a trace for every call',
  width: 1240,
  height: 480,
  nodes: [
    program('desk', 'The desk', 20),
    program('lab', 'The lab', 100),
    program('curl', 'A curl', 180),
    stage('tag', 'Tag', 170),
    stage('budget', 'Budget', 310),
    stage('cache', 'Cache', 450),
    stage('route', 'Route', 590),
    stage('limit', 'Limit', 730),
    stage('retry', 'Retry', 870),
    stage('ledger', 'Ledger', 870, ROW2),
    stage('trace', 'Trace', 650, ROW2),
    { id: 'upstream', label: 'Upstream (replay proxy)', role: 'model', x: 1016, y: ROW1, w: 204 },
    { id: 'ledger-store', label: 'Ledger rows', role: 'memory', x: 848, y: 400, w: 140 },
    { id: 'trace-store', label: 'Trace store', role: 'logs', x: 628, y: 400, w: 140 },
  ],
  edges: [
    edge('desk', 'tag'),
    edge('lab', 'tag'),
    edge('curl', 'tag'),
    edge('tag', 'budget'),
    edge('budget', 'cache'),
    edge('cache', 'route', 'miss'),
    edge('route', 'limit'),
    edge('limit', 'retry'),
    edge('retry', 'upstream'),
    edge('upstream', 'ledger', 'answer'),
    edge('ledger', 'trace'),
    edge('cache', 'ledger', 'hit', [
      [498, 210],
      [918, 210],
    ]),
    edge('ledger', 'ledger-store'),
    edge('trace', 'trace-store'),
  ],
  steps: [
    {
      n: 1,
      highlight: ['desk', 'e-desk-tag', 'lab', 'e-lab-tag', 'curl', 'e-curl-tag', 'tag'],
      caption:
        'Any program that speaks the Messages API can sit on the left: the desk, the lab, a curl. Each points its base URL at the gateway and says who it is in two headers, x-team and x-request-tag. The tag stage reads them first; a call with no team is untagged, and the course config refuses it. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'infra-stages' },
    },
    {
      n: 2,
      highlight: ['tag', 'e-tag-budget', 'budget'],
      caption:
        "Budget comes before anything that can spend. The team's spend so far plus this call's worst case is checked against the team's limit: allow, warn past the warn line, or refuse with an error that names the team and the limit. A refused call costs nothing and is still traced. In the code: section 3.",
      code: { file: 'python', section: 3, source: 'infra-stages' },
    },
    {
      n: 3,
      highlight: ['budget', 'e-budget-cache', 'cache', 'e-cache-ledger', 'ledger'],
      caption:
        'The cache keys a call by its path and body bytes and skips any call that carries tools. A hit answers at once, skips route, limit and retry, and books nothing: over the 957 calls of the course replay it answered 20, each a repeat of a call an earlier set had made. In the code: section 4.',
      code: { file: 'python', section: 4, source: 'infra-stages' },
    },
    {
      n: 4,
      highlight: ['cache', 'e-cache-route', 'route', 'e-route-limit', 'limit', 'e-limit-retry', 'retry', 'e-retry-upstream', 'upstream'],
      caption:
        "A miss is routed (to its target, then its fallbacks, or denied), rate limited per model, and sent upstream with retries and backoff. The body goes byte for byte: in the course the upstream is the replay proxy, which keys a call by those bytes; live, it is api.anthropic.com. In the code: section 7.",
      code: { file: 'python', section: 7, source: 'infra-stages' },
    },
    {
      n: 5,
      highlight: ['upstream', 'e-upstream-ledger', 'ledger', 'e-ledger-ledger-store', 'ledger-store', 'e-ledger-trace', 'trace', 'e-trace-trace-store', 'trace-store'],
      caption:
        "The answer comes back through the last two stages. The ledger prices the usage and books it to the team and the tag; the trace stage writes one line with every stage's decision. Both are side outputs: the caller gets the upstream's answer unchanged. In the code: section 3.",
      code: { file: 'python', section: 3, source: 'infra-gateway' },
    },
    {
      n: 6,
      highlight: ['ledger-store', 'e-ledger-ledger-store', 'ledger', 'trace-store', 'e-trace-trace-store', 'trace'],
      caption:
        "The capstone gate reads what the side outputs hold, after a replay of the course's recorded traffic: four lines, ledger, budgets, stages and figures, all green. In the code: section 3.",
      code: { file: 'python', section: 3, source: 'infra-replay' },
    },
  ],
};
