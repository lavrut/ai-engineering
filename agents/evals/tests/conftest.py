"""Put the eval suite, run.py and desk_core on sys.path (no packaging), as the other agents tests do."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for sub in ("llms/python", "agents", "agents/evals", "agents/evals/tests", "agents/desk/python", "agents/proxy"):   # agents first
    sys.path.insert(0, str(ROOT / sub))
