"""TEST ONLY: a local OAuth 2.1 authorization server for the Lumen MCP servers (Python).

    *** Never run this anywhere but 127.0.0.1, and never for real users. ***
    It AUTO-APPROVES every authorization request with no login page and one fixed test subject
    (TEST_SUBJECT = "user-1").

The signing key: on first start it writes 32 random bytes (hex) to mcp/data/dev-signing.key, mode
0600 and git-ignored (or to $LUMEN_SIGNING_KEY_FILE), and reuses that file afterwards. The resource
servers read the same file. No key is ever written in source code. HS256 is symmetric, so the file
is a secret: a production AS signs with an asymmetric key and publishes only the public half (JWKS).

What it does do properly, because that is what the lessons teach:
  - Authorization code + PKCE (S256 only) for public clients (token_endpoint_auth_method "none").
  - Registration (ruling R4): the pre-registered public client `lumen-cli` is the primary path;
    Dynamic Client Registration (/register) stays on as the secondary path. Client ID Metadata
    Documents are not implemented: the SDK's AS cannot advertise or fetch them, and the SDK client
    accepts only an https client-document URL, which a loopback lab cannot host.
  - Audience binding (RFC 8707): /authorize must name a resource this AS serves (lumen-read or
    lumen-ops); the access token's `aud` is that resource, so a token is good for one server only.
  - RFC 9207: the redirect carries `iss`, which the SDK client checks.
  - The /token request's `resource`, when sent, must equal the one the code (or refresh token) was
    issued for, else invalid_target (the SDK parses it but does not compare it: _guard_token_resource).
  - Codes are single-use, short-lived, bound to their client, and burned by ANY redemption attempt,
    including a failed PKCE check or a wrong redirect_uri. Refresh tokens rotate, expire, and keep
    the code's audience.

It is built from the SDK's own routes (mcp.server.auth.routes.create_auth_routes): metadata at
/.well-known/oauth-authorization-server, /authorize, /token and /register. mint_test_token() is a
plain function for tests and is never routed.

Run:  python mcp/python/auth_server.py      (serves http://127.0.0.1:9000)
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs

import jwt
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
                                      RefreshToken, TokenError, construct_redirect_uri)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.applications import Starlette

from resource_auth import (ISSUER, KEY_BYTES, OPS_RESOURCE, READ_RESOURCE, SCOPE_READ, SCOPE_WRITE,
                           SigningKeyMissing, key_file, load_signing_key)

# ── 1. Configuration ────────────────────────────────────────────────────────
HOST, PORT = "127.0.0.1", 9000
RESOURCES = (READ_RESOURCE, OPS_RESOURCE)  # the only audiences this AS will put in a token
SCOPES = [SCOPE_READ, SCOPE_WRITE]
TEST_SUBJECT = "user-1"  # TEST ONLY: every approval is for this one fixed user
ACCESS_TTL = 600  # seconds
CODE_TTL = 300
REFRESH_TTL = 24 * 3600
MAX_TOKEN_REQUEST = 16 * 1024  # bytes: a /token form is a few hundred
CLI_REDIRECT = "http://127.0.0.1:33418/callback"  # redirect URIs match exactly, port included

# The pre-registered public client (R4 primary): no secret, so PKCE is what protects the code.
LUMEN_CLI = OAuthClientInformationFull(
    client_id="lumen-cli", client_name="Lumen CLI", redirect_uris=[AnyUrl(CLI_REDIRECT)],
    token_endpoint_auth_method="none", grant_types=["authorization_code", "refresh_token"],
    response_types=["code"], scope=" ".join(SCOPES))


# ── 2. Signing key and tokens ───────────────────────────────────────────────
def ensure_signing_key(path: Path | None = None) -> bytes:
    """Create the key file on first start (32 random bytes as hex, mode 0600), or load it.
    The key is written in full to a temp file in the same directory (mkstemp: mode 0600), then
    hard-linked into place. os.link fails if the file exists, so two AS processes starting at once
    cannot both create one, and no reader ever sees a partly written file."""
    path = path or key_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".dev-signing.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(secrets.token_hex(KEY_BYTES) + "\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass  # another process won the race: use its key
        finally:
            os.unlink(tmp)
    return load_signing_key(path)


def mint_test_token(scope: str, audience: str, client_id: str = "lumen-cli", sub: str = TEST_SUBJECT) -> str:
    """For tests only (never exposed over HTTP): a signed access token as this AS would issue it,
    without the browser round trip. `audience` is the resource URI the token is good for. Signed
    with the key file's key (raises SigningKeyMissing without one)."""
    now = int(time.time())
    return jwt.encode({"iss": ISSUER, "aud": audience, "sub": sub, "client_id": client_id, "scope": scope,
                       "iat": now, "exp": now + ACCESS_TTL}, load_signing_key(), algorithm="HS256")


# ── 3. The provider (in memory) ─────────────────────────────────────────────
class TestOnlyProvider:
    """The SDK's OAuthAuthorizationServerProvider, in memory. TEST ONLY: authorize() approves at
    once for TEST_SUBJECT. Resource servers verify the JWT themselves, so load_access_token is unused."""

    __test__ = False  # not a pytest class

    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientInformationFull] = {LUMEN_CLI.client_id: LUMEN_CLI}
        self.codes: dict[str, AuthorizationCode] = {}
        self.refresh: dict[str, RefreshToken] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:  # DCR (R4 secondary)
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource not in RESOURCES:
            raise AuthorizeError("invalid_target", "resource must name lumen-read or lumen-ops (RFC 8707)")
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or [], expires_at=time.time() + CODE_TTL, client_id=client.client_id,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource, subject=TEST_SUBJECT)
        # TEST ONLY: no login, no consent screen. A real AS authenticates the user here.
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state, iss=ISSUER)

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        # The SDK calls this BEFORE it checks expiry, redirect_uri and PKCE. Popping here makes every
        # redemption attempt burn the code, so a failed one (a guessed verifier, another client)
        # cannot be retried. A code presented by another client is invalid_grant.
        code = self.codes.pop(authorization_code, None)
        return code if code is not None and code.client_id == client.client_id else None

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        self.codes.pop(authorization_code.code, None)  # single use
        if authorization_code.resource not in RESOURCES:
            raise TokenError("invalid_target", "resource is required")
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource,
                           authorization_code.subject or TEST_SUBJECT)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        rt = self.refresh.get(refresh_token)
        if rt is not None and rt.expires_at is not None and rt.expires_at < time.time():
            self.refresh.pop(refresh_token, None)
            return None  # expired: invalid_grant (the SDK checks expires_at too)
        return rt if rt is not None and rt.client_id == client.client_id else None

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        self.refresh.pop(refresh_token.token, None)  # rotate
        if refresh_token.resource not in RESOURCES:
            raise TokenError("invalid_target", "resource is required")
        granted = [s for s in scopes if s in refresh_token.scopes] if scopes else refresh_token.scopes
        return self._issue(client.client_id, granted, refresh_token.resource, refresh_token.subject or TEST_SUBJECT)

    async def load_access_token(self, token: str) -> AccessToken | None:
        return None  # resource servers verify the JWT; this AS keeps no access-token table

    async def revoke_token(self, token) -> None:
        if isinstance(token, RefreshToken):
            self.refresh.pop(token.token, None)

    def _issue(self, client_id: str, scopes: list[str], resource: str, subject: str) -> OAuthToken:
        scope = " ".join(scopes)
        rt = secrets.token_urlsafe(32)
        self.refresh[rt] = RefreshToken(token=rt, client_id=client_id, scopes=scopes, resource=resource,
                                        subject=subject, expires_at=int(time.time()) + REFRESH_TTL)
        return OAuthToken(access_token=mint_test_token(scope, resource, client_id=client_id, sub=subject),
                          token_type="Bearer", expires_in=ACCESS_TTL, scope=scope or None, refresh_token=rt)


# ── 4. The app ──────────────────────────────────────────────────────────────
def _guard_token_resource(app, provider: TestOnlyProvider):
    """ASGI wrapper for /token (RFC 8707 §2.2). The SDK parses `resource` there but never compares
    it with the grant, and the provider never sees it. This reads the form, and when `resource` is
    present but differs from the code's (or refresh token's) resource, answers invalid_target and
    burns the grant. Otherwise it replays the body to the SDK handler unchanged."""
    async def guarded(scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await app(scope, receive, send)
        body, more = b"", True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > MAX_TOKEN_REQUEST:
                return await _json(send, 413, {"error": "invalid_request", "error_description": "too large"})
        form = {k: v[0] for k, v in parse_qs(body.decode("latin-1")).items()}
        wanted = form.get("resource")
        if wanted is not None:
            grant = None
            if form.get("grant_type") == "authorization_code":
                grant = provider.codes.get(form.get("code", ""))
                pool, key = provider.codes, form.get("code", "")
            elif form.get("grant_type") == "refresh_token":
                grant = provider.refresh.get(form.get("refresh_token", ""))
                pool, key = provider.refresh, form.get("refresh_token", "")
            if grant is not None and grant.resource != wanted:
                pool.pop(key, None)  # misuse burns the grant
                return await _json(send, 400, {"error": "invalid_target",
                                               "error_description": "resource does not match the grant"})
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await app(scope, replay, send)
    return guarded


async def _json(send, status: int, payload: dict) -> None:
    raw = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store"),
                            (b"content-length", str(len(raw)).encode())]})
    await send({"type": "http.response.body", "body": raw})


def build_auth_app(provider: TestOnlyProvider | None = None) -> Starlette:
    """The SDK's AS routes around the in-memory provider. Registration (DCR) is on. Raises
    SigningKeyMissing when there is no key file (main() creates it first)."""
    load_signing_key()  # fail at build time, not at the first /token request
    # AnyHttpUrl("http://127.0.0.1:9000") would render with a trailing "/" and the SDK client would
    # reject the metadata ("issuer mismatch"); AuthSettings keeps the empty path.
    issuer = AuthSettings(issuer_url=ISSUER, resource_server_url=None).issuer_url
    provider = provider or TestOnlyProvider()
    routes = create_auth_routes(provider, issuer_url=issuer,
                                client_registration_options=ClientRegistrationOptions(
                                    enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES))
    for route in routes:
        if getattr(route, "path", "") == "/token":
            route.app = _guard_token_resource(route.app, provider)
    return Starlette(routes=routes)


# ── 5. Entry point ──────────────────────────────────────────────────────────
def main() -> None:
    import uvicorn
    try:
        ensure_signing_key()
    except SigningKeyMissing as e:
        raise SystemExit(f"auth_server: {e}") from None
    print(f"TEST-ONLY authorization server on {ISSUER} (auto-approves as {TEST_SUBJECT!r}); "
          f"signing key file: {key_file()}", file=sys.stderr)
    uvicorn.run(build_auth_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
