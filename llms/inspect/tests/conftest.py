"""Put the lab and the Inspect task on sys.path; skip cleanly when Inspect is not installed."""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
for p in (HERE.parent / "python", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

pytest.importorskip("inspect_ai", reason="pip install -r llms/inspect/requirements.txt in a venv")
