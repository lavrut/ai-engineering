"""The ML lessons' Try-it scripts, run as the lessons say, against the output the lessons print.

Each lesson's "Try it" saves small scripts (`Save this as `ml/python/NAME.py``), runs them with
`.venv-ml/bin/python NAME.py` from `ml/python`, and shows "You should see" with the output. This
test extracts each script, runs it from a temp folder with `ml/python` as the working directory
(the scripts only read), and compares the output with the lesson's text block line for line, so
a change to the data, the code or a model fails here and names the lesson to update. Read-only
track commands quoted with their output (`mlops.py runs` and `compare`) are checked the same way;
"The output starts:" compares the first lines only.

The other numbers the lessons state are pinned in app/tests/mllessons.test.ts against the
committed reports, runs and registry.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ML = Path(__file__).resolve().parent.parent.parent
LESSONS = ML.parent / "app" / "src" / "content" / "lessons" / "ml"
PY = ML / "python"

BLOCK = re.compile(r"```(\w*)\n(.*?)```", re.S)
SAVE = re.compile(r"Save this as `ml/python/(\w+\.py)`")


def tryit(lesson: str) -> str:
    text = (LESSONS / f"{lesson}.mdx").read_text("utf-8")
    return text[text.index("\n## Try it\n"):]


def cases():
    """(lesson, command, argv, expected output) for every script run followed by its output."""
    out = []
    for path in sorted(LESSONS.glob("*.mdx")):
        lesson = path.stem
        body = tryit(lesson)
        scripts: dict[str, str] = {}
        pending = None                                  # (command, argv) waiting for its output
        last = 0
        for m in BLOCK.finditer(body):
            lang, code = m.group(1), m.group(2)
            before = body[last:m.start()]
            last = m.end()
            if lang == "python":
                name = SAVE.findall(before)
                assert name, f"{lesson}: a python block with no 'Save this as' line"
                scripts[name[-1]] = code
            elif lang == "bash":
                pending = None
                for line in code.splitlines():
                    hit = re.fullmatch(r"\.\./\.\./\.venv-ml/bin/python (\w+\.py)(.*)", line.strip())
                    if hit:
                        name, args = hit.group(1), hit.group(2).split()
                        if name in scripts or (name == "mlops.py" and args[:1] in (["runs"], ["compare"])):
                            pending = (line.strip(), name, args)
            elif lang == "text" and pending and ("You should see" in before or "The output starts" in before):
                whole = "The output starts" not in before
                out.append(pytest.param(lesson, pending, code, scripts.get(pending[1]), whole,
                                        id=f"{lesson}:{pending[1]} {' '.join(pending[2])}".strip()))
                pending = None
    return out


CASES = cases()


def test_every_saved_script_is_checked():
    names = {(c.values[0], c.values[1][1]) for c in CASES}
    for path in sorted(LESSONS.glob("*.mdx")):
        for name in SAVE.findall(tryit(path.stem)):
            assert (path.stem, name) in names, f"{path.stem}: {name} has no checked output"
    assert len(CASES) == 11


@pytest.mark.parametrize("lesson, command, expected, script, whole", CASES)
def test_tryit_output(lesson, command, expected, script, whole, tmp_path, pinned_stack):
    _, name, args = command
    target = PY / name
    if script is not None:
        target = tmp_path / name
        target.write_text(script, "utf-8")
    env = {"PYTHONPATH": str(PY), "PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"}
    run = subprocess.run([sys.executable, str(target), *args], cwd=PY, env=env,
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stderr
    got, want = run.stdout.splitlines(), expected.splitlines()
    if not whole:                                   # "The output starts:" quotes the first lines
        got = got[:len(want)]
    assert got == want, f"{lesson}: {command[0]}"


def test_lesson9_prediction_line():
    """Lesson 9's curl output is exactly what /predict returns for its text (FastAPI's compact JSON)."""
    import json
    import train
    model = json.loads((ML / "models" / "v1.json").read_text("utf-8"))
    text = "my mortgage payment was misapplied"
    body = {**train.score(model, text), "model_version": model["version"]}
    line = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    lesson = tryit("09-serving")
    assert f"""curl -s localhost:8000/predict -d '{{"text": "{text}"}}'""" in lesson
    assert f"```text\n{line}\n```" in lesson


def test_lesson10_monitor_rows(pinned_stack):
    """Lesson 10 quotes the start of monitor.py's monthly table and its three calibrated rows."""
    run = subprocess.run([sys.executable, str(PY / "monitor.py")], cwd=PY, capture_output=True, text=True,
                         timeout=600, env={"PYTHONPATH": str(PY), "PATH": "/usr/bin:/bin"})
    assert run.returncode == 0, run.stderr
    out = run.stdout.splitlines()
    starts, rows = [b for lang, b in BLOCK.findall(tryit("10-drift")) if lang == "text"][:2]
    first = starts.splitlines()
    i = out.index(first[0])
    assert out[i:i + len(first)] == first
    for line in rows.splitlines():
        assert line in out
