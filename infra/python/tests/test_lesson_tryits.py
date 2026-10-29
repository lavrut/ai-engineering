"""The AI Infrastructure lessons' Try-it commands, run as the lessons print them, against the output they show.

The Ontology track's pattern, with what a track of servers needs on top. Each lesson's "## Try it" is a sequence of
```bash blocks, most followed by a ```text block with what the reader should see. One test per lesson runs its
blocks in order, from the repo root, with bash, and compares stdout with the text block line for line. A block whose
prose says it exits with status 1 must exit 1; every other block must exit 0. A bash block with no output shown must
succeed, and one whose prose says it "prints the same line" must print the text block before it.

Servers. A line that starts the agents replay proxy (`proxy.py --port`), the gateway (`gateway.py --port`) or the
injector (`injector.py --port`) is what the lesson runs in a terminal of its own (or with a trailing `&`): the test
starts it as a managed process on a free port, reads the first line it prints (compared like any output when a text
block follows), restarts it when the lesson starts the same server again, and stops every server in `finally`. The
ports the lessons print, 8790 (gateway), 8791 (proxy) and 8793 (injector), are rewritten to the test's ports, and
the environment carries them as INFRA_GATEWAY_PORT, INFRA_PROXY_PORT and INFRA_INJECTOR_PORT: gateway.py moves a
loopback upstream on 8791 or 8793 to those ports (a config copy the lesson made by `sed` keeps its printed port), and
the CLIs take them as their port defaults. The desk's own default URL, port 8790 in desk_core.PROXY_URL, is pointed
at the test's gateway the same way. Output is mapped back to the printed ports before it is compared.

Comparison, read from the prose around each text block:
- the paragraph directly before it says the figures "depend on the wall clock" (rulings I11, I12): by shape, the
  same number of lines, each gate line's name and ok or FAILED word, and each trace line's status;
- the paragraph directly before it says "first lines": the output starts with those lines;
- the paragraph directly before it says "the last line it prints": the last line of stdout and stderr;
- the paragraph after it says the ids "will differ": trace ids, and the fifth field of a trace line (the gateway's
  own milliseconds), are masked; when it also says the "waits" differ, each `'backoff_ms'` value is masked too
  (and must be a whole number), while statuses, costs, models and outcomes stay exact;
- everything else line for line.
A ```yaml block is an edit the lesson asks for: the test makes it, replacing that top-level section of the file the
lesson last copied with `cp`.

Nothing here can pay: no record mode, live mode or paid script is named, every URL is the loopback, and
ANTHROPIC_API_KEY is removed from the environment. The setup line (it installs from the network) is not run; the test
checks it is the README's. The lessons' /tmp scratch goes to a per-test folder. No command may change a committed
file: each test snapshots infra/, agents/data/ and llms/data/, fails if a tracked file changed, and puts every byte
back (git-ignored run outputs included), deleting what the commands created.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import gateway  # noqa: E402

LESSONS = ROOT / "app" / "src" / "content" / "lessons" / "infra"
README = ROOT / "README.md"
SETUP = "python3 -m venv .venv-infra && .venv-infra/bin/pip install -r infra/requirements.txt"
BLOCK = re.compile(r"```(\w*)\n(.*?)```", re.S)
PAID = re.compile(r"--live|--mode record|--record|ANTHROPIC_API_KEY|agents/run\.py|triage_lab\.py|harness\.py|"
                  r"record_|--yes|curl|https://|http://(?!127\.0\.0\.1[:/])")
EXITS_1 = re.compile(r"exits? with status 1")
SERVER = re.compile(r"^(?P<cmd>\S+ (?:agents/proxy/proxy\.py|infra/python/gateway\.py|infra/python/injector\.py)\b"
                    r"(?!.*--cases).*--port \d+.*?)\s*&?\s*$")
PORTS = {"gateway": 8790, "proxy": 8791, "injector": 8793}
ENV_NAMES = {"gateway": "INFRA_GATEWAY_PORT", "proxy": "INFRA_PROXY_PORT", "injector": "INFRA_INJECTOR_PORT"}
TRACE_LINE = re.compile(r"^(?P<id>[0-9a-f]{16}|<id>) (?P<status>\d{3}) \S+ \S+ (?P<ms>\d+) \S+ \S+$")
HEX_ID = re.compile(r"\b[0-9a-f]{16}\b")
BACKOFF = re.compile(r"'backoff_ms': ([^,}]+)")
WATCHED = [ROOT / "infra", ROOT / "agents" / "data", ROOT / "llms" / "data"]
SKIP_PARTS = {"__pycache__", ".pytest_cache"}


@dataclass
class Step:
    kind: str                  # "run" or "yaml"
    code: str
    intro: str                 # the paragraph before the block
    expected: str | None = None
    before_out: str = ""       # the paragraph between the block and its text block
    after_out: str = ""        # the paragraph after the text block
    flags: set = field(default_factory=set)

    @property
    def exit_code(self) -> int:
        return 1 if EXITS_1.search(self.intro) or EXITS_1.search(self.before_out) else 0

    @property
    def mode(self) -> str:
        if "depend on the wall clock" in self.before_out:
            return "shape"
        if "last line it prints" in self.before_out:
            return "last"
        if "will differ" in self.after_out:
            return "waits" if "waits" in self.after_out else "ids"
        return "exact"


def last_paragraph(text: str) -> str:
    parts = [p for p in text.strip().split("\n\n") if p.strip()]
    return parts[-1] if parts else ""


def first_paragraph(text: str) -> str:
    parts = [p for p in text.strip().split("\n\n") if p.strip()]
    return parts[0] if parts else ""


def tryit(path: Path) -> str:
    text = path.read_text("utf-8")
    return text[text.index("\n## Try it\n"):]


def steps(path: Path) -> list[Step]:
    body, out, last = tryit(path), [], 0
    blocks = list(BLOCK.finditer(body))
    for i, m in enumerate(blocks):
        lang, code = m.group(1), m.group(2)
        between = body[last:m.start()]
        last = m.end()
        if lang == "bash":
            if code.strip() == SETUP:
                continue
            out.append(Step("run", code, last_paragraph(between)))
        elif lang == "yaml":
            out.append(Step("yaml", code, last_paragraph(between)))
        elif lang == "text":
            s = out[-1] if out else None
            assert s is not None and s.kind == "run" and s.expected is None, f"{path.stem}: a text block with no command"
            nxt = blocks[i + 1].start() if i + 1 < len(blocks) else len(body)
            s.expected, s.before_out, s.after_out = code, last_paragraph(between), first_paragraph(body[m.end():nxt])
        else:
            raise AssertionError(f"{path.stem}: a ```{lang} block in the Try it")
    for i, s in enumerate(out):
        if s.kind == "run" and s.expected is None and "prints the same line" in s.intro:
            s.expected = next(p.expected for p in reversed(out[:i]) if p.expected is not None).splitlines()[-1] + "\n"
            s.flags.add("same")
    return out


LESSON_FILES = sorted(LESSONS.glob("*.mdx"))
STEPS = {p.stem: steps(p) for p in LESSON_FILES}


# ── the plan, read from the lessons ──────────────────────────────────────────
def test_every_lesson_has_a_try_it_with_commands():
    assert list(STEPS) == ["01-gateway", "02-routing", "03-resilience", "04-cache", "05-budgets", "06-capstone"]
    shown = {k: sum(s.expected is not None for s in v) for k, v in STEPS.items()}
    assert shown == {"01-gateway": 5, "02-routing": 4, "03-resilience": 4, "04-cache": 5, "05-budgets": 7,
                     "06-capstone": 7}
    assert all(s.code.strip() for v in STEPS.values() for s in v)


def test_the_setup_line_is_the_readmes():
    assert SETUP in tryit(LESSONS / "01-gateway.mdx")
    assert SETUP in README.read_text("utf-8")


def test_no_command_can_pay():
    for lesson, v in STEPS.items():
        for s in v:
            assert not PAID.search(s.code), f"{lesson}: {s.code}"
            for line in filter(str.strip, s.code.splitlines()):
                assert re.match(r"(\.venv-(infra|agents)/bin/python |sed |cp |rm -f infra/data/|mkdir -p /tmp/|"
                                r"cd infra/go && go run |ID=\$\(\.venv-infra/bin/python )", line.strip()) or \
                    s.kind == "yaml", f"{lesson}: {line}"
    assert "ANTHROPIC_API_KEY" not in env({})


def test_the_exit_codes_and_comparisons_are_read_from_the_prose():
    """One block exits 1: the desk's untagged refusal in lesson 1. Three are compared by shape: the by-hand replay
    and its limited traces in lesson 1 (both on the wall clock), and lesson 6's slowest calls."""
    ones = [(k, s.code.split()[0]) for k, v in STEPS.items() for s in v if s.exit_code == 1]
    assert ones == [("01-gateway", ".venv-agents/bin/python")]
    shape = [k for k, v in STEPS.items() for s in v if s.expected is not None and s.mode == "shape"]
    assert shape == ["01-gateway", "01-gateway", "06-capstone"]
    assert [k for k, v in STEPS.items() for s in v if "same" in s.flags] == ["02-routing"]


def test_the_cli_port_overrides_are_defaults_only(monkeypatch):
    """The flag wins, else the environment, else the default; a remote upstream is never moved."""
    monkeypatch.delenv("INFRA_GATEWAY_PORT", raising=False)
    monkeypatch.delenv("INFRA_PROXY_PORT", raising=False)
    assert gateway.parse_args([]).port == 8790
    assert gateway.test_upstream("http://127.0.0.1:8791") == "http://127.0.0.1:8791"
    monkeypatch.setenv("INFRA_GATEWAY_PORT", "18790")
    monkeypatch.setenv("INFRA_PROXY_PORT", "18791")
    assert gateway.parse_args([]).port == 18790
    assert gateway.parse_args(["--port", "8790"]).port == 8790
    assert gateway.test_upstream("http://127.0.0.1:8791") == "http://127.0.0.1:18791"
    assert gateway.test_upstream("http://127.0.0.1:9999") == "http://127.0.0.1:9999"
    assert gateway.test_upstream("https://api.anthropic.com") == "https://api.anthropic.com"


# ── running a lesson ─────────────────────────────────────────────────────────
def tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files", "-z", *[str(w.relative_to(ROOT)) for w in WATCHED]], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode()
    return {p for p in out.split("\0") if p}


def snapshot() -> dict[str, bytes]:
    files = [p for w in WATCHED for p in w.rglob("*") if p.is_file() and not SKIP_PARTS & set(p.parts)]
    return {str(p.relative_to(ROOT)): p.read_bytes() for p in files}


@pytest.fixture
def unchanged():
    """Fails a lesson that changed a tracked file, after putting every watched file back."""
    before = snapshot()
    yield
    after = snapshot()
    changed = sorted(k for k in before.keys() | after.keys() if after.get(k) != before.get(k))
    for k in changed:
        if k in before:
            (ROOT / k).write_bytes(before[k])
        else:
            (ROOT / k).unlink()
    committed = sorted(set(changed) & tracked())
    assert not committed, f"a Try-it command changed a committed file: {committed}"


def env(ports: dict[str, int]) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    e.update({ENV_NAMES[r]: str(p) for r, p in ports.items()})
    return e


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Lesson:
    """One lesson's run: its ports, its scratch folder and the servers it started."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.ports = {r: free_port() for r in PORTS}
        self.servers: dict[str, subprocess.Popen] = {}

    def rewrite(self, line: str) -> str:
        line = line.replace("/tmp/", f"{self.tmp}/")
        line = line.replace("desk_core as core;", f"desk_core as core; core.PROXY_URL = "
                                                  f"'http://127.0.0.1:{self.ports['gateway']}';")
        if line.lstrip().startswith("sed "):
            return line           # a config edit keeps the printed upstream; gateway.py moves it (INFRA_*_PORT)
        for role, port in PORTS.items():
            line = re.sub(rf"\b{port}\b", str(self.ports[role]), line)
        return line

    def unmap(self, text: str) -> str:
        text = text.replace(f"{self.tmp}/", "/tmp/").replace(str(self.tmp), "/tmp")
        for role, port in PORTS.items():
            text = text.replace(f"127.0.0.1:{self.ports[role]}", f"127.0.0.1:{port}")
        return text

    def start(self, line: str) -> str:
        role = "proxy" if "proxy.py" in line else "gateway" if "gateway.py" in line else "injector"
        self.stop(role)
        p = subprocess.Popen(["bash", "-c", f"exec {self.rewrite(line)}"], cwd=ROOT, env=env(self.ports),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.servers[role] = p
        first: list[str] = []
        t = threading.Thread(target=lambda: first.append(p.stdout.readline()), daemon=True)
        t.start()
        t.join(30)
        assert first and first[0], f"{role} printed nothing: {p.poll()} {p.stderr.read() if p.poll() is not None else ''}"
        deadline = time.time() + 10
        while True:                          # listening, not only printed
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.ports[role])) == 0:
                    break
            assert time.time() < deadline and p.poll() is None, f"{role} is not listening"
            time.sleep(0.05)
        return self.unmap(first[0])

    def stop(self, role: str | None = None) -> None:
        for r in [role] if role else list(self.servers):
            p = self.servers.pop(r, None)
            if p is not None:
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                for f in (p.stdout, p.stderr):
                    f.close()

    def run(self, s: Step) -> tuple[int, str, str]:
        """The block's lines: servers started, the rest run in order with bash; (exit, stdout, stderr)."""
        code, out, err, group = 0, [], [], []

        def flush() -> None:
            nonlocal code
            if group:
                r = subprocess.run(["bash", "-c", "\n".join(self.rewrite(g) for g in group)], cwd=ROOT,
                                   env=env(self.ports), capture_output=True, text=True, timeout=300)
                code = r.returncode
                out.append(self.unmap(r.stdout))
                err.append(self.unmap(r.stderr))
                group.clear()

        for line in s.code.splitlines():
            m = SERVER.match(line.strip())
            if m:
                flush()
                out.append(self.start(m.group("cmd")))
            elif line.strip():
                group.append(line)
        flush()
        return code, "".join(out), "".join(err)

    def edit_yaml(self, s: Step, last_copy: Path) -> None:
        """Make the edit a ```yaml block asks for: its top-level section replaces the same one in the copy."""
        key = s.code.splitlines()[0]
        lines = last_copy.read_text().splitlines(keepends=True)
        start = next(i for i, x in enumerate(lines) if x.rstrip() == key.rstrip())
        end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() and not lines[i].startswith(" ")),
                   len(lines))
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        last_copy.write_text("".join(lines[:start]) + s.code + "".join(lines[end:]))


def mask(text: str, mode: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = HEX_ID.sub("<id>", line)
        m = TRACE_LINE.match(line)
        line = line[:m.start("ms")] + "<ms>" + line[m.end("ms"):] if m else line
        if mode == "waits":                  # the jittered backoff, by its field name; statuses and costs stay exact
            for w in BACKOFF.findall(line):
                assert w.isdigit(), f"a wait that is not a whole number of ms: {w!r} in {line}"
            line = BACKOFF.sub("'backoff_ms': <ms>", line)
        out.append(line)
    return out


def shape(lines: list[str]) -> list[tuple]:
    """Ruling I11/I12: a gate line by its name and ok/FAILED word, a trace line by its status, any other by kind."""
    def one(line: str) -> tuple:
        g = re.match(r"^gate (\w+): (ok|FAILED):", line)
        t = TRACE_LINE.match(line)
        return ("gate", g.group(1), g.group(2)) if g else ("trace", t.group("status")) if t else ("line",)
    return [one(x) for x in lines]


def compare(s: Step, stdout: str, stderr: str, lesson: str) -> None:
    exp, got = s.expected.splitlines(), stdout.splitlines()
    first = "first lines" in s.before_out
    where = f"{lesson}: {s.code.strip().splitlines()[-1][:90]}\n{stdout}\n{stderr}"
    if s.mode == "last":
        assert (stdout + stderr).splitlines()[-1:] == exp, where
    elif s.mode == "shape":
        assert len(got) >= len(exp) if first else len(got) == len(exp), where
        assert shape(got[:len(exp)]) == shape(exp), where
    else:
        got = got[:len(exp)] if first else got
        assert mask("\n".join(got), s.mode) == mask(s.expected, s.mode), where


@pytest.mark.parametrize("lesson", list(STEPS))
def test_tryit_output(lesson, unchanged, tmp_path):
    if lesson == "01-gateway" and not (ROOT / ".venv-agents" / "bin" / "python").exists():
        pytest.skip("lesson 1 runs the agents proxy and desk with .venv-agents")
    run, last_copy = Lesson(tmp_path), None
    try:
        for s in STEPS[lesson]:
            if s.kind == "yaml":
                run.edit_yaml(s, last_copy)
                continue
            if "go run" in s.code and shutil.which("go") is None:
                continue                     # as test_twin.py: the Go twin needs go
            m = re.match(r"cp \S+ (/tmp/\S+)$", s.code.strip())
            last_copy = Path(run.rewrite(m.group(1))) if m else last_copy
            code, stdout, stderr = run.run(s)
            assert code == s.exit_code, f"{lesson}: exit {code}: {s.code.strip()[:90]}\n{stdout}\n{stderr}"
            if s.expected is not None:
                compare(s, stdout, stderr, lesson)
    finally:
        run.stop()
