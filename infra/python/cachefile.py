#!/usr/bin/env python3
"""
cachefile: the response cache on disk, so a restart keeps what it paid for. One JSON object
keyed by cache key; the body bytes are base64. Standard library only.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

from stages import CacheEntry

# ── 1. Load ─────────────────────────────────────────────────────────────────
def load_cache(path: Path) -> dict[str, CacheEntry]:
    """The saved cache; {} when the file is missing, and {} with a warning on stderr when it is corrupt."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: CacheEntry(status=int(v["status"]), headers=dict(v["headers"]),
                              body=base64.b64decode(v["body"], validate=True), stop_reason=v.get("stop_reason"),
                              stored_at=float(v["stored_at"]), tools=bool(v.get("tools", False)))
                for k, v in raw.items()}
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"warning: {path}: unreadable cache, starting empty ({type(e).__name__})", file=sys.stderr)
        return {}


# ── 2. Save ─────────────────────────────────────────────────────────────────
def save_cache(path: Path, cache: dict[str, CacheEntry]) -> None:
    """Write beside the file then os.replace, so a reader sees the old file or the new one, never half."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {k: {"status": e.status, "headers": e.headers, "body": base64.b64encode(e.body).decode("ascii"),
               "stop_reason": e.stop_reason, "stored_at": e.stored_at, "tools": e.tools}
           for k, e in cache.items()}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    os.replace(tmp, path)
