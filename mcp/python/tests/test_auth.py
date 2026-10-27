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
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
import uvicorn
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auth_server  # noqa: E402
import client as lumen_client  # noqa: E402
import ops_server  # noqa: E402
import read_server  # noqa: E402
import resource_auth  # noqa: E402
from auth_server import mint_test_token  # noqa: E402
from resource_auth import (ISSUER, OPS_RESOURCE, READ_RESOURCE, SCOPE_READ, SCOPE_WRITE,  # noqa: E402
                           TEST_SIGNING_KEY)

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


def app_for(name: str, tmp_path: Path):
    if name == "read":
        return read_server.build_http_app()
    return ops_server.build_http_app(ops_server.build_ops_server(tmp_path / "labels.jsonl", auth=True))


@contextlib.contextmanager
def http(name: str, tmp_path: Path):
    with TestClient(app_for(name, tmp_path), base_url=f"http://{SERVERS[name][2]}") as c:
        yield c


def discover(name: str, tmp_path: Path, token: str | None, **extra: str):
    with http(name, tmp_path) as c:
        return c.post("/mcp", content=DISCOVER, headers=headers(SERVERS[name][2], token, **extra))


def signed(claims: dict, key: str = TEST_SIGNING_KEY, alg: str = "HS256") -> str:
    return jwt.encode(claims, key, algorithm=alg)


def good_claims(name: str) -> dict:
    resource, scope, _ = SERVERS[name]
    now = int(time.time())
    return {"iss": ISSUER, "aud": resource, "sub": "user-1", "client_id": "lumen-cli", "scope": scope,
            "iat": now, "exp": now + 600}


@contextlib.contextmanager
def live(app):
    """A real uvicorn server for `app` in a thread, on a free 127.0.0.1 port. Yields the base URL."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
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
    for aud in (other, "http://127.0.0.1:9999/mcp", [other], [other, "http://x.invalid/mcp"]):
        r = discover(name, tmp_path, signed({**good_claims(name), "aud": aud}))
        assert r.status_code == 401, aud
        assert "resource_metadata=" in r.headers["www-authenticate"]
    # A list that names this server is fine (RFC 7519: aud may be an array).
    assert discover(name, tmp_path, signed({**good_claims(name), "aud": [other, resource]})).status_code == 200


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
        "wrong_key": signed(base, key="another-key-that-is-long-enough-for-hs256!!"),
        "expired": signed({**base, "iat": now - 4000, "exp": now - 3600}),
        "not_yet_valid": signed({**base, "nbf": now + 3600}),
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


# ── 3. Origin with auth (rulings R9 and R18) ───────────────────────────────
@pytest.mark.parametrize("name", ["read", "ops"])
def test_origin_403_with_valid_token(name, tmp_path):
    """R9: a bad Origin is 403 when the token is good. Python runs auth first, so without a token
    the same request is 401 (Go checks Origin first and answers 403 either way)."""
    resource, scope, _ = SERVERS[name]
    token = mint_test_token(scope, resource)
    assert discover(name, tmp_path, token, Origin="http://evil.invalid").status_code == 403
    assert discover(name, tmp_path, token, Origin="http://127.0.0.1:6274").status_code == 200
    assert discover(name, tmp_path, None, Origin="http://evil.invalid").status_code == 401  # the ordering


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
    token = mint_test_token(SCOPE_WRITE, OPS_RESOURCE, client_id="lumen-test-client-7", sub="user-42")
    app = ops_server.build_http_app(ops_server.build_ops_server(path, auth=True))

    async def go(url: str):
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
    token = mint_test_token(SCOPE_READ, READ_RESOURCE)
    target = lumen_client.http_target("http://127.0.0.1:8765/mcp", token)
    assert target.url == "http://127.0.0.1:8765/mcp" and token not in target.url
    assert target.headers == {"Authorization": f"Bearer {token}"}
    assert lumen_client.http_target("http://127.0.0.1:8765/mcp", None) == "http://127.0.0.1:8765/mcp"

    async def go(url: str, tok: str | None):
        async with lumen_client.connect(lumen_client.http_target(url, tok)) as c:
            return await lumen_client.list_tools(c)

    with live(read_server.build_http_app()) as url:
        tools = asyncio.run(go(f"{url}/mcp", token))
        assert [t["name"] for t in tools] == ["classify_complaint", "get_ticket", "search_tickets"]
        with pytest.raises(BaseException):  # no token: the server answers 401 and the client fails
            asyncio.run(go(f"{url}/mcp", None))


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
    claims = jwt.decode(tok["access_token"], TEST_SIGNING_KEY, algorithms=["HS256"], audience=READ_RESOURCE)
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
    claims = jwt.decode(new["access_token"], TEST_SIGNING_KEY, algorithms=["HS256"], audience=OPS_RESOURCE)
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


def test_mint_test_token_is_not_served():
    """mint_test_token is a function for tests, never an HTTP endpoint."""
    paths = {getattr(r, "path", "") for r in auth_server.build_auth_app().routes}
    assert paths == {"/.well-known/oauth-authorization-server", "/authorize", "/token", "/register"}
    assert not any("mint" in p or "test" in p for p in paths)


def test_mint_test_token_claims():
    claims = jwt.decode(mint_test_token(SCOPE_READ, READ_RESOURCE), TEST_SIGNING_KEY, algorithms=["HS256"],
                        audience=READ_RESOURCE)
    assert {k: claims[k] for k in ("iss", "aud", "sub", "client_id", "scope")} == {
        "iss": ISSUER, "aud": READ_RESOURCE, "sub": "user-1", "client_id": "lumen-cli", "scope": SCOPE_READ}
    assert claims["exp"] > claims["iat"]


def test_as_is_labelled_test_only():
    doc = auth_server.__doc__ or ""
    assert "TEST ONLY" in doc.upper().replace("-", " ")
    assert "test" in (auth_server.mint_test_token.__doc__ or "").lower()


def test_go_shares_the_test_issuer_key_and_resources():
    """The Go resource servers verify tokens from this AS: same issuer, key and canonical URIs."""
    go = (ROOT / "mcp" / "go" / "auth" / "auth.go").read_text(encoding="utf-8")
    for value in (ISSUER, TEST_SIGNING_KEY, READ_RESOURCE, OPS_RESOURCE, SCOPE_READ, SCOPE_WRITE):
        assert re.search(r'=\s*"' + re.escape(value) + '"', go), value
