"""Lumen MCP client, part 2 of 3: connecting over stdio or Streamable HTTP (section 1).
client.py re-exports every name here; the Go twin is mcp/go/client/connect.go.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from client_calls import Answerer, deny, elicitation_callback


# ── 1. Connecting (stdio / HTTP) ────────────────────────────────────────────
@dataclass(frozen=True)
class HttpTarget:
    """A Streamable HTTP endpoint plus the headers every request carries."""
    url: str
    headers: dict[str, str]


def http_target(url: str, token: str | None = None) -> Any:
    """The one place the HTTP path is built. With a token, every request carries
    `Authorization: Bearer <token>`; the URL is left exactly as given (a token never goes in it)."""
    if token is None:
        return url
    return HttpTarget(url, {"Authorization": f"Bearer {token}"})


@dataclass
class Refusal:
    """The last non-2xx answer the server gave to a POST (an MCP message), so a failed call can
    say why in one line (refusal_line). The Go client records and prints the same."""
    status: int | None = None


def refusal_line(status: int) -> str:
    return f"client: the server refused the request: HTTP {status} {HTTPStatus(status).phrase}"


@asynccontextmanager
async def connect(target: Any, answer: "Answerer | None" = None, refusal: Refusal | None = None):
    """Open a Client on a StdioServerParameters, an http(s) URL, an HttpTarget, or (in tests) an
    in-process server. The elicitation callback is what declares the `elicitation` capability, so
    a server's confirmation step only runs when the client can answer it. Over HTTP, `refusal`
    (if given) records a non-2xx answer. Redirects: the SDK follows one only within the endpoint's
    origin (the client itself has follow_redirects=False), so the headers never go elsewhere."""
    callback = elicitation_callback(answer or deny)
    if isinstance(target, str) and target.startswith(("http://", "https://")):
        target = HttpTarget(target, {})
    if isinstance(target, HttpTarget):
        # The SDK's own factory (MCP timeouts), from a private module verified on mcp==2.3.0.
        from mcp.shared._httpx_utils import create_mcp_http_client
        async with create_mcp_http_client(headers=target.headers) as http:
            if refusal is not None:
                async def record(response) -> None:
                    if response.request.method == "POST" and response.status_code >= 300:
                        refusal.status = response.status_code
                http.event_hooks["response"].append(record)
            async with Client(streamable_http_client(target.url, http_client=http),
                              elicitation_callback=callback) as client:
                yield client
        return
    async with Client(target, elicitation_callback=callback) as client:
        yield client
