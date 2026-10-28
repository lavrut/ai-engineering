"""Both tracks have a derive.py (agents/derive.py, mcp/python/derive.py). Whichever track's folder a fresh process
starts in, `import derive` must load that track's own file. The agents folders stay ahead of mcp/python on sys.path
(desk_core.agents_first), although the MCP modules put mcp/python at the front each time they load."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "agents"
MCP = ROOT / "mcp" / "python"
MCP_PYTHON = ROOT / ".venv-mcp" / "bin" / "python"


def module_files(cwd: Path, code: str, names: tuple[str, ...], python: Path | str = sys.executable) -> dict[str, Path]:
    """Run `code` in a fresh interpreter started in `cwd`, and return where each named module was loaded from."""
    script = code + f"\nimport sys\nfor m in {names!r}: print(m, sys.modules[m].__file__)\n"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "ANTHROPIC_API_KEY")}
    out = subprocess.run([str(python), "-c", script], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return {name: Path(path).resolve() for name, path in (line.split(" ", 1) for line in out.stdout.splitlines())}


def test_from_agents_run_derive_and_run_evals_are_the_agents_files():
    files = module_files(AGENTS, "import run, derive, sys\nsys.path.append('evals')\nimport run_evals",
                         ("run", "derive", "run_evals"))
    assert files == {"run": AGENTS / "run.py", "derive": AGENTS / "derive.py",
                     "run_evals": AGENTS / "evals" / "run_evals.py"}


def test_from_agents_evals_derive_is_still_the_agents_file():
    """run_evals loads the MCP agent (through desk_core) before anything imports derive."""
    files = module_files(AGENTS / "evals", "import run_evals, derive", ("run_evals", "derive"))
    assert files["derive"] == AGENTS / "derive.py"


@pytest.mark.skipif(not MCP_PYTHON.exists(), reason="needs .venv-mcp")
def test_from_mcp_python_derive_is_the_mcp_file():
    files = module_files(MCP, "import derive", ("derive",), python=MCP_PYTHON)
    assert files["derive"] == MCP / "derive.py"
