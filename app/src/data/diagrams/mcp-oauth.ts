import type { Diagram } from '../../lib/diagram';

// Layout (default node 150x56). Row 1: the lumen-read resource server, the client, the test
// authorization server. The signing key file sits under the resource server; the authorization
// server's edge to it drops from its bottom and runs left along y = 218. Code links: resource_auth.py
// and auth.go (source mcp-auth, sections 1-3 in both) and auth_server.py (Python only, sections 1-5).
export const mcpOauth: Diagram = {
  id: 'mcp-oauth',
  title: 'How a client gets a token for lumen-read',
  width: 870,
  height: 280,
  nodes: [
    { id: 'rs', label: 'lumen-read (resource server)', role: 'tool', x: 30, y: 40, w: 210 },
    { id: 'client', label: 'Client', role: 'user', x: 330, y: 40 },
    { id: 'as', label: 'Test authorization server', role: 'harness', x: 630, y: 40, w: 210 },
    { id: 'key', label: 'Signing key file', role: 'memory', x: 30, y: 190, w: 210 },
  ],
  edges: [
    { id: 'e-client-rs', from: 'client', to: 'rs', label: 'request' },
    { id: 'e-client-as', from: 'client', to: 'as', label: 'authorize, token' },
    { id: 'e-key-rs', from: 'key', to: 'rs', label: 'verifies with' },
    { id: 'e-as-key', from: 'as', to: 'key', label: 'signs with', via: [[735, 218]] },
  ],
  steps: [
    {
      n: 1,
      highlight: ['client', 'e-client-rs', 'rs'],
      caption:
        'With no token, the server answers 401 and a WWW-Authenticate header naming its protected-resource metadata, which says which authorization server to ask. The scope it will need is tickets:read. In the code: section 3.',
      code: { file: 'python', section: 3, source: 'mcp-auth' },
    },
    {
      n: 2,
      highlight: ['client', 'e-client-as', 'as'],
      caption:
        'The client reads the authorization server’s metadata and uses its client id. The pre-registered public client lumen-cli is the main path; dynamic client registration stays on as the second. In the code: section 4.',
      code: { file: 'python', section: 4, source: 'mcp-auth-server' },
    },
    {
      n: 3,
      highlight: ['client', 'e-client-as', 'as'],
      caption:
        'The client sends an authorization code request with PKCE (S256 only) and names the server it wants a token for (RFC 8707). The test server approves every request on its own, so it is for 127.0.0.1 and nothing else. In the code: section 3.',
      code: { file: 'python', section: 3, source: 'mcp-auth-server' },
    },
    {
      n: 4,
      highlight: ['as', 'e-as-key', 'key'],
      caption:
        'The token it issues is signed with a key from a file and its audience is that one server. The key is never in the source: on first start the authorization server writes 32 random bytes to a git-ignored file. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'mcp-auth-server' },
    },
    {
      n: 5,
      highlight: ['key', 'e-key-rs', 'rs'],
      caption:
        'The resource server reads the same key file and refuses to start without it. It issues no tokens and passes none on: it only checks them. In the code: section 1.',
      code: { file: 'python', section: 1, source: 'mcp-auth' },
    },
    {
      n: 6,
      highlight: ['client', 'e-client-rs', 'rs'],
      caption:
        'The client retries with Authorization: Bearer. The server checks signature, issuer and expiry, then the audience, then the scope: a token for another server is 401, a right-audience token without tickets:read is 403. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'mcp-auth' },
    },
  ],
};
