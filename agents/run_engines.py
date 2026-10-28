"""
run_engines — the engines agents/run.py drives (section 1: hand, hand-go, langgraph and sdk, and pointing them
at one proxy run) and the pre-flight check that the desk's tools work before anything is spent (section 2).
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from run_sets import APPROVE, ELICIT, GO_DIR, GO_TIMEOUT, NONESSENTIAL
import desk_core as core  # noqa: E402  (on the path run_sets set)

# ── 1. Engines ──────────────────────────────────────────────────────────────
def engine(name: str) -> core.Engine:
    """The registered Engine (importing its module registers it; hand-go is registered here)."""
    if name not in core.ENGINES:
        if name == "hand":
            import supervisor  # noqa: F401
        elif name == "langgraph":
            import langgraph_desk  # noqa: F401
        elif name == "sdk":
            import sdk_desk  # noqa: F401
        elif name == "hand-go":
            core.register("hand-go", core.Engine(single=lambda t, **kw: go_run("single", t, **kw),
                                                 supervisor=lambda t, **kw: go_run("supervisor", t, **kw)))
    if name not in core.ENGINES:
        raise ValueError(f"unknown engine {name!r}")
    return core.ENGINES[name]


_GO_BIN: list[Path] = []


def go_binary() -> Path:
    """Build cmd/handgo once per process into a temporary directory (removed at exit)."""
    if not _GO_BIN:
        out = Path(tempfile.mkdtemp(prefix="handgo-"))
        atexit.register(shutil.rmtree, out, True)
        subprocess.run(["go", "build", "-o", str(out / "handgo"), "./cmd/handgo"], cwd=GO_DIR, check=True)
        _GO_BIN.append(out / "handgo")
    return _GO_BIN[0]


class GoRunError(RuntimeError):
    def __init__(self, message: str, proxy_status: int) -> None:
        super().__init__(message)
        self.proxy_status = proxy_status


def go_env() -> dict[str, str]:
    """The Go engine's environment: nothing inherited but PATH, TMPDIR and the locale (no key, no proxy
    variables reach it; it calls the proxy URL it is given), plus R9's flag."""
    keep = {k: os.environ[k] for k in ("PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE") if k in os.environ}
    return {**keep, **NONESSENTIAL}


def go_run(desk: str, ticket: dict, *, approve: str, elicit: str, labels_path: Path) -> dict:
    """One hand-go run: cmd/handgo against core.PROXY_URL; the MCP servers run under this Python (.venv-agents)."""
    args = [str(go_binary()), "-desk", desk, "-proxy", core.PROXY_URL, "-python", sys.executable,
            "-approve", approve, "-elicit", elicit, "-labels", str(labels_path)]
    # its own process group, so a timeout (or any failure here) kills the MCP server grandchildren too
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         env=go_env(), start_new_session=True)
    try:
        stdout, stderr = p.communicate(json.dumps({"id": ticket["id"], "text": ticket["text"]}), timeout=GO_TIMEOUT)
    except BaseException as e:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        if isinstance(e, subprocess.TimeoutExpired):
            raise RuntimeError(f"hand-go timed out after {GO_TIMEOUT} s; its process group was killed") from None
        raise
    sys.stderr.write(stderr)
    try:
        doc = json.loads(stdout)
    except ValueError:
        raise RuntimeError(f"hand-go exited {p.returncode} without a result") from None
    if p.returncode != 0 or "error" in doc:
        raise GoRunError(doc.get("error", f"exit {p.returncode}"), int(doc.get("proxy_status") or 0))
    out = doc["output"]
    if desk.startswith("pattern:"):
        return {"result": out, "calls": doc["calls"], "trace": doc["trace"], "errors": []}
    return {**out, "calls": doc["calls"], "trace": doc["trace"]}


def run_pattern(engine_name: str, desk: str, ticket: dict, *, labels_path: Path) -> dict:
    """The five patterns are read-only (no approve or elicit to pass); parallel and orchestrate get labels_path for
    their lumen-read session. Parallel runs workers=1: the same requests as workers=3, in a fixed trace order."""
    name = desk.removeprefix("pattern:")
    if engine_name == "hand-go":
        return go_run(desk, ticket, approve=APPROVE, elicit=ELICIT, labels_path=labels_path)
    import patterns as pt
    fn = getattr(pt, name)
    kw: dict[str, Any] = {}
    if name in ("parallel", "orchestrate"):
        kw["labels_path"] = labels_path
    if name == "parallel":
        kw["workers"] = 1
    r = fn(ticket, **kw)
    return {"result": r.output, "calls": r.calls, "trace": r.trace, "errors": []}


@contextmanager
def pointed_at(url: str) -> Iterator[None]:
    """Point every engine at this proxy run's URL (only differs from PROXY_URL in tests, on a free port), with
    R9's flag in the environment, then restore both."""
    saved_url, saved_env = core.PROXY_URL, {k: os.environ.get(k) for k in NONESSENTIAL}
    sdk = sys.modules.get("sdk_desk")
    saved_sdk = sdk.SDK_ENV["ANTHROPIC_BASE_URL"] if sdk else None
    core.PROXY_URL = url
    if sdk:
        sdk.SDK_ENV["ANTHROPIC_BASE_URL"] = url
        if sdk.SDK_ENV.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") != "1":   # R9/R39: the SDK's own env must have it
            raise RuntimeError("sdk_desk.SDK_ENV lost CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1")
    os.environ.update(NONESSENTIAL)
    try:
        yield
    finally:
        core.PROXY_URL = saved_url
        if sdk:
            sdk.SDK_ENV["ANTHROPIC_BASE_URL"] = saved_sdk
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def harness_trace_in(tmp: str) -> Iterator[None]:
    """The guard's scripted person appends to the harness's trace.jsonl; keep it out of the working directory."""
    harness = core.mcp_agent.load_harness()
    saved = harness.TRACE_FILE
    harness.TRACE_FILE = Path(tmp) / "trace.jsonl"
    try:
        yield
    finally:
        harness.TRACE_FILE = saved


def run_engine(engine_name: str, desk: str, ticket: dict, *, labels_path: Path) -> dict:
    if desk.startswith("pattern:"):
        return run_pattern(engine_name, desk, ticket, labels_path=labels_path)
    fn = getattr(engine(engine_name), desk)
    return fn(ticket, approve=APPROVE, elicit=ELICIT, labels_path=labels_path)    # R33: always explicit


# ── 2. Pre-flight: the tools work before anything is spent ──────────────────
# R59: classify_complaint failed in real recordings (fastapi was missing from .venv-agents), and the models read
# "Error executing tool" as the answer. Every lumen-read tool is called once in memory before a set starts.
PREFLIGHT = (("get_ticket", {"id": "t001"}), ("search_tickets", {"query": "refund"}),
             ("classify_complaint", {"text": "I was charged twice"}))


INSTALL = ".venv-agents/bin/pip install -r agents/requirements.txt"


def preflight() -> str | None:
    """None if every lumen-read tool answers without an error, else what failed (an exception included)."""
    try:
        return _preflight()
    except Exception as e:        # the servers would not even open: the same clean failure, no traceback
        return f"{type(e).__name__}: {e}"


def _preflight() -> str | None:
    with tempfile.TemporaryDirectory(prefix="lumen-preflight-") as tmp, harness_trace_in(tmp), \
            core.open_desk_tools(("read",), guard=False, labels_path=Path(tmp) / "labels.jsonl") as (_s, run_tool, _x):
        for name, args in PREFLIGHT:
            out, is_error = run_tool(name, args)
            if is_error:
                return f"{name}({json.dumps(args)}) failed: {out[:300]}"
    return None
