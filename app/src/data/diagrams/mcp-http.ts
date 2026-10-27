import type { Diagram } from '../../lib/diagram';

// Layout (default node 150x56). Row 1 is Go's order of checks: client, Host and Origin, body cap,
// token, handler. The resource metadata sits under the token check. Python's SDK checks the token
// before the Origin; step 5 says so. Code links: mcp/python/read_server.py section 5 and
// mcp/go/readserver/server.go section 5 for the HTTP app, and mcp/python/resource_auth.py with
// mcp/go/auth/auth.go for the token.
export const mcpHttp: Diagram = {
  id: 'mcp-http',
  title: 'A request to a Lumen server over HTTP',
  width: 1170,
  height: 280,
  nodes: [
    { id: 'client', label: 'Client', role: 'user', x: 30, y: 40 },
    { id: 'origin', label: 'Host and Origin', role: 'grader', x: 270, y: 40 },
    { id: 'cap', label: 'Body cap', role: 'grader', x: 510, y: 40 },
    { id: 'token', label: 'Bearer token', role: 'grader', x: 750, y: 40 },
    { id: 'handler', label: 'MCP handler', role: 'tool', x: 990, y: 40 },
    { id: 'meta', label: 'Resource metadata', role: 'dataset', x: 750, y: 190, w: 180 },
  ],
  edges: [
    { id: 'e-client-origin', from: 'client', to: 'origin', label: 'POST /mcp' },
    { id: 'e-origin-cap', from: 'origin', to: 'cap', label: 'allowed' },
    { id: 'e-cap-token', from: 'cap', to: 'token', label: 'in size' },
    { id: 'e-token-handler', from: 'token', to: 'handler', label: 'valid' },
    { id: 'e-token-meta', from: 'token', to: 'meta', label: '401' },
  ],
  steps: [
    {
      n: 1,
      highlight: ['client', 'e-client-origin', 'origin'],
      caption:
        'Each POST to /mcp stands alone: the servers are stateless. They bind 127.0.0.1 only, and a request with a bad Host or Origin gets 403, for every method. In the code: section 5.',
      code: { file: 'python', section: 5, source: 'mcp-read' },
    },
    {
      n: 2,
      highlight: ['origin', 'e-origin-cap', 'cap'],
      caption:
        'A body over 131,072 bytes is refused before it is read in full. Go answers 413; both servers set the same cap. In the code: section 5.',
      code: { file: 'python', section: 5, source: 'mcp-read' },
    },
    {
      n: 3,
      highlight: ['cap', 'e-cap-token', 'token'],
      caption:
        'The token is checked for signature, issuer and expiry, then for its audience: it must name this server’s own URI. Only then is its scope looked at, so a token minted for lumen-read is simply invalid at lumen-ops. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'mcp-auth' },
    },
    {
      n: 4,
      highlight: ['token', 'e-token-meta', 'meta'],
      caption:
        'A missing or invalid token gets 401 and a WWW-Authenticate header that points at the server’s protected-resource metadata. A valid token without the scope gets 403. In the code: section 3.',
      code: { file: 'python', section: 3, source: 'mcp-auth' },
    },
    {
      n: 5,
      highlight: ['token', 'e-token-handler', 'handler'],
      caption:
        'Only a request that passes every check reaches the handler. The order differs by language: Go checks Origin, then the body cap, then the token; Python’s SDK checks the token before the Origin, so a bad Origin with no token is 401 in Python and 403 in Go. In the code: section 5.',
      code: { file: 'python', section: 5, source: 'mcp-read' },
    },
  ],
};
