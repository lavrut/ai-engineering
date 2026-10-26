"""Entry point for Inspect: importing triage_inspect registers the `replay` model provider.
Inspect loads this module (see pyproject.toml) before it resolves `--model replay/...`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # find triage_inspect next to this file

import triage_inspect  # noqa: E402,F401
