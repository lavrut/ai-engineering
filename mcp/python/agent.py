#!/usr/bin/env python3
"""agent — the Track 1 harness, driving the Lumen MCP servers (Python).

The loop, the permission gate and the executor are harness/python/harness.py, imported and never
edited. This file and the three beside it add what an MCP host adds: agent_host.py connects to
lumen-read (and lumen-ops), turns each MCP tool into a harness Tool, answers the server's confirmation
round with a scripted answer, and applies one extra rule, the untrusted-read guard (its section 4);
agent_model.py makes the model calls (replay and record) and prices them; agent_config.py holds the
configuration. This file runs a scenario and is the entry point.

Both servers run in memory (build_read_server(), build_ops_server(labels_path=...)), reached with the
SDK's in-memory Client: no network, no OAuth. Only the model call can cost money, and by default it
does not: --mode replay (the default) answers from mcp/data/recordings/<name>.jsonl.

Run (free, replay):
    .venv-mcp/bin/python mcp/python/agent.py --scenario lesson5
    .venv-mcp/bin/python mcp/python/agent.py --scenario capstone                       # no guard (the recorded models refused; see simulate.py for one that obeys)
    .venv-mcp/bin/python mcp/python/agent.py --scenario capstone --guard               # guard: the scripted person says no
In replay, lumen-ops writes to a temporary label file (printed); --labels PATH overrides.
Estimate a recording (free, no API client is created):
    .venv-mcp/bin/python mcp/python/agent.py --scenario capstone --guard --mode record --dry-run
Record (paid: prints the estimate, asks y/N, stops at --max-cost; ANTHROPIC_API_KEY from the environment):
    .venv-mcp/bin/python mcp/python/agent.py --scenario lesson5 --mode record --max-cost 10
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the parts below sit beside this file

# The agent is four files; this one runs a scenario (section 1) and is the entry point (2). Every name
# of the other three is re-exported here, so `import agent` and `agent.X` work as before.
from agent_config import (CALL_TIMEOUT, FRAMINGS, HARNESS_DIR, HERE, MAX_CALLS, MAX_COST,  # noqa: E402,F401
                          MAX_TOKENS, MODEL, MODELS, RECORDINGS_DIR, REPO, SCENARIOS, SYSTEM,
                          TRAINING_NOTE, Options, Scenario, record_command, recording_name)
from agent_host import (SERVER_NAMES, UNTRUSTED_TOOLS, Bridge, Session, ToolError,  # noqa: E402,F401
                        carries_untrusted, load_harness, make_approver, make_fn, needs_human, open_tools,
                        result_text, scripted_human, taints)
from agent_model import (BLOCK_FIELDS, CHARS_PER_TOKEN, EST_GROWTH, EST_OUTPUT, TOOL_USE_TOKENS,  # noqa: E402,F401
                         WORST_RESULT_TOKENS, CostLimit, Ledger, ModelCall, ReplayMiss, anthropic_client,
                         as_response, block_dict, confirmed, estimate, find_recording, plain_messages,
                         print_estimate, record_model, replay_model, request_key, response_data,
                         system_prompt, terminal_ask, worst_call_cost)
from client import Answerer, always, connect, deny  # noqa: E402,F401
from ops_server import build_ops_server  # noqa: E402,F401
from read_server import build_read_server  # noqa: E402,F401
from triage_lab import PRICES, call_cost  # noqa: E402,F401  (the lab's price table, dollars per million tokens)


# ── 1. Running a scenario ───────────────────────────────────────────────────
def run_scenario(scenario: str, *, guard: bool, elicit: str, labels_path: Path, model_call: ModelCall,
                 approve: str = "decline", framing: str = "none",
                 human: Callable[[str, dict], bool] | None = None) -> str:
    """One turn of the harness loop on a scenario's task, with that scenario's servers. The
    untrusted-read flag starts clear: it covers this turn only."""
    harness = load_harness()
    spec = SCENARIOS[scenario]
    with open_tools(spec.servers, labels_path=labels_path, elicit=elicit) as session:
        call = model_call(system_prompt(session.instructions, framing), [t.schema() for t in session.tools])
        approver = make_approver(session, guard=guard, human=human or scripted_human(approve))
        return harness.run_agent(spec.task, call=call, approver=approver)


# ── 2. Entry point ──────────────────────────────────────────────────────────
def cost_arg(text: str) -> float:
    """argparse type for --max-cost: a finite number above 0 (nan would make every cap test false)."""
    try:
        value = float(text)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite number above 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="The harness agent on the Lumen MCP servers (replay by default).")
    ap.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    ap.add_argument("--guard", action="store_true", help="a write after an untrusted read needs a person's yes")
    ap.add_argument("--approve", choices=["accept", "decline"], default="decline",
                    help="the scripted person's answer when the guard asks (default: decline)")
    ap.add_argument("--elicit", choices=["accept", "decline"], default="accept",
                    help="the scripted answer to lumen-ops' own confirmation round (default: accept)")
    ap.add_argument("--mode", choices=["replay", "record"], default="replay")
    ap.add_argument("--model", choices=MODELS, default=MODEL, help="the agent's model (prices from the lab's PRICES)")
    ap.add_argument("--framing", choices=FRAMINGS, default="none",
                    help="training: put TRAINING_NOTE before the system prompt (default: none)")
    ap.add_argument("--max-cost", type=cost_arg, default=MAX_COST, help="record mode stops past this many dollars")
    ap.add_argument("--dry-run", action="store_true", help="print the record estimate and exit; calls nothing")
    ap.add_argument("--yes", action="store_true", help="approve the paid calls without asking")
    ap.add_argument("--labels", type=Path, default=None,
                    help="lumen-ops' label file (default: a temporary file, in replay and record)")
    ap.add_argument("--recordings", type=Path, default=RECORDINGS_DIR)
    return ap


def _rows(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def main(argv: list[str] | None = None, *, client_factory: Callable[[], Any] = anthropic_client,
         ask: Callable[[str], str] = terminal_ask) -> int:
    args = build_parser().parse_args(argv)
    opts = Options(args.scenario, guard=args.guard, approve=args.approve, elicit=args.elicit, model=args.model,
                   framing=args.framing, recordings=args.recordings, labels=args.labels)
    ledger = None
    if args.dry_run or args.mode == "record":
        print_estimate(opts, args.max_cost)
        if args.dry_run:
            print("Dry run: nothing was called.")
            return 0
        if client_factory is anthropic_client and not os.environ.get("ANTHROPIC_API_KEY"):
            print("Record mode needs ANTHROPIC_API_KEY in the environment. Nothing was called.")
            return 1
        if not confirmed(args.yes, ask):
            return 1
        ledger = Ledger()
        model_call = record_model(opts, client_factory=client_factory, max_cost=args.max_cost, ledger=ledger)
    else:
        model_call = replay_model(opts)
    # Both modes default to a temporary label file (M11): a real model that obeyed t061 in record mode
    # must not append `outage` rows to the repo's mcp/data/labels.jsonl. --labels PATH keeps them.
    labels = args.labels or Path(tempfile.mkdtemp(prefix="lumen-agent-")) / "labels.jsonl"
    if opts.writes:
        print(f"labels: {labels}" + ("  (temporary)" if args.labels is None else ""))

    harness = load_harness()
    trace_was = harness.TRACE_FILE
    trace_dir = Path(args.recordings)
    if not trace_dir.is_dir():
        trace_dir = Path(tempfile.mkdtemp(prefix="lumen-agent-"))
    harness.TRACE_FILE = trace_dir / "trace.jsonl"  # next to the recordings (git-ignored), else a temp dir
    before = _rows(labels)
    try:
        answer = run_scenario(args.scenario, guard=args.guard, approve=args.approve, elicit=args.elicit,
                              framing=args.framing,
                              labels_path=labels, model_call=model_call)
    except ReplayMiss as e:
        print(f"agent: {e}", file=sys.stderr)
        return 1
    except CostLimit as e:
        print(f"Stopped: {e}. Finished calls are saved.")
        return 2
    finally:
        harness.TRACE_FILE = trace_was
    print("\nagent >", answer)
    if opts.writes:
        print(f"label rows written this run: {_rows(labels) - before} ({labels})")
    if ledger is not None:
        print(f"paid calls {ledger.calls}, spent ${ledger.spent:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
