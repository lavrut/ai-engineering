"""The ontology lessons' Try-it commands, run as the lessons print them, against the output the lessons show.

Each lesson's "## Try it" has ```bash blocks, each followed by a ```text block with what the reader
should see. This test runs every bash block once, from the repo root, with bash (the commands use
`.venv-ontology/bin/python` and a few shell tools), and compares its stdout with the text block, line
for line. A block whose prose says it exits with status 1 must exit 1; every other block must exit 0.
The one command not run is lesson 1's setup line (it installs from the network); the test checks it is
the README's.

None of these commands can pay: no track's record mode or paid script is named, and ANTHROPIC_API_KEY
is removed from the environment they run in. The scratch files the lessons write under /tmp go to a
per-test temporary folder instead. No command may change a committed file: each test snapshots the
ontology's files and every input the scripts read, fails if any changed, and puts the bytes back first.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LESSONS = ROOT / "app" / "src" / "content" / "lessons" / "ontology"
README = ROOT / "README.md"

SETUP = "python3 -m venv .venv-ontology && .venv-ontology/bin/pip install -r ontology/requirements.txt"
BLOCK = re.compile(r"```(\w*)\n(.*?)```", re.S)
PAID = re.compile(r"--mode record|--record|ANTHROPIC_API_KEY|agents/run\.py|triage_lab\.py|harness\.py|record_|--yes|curl|https?://")
EXITS_1 = re.compile(r"exits? with status 1")
# The committed files a command could touch: the ontology's own, and every input its scripts read.
WATCHED = [ROOT / "ontology", ROOT / "llms" / "data", ROOT / "ml" / "data" / "category_map.json",
           ROOT / "agents" / "data" / "traces", ROOT / "data" / "contracts", ROOT / "data" / "data"]


def tryit(path: Path) -> str:
    text = path.read_text("utf-8")
    return text[text.index("\n## Try it\n"):]


def cases():
    """(lesson, command, expected text, exit code) for every bash block followed by its output."""
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
                assert "You should see" in between, f"{path.stem}: say what the output is before it"
                command, intro = pending
                exit_code = 1 if EXITS_1.search(intro) else 0
                out.append(pytest.param(path.stem, command, code, exit_code,
                                        id=f"{path.stem}:{command.strip().splitlines()[0][:70]}"))
                pending = None
        assert pending is None, f"{path.stem}: the last bash block shows no output"
    return out


CASES = cases()


def snapshot() -> dict[str, bytes]:
    """The bytes of every committed file the commands could touch."""
    files = []
    for w in WATCHED:
        files += [w] if w.is_file() else sorted(p for p in w.rglob("*") if p.is_file())
    return {str(p.relative_to(ROOT)): p.read_bytes() for p in files
            if "__pycache__" not in p.parts and ".pytest_cache" not in p.parts}


def env() -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    return e


def test_every_lesson_has_a_try_it_with_commands():
    lessons = sorted(p.stem for p in LESSONS.glob("*.mdx"))
    assert lessons == ["01-what", "02-lumen", "03-grounding", "04-architectures", "05-capstone"]
    per = {lesson: sum(1 for c in CASES if c.values[0] == lesson) for lesson in lessons}
    assert per == {"01-what": 1, "02-lumen": 3, "03-grounding": 4, "04-architectures": 4, "05-capstone": 4}


def test_the_setup_line_is_the_readmes():
    assert SETUP in tryit(LESSONS / "01-what.mdx")
    assert SETUP in README.read_text("utf-8")


def test_no_command_can_pay():
    for c in CASES:
        assert not PAID.search(c.values[1]), c.values[1]
        assert ".venv-ontology/bin/python" in c.values[1] or c.values[1].lstrip().startswith("grep "), c.values[1]
    assert "ANTHROPIC_API_KEY" not in env()


def test_the_exit_codes_are_read_from_the_prose():
    """Four blocks say they exit with status 1: the bad record, the record as ticket and answer, the typo in a
    permission, the gate."""
    assert sorted(c.values[0] for c in CASES if c.values[3] == 1) == ["02-lumen", "02-lumen", "04-architectures", "05-capstone"]


@pytest.fixture
def unchanged():
    """Fails a command that changed a committed file, after putting the file back."""
    before = snapshot()
    yield
    after = snapshot()
    changed = sorted(k for k in before.keys() | after.keys() if after.get(k) != before.get(k))
    for k in changed:
        if k in before:
            (ROOT / k).write_bytes(before[k])
        else:
            (ROOT / k).unlink()
    assert not changed, f"a Try-it command changed a committed file: {changed}"


@pytest.mark.parametrize("lesson, command, expected, exit_code", CASES)
def test_tryit_output(lesson, command, expected, exit_code, unchanged, tmp_path):
    # The lessons' scratch files live under /tmp; each run gets its own folder instead, so two runs never collide
    # and pytest cleans up. No expected output names a /tmp path.
    assert "/tmp/" not in expected
    command = command.replace("/tmp/", f"{tmp_path}/")
    run = subprocess.run(["bash", "-c", command], cwd=ROOT, env=env(), capture_output=True, text=True, timeout=600)
    assert run.returncode == exit_code, f"{lesson}: exit {run.returncode}\n{run.stdout}\n{run.stderr}"
    assert run.stdout.splitlines() == expected.splitlines(), f"{lesson}: {command.strip().splitlines()[0]}\n{run.stdout}"
