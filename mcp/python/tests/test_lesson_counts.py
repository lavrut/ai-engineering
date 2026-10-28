"""The MCP lessons tell the learner how many tests a pytest command passes ("We got `8 passed`"). This
runs each such command from the lessons with --collect-only and checks the count, so adding or removing a
test cannot leave a lesson saying the wrong number again (review finding I2). app/tests/mcplessons.test.ts
pins the rest of each lesson's numbers."""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LESSONS = ROOT / "app" / "src" / "content" / "lessons" / "mcp"
COMMAND = re.compile(r"^\.venv-mcp/bin/python -m pytest (?P<args>.+)$", re.M)
CLAIM = re.compile(r"We got `(?P<n>\d+) passed`")


def claims() -> list[tuple[str, str, int]]:
    """(lesson, pytest arguments, the count it states) for every lesson that runs pytest."""
    out = []
    for path in sorted(LESSONS.glob("*.mdx")):
        text = path.read_text(encoding="utf-8")
        commands, counts = COMMAND.findall(text), CLAIM.findall(text)
        assert len(commands) == len(counts), f"{path.name}: {len(commands)} pytest commands, {len(counts)} stated counts"
        out += [(path.stem, a, int(n)) for a, n in zip(commands, counts)]
    return out


def test_the_lessons_state_three_counts():
    assert [(lesson, n) for lesson, _, n in claims()] == [("08-write-tools", 3), ("09-auth", 8), ("10-apps", 5)]


@pytest.mark.parametrize(("lesson", "args", "stated"), claims())
def test_lesson_pytest_count_matches_a_real_collection(lesson, args, stated):
    run = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                          *[a for a in shlex.split(args) if a != "-q"]], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
    collected = [line for line in run.stdout.splitlines() if "::" in line]
    assert len(collected) == stated, f"{lesson} says {stated} passed; `pytest {args}` collects {len(collected)}"
