import type { Diagram } from '../../lib/diagram';

// The data fabric of this course, as data/python/catalog.py's lineage.json records it: the 12 catalogued
// datasets in the middle column, the tracks that write them on the left (p-*), the tracks that read them on the
// right (c-*; a lineage consumer such as mcp-read-server folds into its track), and the contract an edge travels
// under as its label. tickets_v2 sits under tickets, joined by the migration edge. diagrams.test.ts rebuilds the
// edges from lineage.json, so the drawing cannot drift from the data. Each linked caption ends with its section.
const DS_X = 430;
const DS_W = 220;
const ds = (id: string, y: number) => ({ id, label: id, role: 'dataset' as const, x: DS_X, y, w: DS_W });
const producer = (track: string, label: string, y: number) => ({ id: `p-${track}`, label, role: 'tool' as const, x: 30, y, w: 170 });
const consumer = (track: string, label: string, y: number) => ({ id: `c-${track}`, label, role: 'user' as const, x: 880, y, w: 170 });
const edge = (from: string, to: string, label?: string) => ({ id: `e-${from}-${to}`, from, to, ...(label ? { label } : {}) });

export const dataFabric: Diagram = {
  id: 'data-fabric',
  title: 'The course as a data fabric: who writes each dataset, who reads it, and the contracts between them',
  width: 1080,
  height: 910,
  nodes: [
    producer('llms', 'llms writes', 200),
    producer('data', 'data writes', 130),
    producer('ml', 'ml writes', 410),
    producer('mcp', 'mcp writes', 620),
    producer('agents', 'agents writes', 760),
    ds('tickets', 20),
    ds('tickets_v2', 130),
    ds('model_responses', 200),
    ds('reply_labels', 270),
    ds('complaints', 340),
    ds('category_map', 410),
    ds('ml_registry', 480),
    ds('ml_reports', 550),
    ds('mcp_derived', 620),
    ds('mcp_recordings', 690),
    ds('agents_traces', 760),
    ds('agents_derived', 830),
    consumer('mcp', 'mcp reads', 20),
    consumer('agents', 'agents reads', 130),
    consumer('llms', 'llms reads', 235),
    consumer('ml', 'ml reads', 445),
    consumer('app', 'app reads', 655),
  ],
  edges: [
    edge('p-llms', 'tickets', 'tickets.v1'),
    edge('p-llms', 'model_responses'),
    edge('p-llms', 'reply_labels'),
    edge('p-data', 'tickets_v2', 'tickets.v2'),
    edge('p-ml', 'complaints', 'complaints.v1'),
    edge('p-ml', 'category_map'),
    edge('p-ml', 'ml_registry'),
    edge('p-ml', 'ml_reports'),
    edge('p-mcp', 'mcp_derived'),
    edge('p-mcp', 'mcp_recordings'),
    edge('p-agents', 'agents_traces', 'agent-traces.v1'),
    edge('p-agents', 'agents_derived'),
    edge('tickets', 'tickets_v2', 'tickets.v1, migrated'),
    edge('tickets', 'c-mcp', 'tickets.v1'),
    edge('tickets', 'c-agents', 'tickets.v1'),
    edge('tickets_v2', 'c-agents', 'tickets.v2'),
    edge('model_responses', 'c-llms'),
    edge('model_responses', 'c-app'),
    edge('reply_labels', 'c-llms'),
    edge('reply_labels', 'c-app'),
    edge('complaints', 'c-mcp', 'complaints.v1'),
    edge('category_map', 'c-ml'),
    edge('category_map', 'c-app'),
    edge('ml_registry', 'c-mcp'),
    edge('ml_registry', 'c-ml'),
    edge('ml_reports', 'c-mcp'),
    edge('ml_reports', 'c-app'),
    edge('mcp_derived', 'c-app'),
    edge('mcp_recordings', 'c-mcp'),
    edge('mcp_recordings', 'c-agents'),
    edge('agents_traces', 'c-agents', 'agent-traces.v1'),
    edge('agents_derived', 'c-app'),
  ],
  steps: [
    {
      n: 1,
      highlight: ['p-llms', 'e-p-llms-tickets', 'tickets', 'e-tickets-c-mcp', 'c-mcp'],
      caption:
        'A fabric is the map of who writes what and who reads it: 12 datasets across the course, written by 5 tracks and read by the tracks and the app. Each line is a read or a write that lineage.json records; a label on a line is the contract the data travels under.',
    },
    {
      n: 2,
      highlight: ['p-ml', 'e-p-ml-complaints', 'complaints', 'e-p-ml-category_map', 'category_map', 'e-p-ml-ml_registry', 'ml_registry', 'e-p-ml-ml_reports', 'ml_reports'],
      caption:
        'The catalogue lists every dataset once: its path, files, rows, a schema sketch, the track that writes it and the tracks that read it. ml writes four of them; only complaints has a contract. In the code: section 3.',
      code: { file: 'python', section: 3, source: 'data-catalog' },
    },
    {
      n: 3,
      highlight: ['p-llms', 'e-p-llms-tickets', 'tickets', 'e-tickets-c-mcp', 'c-mcp', 'e-tickets-c-agents', 'c-agents'],
      caption:
        'A contract rides on the edge: tickets.v1 is what llms promises and what the MCP read server and the agents eval rely on. The consumer is named inside the contract, in its consumers section, so the producer knows whom a change would break. In the code: section 5.',
      code: { file: 'python', section: 5, source: 'data-contract-tickets' },
    },
    {
      n: 4,
      highlight: ['p-agents', 'e-p-agents-agents_traces', 'agents_traces', 'e-agents_traces-c-agents', 'c-agents'],
      caption:
        'Quality and freshness are checked where the data lives: quality.py runs each contract over its files and writes one verdict per rule to verdicts.json, the agent traces once per trace set. In the code: section 4.',
      code: { file: 'python', section: 4, source: 'data-quality' },
    },
    {
      n: 5,
      highlight: ['p-ml', 'e-p-ml-complaints', 'complaints', 'e-complaints-c-mcp', 'c-mcp'],
      caption:
        'The same rules run in Python and in Go, verdict for verdict: required, type, enum and pattern for each field, then the quality rules and freshness. The Go rules are in rules.go, section 7. In the code: section 3.',
      code: { file: 'python', section: 3, source: 'data-contract', goSection: 7 },
    },
    {
      n: 6,
      highlight: [
        'tickets', 'e-tickets-tickets_v2', 'tickets_v2', 'e-p-data-tickets_v2', 'p-data', 'e-tickets_v2-c-agents', 'c-agents', 'e-tickets-c-mcp', 'c-mcp',
        'e-tickets-c-agents',
      ],
      caption:
        'A breaking change: tickets.v2 renames priority to urgency. The v1 file stays, and a migration writes the v2 file beside it. The MCP read server and the agents eval have not moved: both stay on v1 until its sunset, 2027-01-31. A shim reads the v2 file to show what that move will look like for the agents eval. The gate is green only when every consumer passes the version it declares. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'data-gate' },
    },
  ],
};
