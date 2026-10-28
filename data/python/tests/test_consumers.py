"""Consumer-contract tests: each consumer reads the path its contract governs, and what it reads holds.

The MCP read server is other tracks' code, imported by file path and never
edited: its ticket source (mcp/core/python/lumen_core/tickets.py) reads tickets.v1.
The agents eval moved to tickets.v2 in the capstone, through the shim
data/python/consumers/agents_eval_v2.py; its own loader (agents/desk/python/desk_core.py)
still reads the v1 file, which must therefore keep meeting tickets.v1 until the
sunset. If a reader moves to another file, or its file stops meeting the version
it declares, these fail first.

The injection ticket t061 (mcp/data/injection.jsonl) is a canary both consumers
add on purpose; it is outside every tickets contract, so these tests leave it out.
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
TICKETS_V2 = ROOT / "data" / "contracts" / "tickets.v2.yaml"
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


def test_agents_eval_reads_tickets_v2(isolated):
    shim = importlib.import_module("consumers.agents_eval_v2")
    contract = C.load_contract(TICKETS_V2)
    want = governed_path(contract, "agents-eval")
    assert shim.SOURCE == want, f"reads {shim.SOURCE}, the contract governs {want}"
    read = C.read_rows(shim.SOURCE)
    assert_all_ok(C.validate(contract, read))
    picked = [t for t in shim.load_eval_tickets_v2() if t["id"] != INJECTION_ID]
    assert len(picked) == 12
    assert all(shim.to_v1_shape(r) in picked for r in read if r["id"] in {t["id"] for t in picked})
    assert len({t["id"] for t in picked} & {r["id"] for r in read}) == 12  # every eval ticket is a v2 row


def test_agents_desk_loader_still_reads_a_v1_file(monkeypatch, isolated):
    # The agents track's own eval (desk_core) still reads the v1 file: the move to v2 is the
    # data track's shim, and nothing in agents/ calls it. desk_core is no longer a declared v1
    # consumer, so the governed path is borrowed from the v1 consumer that remains.
    # desk_core imports mcp/python/agent.py at module level for the live desk runs, which needs
    # the MCP SDK; the ticket loader never touches it, so a stub stands in and nothing is installed.
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    sys.path.insert(0, str(ROOT / "agents" / "desk" / "python"))
    desk_core = importlib.import_module("desk_core")
    contract = C.load_contract(TICKETS_V1)
    assert Path(desk_core.LUMEN_TICKETS) == governed_path(contract, "mcp-read-server")
    assert_all_ok(C.validate(contract, desk_core._rows(desk_core.LUMEN_TICKETS)))
