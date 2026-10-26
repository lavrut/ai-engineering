import sys
from pathlib import Path

import pytest

# The tests import the course code directly: ml/python (the lab) and ml/data (the fetch script).
ML = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ML / "python"))
sys.path.insert(0, str(ML / "data"))

# The versions that wrote the committed models, runs and reports. Floating-point sums differ in
# the last digit between releases, so a byte-for-byte rebuild only makes sense on these.
# requirements.txt pins the same three.
PINNED = {"scikit-learn": "1.8.0", "numpy": "2.5.4", "scipy": "1.17.1"}


@pytest.fixture
def pinned_stack():
    from importlib.metadata import version
    if any(version(name) != want for name, want in PINNED.items()):
        pytest.skip("byte-identical rebuild needs scikit-learn 1.8.0, numpy 2.5.4, "
                    "scipy 1.17.1 — see requirements.txt")
