"""labels — an append-only JSONL label store with provenance and a file lock."""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import CATEGORIES, LABELS_PATH


# ── 1. Writing ──────────────────────────────────────────────────────────────
def write_label(ticket_id: str, label: str, *, source: str, client_id: str,
                model: str | None, confirmed: bool, path: Path = LABELS_PATH) -> dict:
    """Append one row under an exclusive flock, so concurrent processes never interleave.
    The whole line goes out in a single os.write on an O_APPEND descriptor."""
    if label not in CATEGORIES:
        raise ValueError("label must be one of: " + ", ".join(CATEGORIES))
    if source not in ("agent", "human"):
        raise ValueError("source must be agent or human")
    row = {"ticket_id": ticket_id, "label": label, "source": source, "client_id": client_id,
           "model": model, "confirmed": bool(confirmed),
           "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, data)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return row


# ── 2. Eligibility ──────────────────────────────────────────────────────────
def eligible_for_eval(row: dict) -> bool:
    """Only a human's confirmed label may enter an eval set."""
    return row.get("source") == "human" and row.get("confirmed") is True
