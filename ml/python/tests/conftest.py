import sys
from pathlib import Path

# The tests import the course code directly: ml/python (the lab) and ml/data (the fetch script).
ML = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ML / "python"))
sys.path.insert(0, str(ML / "data"))
