"""Put lumen_core on sys.path (no packaging) so tests import it directly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "python"))
