"""--save-token writes a NEW file, mode 0600: it refuses an existing file or a symlink, and never prints a token."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth_live_check import save_token_file  # noqa: E402


def test_creates_a_private_file_and_parent(tmp_path):
    target = tmp_path / ".lumen" / "read.tok"
    save_token_file(target, "tok-123")
    assert target.read_text() == "tok-123\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_refuses_an_existing_file_and_leaves_it(tmp_path):
    target = tmp_path / "read.tok"
    target.write_text("keep me")
    with pytest.raises(SystemExit, match="already exists"):
        save_token_file(target, "tok-123")
    assert target.read_text() == "keep me"


def test_refuses_a_symlink_even_a_dangling_one(tmp_path):
    victim = tmp_path / "victim"
    link = tmp_path / "read.tok"
    os.symlink(victim, link)  # dangling: O_CREAT|O_EXCL still refuses to follow it
    with pytest.raises(SystemExit):
        save_token_file(link, "tok-123")
    assert not victim.exists()
