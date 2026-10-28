import type { Diagram } from '../../lib/diagram';

// Layout (default node 150x56). Row 1: the command, connecting, discovering, calling, the JSON
// printed. The answerer sits under "Call a tool". Code links point at mcp/python/client.py and
// mcp/go/client/main.go (same section numbers); each caption ends with its section.
export const mcpClient: Diagram = {
  id: 'mcp-client',
  title: 'What the Lumen client does',
  width: 1170,
  height: 280,
  nodes: [
    { id: 'cmd', label: 'Command', role: 'user', x: 30, y: 40 },
    { id: 'connect', label: 'Connect', role: 'harness', x: 270, y: 40 },
    { id: 'discover', label: 'Discover', role: 'tool', x: 510, y: 40 },
    { id: 'call', label: 'Call a tool', role: 'tool', x: 750, y: 40 },
    { id: 'output', label: 'JSON on stdout', role: 'logs', x: 990, y: 40 },
    { id: 'answer', label: 'Answerer', role: 'user', x: 750, y: 190 },
  ],
  edges: [
    { id: 'e-cmd-connect', from: 'cmd', to: 'connect', label: 'flags' },
    { id: 'e-connect-discover', from: 'connect', to: 'discover', label: 'session' },
    { id: 'e-discover-call', from: 'discover', to: 'call', label: 'tool names' },
    { id: 'e-call-output', from: 'call', to: 'output', label: 'result' },
    { id: 'e-call-answer', from: 'call', to: 'answer', label: 'input_required' },
  ],
  steps: [
    {
      n: 1,
      highlight: ['cmd', 'e-cmd-connect', 'connect'],
      caption:
        'One command per run. The flags pick the server (here --stdio, which starts lumen-read as a child process) and how to answer a question (--yes, --no or ask). In the code: section 1.',
      code: { file: 'python', section: 1, source: 'mcp-client' },
    },
    {
      n: 2,
      highlight: ['connect', 'e-connect-discover', 'discover'],
      caption:
        'The client connects: over stdio it starts the process and exchanges messages on its input and output. The HTTP path through this section is lesson 7. In the code: section 1.',
      code: { file: 'python', section: 1, source: 'mcp-client-connect' },
    },
    {
      n: 3,
      highlight: ['discover', 'e-discover-call', 'call'],
      caption:
        'Discovery lists the tools, resources, resource templates and prompts, sorted by name. Schemas are cut down to their shape so the Python and Go clients print the same bytes. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'mcp-client' },
    },
    {
      n: 4,
      highlight: ['call', 'e-call-output', 'output'],
      caption:
        'A call prints one JSON shape: {"structured": ...} on success, {"error": text} when the tool reports an error. Resources and prompts print their own shapes. In the code: section 1.',
      code: { file: 'python', section: 1, source: 'mcp-client-calls' },
    },
    {
      n: 5,
      highlight: ['call', 'e-call-answer', 'answer'],
      caption:
        'A server that needs a confirmation answers with input_required instead of a result. The client asks its answerer, and retries with the answer. The answer is the client’s own claim: the server cannot tell a person from --yes. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'mcp-client-calls' },
    },
  ],
};
