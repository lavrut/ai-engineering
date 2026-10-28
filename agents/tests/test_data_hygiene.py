"""Data hygiene (ruling R73): no committed file under agents/data carries an absolute local path, a user id, an
email address or an API key prefix. No API calls, ever.

It reads the real recordings, traces and derived files (not fixtures). A recording's streamed reply is split
across SSE deltas, so a path can hide across two events: each content block's text, thinking and partial_json
deltas are joined in order and scanned too, as is every string in each file after JSON decoding (which undoes
any escaping), beside the raw text.

Why: the Agent SDK's CLI tells the model its working directory, and the model once repeated a per-user temp path
in its reply; six recordings carried it until they were pruned (agents/prune.py). The same can recur on any new
SDK recording, so this test runs on every suite.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "agents" / "data"
SCANNED = ("recordings", "traces", "derived")

FORBIDDEN = {
    "absolute path": re.compile(r"/Users/|/private/var|/var/folders|/home/"),
    "API key prefix": re.compile(r"sk-ant-"),
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "user id value": re.compile(r"user_[0-9a-f]{16,}|_session_[0-9a-f-]{8,}"),
}


def _files() -> list[Path]:
    return sorted(p for d in SCANNED for p in (DATA / d).rglob("*.json"))


def _strings(x: Any) -> Iterator[str]:
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def _keys(x: Any) -> Iterator[str]:
    if isinstance(x, dict):
        for k, v in x.items():
            yield str(k)
            yield from _keys(v)
    elif isinstance(x, list):
        for v in x:
            yield from _keys(v)


def joined_deltas(events: list[str]) -> list[str]:
    """Each content block's streamed text, thinking and partial_json, joined in event order."""
    blocks: dict[tuple[int, str], list[str]] = {}
    for event in events:
        for line in str(event).splitlines():
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:])
            except ValueError:
                continue
            delta = data.get("delta") if isinstance(data, dict) else None
            if not isinstance(delta, dict):
                continue
            for field in ("text", "thinking", "partial_json"):
                if isinstance(delta.get(field), str):
                    blocks.setdefault((int(data.get("index", 0)), field), []).append(delta[field])
    return ["".join(parts) for parts in blocks.values()]


def findings(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    texts = [raw]
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = None
    if doc is not None:
        texts += list(_strings(doc))
        events = (doc.get("response") or {}).get("sse_events") if isinstance(doc, dict) else None
        if isinstance(events, list):
            texts += joined_deltas(events)
    out = [name for name, pat in FORBIDDEN.items() if any(pat.search(t) for t in texts)]
    if doc is not None and any(k == "user_id" for k in _keys(doc)):
        out.append("user_id field")
    return out


def test_the_scan_sees_every_data_kind():
    files = _files()
    for d in SCANNED:
        assert any(p.is_relative_to(DATA / d) for p in files), d


def test_no_local_path_user_id_email_or_key_in_committed_data():
    bad = {str(p.relative_to(ROOT)): f for p in _files() if (f := findings(p))}
    assert bad == {}


def test_a_path_split_across_sse_deltas_is_found(tmp_path):
    """The case a plain grep missed: the path arrives in two text deltas."""
    events = ['event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
              '"delta":{"type":"text_delta","text":"cwd /private/v"}}\n\n',
              'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
              '"delta":{"type":"text_delta","text":"ar/folders/x/T/work"}}\n\n']
    p = tmp_path / "rec.json"
    p.write_text(json.dumps({"request_summary": {}, "response": {"status": 200, "sse_events": events}}))
    assert findings(p) == ["absolute path"]


@pytest.mark.parametrize("text, kind", [
    ("see /Users/someone/x", "absolute path"),
    ("/home/someone", "absolute path"),
    ("key sk-ant-abc", "API key prefix"),
    ("mail me at someone@example.com", "email address"),
    ("user_0123456789abcdef0123_account__session_1234abcd-0000", "user id value"),
])
def test_each_pattern_fires(tmp_path, text, kind):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"output": {"reply": text}}))
    assert kind in findings(p)


def test_a_user_id_field_is_found(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"body": {"metadata": {"user_id": "x"}}}))
    assert findings(p) == ["user_id field"]
