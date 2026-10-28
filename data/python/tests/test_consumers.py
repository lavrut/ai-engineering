"""Consumer-contract tests: each consumer reads the path its contract governs, and what it reads holds.

The consumers are other tracks' code, imported by file path and never edited:
the MCP read server's ticket source (mcp/core/python/lumen_core/tickets.py) and
the agents eval's ticket loader (agents/desk/python/desk_core.py). If either
moves to another file, or the file stops meeting tickets.v1, these fail first.

The injection ticket t061 (mcp/data/injection.jsonl) is a canary both consumers
add on purpose; it is outside tickets.v1's scope, so these tests leave it out.
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import contract as C  # noqa: E402

ROOT = HERE.parents[2]
TICKETS_V1 = ROOT / "data" / "contracts" / "tickets.v1.yaml"
INJECTION_ID = "t061"  # out of contract scope (see the module docstring)


def governed_path(contract: dict, consumer: str) -> Path:
    """The path a contract's consumer entry names: `reads` is "<path> via <module>"."""
    entry = next(c for c in contract["consumers"] if c["name"] == consumer)
    return ROOT / entry["reads"].split(" via ")[0]


def assert_all_ok(verdicts):
    bad = [x for x in verdicts if not x["ok"]]
    assert not bad, "\n".join(C.format_lines(bad))


@pytest.fixture
def isolated(monkeypatch):
    """Imports for one test: sys.path and sys.modules come back as they were, no .pyc written."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


def test_mcp_read_server_reads_tickets_v1(isolated):
    sys.path.insert(0, str(ROOT / "mcp" / "core" / "python"))
    tickets = importlib.import_module("lumen_core.tickets")
    contract = C.load_contract(TICKETS_V1)
    want = governed_path(contract, "mcp-read-server")
    assert Path(tickets.SOURCES[0]) == want, f"reads {tickets.SOURCES[0]}, the contract governs {want}"
    rows = [t for t in tickets.load_tickets() if t["id"] != INJECTION_ID]
    assert_all_ok(C.validate(contract, rows))


def test_agents_eval_reads_tickets_v1(isolated):
    # desk_core imports mcp/python/agent.py at module level for the live desk runs, which needs
    # the MCP SDK; the ticket loader never touches it, so a stub stands in and nothing is installed.
    sys.modules["agent"] = types.ModuleType("agent")
    sys.path.insert(0, str(ROOT / "agents" / "desk" / "python"))
    desk_core = importlib.import_module("desk_core")
    contract = C.load_contract(TICKETS_V1)
    want = governed_path(contract, "agents-eval")
    assert Path(desk_core.LUMEN_TICKETS) == want, f"reads {desk_core.LUMEN_TICKETS}, the contract governs {want}"
    read = desk_core._rows(desk_core.LUMEN_TICKETS)  # the reader load_eval_tickets uses
    assert_all_ok(C.validate(contract, read))
    picked = [t for t in desk_core.load_eval_tickets() if t["id"] != INJECTION_ID]
    assert len(picked) == 12
    assert all(t in read for t in picked)  # every eval ticket is a row that passed the contract
