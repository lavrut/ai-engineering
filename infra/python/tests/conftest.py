import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "infra" / "data" / "fixtures"
sys.path.insert(0, str(ROOT / "infra" / "python"))


@pytest.fixture
def tmp_traces(tmp_path):
    d = tmp_path / "traces"
    d.mkdir()
    return d
