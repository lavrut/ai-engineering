"""The resource-server half of OAuth for lumen-read (HTTP) and lumen-ops (Python).

Both servers are OAuth 2.1 resource servers: they never issue tokens, they only check them. A token
is accepted when it is signed by the test AS, unexpired, from the expected issuer, and its audience
(RFC 8707) names this server's canonical URI. The SDK then checks the scope (403 if missing) and
answers 401 with `WWW-Authenticate: ... resource_metadata="..."` for a missing or rejected token.
No token is ever passed on to anything else: the servers call no upstream API.

Layering (one check, one place):
  - JWTVerifier: signature, `iss`, `exp`/`nbf`/`iat`, and the claims we need. It REPORTS `aud` as
    AccessToken.resource; it does not judge it.
  - AuthSettings(validate_token_resource=True): the SDK compares that resource with this server's
    canonical URI, and rejects (401) on mismatch.
  - AuthSettings(required_scopes=[...]): the SDK's 403 for a missing scope.

The Go servers do the same in mcp/go/auth/auth.go, where the audience check lives in the verifier
(ruling R7: the Go SDK's middleware does not check audience).
"""
from __future__ import annotations

import jwt
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

# ── 1. Configuration ────────────────────────────────────────────────────────
ISSUER = "http://127.0.0.1:9000"  # the local test AS (auth_server.py)
READ_RESOURCE = "http://127.0.0.1:8765/mcp"  # lumen-read's canonical URI (the token audience)
OPS_RESOURCE = "http://127.0.0.1:8766/mcp"  # lumen-ops's canonical URI
SCOPE_READ = "tickets:read"
SCOPE_WRITE = "tickets:write"
LEEWAY_SECONDS = 30  # clock skew allowed on exp / nbf / iat

# TEST-ONLY signing key, shared by the test AS and the resource servers in both languages (Go keeps
# the same string in mcp/go/auth/auth.go). It is public: anyone who reads it can mint tokens, which
# is acceptable only for a loopback lab. Production uses an asymmetric key (RS256/ES256) published
# by the AS as a JWKS, so resource servers hold no secret at all.
TEST_SIGNING_KEY = "lumen-TEST-ONLY-hs256-key-public-do-not-use-in-production"


# ── 2. Token verifier ───────────────────────────────────────────────────────
class JWTVerifier:
    """Verify an HS256 access token from the test AS. Returns None (the SDK answers 401) for any
    token that is malformed, badly signed, from another issuer, expired or not yet valid, or that
    lacks a client_id, subject or audience."""

    def __init__(self, resource: str) -> None:
        self.resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(token, TEST_SIGNING_KEY, algorithms=["HS256"], issuer=ISSUER, leeway=LEEWAY_SECONDS,
                                options={"verify_aud": False, "require": ["exp", "iss", "aud", "sub"]})
        except jwt.PyJWTError:
            return None
        client_id, subject = claims.get("client_id"), claims.get("sub")
        if not isinstance(client_id, str) or not client_id or not isinstance(subject, str) or not subject:
            return None
        scope = claims.get("scope", "")
        if not isinstance(scope, str):
            return None
        return AccessToken(token=token, client_id=client_id, scopes=scope.split(), expires_at=int(claims["exp"]),
                           resource=self._resource(claims["aud"]), subject=subject, claims={"iss": claims["iss"]})

    def _resource(self, aud: object) -> str | None:
        """RFC 7519: aud is a string or a list of strings. Report the entry naming this server if
        there is one, else what the token names; the SDK makes the decision (validate_token_resource)."""
        if isinstance(aud, str):
            return aud
        if isinstance(aud, list) and all(isinstance(a, str) for a in aud):
            if self.resource in aud:
                return self.resource
            return aud[0] if aud else None
        return None


# ── 3. Resource-server settings ─────────────────────────────────────────────
def resource_server(resource: str, scope: str) -> dict:
    """Keyword arguments for MCPServer(...) that make it a resource server for `resource`."""
    return {
        "token_verifier": JWTVerifier(resource),
        "auth": AuthSettings(issuer_url=ISSUER, resource_server_url=resource, required_scopes=[scope],
                             validate_token_resource=True),  # explicit: unset warns and means False
    }
