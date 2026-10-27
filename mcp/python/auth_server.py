"""TEST ONLY: a local OAuth 2.1 authorization server for the Lumen MCP servers (Python).

    *** Never run this anywhere but 127.0.0.1, and never for real users. ***
    It AUTO-APPROVES every authorization request with no login page and one fixed test subject
    (TEST_SUBJECT = "user-1"), and it signs tokens with a public test key (resource_auth.TEST_SIGNING_KEY).

What it does do properly, because that is what the lessons teach:
  - Authorization code + PKCE (S256 only) for public clients (token_endpoint_auth_method "none").
  - Registration (ruling R4): the pre-registered public client `lumen-cli` is the primary path;
    Dynamic Client Registration (/register) stays on as the secondary path. Client ID Metadata
    Documents are not implemented: the SDK's AS cannot advertise or fetch them, and the SDK client
    accepts only an https client-document URL, which a loopback lab cannot host.
  - Audience binding (RFC 8707): /authorize must name a resource this AS serves (lumen-read or
    lumen-ops); the access token's `aud` is that resource, so a token is good for one server only.
  - RFC 9207: the redirect carries `iss`, which the SDK client checks.
  - Codes are single-use and short-lived; refresh tokens rotate and keep the code's audience.

It is built from the SDK's own routes (mcp.server.auth.routes.create_auth_routes): metadata at
/.well-known/oauth-authorization-server, /authorize, /token and /register. mint_test_token() is a
plain function for tests and is never routed.

Run:  python mcp/python/auth_server.py      (serves http://127.0.0.1:9000)
"""
from __future__ import annotations

import secrets
import time

import jwt
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
                                      RefreshToken, TokenError, construct_redirect_uri)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.applications import Starlette

from resource_auth import ISSUER, OPS_RESOURCE, READ_RESOURCE, SCOPE_READ, SCOPE_WRITE, TEST_SIGNING_KEY

# ── 1. Configuration ────────────────────────────────────────────────────────
HOST, PORT = "127.0.0.1", 9000
RESOURCES = (READ_RESOURCE, OPS_RESOURCE)  # the only audiences this AS will put in a token
SCOPES = [SCOPE_READ, SCOPE_WRITE]
TEST_SUBJECT = "user-1"  # TEST ONLY: every approval is for this one fixed user
ACCESS_TTL = 600  # seconds
CODE_TTL = 300
CLI_REDIRECT = "http://127.0.0.1:33418/callback"  # redirect URIs match exactly, port included

# The pre-registered public client (R4 primary): no secret, so PKCE is what protects the code.
LUMEN_CLI = OAuthClientInformationFull(
    client_id="lumen-cli", client_name="Lumen CLI", redirect_uris=[AnyUrl(CLI_REDIRECT)],
    token_endpoint_auth_method="none", grant_types=["authorization_code", "refresh_token"],
    response_types=["code"], scope=" ".join(SCOPES))


# ── 2. Tokens ───────────────────────────────────────────────────────────────
def mint_test_token(scope: str, audience: str, client_id: str = "lumen-cli", sub: str = TEST_SUBJECT) -> str:
    """For tests only (never exposed over HTTP): a signed access token as this AS would issue it,
    without the browser round trip. `audience` is the resource URI the token is good for."""
    now = int(time.time())
    return jwt.encode({"iss": ISSUER, "aud": audience, "sub": sub, "client_id": client_id, "scope": scope,
                       "iat": now, "exp": now + ACCESS_TTL}, TEST_SIGNING_KEY, algorithm="HS256")


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
        code = self.codes.get(authorization_code)
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
                                        subject=subject)
        return OAuthToken(access_token=mint_test_token(scope, resource, client_id=client_id, sub=subject),
                          token_type="Bearer", expires_in=ACCESS_TTL, scope=scope or None, refresh_token=rt)


# ── 4. The app ──────────────────────────────────────────────────────────────
def build_auth_app(provider: TestOnlyProvider | None = None) -> Starlette:
    """The SDK's AS routes around the in-memory provider. Registration (DCR) is on."""
    # AnyHttpUrl("http://127.0.0.1:9000") would render with a trailing "/" and the SDK client would
    # reject the metadata ("issuer mismatch"); AuthSettings keeps the empty path.
    issuer = AuthSettings(issuer_url=ISSUER, resource_server_url=None).issuer_url
    routes = create_auth_routes(provider or TestOnlyProvider(), issuer_url=issuer,
                                client_registration_options=ClientRegistrationOptions(
                                    enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES))
    return Starlette(routes=routes)


# ── 5. Entry point ──────────────────────────────────────────────────────────
def main() -> None:
    import uvicorn
    print(f"TEST-ONLY authorization server on {ISSUER} (auto-approves as {TEST_SUBJECT!r})")
    uvicorn.run(build_auth_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
