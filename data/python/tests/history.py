"""Whether this checkout has its full git history, probed once per session (ruling D17).

A shallow clone (CI's default checkout) dates every file to its one commit, and a zip
or `git archive` copy has no git at all. The tests that measure real commit dates skip
there, by name, and say how to get the history; everything else still runs.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quality as Q  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
NEEDS_HISTORY = "needs full git history: run git fetch --unshallow"
HAS_HISTORY = Q.has_history(ROOT)
needs_history = pytest.mark.skipif(not HAS_HISTORY, reason=NEEDS_HISTORY)
