"""classify — CFPB-style complaint text through the Track 3 production model.

This is NOT used to label Lumen tickets: its categories are the complaint products, not the
ticket categories. It reuses ml/serve's loader (registry + SHA-256 check) and train.score,
so the answer is the one /predict gives.
"""
from __future__ import annotations

import importlib.util
import sys
import threading

from . import REPO_ROOT

# ── 1. Loading the production model ─────────────────────────────────────────
_SERVER_PATH = REPO_ROOT / "ml" / "serve" / "server.py"
_cache: dict = {}
_lock = threading.Lock()   # tool calls can run on worker threads: load the model once


def _load() -> tuple:
    """Import ml/serve/server.py by path (it puts ml/python on sys.path itself), then load
    the production model once. ML_ROOT is honoured, as in the server."""
    with _lock:
        if not _cache:
            _load_locked()
    return _cache["server"], _cache["model"]


def _load_locked() -> None:
    spec = importlib.util.spec_from_file_location("ml_serve_server", _SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ml_serve_server"] = mod
    spec.loader.exec_module(mod)
    _cache["server"] = mod
    _cache["model"] = mod.load_production(mod.ml_root())


# ── 2. Classifying ──────────────────────────────────────────────────────────
def classify(text: str) -> dict:
    """{label, scores, low_confidence, model_version}, the shape of ml/serve's /predict."""
    server, model = _load()
    result = server.train.score(model, text, model["index"])
    return {**result, "model_version": model["version"]}
