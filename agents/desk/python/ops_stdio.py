#!/usr/bin/env python3
"""ops_stdio — lumen-ops over stdio, for the hand-go engine's own child process only.

The Python desks reach lumen-ops in memory (desk_core.open_desk_tools -> build_ops_server()). Go cannot
share a Python process, so agents/desk/go's MCP Tools spawns this file and speaks MCP over its stdin and
stdout: the same server (build_ops_server(labels_path=...), no HTTP and so no bearer check, exactly as the
in-memory one), with the pipe private to the parent that spawned it. ops_server.py's "HTTP only by design" is
about the learner-facing deployment, which stays HTTP-only (with OAuth) for every other host; this file imports
it and changes nothing there (ruling R48).

Run (by the Go engine, never by hand):
    python agents/desk/python/ops_stdio.py --labels PATH
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "mcp" / "python"))

from ops_server import build_ops_server  # noqa: E402  (import only, never edited)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="lumen-ops over stdio (the hand-go engine's child process)")
    ap.add_argument("--labels", type=Path, required=True, help="append-only label file")
    args = ap.parse_args(argv)
    build_ops_server(labels_path=args.labels).run()


if __name__ == "__main__":
    main()
