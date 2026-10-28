import type { Diagram } from '../../lib/diagram';

// Layout (default node 150x56). Row 1 runs left to right: model, agent, MCP client, lumen-read, the
// tickets. Row 2 sits under lumen-read: lumen-ops and the label file; the client's edge to lumen-ops
// drops from the client's bottom and turns right. Each caption ends with the section it links to
// (the test pins the number against the step's code link).
export const mcpArchitecture: Diagram = {
  id: 'mcp-architecture',
  title: 'The Lumen servers and the agent that uses them',
  width: 1170,
  height: 280,
  nodes: [
    { id: 'model', label: 'Claude (model)', role: 'model', x: 30, y: 40 },
    { id: 'agent', label: 'Agent (host)', role: 'harness', x: 270, y: 40 },
    { id: 'client', label: 'MCP client', role: 'harness', x: 510, y: 40 },
    { id: 'read', label: 'lumen-read', role: 'tool', x: 750, y: 40 },
    { id: 'tickets', label: 'Tickets', role: 'dataset', x: 990, y: 40 },
    { id: 'ops', label: 'lumen-ops', role: 'tool', x: 750, y: 190 },
    { id: 'labels', label: 'Label file', role: 'memory', x: 990, y: 190 },
  ],
  edges: [
    { id: 'e-model-agent', from: 'model', to: 'agent', label: 'tool_use' },
    { id: 'e-agent-client', from: 'agent', to: 'client', label: 'call' },
    { id: 'e-client-read', from: 'client', to: 'read', label: 'tools/call' },
    { id: 'e-read-tickets', from: 'read', to: 'tickets', label: 'reads' },
    { id: 'e-client-ops', from: 'client', to: 'ops', label: 'label_ticket', via: [[585, 218]] },
    { id: 'e-ops-labels', from: 'ops', to: 'labels', label: 'appends' },
  ],
  steps: [
    {
      n: 1,
      highlight: ['model', 'e-model-agent', 'agent'],
      caption:
        'The model never talks to a server. It asks for a tool by name. The agent turns each MCP tool into a harness tool, so the harness’s permission gate and executor treat it like any other. In the code: section 3.',
      code: { file: 'python', section: 3, source: 'mcp-agent-host' },
    },
    {
      n: 2,
      highlight: ['agent', 'e-agent-client', 'client'],
      caption:
        'The agent reaches a server through an MCP client: it connects (stdio, or Streamable HTTP with a bearer token), then discovers the tools and calls them. In the code: section 1.',
      code: { file: 'python', section: 1, source: 'mcp-client-connect' },
    },
    {
      n: 3,
      highlight: ['client', 'e-client-read', 'read'],
      caption:
        'lumen-read offers three tools, search_tickets, get_ticket and classify_complaint, all marked readOnlyHint. Ticket text comes back only inside a field named untrusted_body. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'mcp-read' },
    },
    {
      n: 4,
      highlight: ['read', 'e-read-tickets', 'tickets'],
      caption:
        'The same server also offers the ticket as a resource, lumen://tickets/{id}, and an eval report as lumen://reports/eval. Both read the shared ticket data. In the code: section 3.',
      code: { file: 'python', section: 3, source: 'mcp-read' },
    },
    {
      n: 5,
      highlight: ['client', 'e-client-ops', 'ops'],
      caption:
        'lumen-ops has one tool, label_ticket. Its first call does not write: after checking the arguments, the server asks one yes or no question. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'mcp-ops' },
    },
    {
      n: 6,
      highlight: ['ops', 'e-ops-labels', 'labels'],
      caption:
        'Only an accepted answer writes. The server appends one row that says who and how: source, client_id, and confirmed. A decline writes nothing. In the code: section 4.',
      code: { file: 'python', section: 4, source: 'mcp-ops' },
    },
  ],
};
