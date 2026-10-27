"""The resource-server half of OAuth for lumen-read (HTTP) and lumen-ops (Python).

Both servers are OAuth 2.1 resource servers: they never issue tokens, they only check them. A token
is accepted when it is signed by the test AS, unexpired, from the expected issuer, and its audience
(RFC 8707) names this server's canonical URI. The SDK then checks the scope (403 if missing) and
answers 401 with `WWW-Authenticate: ... resource_metadata="..."` for a missing or rejected token.
No token is ever passed on to anything else: the servers call no upstream API.

Layering (one check, one place), in the order the checks run (ruling R24):
  1. JWTVerifier: signature, `iss`, `exp`/`nbf`/`iat`, and the claims we need. It REPORTS `aud` as
     AccessToken.resource; it does not judge it.
  2. AuthSettings(validate_token_resource=True): the SDK's bearer backend compares that resource
     with this server's canonical URI, and rejects (401) on mismatch.
  3. AuthSettings(required_scopes=[...]): the SDK's 403 for a missing scope.
Audience before scope, as the spec says: a token issued for another server is an invalid token
here (401), whatever scopes it carries. So lumen-read's token at lumen-ops is 401, not 403.

The signing key is never in the source (ruling R25). The test AS writes 32 random bytes to
mcp/data/dev-signing.key (git-ignored, mode 0600) on first start; the resource servers read that
file (or the one named by LUMEN_SIGNING_KEY_FILE) and refuse to start without it.

The Go servers do the same in mcp/go/auth/auth.go, where the audience check lives in the verifier
(ruling R7: the Go SDK's middleware does not check audience).
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

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
KEY_FILE_ENV = "LUMEN_SIGNING_KEY_FILE"
DEFAULT_KEY_FILE = Path(__file__).resolve().parents[1] / "data" / "dev-signing.key"
KEY_BYTES = 32


class SigningKeyMissing(RuntimeError):
    """No usable signing key file. Never answered with a default key: the caller stops."""


def key_file() -> Path:
    """The key file: $LUMEN_SIGNING_KEY_FILE, else mcp/data/dev-signing.key."""
    return Path(os.environ.get(KEY_FILE_ENV) or DEFAULT_KEY_FILE)


def load_signing_key(path: Path | None = None) -> bytes:
    """The HS256 key the test AS signs with: 32 bytes, stored as one line of hex, readable by the
    owner only. HS256 is symmetric, so whoever holds this file can mint tokens; that is acceptable
    only on a loopback lab. Production uses an asymmetric key (RS256/ES256) that the AS publishes
    as a JWKS, so resource servers hold no secret at all. The Go twin is auth.LoadSigningKey."""
    path = path or key_file()
    fix = f"start the test AS first (python mcp/python/auth_server.py), or set {KEY_FILE_ENV}"
    try:
        mode = path.stat().st_mode
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        raise SigningKeyMissing(f"signing key file {path} not found or unreadable: {fix}") from None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SigningKeyMissing(f"signing key file {path} is readable by others: chmod 600 {path}")
    try:
        key = bytes.fromhex(text)
    except ValueError:
        key = b""
    if len(key) != KEY_BYTES:
        raise SigningKeyMissing(f"signing key file {path} must hold {KEY_BYTES} bytes as hex: {fix}")
    return key


# ── 2. Token verifier ───────────────────────────────────────────────────────
class JWTVerifier:
    """Verify an HS256 access token from the test AS (check 1 of 3). Returns None (the SDK answers
    401) for any token that is malformed, badly signed, from another issuer, expired or not yet
    valid, or that lacks a client_id, subject or audience. The key is read once, at construction,
    so a server without a key file fails at startup, not on its first request."""

    def __init__(self, resource: str, key: bytes | None = None) -> None:
        self.resource = resource
        self._key = key if key is not None else load_signing_key()

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(token, self._key, algorithms=["HS256"], issuer=ISSUER, leeway=LEEWAY_SECONDS,
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
        there is one, else what the token names. The SDK makes the decision (check 2,
        validate_token_resource) while authenticating the request, before any scope is looked at."""
        if isinstance(aud, str):
            return aud
        if isinstance(aud, list) and all(isinstance(a, str) for a in aud):
            if self.resource in aud:
                return self.resource
            return aud[0] if aud else None
        return None


# ── 3. Resource-server settings ─────────────────────────────────────────────
def resource_server(resource: str, scope: str) -> dict:
    """Keyword arguments for MCPServer(...) that make it a resource server for `resource`.
    Raises SigningKeyMissing when there is no key file."""
    return {
        "token_verifier": JWTVerifier(resource),
        "auth": AuthSettings(issuer_url=ISSUER, resource_server_url=resource,
                             # check 3, by RequireAuthMiddleware, only on a token that passed 1 and 2: 403
                             required_scopes=[scope],
                             # check 2, in the bearer backend: wrong audience -> no user -> 401.
                             # Explicit: unset warns and means False.
                             validate_token_resource=True),
    }
