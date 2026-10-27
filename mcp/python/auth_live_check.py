"""The live OAuth check: the SDK's own OAuth client runs the real authorization-code + PKCE flow
against the local TEST-ONLY AS (127.0.0.1:9000), then calls one tool on a Lumen resource server.

The "browser" is headless: it GETs the /authorize URL, and the test AS auto-approves with a 302
whose `code`, `state` and `iss` this script hands back to the SDK client. Steps and commands:
mcp/python/AUTH_LIVE_CHECK.md. It never prints a token. With --save-token, the access token is
written to a file (mode 0600) for the clients' --token-file / -token-file.

Run:  python mcp/python/auth_live_check.py --url http://127.0.0.1:8765/mcp [--dcr] [--yes]
          [--save-token PATH] TOOL JSON_ARGS
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx2
import jwt
from mcp import Client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client  # private; verified on mcp==2.3.0
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata
from mcp_types import ElicitResult
from pydantic import AnyUrl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth_server import CLI_REDIRECT, LUMEN_CLI  # noqa: E402


# ── 1. Token storage and the headless browser ───────────────────────────────
class MemoryStorage(TokenStorage):
    """Pre-loaded with lumen-cli (pre-registered, ruling R4), or empty so the client uses DCR."""

    def __init__(self, client) -> None:
        self.tokens, self.client = None, client

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens) -> None:
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, client_info) -> None:
        self.client = client_info


_redirect: dict = {}


async def open_browser(url: str) -> None:
    """Follow nothing: the test AS answers /authorize with a 302 to the loopback callback."""
    async with httpx2.AsyncClient() as h:
        r = await h.get(url, follow_redirects=False)
    _redirect.update(parse_qs(urlparse(r.headers["location"]).query))


async def read_callback() -> AuthorizationCodeResult:
    if "error" in _redirect:
        raise SystemExit(f"authorize refused: {_redirect['error'][0]}")
    return AuthorizationCodeResult(code=_redirect["code"][0], state=_redirect.get("state", [None])[0],
                                   iss=_redirect.get("iss", [None])[0])


# ── 2. The flow ─────────────────────────────────────────────────────────────
async def run(url: str, dcr: bool, yes: bool, tool: str, args: dict, save: Path | None) -> None:
    meta = OAuthClientMetadata(client_name="lumen-live-check", redirect_uris=[AnyUrl(CLI_REDIRECT)],
                               token_endpoint_auth_method="none", grant_types=["authorization_code", "refresh_token"])
    storage = MemoryStorage(None if dcr else LUMEN_CLI)
    oauth = OAuthClientProvider(url, meta, storage, open_browser, read_callback)

    async def answer(context, params) -> ElicitResult:
        return ElicitResult(action="accept", content={"confirm": True}) if yes else ElicitResult(action="decline")

    async with create_mcp_http_client(auth=oauth) as http:
        async with Client(streamable_http_client(url, http_client=http), elicitation_callback=answer) as c:
            result = await c.call_tool(tool, args)
    claims = jwt.decode(storage.tokens.access_token, options={"verify_signature": False})  # display only
    print(f"registration: {'DCR' if dcr else 'pre-registered lumen-cli'}")
    print(f"token claims: aud={claims['aud']} scope={claims['scope']} client_id={claims['client_id']} "
          f"sub={claims['sub']}")
    print("result:", "isError " + result.content[0].text if result.is_error else json.dumps(result.structured_content))
    if save is not None:
        fd = os.open(save, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(storage.tokens.access_token + "\n")
        print(f"access token saved to {save} (mode 0600)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Live OAuth flow against the local test AS (127.0.0.1 only)")
    ap.add_argument("--url", required=True, help="resource server, e.g. http://127.0.0.1:8765/mcp")
    ap.add_argument("--dcr", action="store_true", help="register dynamically instead of using lumen-cli")
    ap.add_argument("--yes", action="store_true", help="accept a confirmation question (label_ticket)")
    ap.add_argument("--save-token", type=Path, help="write the access token to this file (mode 0600)")
    ap.add_argument("tool")
    ap.add_argument("arguments", help="tool arguments as a JSON object")
    ns = ap.parse_args()
    if urlparse(ns.url).hostname != "127.0.0.1":
        raise SystemExit("this check talks to 127.0.0.1 only")
    asyncio.run(run(ns.url, ns.dcr, ns.yes, ns.tool, json.loads(ns.arguments), ns.save_token))


if __name__ == "__main__":
    main()
