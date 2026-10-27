"""lumen_core — the shared core behind both Lumen MCP servers (Python and Go match it).

Ticket text is untrusted data: it only ever leaves this package inside `untrusted_body`.
"""
from __future__ import annotations

from pathlib import Path

# ── 1. Constants ────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[4]
CATEGORIES = ["billing", "bug", "account", "how_to", "feature_request", "outage"]  # the lab's set
LABELS_PATH = REPO_ROOT / "mcp" / "data" / "labels.jsonl"    # git-ignored

MAX_QUERY_CHARS = 200
MIN_LIMIT, MAX_LIMIT, DEFAULT_LIMIT = 1, 20, 5
MAX_TEXT_CHARS = 20_000
MAX_BODY_BYTES = 131_072
