"""Tests for the labelling MCP App: the ui:// resource, its link from label_ticket, and the HTML itself.

The App is verified without rendering it (ruling R3); the course's AppPreview aid renders it later.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp_types import ElicitResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lumen_core import CATEGORIES  # noqa: E402
from ops_server import APP_PATH, APP_URI, build_ops_server  # noqa: E402

MIME = "text/html;profile=mcp-app"
HTML = APP_PATH.read_text(encoding="utf-8")


def with_client(fn, tmp_path, **kw):
    async def go():
        async with Client(build_ops_server(tmp_path / "labels.jsonl"), **kw) as c:
            return await fn(c)
    return asyncio.run(go())


def test_app_resource_mime(tmp_path):
    async def run(c):
        listed = await c.list_resources()
        read = await c.read_resource(APP_URI)
        return listed, read
    listed, read = with_client(run, tmp_path)
    assert [str(r.uri) for r in listed.resources] == [APP_URI] == ["ui://lumen/label.html"]
    assert listed.resources[0].mime_type == MIME
    [content] = read.contents
    assert content.mime_type == MIME and content.text == HTML
    assert content.meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": [], "frameDomains": [],
                                         "baseUriDomains": []}  # no external origins


def test_label_tool_links_app(tmp_path):
    async def run(c):
        return (await c.list_tools()).tools
    [tool] = with_client(run, tmp_path)
    assert tool.name == "label_ticket"
    assert tool.meta["ui"]["resourceUri"] == APP_URI
    assert tool.annotations.read_only_hint is False  # the write annotations survive
    assert sorted(tool.input_schema["properties"]) == ["id", "label"]


def test_app_html_no_external_origins():
    assert not re.search(r"https?://", HTML, re.I)
    assert not re.search(r"//[a-z0-9.-]+\.[a-z]{2,}", HTML, re.I)  # protocol-relative URLs
    assert not re.search(r"<(script|link|img|iframe)[^>]+\b(src|href)=", HTML, re.I)
    assert "@import" not in HTML and "url(" not in HTML and "fetch(" not in HTML and "XMLHttpRequest" not in HTML


def test_app_html_accessibility_and_protocol():
    for cat in CATEGORIES:
        assert f'value="{cat}"' in HTML
    assert re.search(r'<label[^>]+for="category"', HTML) and 'id="category"' in HTML
    assert re.search(r'aria-live="polite"', HTML) and 'role="status"' in HTML
    assert ">Confirm<" in HTML and ">Cancel<" in HTML
    assert "prefers-color-scheme: dark" in HTML
    for method in ("ui/initialize", "ui/notifications/initialized", "ui/notifications/tool-input",
                   "ui/notifications/tool-result", "tools/call", "2026-01-26"):
        assert method in HTML
    assert "label_ticket" in HTML


def test_app_button_does_not_bypass_confirmation(tmp_path):
    """The App's Confirm is just a tools/call; the server still asks. Nothing is written if declined."""
    path = tmp_path / "labels.jsonl"
    asked: list = []

    async def cb(ctx, params):
        asked.append(params.message)
        return ElicitResult(action="decline")

    async def run(c):
        return await c.call_tool("label_ticket", {"id": "t001", "label": "bug"})
    r = with_client(run, tmp_path, elicitation_callback=cb)
    assert r.is_error and len(asked) == 1 and not path.exists()
