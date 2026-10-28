"""Tests for authorization: the local test AS, and lumen-read (HTTP) and lumen-ops as OAuth resource servers.

HTTP-level checks drive the ASGI apps with Starlette's TestClient. The two end-to-end checks (the label
row's client_id comes from the token; the client's --token path) run a real uvicorn server in a thread on
a free 127.0.0.1 port. Tokens come from auth_server.mint_test_token (test-only, never served over HTTP).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx2
import jwt
import pytest
from mcp.shared.exceptions import MCPError
import uvicorn
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auth_server  # noqa: E402
import client as lumen_client  # noqa: E402
import ops_server  # noqa: E402
import read_server  # noqa: E402
import resource_auth  # noqa: E402
from auth_server import mint_test_token  # noqa: E402
from conftest import write_key_file  # noqa: E402
from resource_auth import (ISSUER, OPS_RESOURCE, READ_RESOURCE, SCOPE_READ, SCOPE_WRITE,  # noqa: E402
                           SigningKeyMissing, load_signing_key)

ROOT = Path(__file__).resolve().parents[3]
META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}
DISCOVER = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": META}}).encode()

# server name -> (canonical URI, the scope it requires, Host header)
SERVERS = {"read": (READ_RESOURCE, SCOPE_READ, "127.0.0.1:8765"),
           "ops": (OPS_RESOURCE, SCOPE_WRITE, "127.0.0.1:8766")}


# ── 1. Helpers ──────────────────────────────────────────────────────────────
def headers(host: str, token: str | None = None, **extra: str) -> dict:
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": host,
         "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "server/discover", **extra}
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    return h


def app_for(name: str, tmp_path: Path, port: int | None = None):
    """The server's HTTP app. `port` is the port it is served on (its Host allow-list); default its own."""
    port = port or int(SERVERS[name][2].split(":")[1])
    if name == "read":
        return read_server.build_http_app(port=port)
    return ops_server.build_http_app(ops_server.build_ops_server(tmp_path / "labels.jsonl", auth=True, port=port), port=port)


@contextlib.contextmanager
def http(name: str, tmp_path: Path):
    with TestClient(app_for(name, tmp_path), base_url=f"http://{SERVERS[name][2]}") as c:
        yield c


def discover(name: str, tmp_path: Path, token: str | None, **extra: str):
    with http(name, tmp_path) as c:
        return c.post("/mcp", content=DISCOVER, headers=headers(SERVERS[name][2], token, **extra))


def signed(claims: dict, key: bytes | str | None = None, alg: str = "HS256") -> str:
    """Sign with this test's key (conftest), or with `key`."""
    return jwt.encode(claims, load_signing_key() if key is None else key, algorithm=alg)


def good_claims(name: str) -> dict:
    resource, scope, _ = SERVERS[name]
    now = int(time.time())
    return {"iss": ISSUER, "aud": resource, "sub": "user-1", "client_id": "lumen-cli", "scope": scope,
            "iat": now, "exp": now + 600}


@contextlib.contextmanager
def live(make_app):
    """A real uvicorn server in a thread, on a free 127.0.0.1 port. `make_app(port)` builds the app
    for that port (its Host allow-list names it, ruling R42). Yields the base URL."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = make_app(port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()


def rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# ── 2. The resource servers: 401 / 403 / 200 ────────────────────────────────
@pytest.mark.parametrize("name", ["read", "ops"])
def test_no_token_401_with_metadata(name, tmp_path):
    r = discover(name, tmp_path, None)
    assert r.status_code == 401
    port = SERVERS[name][2].split(":")[1]
    want = f'resource_metadata="http://127.0.0.1:{port}/.well-known/oauth-protected-resource/mcp"'
    assert want in r.headers["www-authenticate"]


@pytest.mark.parametrize("name", ["read", "ops"])
def test_protected_resource_metadata(name, tmp_path):
    resource, scope, host = SERVERS[name]
    with http(name, tmp_path) as c:
        prm = c.get("/.well-known/oauth-protected-resource/mcp", headers={"Host": host}).json()
    assert prm["resource"] == resource
    assert prm["authorization_servers"] == [ISSUER]
    assert prm["scopes_supported"] == [scope]
    assert prm["bearer_methods_supported"] == ["header"]


@pytest.mark.parametrize("name", ["read", "ops"])
def test_right_token_ok(name, tmp_path):
    resource, scope, _ = SERVERS[name]
    r = discover(name, tmp_path, mint_test_token(scope, resource))
    assert r.status_code == 200
    assert "2026-07-28" in r.json()["result"]["supportedVersions"]


@pytest.mark.parametrize("name", ["read", "ops"])
def test_wrong_audience_401(name, tmp_path):
    resource, scope, _ = SERVERS[name]
    other = OPS_RESOURCE if resource == READ_RESOURCE else READ_RESOURCE
    # R30: a list with more than one entry is refused even when it names this server.
    for aud in (other, "http://127.0.0.1:9999/mcp", [other], [other, "http://x.invalid/mcp"],
                [other, resource], [resource, resource], [], ""):
        r = discover(name, tmp_path, signed({**good_claims(name), "aud": aud}))
        assert r.status_code == 401, aud
        assert "resource_metadata=" in r.headers["www-authenticate"]
    # A one-entry list naming this server is fine (RFC 7519: aud may be an array).
    assert discover(name, tmp_path, signed({**good_claims(name), "aud": [resource]})).status_code == 200


@pytest.mark.parametrize("name", ["read", "ops"])
def test_wrong_scope_403(name, tmp_path):
    resource, scope, _ = SERVERS[name]
    other = SCOPE_WRITE if scope == SCOPE_READ else SCOPE_READ
    for granted in (other, "", "tickets:admin"):
        r = discover(name, tmp_path, mint_test_token(granted, resource))
        assert r.status_code == 403, granted
        assert "resource_metadata=" in r.headers["www-authenticate"]


@pytest.mark.parametrize("name", ["read", "ops"])
def test_invalid_tokens_401(name, tmp_path):
    now = int(time.time())
    base = good_claims(name)
    bad = {
        "garbage": "not-a-jwt",
        "wrong_key": signed(base, key=secrets.token_bytes(32)),
        "expired": signed({**base, "iat": now - 4000, "exp": now - 3600}),
        "expired_10s": signed({**base, "exp": now - 10}),  # R29: no leeway on exp
        "not_yet_valid": signed({**base, "nbf": now + 3600}),
        "iat_future": signed({**base, "iat": now + 3600}),
        "no_sub": signed({k: v for k, v in base.items() if k != "sub"}),
        "empty_sub": signed({**base, "sub": ""}),
        "wrong_issuer": signed({**base, "iss": "http://127.0.0.1:9001"}),
        "no_exp": signed({k: v for k, v in base.items() if k != "exp"}),
        "no_aud": signed({k: v for k, v in base.items() if k != "aud"}),
        "no_client_id": signed({k: v for k, v in base.items() if k != "client_id"}),
        "alg_none": jwt.encode(base, None, algorithm="none"),
    }
    for case, token in bad.items():
        r = discover(name, tmp_path, token)
        assert r.status_code == 401, case
        assert "resource_metadata=" in r.headers["www-authenticate"], case


@pytest.mark.parametrize("name", ["read", "ops"])
def test_clock_skew_only_on_nbf_and_iat(name, tmp_path):
    """R29: nbf / iat up to 30 s in the future are tolerated (AS clock ahead); exp never is."""
    now = int(time.time())
    base = good_claims(name)
    assert discover(name, tmp_path, signed({**base, "nbf": now + 20, "iat": now + 20})).status_code == 200
    assert discover(name, tmp_path, signed({**base, "exp": now - 10})).status_code == 401
    assert discover(name, tmp_path, signed({**base, "exp": now + 10})).status_code == 200


def test_verifier_refuses_an_empty_key():
    with pytest.raises(ValueError, match="empty signing key"):
        resource_auth.JWTVerifier(READ_RESOURCE, key=b"")


@pytest.mark.parametrize("name", ["read", "ops"])
def test_token_in_query_string_is_ignored(name, tmp_path):
    """Bearer tokens travel only in the Authorization header (RFC 6750 §2.3 is not supported)."""
    resource, scope, host = SERVERS[name]
    token = mint_test_token(scope, resource)
    with http(name, tmp_path) as c:
        r = c.post(f"/mcp?access_token={token}", content=DISCOVER, headers=headers(host))
    assert r.status_code == 401


def test_cross_server_token_rejected(tmp_path):
    """Review focus 3: a token minted for lumen-read never works on lumen-ops.

    Order of checks (both languages): the audience is part of verifying the token (Python:
    validate_token_resource=True in the SDK's bearer backend; Go: the verifier, ruling R7), and the
    scope is checked only on a token that verified. So:
      - the lumen-read token itself (read scope, read audience) at lumen-ops: 401, for the audience;
      - the same token with a forged tickets:write scope: still 401, for the audience;
      - a token for lumen-ops's audience that lacks tickets:write: 403, for the scope.
    The brief expected 403 for the first case; that needs scope checked before audience, which
    neither binding mechanism does (see the Task 8 report)."""
    read_token = mint_test_token(SCOPE_READ, READ_RESOURCE)
    assert discover("read", tmp_path, read_token).status_code == 200  # a good token, for lumen-read
    r = discover("ops", tmp_path, read_token)
    assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]
    assert discover("ops", tmp_path, mint_test_token(SCOPE_WRITE, READ_RESOURCE)).status_code == 401  # forged scope
    r = discover("ops", tmp_path, mint_test_token(SCOPE_READ, OPS_RESOURCE))  # right audience, read scope
    assert r.status_code == 403 and 'error="insufficient_scope"' in r.headers["www-authenticate"]
    # And the other way round: an ops token is no key to lumen-read.
    assert discover("read", tmp_path, mint_test_token(SCOPE_WRITE, OPS_RESOURCE)).status_code == 401
    assert discover("read", tmp_path, mint_test_token(SCOPE_WRITE, READ_RESOURCE)).status_code == 403


def test_validate_token_resource_is_explicit():
    for mcp in (read_server.build_read_server(auth=True), ops_server.build_ops_server(auth=True)):
        assert mcp.settings.auth is not None
        assert mcp.settings.auth.validate_token_resource is True
    assert str(read_server.build_read_server(auth=True).settings.auth.resource_server_url) == READ_RESOURCE
    assert ops_server.build_ops_server(auth=True).settings.auth.required_scopes == [SCOPE_WRITE]


def test_http_app_refuses_a_server_without_auth(tmp_path):
    """No unauthenticated HTTP: an HTTP app is never built around a server that has no auth."""
    with pytest.raises(ValueError, match="auth"):
        ops_server.build_http_app(ops_server.build_ops_server(tmp_path / "l.jsonl"))
    with pytest.raises(ValueError, match="auth"):
        read_server.build_http_app(read_server.build_read_server())


# ── 3. Host and Origin (rulings R18 and R42) ────────────────────────────────
@pytest.mark.parametrize("name", ["read", "ops"])
def test_origin_403_with_or_without_token(name, tmp_path):
    """R42: Host and Origin are checked before the token, as in Go, so a bad Origin is 403 with a
    good token and without one (this replaces R9's 401 for the no-token case)."""
    resource, scope, _ = SERVERS[name]
    token = mint_test_token(scope, resource)
    assert discover(name, tmp_path, token, Origin="http://evil.invalid").status_code == 403
    assert discover(name, tmp_path, token, Origin="http://127.0.0.1:6274").status_code == 200
    assert discover(name, tmp_path, None, Origin="http://evil.invalid").status_code == 403


@pytest.mark.parametrize("name", ["read", "ops"])
def test_dns_rebinding_refused_over_real_http(name, tmp_path):
    """R42, on a real uvicorn server: a rebinding page's request names its own host in both Host and
    Origin. Both are checked against explicit allow-lists, never against each other, so it is 403
    even with a valid token. The server's own two local origins and the Inspector's two still work."""
    _, scope, _ = SERVERS[name]
    with live(lambda port: app_for(name, tmp_path, port)) as url:
        port = url.rsplit(":", 1)[1]
        token = mint_test_token(scope, resource_auth.resource_uri(int(port)))  # R46: the audience names the port

        def post(host: str, origin: str | None = None) -> int:
            extra = {"Origin": origin} if origin else {}
            return httpx2.post(f"{url}/mcp", content=DISCOVER, headers=headers(host, token, **extra)).status_code

        assert post(f"evil.invalid:{port}", f"http://evil.invalid:{port}") == 403
        assert post(f"evil.invalid:{port}") == 403
        assert post(f"127.0.0.1:{port}", f"http://evil.invalid:{port}") == 403
        assert post(f"127.0.0.1:{port}", "http://127.0.0.1:5555") == 403  # another local port is not trusted
        assert post(f"127.0.0.1:{port}") == 200
        for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}", "http://127.0.0.1:6274",
                       "http://localhost:6274"):
            assert post(f"127.0.0.1:{port}", origin) == 200, origin
        assert post(f"localhost:{port}", f"http://localhost:{port}") == 200


@pytest.mark.parametrize("name", ["read", "ops"])
def test_get_with_bad_origin_403(name, tmp_path):
    """R18: the Origin check covers GET too, not only unsafe methods."""
    resource, scope, host = SERVERS[name]
    token = mint_test_token(scope, resource)
    get_headers = {"Accept": "text/event-stream", "Host": host, "Authorization": f"Bearer {token}",
                   "MCP-Protocol-Version": "2026-07-28"}
    with http(name, tmp_path) as c:
        bad = c.get("/mcp", headers={**get_headers, "Origin": "http://evil.invalid"})
        good = c.get("/mcp", headers={**get_headers, "Origin": "http://127.0.0.1:6274"})
    assert bad.status_code == 403
    assert good.status_code != 403


# ── 4. Identity comes from the verified token ───────────────────────────────
def test_ops_client_id_from_token(tmp_path):
    """The label row's client_id is the verified token's client_id (not anything the client says)."""
    path = tmp_path / "labels.jsonl"
    def app(port: int):
        return ops_server.build_http_app(ops_server.build_ops_server(path, auth=True, port=port), port=port)

    async def go(url: str):
        token = mint_test_token(SCOPE_WRITE, f"{url}/mcp", client_id="lumen-test-client-7", sub="user-42")
        target = lumen_client.http_target(f"{url}/mcp", token)
        async with lumen_client.connect(target, lumen_client.always(True)) as c:
            return await lumen_client.call(c, "label_ticket", {"id": "t001", "label": "billing"})

    with live(app) as url:
        out = asyncio.run(go(url))
    assert out == {"structured": {"ticket_id": "t001", "label": "billing", "written": True}}
    [row] = rows(path)
    assert row["client_id"] == "lumen-test-client-7"
    assert row["source"] == "agent" and row["confirmed"] is True


def test_verifier_fills_client_id_and_subject():
    v = resource_auth.JWTVerifier(OPS_RESOURCE)
    tok = asyncio.run(v.verify_token(mint_test_token(SCOPE_WRITE, OPS_RESOURCE, client_id="c-1", sub="u-9")))
    assert tok is not None
    assert (tok.client_id, tok.subject, tok.scopes, tok.resource) == ("c-1", "u-9", [SCOPE_WRITE], OPS_RESOURCE)
    assert tok.claims == {"iss": ISSUER}


def test_client_token_goes_in_the_header_only(tmp_path):
    """R19: --token becomes `Authorization: Bearer`, never part of the URL."""
    token = mint_test_token(SCOPE_READ, READ_RESOURCE)  # only the header handling is checked here
    target = lumen_client.http_target("http://127.0.0.1:8765/mcp", token)
    assert target.url == "http://127.0.0.1:8765/mcp" and token not in target.url
    assert target.headers == {"Authorization": f"Bearer {token}"}
    assert lumen_client.http_target("http://127.0.0.1:8765/mcp", None) == "http://127.0.0.1:8765/mcp"

    async def go(url: str, tok: str | None, refusal: lumen_client.Refusal):
        async with lumen_client.connect(lumen_client.http_target(url, tok), refusal=refusal) as c:
            return await lumen_client.list_tools(c)

    with live(lambda port: read_server.build_http_app(port=port)) as url:
        token = mint_test_token(SCOPE_READ, f"{url}/mcp")
        ok = lumen_client.Refusal()
        tools = asyncio.run(go(f"{url}/mcp", token, ok))
        assert [t["name"] for t in tools] == ["classify_complaint", "get_ticket", "search_tickets"]
        assert ok.status is None
        refused = lumen_client.Refusal()
        with pytest.raises(Exception) as e:  # no token: the server answers 401 and the client fails
            asyncio.run(go(f"{url}/mcp", None, refused))
    assert isinstance(lumen_client.innermost(e.value), MCPError)
    assert refused.status == 401
    assert lumen_client.refusal_line(401) == "client: the server refused the request: HTTP 401 Unauthorized"


# ── 5. The test authorization server ────────────────────────────────────────
VERIFIER = secrets.token_urlsafe(48)
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
REDIRECT = "http://127.0.0.1:33418/callback"


def _as() -> TestClient:
    return TestClient(auth_server.build_auth_app(), base_url=ISSUER)


def _authorize(c: TestClient, resource: str | None, scope: str, client_id: str = "lumen-cli"):
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": CHALLENGE,
         "code_challenge_method": "S256", "state": "st-1", "scope": scope}
    if resource is not None:
        q["resource"] = resource
    return c.get("/authorize", params=q, follow_redirects=False)


def test_as_binds_127_0_0_1_port_9000():
    assert (auth_server.HOST, auth_server.PORT) == ("127.0.0.1", 9000)
    assert ISSUER == "http://127.0.0.1:9000"


def test_as_metadata():
    with _as() as c:
        md = c.get("/.well-known/oauth-authorization-server").json()
    assert md["issuer"] == ISSUER  # no trailing slash: the SDK client compares it exactly
    assert md["authorization_endpoint"] == f"{ISSUER}/authorize"
    assert md["token_endpoint"] == f"{ISSUER}/token"
    assert md["registration_endpoint"] == f"{ISSUER}/register"  # DCR, the secondary path (R4)
    assert md["code_challenge_methods_supported"] == ["S256"]
    assert "client_id_metadata_document_supported" not in md or not md["client_id_metadata_document_supported"]


def test_as_preregistered_pkce_flow_mints_an_audience_bound_token(tmp_path):
    """R4 primary path: lumen-cli (public client, PKCE). The token's aud is the resource from /authorize."""
    with _as() as c:
        r = _authorize(c, READ_RESOURCE, SCOPE_READ)
        assert r.status_code == 302
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert q["state"] == ["st-1"] and q["iss"] == [ISSUER]  # RFC 9207
        tok = c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0],
                                     "redirect_uri": REDIRECT, "client_id": "lumen-cli",
                                     "code_verifier": VERIFIER, "resource": READ_RESOURCE}).json()
        replay = c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0],
                                        "redirect_uri": REDIRECT, "client_id": "lumen-cli",
                                        "code_verifier": VERIFIER, "resource": READ_RESOURCE})
    assert replay.status_code == 400  # codes are single use
    claims = jwt.decode(tok["access_token"], load_signing_key(), algorithms=["HS256"], audience=READ_RESOURCE)
    assert claims["aud"] == READ_RESOURCE and claims["iss"] == ISSUER
    assert claims["client_id"] == "lumen-cli" and claims["sub"] == auth_server.TEST_SUBJECT == "user-1"
    assert claims["scope"] == SCOPE_READ
    assert tok["token_type"].lower() == "bearer" and tok["refresh_token"]
    assert discover("read", tmp_path, tok["access_token"]).status_code == 200
    assert discover("ops", tmp_path, tok["access_token"]).status_code == 401  # read audience at ops


def test_as_wrong_pkce_verifier_rejected():
    with _as() as c:
        q = parse_qs(urlparse(_authorize(c, OPS_RESOURCE, SCOPE_WRITE).headers["location"]).query)
        r = c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT,
                                   "client_id": "lumen-cli", "code_verifier": "x" * 50})
    assert r.status_code == 400


def test_as_refresh_keeps_audience():
    with _as() as c:
        q = parse_qs(urlparse(_authorize(c, OPS_RESOURCE, SCOPE_WRITE).headers["location"]).query)
        tok = c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0],
                                     "redirect_uri": REDIRECT, "client_id": "lumen-cli",
                                     "code_verifier": VERIFIER}).json()
        new = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                     "client_id": "lumen-cli"}).json()
        again = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                       "client_id": "lumen-cli"})
    claims = jwt.decode(new["access_token"], load_signing_key(), algorithms=["HS256"], audience=OPS_RESOURCE)
    assert claims["scope"] == SCOPE_WRITE
    assert again.status_code == 400  # refresh tokens rotate


@pytest.mark.parametrize("resource", [None, "http://127.0.0.1:9999/mcp", "https://evil.example/mcp"])
def test_as_requires_a_known_resource(resource):
    """RFC 8707: no resource, or one this AS does not serve, gets no code."""
    with _as() as c:
        r = _authorize(c, resource, SCOPE_READ)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["invalid_target"] and "code" not in q


def _code(c: TestClient, resource: str = READ_RESOURCE, scope: str = SCOPE_READ) -> str:
    return parse_qs(urlparse(_authorize(c, resource, scope).headers["location"]).query)["code"][0]


def _redeem(c: TestClient, code: str, **over):
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": "lumen-cli",
            "code_verifier": VERIFIER, **over}
    return c.post("/token", data={k: v for k, v in data.items() if v is not None})


def test_as_plain_pkce_refused():
    """Only S256: a `plain` challenge (the verifier itself) gets no code."""
    with _as() as c:
        r = c.get("/authorize", follow_redirects=False, params={
            "response_type": "code", "client_id": "lumen-cli", "redirect_uri": REDIRECT, "code_challenge": VERIFIER,
            "code_challenge_method": "plain", "state": "s", "scope": SCOPE_READ, "resource": READ_RESOURCE})
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["invalid_request"] and "code" not in q


@pytest.mark.parametrize("redirect", ["http://127.0.0.1:33419/callback", "http://127.0.0.1:33418/callback/",
                                      "http://localhost:33418/callback", "https://127.0.0.1:33418/callback"])
def test_as_redirect_uri_must_match_exactly(redirect):
    """Port, trailing slash, localhost vs 127.0.0.1, scheme: any difference is refused, and the AS
    answers itself (400) instead of redirecting to the unregistered URI."""
    with _as() as c:
        r = c.get("/authorize", follow_redirects=False, params={
            "response_type": "code", "client_id": "lumen-cli", "redirect_uri": redirect, "code_challenge": CHALLENGE,
            "code_challenge_method": "S256", "state": "s", "scope": SCOPE_READ, "resource": READ_RESOURCE})
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_as_redirect_mismatch_at_token():
    with _as() as c:
        r = _redeem(c, _code(c), redirect_uri="http://127.0.0.1:33418/other")
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_as_code_from_another_client_is_invalid_grant():
    """A code issued to lumen-cli, presented by another (registered) client: invalid_grant, and burned."""
    with _as() as c:
        other = c.post("/register", json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                                          "grant_types": ["authorization_code"], "response_types": ["code"],
                                          "scope": SCOPE_READ}).json()["client_id"]
        code = _code(c)
        stolen = _redeem(c, code, client_id=other)
        after = _redeem(c, code)
    assert stolen.status_code == 400 and stolen.json()["error"] == "invalid_grant"
    assert after.status_code == 400 and after.json()["error"] == "invalid_grant"


def test_as_failed_pkce_burns_the_code():
    """A wrong verifier fails, and the right one cannot be tried afterwards: no guessing."""
    with _as() as c:
        code = _code(c)
        guess = _redeem(c, code, code_verifier="x" * 50)
        right = _redeem(c, code)
    assert guess.status_code == 400 and guess.json()["error"] == "invalid_grant"
    assert right.status_code == 400 and right.json()["error"] == "invalid_grant"


def test_as_code_expires():
    provider = auth_server.TestOnlyProvider()
    with TestClient(auth_server.build_auth_app(provider), base_url=ISSUER) as c:
        code = _code(c)
        provider.codes[code].expires_at = time.time() - 1
        r = _redeem(c, code)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_as_token_resource_must_match_the_code():
    """RFC 8707: the resource at /token must be the code's. A different one is invalid_target and
    burns the code; the same one (or none) is fine."""
    with _as() as c:
        code = _code(c)
        r = _redeem(c, code, resource=OPS_RESOURCE)
        after = _redeem(c, code, resource=READ_RESOURCE)
        ok = _redeem(c, _code(c), resource=READ_RESOURCE)
    assert r.status_code == 400 and r.json()["error"] == "invalid_target"
    assert after.status_code == 400  # burned
    assert ok.status_code == 200
    claims = jwt.decode(ok.json()["access_token"], load_signing_key(), algorithms=["HS256"], audience=READ_RESOURCE)
    assert claims["aud"] == READ_RESOURCE


def test_as_refresh_resource_must_match_and_tokens_expire():
    provider = auth_server.TestOnlyProvider()
    with TestClient(auth_server.build_auth_app(provider), base_url=ISSUER) as c:
        rt = _redeem(c, _code(c)).json()["refresh_token"]
        assert provider.refresh[rt].expires_at is not None
        assert provider.refresh[rt].expires_at > time.time() + auth_server.REFRESH_TTL - 60
        wrong = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": "lumen-cli",
                                       "resource": OPS_RESOURCE})
        assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_target"
        rt2 = _redeem(c, _code(c)).json()["refresh_token"]
        provider.refresh[rt2].expires_at = int(time.time()) - 1
        old = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": rt2, "client_id": "lumen-cli"})
    assert old.status_code == 400 and old.json()["error"] == "invalid_grant"


def test_as_unknown_client_rejected():
    with _as() as c:
        r = _authorize(c, READ_RESOURCE, SCOPE_READ, client_id="nobody")
    assert r.status_code == 400


def test_as_dynamic_registration():
    """R4 secondary path: DCR registers a public client, which can then authorize."""
    with _as() as c:
        reg = c.post("/register", json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                                        "grant_types": ["authorization_code", "refresh_token"],
                                        "response_types": ["code"], "client_name": "dcr-test",
                                        "scope": SCOPE_READ})
        assert reg.status_code == 201, reg.text
        cid = reg.json()["client_id"]
        r = _authorize(c, READ_RESOURCE, SCOPE_READ, client_id=cid)
    assert r.status_code == 302 and "code=" in r.headers["location"]


def _register(c: TestClient, redirects: list[str], **headers: str):
    return c.post("/register", headers=headers, json={
        "redirect_uris": redirects, "token_endpoint_auth_method": "none", "grant_types": ["authorization_code"],
        "response_types": ["code"], "scope": SCOPE_WRITE})


@pytest.mark.parametrize("redirect", ["http://127.0.0.1:33418/callback", "http://127.0.0.1/cb",
                                      "http://localhost:6274/oauth/callback", "http://LOCALHOST:1/a/b/c?x=1"])
def test_as_registers_loopback_http_redirects(redirect):
    """P5: any port and any path, as long as the scheme is http and the host is 127.0.0.1 or localhost."""
    with _as() as c:
        r = _register(c, [redirect])
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("redirects", [
    ["https://evil.example/cb"],                 # the reviewer's page (I1)
    ["http://evil.example/cb"],                  # a public host over http
    ["https://127.0.0.1:33418/callback"],        # https, even on loopback: the rule is http + loopback host
    ["http://192.168.1.5:33418/callback"],       # a non-loopback IP
    ["http://10.0.0.1/cb"],
    ["http://127.0.0.2/cb"],                     # not one of the two named hosts
    ["http://localhost.evil.example/cb"],        # a host that only starts with "localhost"
    ["http://127.0.0.1.nip.io/cb"],
    ["http://user@127.0.0.1/cb"],                # userinfo: refused rather than parsed
    ["http://127.0.0.1:33418/callback", "https://evil.example/cb"],  # one bad entry spoils the list
])
def test_as_refuses_non_loopback_redirects(redirects):
    """P5: registration refuses the redirect URI with RFC 7591's invalid_redirect_uri, so no code can be sent
    off the machine."""
    provider = auth_server.TestOnlyProvider()
    with TestClient(auth_server.build_auth_app(provider), base_url=ISSUER) as c:
        r = _register(c, redirects)
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri", r.text
    assert set(provider.clients) == {"lumen-cli"}


@pytest.mark.parametrize("path", ["/register", "/token", "/.well-known/oauth-authorization-server"])
def test_as_cors_is_loopback_only(path):
    """P5: a preflight from a loopback http origin is answered for that origin (never `*`); one from any other
    origin gets 403 and no CORS headers, so the browser blocks the page's request."""
    pre = {"Access-Control-Request-Method": "POST" if path != "/.well-known/oauth-authorization-server" else "GET",
           "Access-Control-Request-Headers": "content-type"}
    with _as() as c:
        good = c.options(path, headers={"Origin": "http://localhost:6274", **pre})
        bad = c.options(path, headers={"Origin": "https://evil.example", **pre})
        meta = c.get("/.well-known/oauth-authorization-server", headers={"Origin": "http://127.0.0.1:6274"})
    assert good.status_code == 200 and good.headers["access-control-allow-origin"] == "http://localhost:6274"
    assert bad.status_code == 403 and "access-control-allow-origin" not in bad.headers
    assert meta.headers["access-control-allow-origin"] == "http://127.0.0.1:6274"


@pytest.mark.parametrize("origin", ["https://evil.example", "http://evil.example", "https://localhost:6274",
                                    "http://192.168.1.5:8080", "null"])
def test_as_refuses_requests_from_other_origins(origin):
    """P5: a browser request carrying a non-loopback Origin is refused server-side (403), even a `no-cors` one
    whose response the page could not read, so it registers nothing and redeems nothing."""
    provider = auth_server.TestOnlyProvider()
    with TestClient(auth_server.build_auth_app(provider), base_url=ISSUER) as c:
        code = _code(c)
        reg = _register(c, [REDIRECT], Origin=origin)
        tok = c.post("/token", headers={"Origin": origin}, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": "lumen-cli",
            "code_verifier": VERIFIER})
        ok = _redeem(c, _code(c))
    assert reg.status_code == 403 and "access-control-allow-origin" not in reg.headers
    assert tok.status_code == 403 and "access-control-allow-origin" not in tok.headers
    assert set(provider.clients) == {"lumen-cli"}
    assert ok.status_code == 200  # no Origin (the CLI) still works


def test_a_page_at_an_https_origin_cannot_obtain_a_code():
    """I1, the reviewer's in-process probe: a page at https://evil.example registers a client with its own
    redirect URI, then sends the browser to /authorize for tickets:write at lumen-ops. Every route to a code
    at its origin is closed: the registration is refused (by Origin, and by redirect URI without one), the
    pre-registered client will not redirect there, and nothing answers it with CORS."""
    evil = "https://evil.example/cb"
    with _as() as c:
        from_page = _register(c, [evil], Origin="https://evil.example")
        from_script = _register(c, [evil])
        authorize = c.get("/authorize", follow_redirects=False, params={
            "response_type": "code", "client_id": "lumen-cli", "redirect_uri": evil, "code_challenge": CHALLENGE,
            "code_challenge_method": "S256", "state": "s", "scope": SCOPE_WRITE, "resource": OPS_RESOURCE})
    assert from_page.status_code == 403 and "access-control-allow-origin" not in from_page.headers
    assert from_script.status_code == 400 and from_script.json()["error"] == "invalid_redirect_uri"
    assert authorize.status_code == 400 and "location" not in authorize.headers


def test_mint_test_token_is_not_served():
    """mint_test_token is a function for tests, never an HTTP endpoint."""
    paths = {getattr(r, "path", "") for r in auth_server.build_auth_app().routes}
    assert paths == {"/.well-known/oauth-authorization-server", "/authorize", "/token", "/register"}
    assert not any("mint" in p or "test" in p for p in paths)


def test_mint_test_token_claims():
    claims = jwt.decode(mint_test_token(SCOPE_READ, READ_RESOURCE), load_signing_key(), algorithms=["HS256"],
                        audience=READ_RESOURCE)
    assert {k: claims[k] for k in ("iss", "aud", "sub", "client_id", "scope")} == {
        "iss": ISSUER, "aud": READ_RESOURCE, "sub": "user-1", "client_id": "lumen-cli", "scope": SCOPE_READ}
    assert claims["exp"] > claims["iat"]


def test_as_is_labelled_test_only():
    doc = auth_server.__doc__ or ""
    assert "TEST ONLY" in doc.upper().replace("-", " ")
    assert "test" in (auth_server.mint_test_token.__doc__ or "").lower()


def test_go_shares_the_issuer_resources_and_key_file():
    """The Go resource servers verify tokens from this AS: same issuer, canonical URIs, scopes, and the
    same key file (path and env override). No signing key is written in either language's source."""
    go = (ROOT / "mcp" / "go" / "auth" / "auth.go").read_text(encoding="utf-8")
    for value in (ISSUER, READ_RESOURCE, OPS_RESOURCE, SCOPE_READ, SCOPE_WRITE, "LUMEN_SIGNING_KEY_FILE",
                  "dev-signing.key"):
        assert re.search(r'"' + re.escape(value) + '"', go), value
    assert resource_auth.KEY_FILE_ENV == "LUMEN_SIGNING_KEY_FILE"
    assert resource_auth.DEFAULT_KEY_FILE == ROOT / "mcp" / "data" / "dev-signing.key"


# ── 6. The signing key file (ruling R25) ────────────────────────────────────
def test_as_generates_the_key_on_first_start(tmp_path):
    path = tmp_path / "data" / "dev-signing.key"
    key = auth_server.ensure_signing_key(path)
    assert len(key) == 32
    assert path.stat().st_mode & 0o777 == 0o600
    text = path.read_text(encoding="ascii")
    assert re.fullmatch(r"[0-9a-f]{64}\n", text)  # the format Go reads too
    assert auth_server.ensure_signing_key(path) == key  # a second start reuses it
    assert bytes.fromhex(text.strip()) == key


def test_concurrent_first_starts_agree_on_one_complete_key(tmp_path):
    """Item 8: many AS starts at once. Each gets the same full key (written to a temp file, then
    linked into place: a reader never sees a partial file), and no temp file is left behind."""
    import concurrent.futures
    path = tmp_path / "data" / "dev-signing.key"
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        keys = list(pool.map(lambda _: auth_server.ensure_signing_key(path), range(64)))
    assert len(set(keys)) == 1 and len(keys[0]) == 32
    assert sorted(x.name for x in path.parent.iterdir()) == ["dev-signing.key"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_keys_differ_per_generation(tmp_path):
    assert auth_server.ensure_signing_key(tmp_path / "a") != auth_server.ensure_signing_key(tmp_path / "b")


def test_env_names_the_key_file(signing_key, monkeypatch):
    assert resource_auth.key_file() == signing_key
    monkeypatch.delenv("LUMEN_SIGNING_KEY_FILE")
    assert resource_auth.key_file() == resource_auth.DEFAULT_KEY_FILE


def test_missing_key_file_fails_clearly(tmp_path, monkeypatch):
    """No key file: every user of the key refuses, naming the fix. There is no default key."""
    monkeypatch.setenv("LUMEN_SIGNING_KEY_FILE", str(tmp_path / "absent.key"))
    for build in (read_server.build_http_app, ops_server.build_http_app, auth_server.build_auth_app,
                  lambda: mint_test_token(SCOPE_READ, READ_RESOURCE)):
        with pytest.raises(SigningKeyMissing) as e:
            build()
        assert "start the test AS first" in str(e.value) and "absent.key" in str(e.value)


@pytest.mark.parametrize("script,args", [("read_server.py", ["--http"]), ("ops_server.py", [])])
def test_server_without_key_exits_with_the_message(tmp_path, script, args):
    env = {**__import__("os").environ, "LUMEN_SIGNING_KEY_FILE": str(tmp_path / "absent.key")}
    r = subprocess.run([sys.executable, str(ROOT / "mcp" / "python" / script), *args, "--port", "1"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode != 0
    assert "start the test AS first" in r.stderr and "Traceback" not in r.stderr


@pytest.mark.parametrize("bad", ["0644", "short", "nothex"])
def test_unusable_key_file_refused(tmp_path, monkeypatch, bad):
    path = write_key_file(tmp_path / "k")
    if bad == "0644":
        path.chmod(0o644)  # readable by others: refuse, like ssh does
    elif bad == "short":
        path.write_text("abcd\n")
    else:
        path.write_text("zz" * 32 + "\n")
    monkeypatch.setenv("LUMEN_SIGNING_KEY_FILE", str(path))
    with pytest.raises(SigningKeyMissing):
        load_signing_key()


def test_token_from_another_key_is_401(tmp_path, monkeypatch):
    """Two AS starts with different keys: the old key's tokens no longer verify."""
    token = mint_test_token(SCOPE_READ, READ_RESOURCE)
    monkeypatch.setenv("LUMEN_SIGNING_KEY_FILE", str(write_key_file(tmp_path / "other.key")))
    assert discover("read", tmp_path, token).status_code == 401


def test_key_file_is_git_ignored():
    assert "mcp/data/dev-signing.key" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


# ── 7. A non-default port (R46) ─────────────────────────────────────────────
NONDEFAULT = {"read": 18765, "ops": 18766}


@pytest.mark.parametrize("name", ["read", "ops"])
def test_nondefault_port_audience_and_prm(name, tmp_path):
    """The canonical resource URI and the PRM follow --port. A token for the URI of the port the
    server really listens on works; a token for the default-port URI is the wrong audience (401)."""
    default_resource, scope, _ = SERVERS[name]
    port = NONDEFAULT[name]
    host, resource = f"127.0.0.1:{port}", resource_auth.resource_uri(port)
    assert resource == f"http://127.0.0.1:{port}/mcp" and resource != default_resource
    with TestClient(app_for(name, tmp_path, port), base_url=f"http://{host}") as c:
        unauth = c.post("/mcp", content=DISCOVER, headers=headers(host))
        assert unauth.status_code == 401
        assert f'resource_metadata="http://{host}/.well-known/oauth-protected-resource/mcp"' in unauth.headers["www-authenticate"]
        prm = c.get("/.well-known/oauth-protected-resource/mcp", headers={"Host": host}).json()
        assert prm["resource"] == resource
        assert c.post("/mcp", content=DISCOVER, headers=headers(host, mint_test_token(scope, resource))).status_code == 200
        assert c.post("/mcp", content=DISCOVER, headers=headers(host, mint_test_token(scope, default_resource))).status_code == 401


def test_building_a_server_for_one_port_and_serving_it_on_another_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not port 18765"):
        read_server.build_http_app(read_server.build_read_server(auth=True), port=18765)
    with pytest.raises(ValueError, match="not port 18766"):
        ops_server.build_http_app(ops_server.build_ops_server(tmp_path / "l.jsonl", auth=True), port=18766)


def test_test_as_mints_for_servers_on_other_ports():
    """The test AS takes the audiences from --read-port/--ops-port; any other resource is invalid_target."""
    custom = auth_server.TestOnlyProvider((resource_auth.resource_uri(18765), resource_auth.resource_uri(18766)))
    assert custom.resources == ("http://127.0.0.1:18765/mcp", "http://127.0.0.1:18766/mcp")
    assert auth_server.TestOnlyProvider().resources == (READ_RESOURCE, OPS_RESOURCE)
    with TestClient(auth_server.build_auth_app(custom), base_url=ISSUER, follow_redirects=False) as c:
        ok = _authorize(c, resource_auth.resource_uri(18765), SCOPE_READ)
        assert ok.status_code == 302 and "code=" in ok.headers["location"]
        bad = _authorize(c, READ_RESOURCE, SCOPE_READ)  # the default port is not served by this AS
        assert "invalid_target" in (bad.headers.get("location", "") + bad.text)
