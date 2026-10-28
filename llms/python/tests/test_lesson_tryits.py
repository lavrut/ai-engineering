"""The LLMs lessons' Try-it commands, run as the lessons say, against the output the lessons print.

Each lesson's "Try it" runs the lab (`../../.venv-llms/bin/python triage_lab.py ...`) or a small
script it saves (`Save this as `llms/python/NAME.py``) from `llms/python`, and shows the output in a
text block. This test runs every such command that reads the committed data (commands on a copied
data root are skipped), in replay, and compares the output with the block line for line, so a
re-record, a relabel or a code change fails here and names the lesson to update. A block
introduced with "then:" quotes the last lines only; `echo "exit $?"` adds the exit line.

It also runs every paid command a lesson quotes an estimate for, with stdin closed: the lab prints
the estimate and declines, so nothing is called (the SDK client is replaced by one that fails).

The other numbers the lessons state are pinned in app/tests/llmslessons.test.ts.

RELABEL: lesson 6's `agreement` and `kappa_demo.py` blocks come from the 30 Claude-drafted reply labels;
reviewing the labels changes them (see the RELABEL tests in app/tests/llmslessons.test.ts).
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

import triage_lab as lab

PY = Path(__file__).resolve().parent.parent
LLMS = PY.parent
LESSONS = LLMS.parent / "app" / "src" / "content" / "lessons" / "llms"
VENV = "../../.venv-llms/bin/python "

BLOCK = re.compile(r"^( *)```(\w*)\n(.*?)^\1```", re.S | re.M)
SAVE = re.compile(r"[Ss]ave this as `llms/python/(\w+\.py)`")


def lesson_text(lesson: str) -> str:
    return (LESSONS / f"{lesson}.mdx").read_text("utf-8")


def tryit(lesson: str) -> str:
    text = lesson_text(lesson)
    return text[text.index("\n## Try it\n"):]


def dedent(code: str, indent: str) -> str:
    return "\n".join(l[len(indent):] if l.startswith(indent) else l for l in code.split("\n"))


def cases():
    """(lesson, argv, echo exit?, expected text, script, tail only?) for every command followed by its output."""
    out = []
    for path in sorted(LESSONS.glob("*.mdx")):
        lesson = path.stem
        body = tryit(lesson)
        scripts: dict[str, str] = {}
        pending = None
        last = 0
        for m in BLOCK.finditer(body):
            indent, lang, code = m.group(1), m.group(2), dedent(m.group(3), m.group(1))
            before = body[last:m.start()]
            last = m.end()
            if lang == "python":
                name = SAVE.findall(before)
                assert name, f"{lesson}: a python block with no 'Save this as' line"
                scripts[name[-1]] = code
                pending = None
            elif lang == "bash":
                pending = None
                for line in code.strip().splitlines():
                    line = line.strip()
                    if line.startswith(VENV) and "~" not in line and "$HOME" not in line:
                        argv = line[len(VENV):].split()
                        if argv[0] == "triage_lab.py" or argv[0] in scripts:
                            pending = [argv, False]
                    elif line == 'echo "exit $?"' and pending:
                        pending[1] = True
            elif lang == "text" and pending:
                tail = before.rstrip().endswith("then:")
                argv, echo = pending
                out.append(pytest.param(lesson, argv, echo, code, scripts.get(argv[0]), tail,
                                        id=f"{lesson}:{' '.join(argv)}"))
                pending = None
    return out


CASES = cases()


def run_py(argv, script, tmp_path):
    target = PY / argv[0]
    if script is not None:
        target = tmp_path / argv[0]
        target.write_text(script, "utf-8")
    env = {"PYTHONPATH": str(PY), "PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"}   # no ANTHROPIC_API_KEY
    return subprocess.run([sys.executable, str(target), *argv[1:]], cwd=PY, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)


def test_every_saved_script_is_checked():
    names = {(c.values[0], c.values[1][0]) for c in CASES}
    for path in sorted(LESSONS.glob("*.mdx")):
        for name in SAVE.findall(tryit(path.stem)):
            if name in ("new_version.py", "worse_version.py"):      # they write to a copied data root
                continue
            assert (path.stem, name) in names, f"{path.stem}: {name} has no checked output"
    assert len(CASES) == 15
    assert all("--mode" not in c.values[1] for c in CASES)   # output checks replay; paid commands go through test_quoted_estimates


@pytest.mark.parametrize("lesson, argv, echo, expected, script, tail", CASES)
def test_tryit_output(lesson, argv, echo, expected, script, tail, tmp_path):
    run = run_py(argv, script, tmp_path)
    got = run.stdout.splitlines() + ([f"exit {run.returncode}"] if echo else [])
    want = expected.splitlines()
    if tail:
        got = got[-len(want):]
    assert run.returncode == (1 if ": FAIL" in expected else 0), run.stderr
    assert got == want, f"{lesson}: {' '.join(argv)}"


def test_lesson3_matrix_is_the_demo_output(tmp_path):
    """Lesson 3's Concept shows the v1_basic priority matrix; graders_demo.py prints it first."""
    text = lesson_text("03-graders")
    script = re.search(r"```python\n(.*?)```", tryit("03-graders"), re.S).group(1)
    matrix = re.search(r"```text\n(expected .*?)```", text, re.S).group(1)
    run = run_py(["graders_demo.py"], script, tmp_path)
    assert run.stdout.splitlines()[:5] == matrix.splitlines()


def test_lesson4_compare_prose(tmp_path):
    run = run_py(["triage_lab.py", "compare", "--prompt", "v2_rules", "--vs", "v3_examples"], None, tmp_path)
    assert "diff +0.0%   band +/-15.8%" in run.stdout and "within the noise: not evidence of a difference" in run.stdout
    assert "It prints `diff +0.0%   band +/-15.8%` and `within the noise: not evidence of a difference`." in tryit("04-runner")


def test_lesson8_both_lines(tmp_path):
    """Lesson 8 quotes the five `both accuracy` lines of its first block."""
    got = []
    for model, effort in [(None, None), ("claude-sonnet-5-5", "low"), ("claude-sonnet-5-5", "high"),
                          ("claude-opus-5-5", "low"), ("claude-opus-5-5", "high")]:
        argv = ["triage_lab.py", "run", "--prompt", "v2_rules"] + (["--model", model, "--effort", effort] if model else [])
        out = run_py(argv, None, tmp_path).stdout
        got.append(re.search(r"both accuracy\s+(\S+)\s+noise band \+/-15\.8%", out).group(1))
    assert got == ["97.5%", "85.0%", "87.5%", "95.0%", "95.0%"]
    assert ("The `both accuracy` lines read 97.5%, 85.0%, 87.5%, 95.0% and 95.0%, each with `noise band +/-15.8%`."
            in tryit("08-model"))


def test_lesson10_go_block_matches_python():
    """The Go gate block repeats the Python verdict; `exit status 1` is go run's own line."""
    blocks = re.findall(r"```text\n(.*?)```", tryit("10-capstone"), re.S)
    assert blocks[3].splitlines() == blocks[0].splitlines()[:-1] + ["exit status 1"]


# ── the estimates the lessons quote, with the paid call declined ─────────────
def added_ticket_root(tmp_path, ticket_id):
    """A copied data root with one extra dev ticket, as lessons 2 and 10 do in ~/lumen-data and ~/lumen-gate."""
    root = tmp_path / "data"
    shutil.copytree(LLMS / "data", root)
    with (root / "tickets.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"id": ticket_id, "text": "Search returns nothing since this morning.", "category": "bug",
                            "priority": "normal", "tags": [], "split": "dev"}) + "\n")
    return root


ESTIMATES = [
    # lesson, argv, needs an added ticket, (estimate, worst case), the lesson's words
    ("01-what", "run --prompt v1_basic --mode live", None, ("$0.02", "$0.04"),
     "The lab prints its estimate before calling: `$0.02`, worst case `$0.04`"),
    ("02-dataset", "run --prompt v1_basic --mode record", "x001", ("$0.02", "$0.04"),
     "41 Haiku calls, `$0.02`, worst case `$0.04`"),
    ("03-graders", "run --prompt v1_basic --mode live", None, ("$0.02", "$0.04"),
     "an estimated `$0.02` with Haiku, worst case `$0.04`"),
    ("04-runner", "run --prompt v2_rules --reps 3 --mode live", None, ("$0.05", "$0.13"),
     "120 Haiku calls: the lab estimates `$0.05`, worst case `$0.13`"),
    ("05-judge", "judge --prompt v2_rules --split test --mode live", None, ("$0.47", "$0.91"),
     "20 Opus calls. The lab estimates `$0.47`, worst case `$0.91`"),
    ("07-prompt", "run --prompt v2_rules --mode record", None, ("$0.02", "$0.04"),
     "40 Haiku calls, `$0.02`, worst case `$0.04`"),
    ("07-prompt", "judge --prompt v2_rules --split dev --mode live", None, ("$0.94", "$1.82"),
     "40 Opus calls, estimated at `$0.94`, worst case `$1.82`"),
    ("08-model", "run --model claude-sonnet-5-5 --effort medium --mode record", None, ("$0.35", "$0.87"),
     "40 Sonnet calls, `$0.35`, worst case `$0.87`"),
    ("08-model", "run --model claude-opus-5-5 --effort medium --mode record", None, ("$0.70", "$1.74"),
     "Opus at `medium` would be `$0.70`, worst case `$1.74`"),
    ("10-capstone", "run --prompt v1_basic --mode record", "t061", ("$0.02", "$0.04"),
     "It counts all 41 tickets, `$0.02`, worst case `$0.04`"),
    ("10-capstone", "run --prompt v5_no_priority --mode record", "t061", ("$0.02", "$0.04"),
     "41 Haiku calls with the ticket from the first exercise, `$0.02`, worst case `$0.04`"),
    ("11-framework", "run --prompt v2_rules --split all --reps 3 --mode live", None, ("$0.08", "$0.20"),
     "about $0.08 (worst case $0.20, if every reply uses all 2,000 `max_tokens`)"),
]


@pytest.mark.parametrize("lesson, argv, added, figures, words", ESTIMATES,
                         ids=[f"{e[0]}:{e[1]}" for e in ESTIMATES])
def test_quoted_estimates(lesson, argv, added, figures, words, tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("the API must not be called")
    monkeypatch.setattr(anthropic, "Anthropic", boom)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    args = argv.split()
    if added:
        root = added_ticket_root(tmp_path, added)
        if "v5_no_priority" in argv:                      # lesson 10's worse_version.py, in the copy
            prompts = json.loads((root / "prompts.json").read_text())
            text = prompts["triage"]["v2_rules"]
            start, end = text.index("Priorities (check"), text.index("Reply:")
            prompts["triage"]["v5_no_priority"] = text[:start] + text[end:]
            (root / "prompts.json").write_text(json.dumps(prompts))
        args = ["--data", str(root)] + args
    assert lab.main(args) == 1                            # declined: nothing called
    out = capsys.readouterr().out
    assert f"Estimated cost {figures[0]} (worst case {figures[1]} if every reply uses max_tokens)" in out
    assert "Nothing was called" in out
    assert words in " ".join(lesson_text(lesson).split()), f"{lesson}: {words}"
