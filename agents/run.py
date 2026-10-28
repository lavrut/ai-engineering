#!/usr/bin/env python3
"""run — one command runs any engine on any desk, through the record/replay proxy, and writes the traces.

    python agents/run.py --engine hand|hand-go|langgraph|sdk --desk pattern:<name>|pattern:all|single|supervisor|all
                         [--tickets all|<id>[,<id>...]] [--mode replay|record] [--refresh] [--dry-run] [--yes]
                         [--max-cost N] [--max-calls N]
    python agents/run.py --set patterns|patterns-go|desk-hand|desk-langgraph|desk-sdk [...]

Replay (the default) is free: each ticket gets its own proxy run (R25) answering from
agents/data/recordings/<set>/, and a request with no recording stops the run with the command that records it.
`--dry-run` prints what recording the set would cost (calls, typical, worst case, the cap) and calls nothing.
`--mode record` is paid: it needs ANTHROPIC_API_KEY in the environment (read by the proxy only, never by an
engine), prints the estimate, asks y/N once (`--yes` answers yes: only after the user approved), then records
ticket by ticket, with the cap checked by the proxy before every call. The engines always use the dummy key.
`--refresh` (record mode only) re-records: a request already in the store goes upstream again, paid, and its new
recording replaces the old one.

Each finished run writes agents/data/traces/<set>/<engine>-<desk>-<ticket>.json: the R2 trace records plus the
run's output, errors, the engine's own cost_usd (the Agent SDK's estimate; None elsewhere), the proxy's spend, and
`proxy_calls`, the proxy's ledger (R62): per model call, the recording's upstream ms and tokens and their cost. The
eval reads cost and latency from it, so a replay (or a store hit while recording) costs and takes what the recorded
call did. Replaying a set rebuilds its traces, ledger included.

run.py is four files, and `import run` reaches all of it: run_sets.py (the sets, the caps, the estimate),
run_engines.py (the engines and the pre-flight), run_traces.py (the trace files) and this one (one ticket
through one proxy run, and the command line).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Sections of run_sets, run_engines and run_traces, under the names run.<name> has always had.
from run_sets import (AGENTS, ALL_TICKETS, APPROVE, DEFAULT_MAX_CALLS, DESKS, ELICIT, ENGINE_NAMES,  # noqa: F401
                      MAX_CALLS_LIMIT, MAX_TURNS_SINGLE, MODEL, PATTERN_TICKETS, PATTERNS, REPO, SDK_MEASURED, SETS,
                      STORE_DIR, SUPERVISOR_MAX_CALLS, TRACES_DIR, TYPICAL_CALLS, SetSpec, _schemas, estimate_run,
                      estimate_set, max_calls_for, print_estimate, set_for)
from run_engines import (INSTALL, GoRunError, engine, go_run, harness_trace_in, pointed_at,  # noqa: F401
                         preflight, run_engine)
from run_traces import comparable, dump, log_path, rel, trace_doc, trace_path, write_trace  # noqa: F401
import desk_core as core  # noqa: E402  (on the path run_sets set)
import proxy  # noqa: E402
from triage_lab import PRICES, call_cost  # noqa: E402,F401  (the LLMs lab's prices, import only)
from lumen_core import LABELS_PATH  # noqa: E402  (mcp/data/labels.jsonl, git-ignored; on sys.path via desk_core)
import run_engines  # noqa: E402  (the parts themselves, for the patch rule below)
import run_sets  # noqa: E402
import run_traces  # noqa: E402


# A patch on this module reaches the parts (M3). The names above are copies of the parts' names, so patching one
# here (monkeypatch.setattr(run, "MODEL", ...)) also sets it in every part that holds that name, and the
# patched value is what the code sees wherever it looks the name up. Undoing the patch sets the old value back
# the same way. Reading a name works as before.
class _Facade(types.ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        for part in _PARTS:
            if name in vars(part):
                setattr(part, name, value)
        super().__setattr__(name, value)


_PARTS = (run_sets, run_engines, run_traces)
sys.modules[__name__].__class__ = _Facade


# ── 1. One ticket, one proxy run ────────────────────────────────────────────
def record_command(engine_name: str, desk: str, ticket_id: str, max_calls: int | None) -> str:
    """The command that records exactly this run (named in a replay miss)."""
    cmd = f"python agents/run.py --engine {engine_name} --desk {desk} --tickets {ticket_id} --mode record"
    return cmd + (f" --max-calls {max_calls}" if max_calls is not None else "")


def _is_proxy_refusal(e: BaseException) -> bool:
    if isinstance(e, (core.ProxyError, GoRunError)):
        return True
    status = getattr(e, "status_code", None)    # anthropic.APIStatusError (LangGraph's ChatAnthropic raises it)
    return status in (402, 404)


@dataclass
class Outcome:
    ok: bool
    spent: float
    cap_hit: bool
    line: str


CLOSE_WAIT = 30.0     # seconds run.py waits for in-flight proxy calls before closing the upstream client


def wait_idle(server: Any, timeout: float) -> bool:
    """Poll every 50 ms until no proxy handler is busy (bounded by `timeout`). True if idle."""
    deadline = time.monotonic() + timeout
    while True:
        with server.lock:
            busy = any(getattr(h, "busy", False) for h in server.connections.values())
        if not busy:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def capped(server: Any, label: str, e: BaseException | None) -> Outcome:
    """A run the proxy refused a call to is incomplete and writes no trace. This ticket's call limit (--max-calls)
    stops this ticket only; the dollar cap stops the set (Outcome.cap_hit)."""
    why = f" ({e})" if e is not None else ""
    if server.paid_calls >= server.max_calls:
        return Outcome(False, server.spent, False,
                       f"{label}: stopped at --max-calls {server.max_calls}; incomplete, no trace{why}")
    return Outcome(False, server.spent, True, f"{label}: stopped by the cost cap; incomplete, no trace{why}")


def run_ticket(*, engine_name: str, desk: str, ticket: dict, set_name: str, mode: str, max_cost: float,
               max_calls: int | None, port: int, store_dir: Path, traces_dir: Path, approved: bool,
               transport: Any = None, refresh: bool = False) -> Outcome:
    """One ticket through its own proxy run. `approved` (no default) is the user's yes to the paid calls: main
    passes proxy.confirm(--yes); record mode without it builds no server (make_server's gate, R13). `refresh`
    re-records what the store already holds (R62), atomically per ticket (R65): the proxy stages the new
    recordings, and they replace the old ones only when the run finished ok (its trace written); otherwise they
    are discarded and the old recordings and trace stay, consistent with each other."""
    cmd = record_command(engine_name, desk, ticket["id"], max_calls)
    engine(engine_name)                 # import (and so register) the engine first, so pointed_at sees its module
    server = proxy.make_server(mode, port, store_dir, set_name=set_name, max_cost=max_cost,
                               max_calls=max_calls_for(desk, max_calls), transport=transport,
                               yes=approved, record_command=cmd,
                               estimate=False,             # main printed the set's estimate once
                               log_file=log_path(traces_dir, set_name), refresh=refresh)
    out = _run_ticket(server, engine_name=engine_name, desk=desk, ticket=ticket, set_name=set_name, mode=mode,
                      traces_dir=traces_dir, cmd=cmd)
    if server.refresh:
        if out.ok:
            server.commit_staged()
        else:
            n = server.discard_staged()
            out = Outcome(out.ok, out.spent, out.cap_hit, f"{out.line}; refresh: {n} new recording(s) discarded, "
                                                          "the old recordings and trace are kept")
    return out


def _run_ticket(server: Any, *, engine_name: str, desk: str, ticket: dict, set_name: str, mode: str,
                traces_dir: Path, cmd: str) -> Outcome:
    """Run the engine against this ticket's proxy, close the proxy, and write the trace when the run is usable."""
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    label = f"{engine_name} {desk} {ticket['id']}"
    try:
        with tempfile.TemporaryDirectory(prefix="lumen-run-") as tmp, harness_trace_in(tmp), \
                pointed_at(f"http://{proxy.HOST}:{server.server_address[1]}"):
            labels = LABELS_PATH if mode == "record" else Path(tmp) / "labels.jsonl"   # R33
            try:
                result = run_engine(engine_name, desk, ticket, labels_path=labels)
            except Exception as e:
                if server.cap_hit:
                    return capped(server, label, e)
                if mode == "replay" and _is_proxy_refusal(e):
                    print(f"run: {label}: replay miss ({e})", file=sys.stderr)
                    return Outcome(False, 0.0, False, f"{label}: replay miss; record with: {cmd}")
                return Outcome(False, server.spent, False, f"{label}: failed: {type(e).__name__}: {e}")
    finally:
        server.shutdown()
        server.server_close()           # drops idle connections; one mid-request still answers
        if server.client is not None:
            if not wait_idle(server, CLOSE_WAIT):   # let an in-flight paid call get its reply recorded first
                print(f"run: a proxy handler was still busy after {CLOSE_WAIT:g} s; closing anyway", file=sys.stderr)
            server.client.close()
        thread.join(timeout=10)
    if server.cap_hit:                  # an engine that swallowed the refusal: still incomplete, still no trace
        return capped(server, label, None)
    if server.misses:                   # a miss an engine absorbed (the SDK's sub-agents do): incomplete (R59)
        print(f"run: {label}: {server.misses} replay miss(es) absorbed by the engine", file=sys.stderr)
        return Outcome(False, 0.0, False, f"{label}: replay miss; record with: {cmd}")
    doc = trace_doc(set_name=set_name, engine_name=engine_name, desk=desk, ticket_id=ticket["id"], mode=mode,
                    result=result, spent=server.spent, paid_calls=server.paid_calls, proxy_calls=server.calls)
    path = trace_path(traces_dir, set_name, engine_name, desk, ticket["id"])
    try:
        what = write_trace(path, doc)
    except RuntimeError as e:
        return Outcome(False, server.spent, False, f"{label}: {e}")
    models = sum(1 for r in doc["trace"] if r["kind"] == "model")
    return Outcome(True, server.spent, server.cap_hit, f"{label}: ok, {models} model calls, spent ${server.spent:.4f}; {what}")


# ── 2. Entry point ──────────────────────────────────────────────────────────
def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run an engine on a desk through the record/replay proxy (replay by default).")
    p.add_argument("--set", dest="set_name", choices=sorted(SETS), help="a whole recording set")
    p.add_argument("--engine", choices=ENGINE_NAMES)
    p.add_argument("--desk", help="pattern:<name>, pattern:all, single, supervisor or all")
    p.add_argument("--tickets", default="all", help="all, or ticket ids separated by commas")
    p.add_argument("--mode", choices=["replay", "record"], default="replay")
    p.add_argument("--refresh", action="store_true",
                   help="record mode: re-record even what the store holds (every call is paid)")
    p.add_argument("--dry-run", action="store_true", help="print the recording estimate and exit; calls nothing")
    p.add_argument("--yes", action="store_true", help="approve the paid calls without asking (only after the user approved)")
    p.add_argument("--max-cost", type=proxy.cost_arg, default=proxy.DEFAULT_MAX_COST, help="dollars for the whole run of the set")
    p.add_argument("--max-calls", type=int, default=None,
                   help=f"paid calls per ticket (default {DEFAULT_MAX_CALLS}, supervisor {SUPERVISOR_MAX_CALLS}; at most {MAX_CALLS_LIMIT})")
    p.add_argument("--port", type=int, default=proxy.DEFAULT_PORT, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def resolve(args: argparse.Namespace) -> tuple[SetSpec, tuple[str, ...], tuple[str, ...]]:
    """(set, desks, tickets) from --set or --engine/--desk, narrowed by --tickets."""
    if args.set_name:
        spec = SETS[args.set_name]
        desks = spec.desks
    else:
        if not (args.engine and args.desk):
            raise ValueError("give --set NAME, or --engine and --desk")
        if args.desk == "pattern:all":
            desks = tuple(f"pattern:{p}" for p in PATTERNS)
        elif args.desk == "all":
            desks = DESKS
        else:
            if args.desk.startswith("pattern:") and args.desk.removeprefix("pattern:") not in PATTERNS:
                raise ValueError(f"unknown pattern {args.desk!r}; one of {', '.join(PATTERNS)}")
            desks = (args.desk,)
        names = {set_for(args.engine, d) for d in desks}
        spec = SETS[names.pop()]
    if args.tickets == "all":
        tickets = spec.tickets
    else:
        tickets = tuple(t.strip() for t in args.tickets.split(",") if t.strip())
        unknown = [t for t in tickets if t not in ALL_TICKETS]
        if unknown:
            raise ValueError(f"unknown ticket(s) {', '.join(unknown)}; the eval tickets are {', '.join(ALL_TICKETS)}")
    return spec, desks, tickets


REFRESH_NOTE = ("  --refresh: recordings already in the store are ignored, so every call is paid and re-recorded "
                "(the estimate above already assumes every call is paid); a ticket's new recordings replace the old "
                "only when it finishes ok. After a stop, refresh only the failed tickets with --tickets <id>,<id>")


def set_command(args: argparse.Namespace, spec: SetSpec, desks: tuple[str, ...], tickets: tuple[str, ...]) -> str:
    """The exact record command for what this invocation selected. R71: never with --yes, so a pasted command still
    asks y/N; --yes is for a scripted run after the user approved the estimate."""
    if args.set_name and tickets == spec.tickets:
        cmd = f"python agents/run.py --set {spec.name}"
    else:
        desk = args.desk if not args.set_name else ("pattern:all" if desks[0].startswith("pattern:") else "all")
        cmd = f"python agents/run.py --engine {spec.engine} --desk {desk} --tickets {','.join(tickets) if tickets != spec.tickets else 'all'}"
    cmd += " --mode record" + (" --refresh" if args.refresh else "") + f" --max-cost {args.max_cost:g}"
    if args.max_calls is not None:
        cmd += f" --max-calls {args.max_calls}"
    return cmd


def main(argv: list[str] | None = None, *, store_dir: Path = STORE_DIR, traces_dir: Path = TRACES_DIR,
         transport: Any = None) -> int:
    """`transport` (tests only) is the proxy's fake upstream for record mode."""
    args = parse_args(argv)
    try:
        spec, desks, tickets = resolve(args)
    except ValueError as e:
        print(f"run: {e}")
        return 2
    if args.refresh and args.mode != "record":
        print("run: --refresh re-records, so it needs --mode record (and the same approval as any paid run)")
        return 2
    if args.max_calls is not None and not 1 <= args.max_calls <= MAX_CALLS_LIMIT:
        print(f"run: --max-calls must be 1 to {MAX_CALLS_LIMIT} (R25)")
        return 2
    if spec.engine == "hand-go" and shutil.which("go") is None:
        print("run: the hand-go engine needs the Go toolchain (go) on PATH")
        return 2
    sub = SetSpec(spec.name, spec.engine, desks, tickets)
    if not args.dry_run:
        failed_tool = preflight()      # before any estimate, server or socket (R59)
        if failed_tool is not None:
            print(f"run: pre-flight tool check failed: {failed_tool}. Run `{INSTALL}` and try again. "
                  f"Nothing was called.")
            return 2
    if args.dry_run:
        print_estimate(sub, tickets=tickets, max_calls=args.max_calls, max_cost=args.max_cost)
        if args.refresh:
            print(REFRESH_NOTE)
        print("Record (paid; run only after the user's explicit approve):")
        print(f"  {set_command(args, spec, desks, tickets)}")
        print("Dry run: nothing was called.")
        return 0
    if args.mode == "record":
        if not os.environ.get("ANTHROPIC_API_KEY"):    # before any estimate, server or socket
            print("run: record mode needs ANTHROPIC_API_KEY in the environment. Nothing was called.")
            return 2
        print_estimate(sub, tickets=tickets, max_calls=args.max_calls, max_cost=args.max_cost)
        if args.refresh:
            print(REFRESH_NOTE)
    approved = args.mode == "record" and proxy.confirm(args.yes)      # asked once for the set
    if args.mode == "record" and not approved:
        return 1
    by_id = {t["id"]: t for t in core.TICKETS}
    spent, failed = 0.0, 0
    for desk in desks:
        for tid in tickets:
            left = args.max_cost - spent
            if args.mode == "record" and left <= 0:
                print(f"Stopped: the set's cap ${args.max_cost:.2f} is spent; the rest of {spec.name} is incomplete.")
                return 3
            out = run_ticket(engine_name=spec.engine, desk=desk, ticket=by_id[tid], set_name=spec.name,
                             mode=args.mode, max_cost=left, max_calls=args.max_calls, port=args.port,
                             store_dir=Path(store_dir), traces_dir=Path(traces_dir), approved=approved,
                             transport=transport, refresh=args.refresh)
            spent += out.spent
            print(out.line, flush=True)
            if out.cap_hit:
                print(f"Stopped by the cost cap after ${spent:.4f}; set {spec.name} is incomplete. Recordings so far are saved.")
                return 3
            failed += not out.ok
    if args.mode == "record":
        print(f"Set {spec.name}: spent ${spent:.4f} in total.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
