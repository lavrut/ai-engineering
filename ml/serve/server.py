#!/usr/bin/env python3
"""
server — the production model behind a small HTTP API (FastAPI).

Contract (the Go server in Task 8 matches it exactly):

    GET  /health   -> 200 {"status": "ok", "model_version": "v1"}
    POST /predict  {"text": "..."}
                   -> 200 {"label", "scores": {label: float}, "low_confidence": bool,
                           "model_version"}
                   -> 400 {"error": "text must be 1 to 20000 characters"}
                          when text is "" or longer than 20,000 characters
                   -> 400 {"error": "body must be a JSON object with a string field \\"text\\""}
                          when the body is not valid UTF-8 JSON, is not an object, or has no
                          string "text"

Length counts Unicode code points (Python len), not bytes: Go must count runes. Text of only
spaces is valid; it has no known word, so it gets the majority class with low_confidence true.
FastAPI's own 422 reply is replaced: every bad request is 400 with one of the two bodies above.

Run (from the repo root; ML_ROOT defaults to ml/, where registry.json and models/ live):
    uvicorn server:app --app-dir ml/serve --port 8000
    curl -s localhost:8000/predict -d '{"text": "my mortgage payment was misapplied"}'
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ML_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ML_DIR / "python"))
import mlops  # noqa: E402  (registry helpers and sha256_file)
import train  # noqa: E402  (train.score is the one scorer)

# ── 1. Configuration ────────────────────────────────────────────────────────
MAX_CHARS = 20_000
LENGTH_ERROR = {"error": f"text must be 1 to {MAX_CHARS} characters"}
BODY_ERROR = {"error": 'body must be a JSON object with a string field "text"'}


def ml_root() -> Path:
    """Where registry.json and models/ live. Read at startup so tests can point elsewhere."""
    return Path(os.environ.get("ML_ROOT", ML_DIR))


# ── 2. Loading the production model ─────────────────────────────────────────
class StartupError(RuntimeError):
    """The registry, the model file or its hash is wrong. The server must not start."""


def load_production(root: Path) -> dict:
    """Read the registry, find the production version, verify the file's SHA-256, and load it.
    The term -> position table is built once here and reused by every request."""
    reg_path = root / "registry.json"
    if not reg_path.is_file():
        raise StartupError(f"registry not found: {reg_path}")
    try:
        registry = mlops.read_json(reg_path)
        version = mlops.production_version(registry)
    except (ValueError, KeyError, TypeError) as e:
        raise StartupError(f"registry unreadable: {reg_path} ({e})") from e
    if version is None:
        raise StartupError(f"registry has no version in stage 'production': {reg_path}")
    entry = registry["versions"][version]
    model_path = root / entry["path"]
    if not model_path.is_file():
        raise StartupError(f"model file for {version} not found: {model_path}")
    actual = mlops.sha256_file(model_path)
    if actual != entry["sha256"]:
        raise StartupError(f"SHA-256 mismatch for {version}: registry says {entry['sha256']}, "
                           f"file is {actual}")
    try:
        model = json.loads(model_path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise StartupError(f"model file for {version} is not valid JSON: {model_path}") from e
    model["index"] = {t: i for i, t in enumerate(model["vocabulary"])}
    return model


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.model = load_production(ml_root())     # raises, and the server never starts
    yield


app = FastAPI(title="Complaint classifier", lifespan=lifespan)


# ── 3. Request validation ───────────────────────────────────────────────────
def bad_request(body: dict) -> JSONResponse:
    return JSONResponse(body, status_code=400)


async def read_text(request: Request) -> str | None:
    """The "text" field, or None when the body is malformed. We parse by hand so that no
    framework default (the 422 reply) can leak into the contract."""
    try:
        data = json.loads(await request.body())     # bytes: invalid UTF-8 raises ValueError
    except ValueError:
        return None
    text = data.get("text") if isinstance(data, dict) else None
    return text if isinstance(text, str) else None


# ── 4. Endpoints ────────────────────────────────────────────────────────────
@app.get("/health")
def health(request: Request):
    return {"status": "ok", "model_version": request.app.state.model["version"]}


@app.post("/predict")
async def predict(request: Request):
    text = await read_text(request)
    if text is None:
        return bad_request(BODY_ERROR)
    if not 1 <= len(text) <= MAX_CHARS:            # len counts code points, not bytes
        return bad_request(LENGTH_ERROR)
    model = request.app.state.model
    result = train.score(model, text, model["index"])
    return {**result, "model_version": model["version"]}


# ── 5. Entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
