"""The data lessons' Try-it commands, run as the lessons print them, against the output the lessons show.

Each lesson's "## Try it" has ```bash blocks, each followed by a ```text block with what the reader
should see. This test runs every bash block once, from the repo root, with bash (the commands use
`.venv-data/bin/python`, `go` and a few shell tools), and compares its stdout with the text block:

- "You should see:" compares the whole output, line for line;
- "The last line begins:" compares the last lines' starts, with runs of spaces and tabs squashed
  (pytest and go test end with a time, or "(cached)", that changes from run to run).

A block whose prose says "exits with status 1" must exit 1; every other block must exit 0. The one
command not run is lesson 1's setup line (it installs from the network); the test checks it is the
README's. `quality.py --live` judges freshness as of today: its first line is compared with today's
date, and once a dataset is past its max_age_days the run must fail, which lesson 3 says is the point.

None of these commands can pay: no track's record mode is named, and ANTHROPIC_API_KEY is removed
from the environment they run in. Some commands rewrite derived files (catalog.py, gate.py) with
the bytes already committed, so the test also checks that no committed file under data/ changed.
"""
import hashlib
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LESSONS = ROOT / "app" / "src" / "content" / "lessons" / "data"
README = ROOT / "README.md"
sys.path.insert(0, str(HERE.parent))

import quality as Q  # noqa: E402

SETUP = "python3 -m venv .venv-data && .venv-data/bin/pip install -r data/requirements.txt"
BLOCK = re.compile(r"```(\w*)\n(.*?)```", re.S)
PAID = re.compile(r"--mode record|--record|ANTHROPIC_API_KEY|agents/run\.py|triage_lab\.py|harness\.py|record_|--yes")
WATCHED = [ROOT / "data" / "data", ROOT / "data" / "contracts", ROOT / "data" / "fixtures"]


def tryit(path: Path) -> str:
    text = path.read_text("utf-8")
    return text[text.index("\n## Try it\n"):]


def cases():
    """(lesson, command, expected text, mode, exit code) for every bash block followed by its output."""
    out = []
    for path in sorted(LESSONS.glob("*.mdx")):
        body = tryit(path)
        pending, last = None, 0  # pending: (command, the paragraph that introduces it)
        for m in BLOCK.finditer(body):
            lang, code = m.group(1), m.group(2)
            between = body[last:m.start()]
            last = m.end()
            if lang == "bash":
                assert pending is None, f"{path.stem}: a bash block with no output shown: {pending[0][:60]}"
                if code.strip() == SETUP:
                    continue
                pending = (code, between.strip().split("\n\n")[-1])
            elif lang == "text":
                assert pending is not None, f"{path.stem}: a text block with no command before it"
                command, intro = pending
                if "The last line begins" in between:
                    mode = "begins"
                else:
                    assert "You should see" in between, f"{path.stem}: say what the output is before it"
                    mode = "whole"
                exit_code = 1 if "exits with status 1" in intro + between else 0
                out.append(pytest.param(path.stem, command, code, mode, exit_code,
                                        id=f"{path.stem}:{command.strip().splitlines()[0][:70]}"))
                pending = None
        assert pending is None, f"{path.stem}: the last bash block shows no output"
    return out


CASES = cases()


def squash(line: str) -> str:
    return re.sub(r"[ \t]+", " ", line).strip()


def digest() -> dict[str, str]:
    """sha256 of every committed file the commands could touch."""
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for d in WATCHED for p in sorted(d.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def env() -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    return e


def stale_today() -> bool:
    """True once any dataset quality.py checks is older than its contract allows, as of today."""
    doc = Q.build(date.today())
    return any(not Q.as_expected(entry) for entry in doc["datasets"].values())


def test_every_lesson_has_a_try_it_with_commands():
    lessons = sorted(p.stem for p in LESSONS.glob("*.mdx"))
    assert lessons == ["01-fabric", "02-catalog", "03-quality", "04-contract", "05-enforce", "06-capstone"]
    per = {lesson: sum(1 for c in CASES if c.values[0] == lesson) for lesson in lessons}
    assert all(n >= 1 for n in per.values()), per
    assert len(CASES) == 18


def test_the_setup_line_is_the_readmes():
    assert SETUP in tryit(LESSONS / "01-fabric.mdx")
    assert SETUP in README.read_text("utf-8")


def test_no_command_can_pay():
    for c in CASES:
        assert not PAID.search(c.values[1]), c.values[1]
    assert "ANTHROPIC_API_KEY" not in env()


@pytest.fixture(scope="module")
def unchanged():
    before = digest()
    yield
    assert digest() == before, "a Try-it command changed a committed file under data/"


@pytest.mark.parametrize("lesson, command, expected, mode, exit_code", CASES)
def test_tryit_output(lesson, command, expected, mode, exit_code, unchanged):
    run = subprocess.run(["bash", "-c", command], cwd=ROOT, env=env(), capture_output=True, text=True, timeout=600)
    got, want = run.stdout.splitlines(), expected.splitlines()
    if "quality.py --live" in command:
        want[0] = f"as of {date.today().isoformat()}"
        if stale_today():  # lesson 3: the live run fails once a dataset is too old, on purpose
            assert run.returncode == 1 and got[0] == want[0], run.stdout
            return
    assert run.returncode == exit_code, f"{lesson}: exit {run.returncode}\n{run.stdout}\n{run.stderr}"
    if mode == "begins":
        tail = [squash(x) for x in got[-len(want):]]
        assert all(t.startswith(squash(w)) for t, w in zip(tail, want)) and len(tail) == len(want), f"{lesson}: {run.stdout}"
    else:
        assert got == want, f"{lesson}: {command.strip().splitlines()[0]}"
