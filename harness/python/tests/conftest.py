import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import harness


@pytest.fixture(autouse=True)
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(harness, "WORKSPACE", root)
    monkeypatch.setattr(harness, "TRACE_FILE", tmp_path / "trace.jsonl")
    return root


def block(name, input, id="tu_1"):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def text(s):
    return SimpleNamespace(type="text", text=s)


def reply(stop_reason, *blocks, input_tokens=100):
    return SimpleNamespace(
        content=list(blocks), stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=10))
