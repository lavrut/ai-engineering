"""
responses: what the gateway reads from and writes into bodies: the canonical JSON, a request's
text and cache key, an error body, and the usage, stop_reason and retry-after of an answer, JSON
or a server-sent event stream (message_start carries the first usage; message_delta the final
output tokens and the stop_reason). Standard library only; a malformed body answers None.
"""
from __future__ import annotations

import hashlib
import json

from ledger import USAGE_FIELDS


def canonical(obj) -> bytes:
    """Sorted keys, no spaces, UTF-8 unescaped: the one serialisation both twins can produce."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def error_body(kind: str, message: str) -> bytes:
    """The Messages API's error shape."""
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode()


def messages_text(body: dict) -> str:
    """The messages' text in order: string contents and text blocks."""
    parts = []
    for m in body.get("messages") or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts += [b["text"] for b in c if isinstance(b, dict) and isinstance(b.get("text"), str)]
    return "\n".join(parts)


def cache_key(path: str, body: bytes) -> str:
    """sha256 hex of the path, a newline and the body bytes as received (the body names the model)."""
    return hashlib.sha256(path.encode() + b"\n" + body).hexdigest()


def is_sse(headers: dict[str, str], body: bytes) -> bool:
    ct = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
    return "text/event-stream" in ct or body.lstrip().startswith((b"event:", b"data:"))


def sse_data(body: bytes) -> list[dict]:
    out = []
    for line in body.decode("utf-8", "replace").splitlines():
        if line.startswith("data:"):
            try:
                d = json.loads(line[5:])
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def json_body(body: bytes) -> dict | None:
    try:
        d = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return d if isinstance(d, dict) else None


def response_usage(headers: dict[str, str], body: bytes) -> dict | None:
    """The four usage fields (a missing one is 0), or None when the answer carries no usage."""
    usage: dict | None = None
    if is_sse(headers, body):
        for d in sse_data(body):
            if d.get("type") == "message_start" and isinstance((d.get("message") or {}).get("usage"), dict):
                usage = dict(d["message"]["usage"])
            elif d.get("type") == "message_delta" and isinstance(d.get("usage"), dict):
                usage = {**(usage or {}), **{k: v for k, v in d["usage"].items() if v is not None}}
    else:
        d = json_body(body)
        usage = dict(d["usage"]) if d and isinstance(d.get("usage"), dict) else None
    if usage is None:
        return None
    return {k: int(usage.get(k) or 0) for k in USAGE_FIELDS}


def stop_reason(headers: dict[str, str], body: bytes) -> str | None:
    """Why the model stopped (end_turn, tool_use, max_tokens, ...), or None when the body does not say."""
    if is_sse(headers, body):
        reason = None
        for d in sse_data(body):
            if d.get("type") == "message_start":
                reason = (d.get("message") or {}).get("stop_reason") or reason
            elif d.get("type") == "message_delta":
                reason = (d.get("delta") or {}).get("stop_reason") or reason
        return reason if isinstance(reason, str) else None
    d = json_body(body)
    r = d.get("stop_reason") if d else None
    return r if isinstance(r, str) else None


def retry_after_ms(headers: dict[str, str]) -> int:
    """An answer's retry-after (seconds) in ms; 0 when absent or unreadable."""
    try:
        return max(0, int(float({k.lower(): v for k, v in headers.items()}.get("retry-after", 0)) * 1000))
    except ValueError:
        return 0
