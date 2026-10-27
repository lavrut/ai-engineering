"""Put lumen_core on sys.path (no packaging) so tests import it directly, and give every test its own
random signing key (ruling R25): a fresh 32-byte key in a tmp file, named by LUMEN_SIGNING_KEY_FILE.
No test ever reads mcp/data/dev-signing.key."""
import os
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "python"))


def write_key_file(path: Path) -> Path:
    """A key file in the AS's format: 32 random bytes as hex, one line, mode 0600."""
    path.write_text(secrets.token_hex(32) + "\n", encoding="ascii")
    os.chmod(path, 0o600)
    return path


@pytest.fixture(autouse=True)
def signing_key(tmp_path_factory, monkeypatch) -> Path:
    path = write_key_file(tmp_path_factory.mktemp("key") / "signing.key")
    monkeypatch.setenv("LUMEN_SIGNING_KEY_FILE", str(path))
    return path
