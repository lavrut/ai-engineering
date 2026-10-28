"""
judge_set — the eval's judge set, for agents/evals/run_evals.py: the judge requests the current traces ask for,
what recording them would cost, and recording them (paid: --record-judge, after the estimate and the y/N).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import desk_core as core
import proxy
from run_evals import DESK_SETS, DESKS, ENGINES, JUDGE_MODEL, JUDGE_OUTPUT, JUDGE_SET, judge_all, judge_items, judge_messages, load
from triage_lab import JUDGE_MODEL as LAB_JUDGE_MODEL  # the LLMs lab, import only
from triage_lab import EST_TOKENS, PRICES, _judge_system, call_cost, load_prompts

# ── 1. The judge set: estimate and record ───────────────────────────────────
def present_docs(traces_dir: Path) -> list[dict]:
    """Every engine x desk x ticket trace that is usable (load), in a fixed order."""
    return [d for e in ENGINES for k in DESKS for t in DESK_SETS[e].tickets
            if (d := load(traces_dir, e, k, t)) is not None]


def judge_key(ticket_id: str, reply: str) -> str:
    """The store key of the judge request for one (ticket, reply): what the proxy files it under."""
    tickets = {t["id"]: t for t in core.TICKETS}
    return proxy.request_key("POST", "/v1/messages", judge_body(tickets[ticket_id], reply))


def judge_keys(traces_dir: Path) -> set[str]:
    """R73: the keys of every judge request the current traces ask for (agents/prune.py keeps exactly these)."""
    return {judge_key(tid, reply) for tid, reply in judge_items(present_docs(traces_dir))}


def judge_plan(traces_dir: Path, store_dir: Path) -> dict:
    planned = sum(len(DESK_SETS[e].tickets) * len(DESKS) for e in ENGINES)
    present = present_docs(traces_dir)
    store = proxy.Store(Path(store_dir) / JUDGE_SET)
    items = judge_items(present)
    # recorded: the requests whose key is in the store (a recording of an older judge request does not count)
    recorded = sum(store.find(judge_key(tid, reply)) is not None for tid, reply in items)
    return {"planned": planned, "traces": len(present), "items": items, "recorded": recorded}


def judge_body(ticket: dict, reply: str) -> dict:
    """The request desk_core.claude sends for one judge call (the estimate and the recorded count)."""
    return {"model": JUDGE_MODEL, "max_tokens": core.MAX_TOKENS, "system": _judge_system(load_prompts()),
            "messages": judge_messages(ticket, reply), "output_config": JUDGE_OUTPUT}


def print_judge_estimate(plan: dict, max_cost: float, refresh: bool = False) -> None:
    """Input per call: the lab's EST_TOKENS for the judge, or the proxy's measure of the real requests (the
    traces present now, 4 characters per token over the request JSON) when that is larger, so it never
    understates; the worst case takes the largest request measured."""
    est_in, est_out = EST_TOKENS["judge"]
    tickets = {t["id"]: t for t in core.TICKETS}
    sizes = [proxy.estimate_input_tokens(judge_body(tickets[tid], reply)) for tid, reply in plan["items"]]
    typ_in = max(est_in, round(sum(sizes) / len(sizes))) if sizes else est_in
    worst_in = max([est_in, *sizes])
    n = plan["planned"]
    p_in, p_out = PRICES[JUDGE_MODEL]
    print(f"Recording set {JUDGE_SET}: the eval's judge, {JUDGE_MODEL} (the LLMs lab grades with {LAB_JUDGE_MODEL}; "
          f"haiku here for cost, as the desks' reviewer, R21), structured output (output_config json_schema, R63)")
    print(f"  prices ${p_in:.2f} input / ${p_out:.2f} output per million tokens (llms/python/triage_lab.py PRICES)")
    print(f"  calls: one per reply to judge, i.e. per complete trace: {n} planned "
          f"({len(ENGINES)} engines x {len(DESKS)} desks x 13 tickets)")
    print(f"  now: {plan['traces']} complete traces, {len(plan['items'])} distinct (ticket, reply) requests; "
          f"{plan['recorded']} already recorded (replayed free"
          + ("; but --refresh re-records them: every call is paid)" if refresh else ")"))
    print(f"  input per call: typical {typ_in}, largest {worst_in} tokens (the lab's EST_TOKENS {est_in}, or the "
          f"requests measured from the traces present, whichever is larger)")
    print(f"  typical ${n * call_cost(JUDGE_MODEL, typ_in, est_out):.4f} ({est_out} output tokens per call, the "
          f"lab's EST_TOKENS); worst case ${n * call_cost(JUDGE_MODEL, worst_in, core.MAX_TOKENS):.4f} "
          f"(every call at {core.MAX_TOKENS} output tokens)")
    print(f"  cap ${max_cost:.2f} for the set, checked by the proxy before every call")
    if refresh:
        print("  --refresh: recordings already in the store are ignored, so every call is paid and re-recorded "
              "(the estimate above already assumes every call is paid)")


def record_judge(args: argparse.Namespace, traces_dir: Path, store_dir: Path, transport: Any) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):     # before any estimate, server or socket
        print("run_evals: --record-judge needs ANTHROPIC_API_KEY in the environment. Nothing was called.")
        return 2
    plan = judge_plan(traces_dir, store_dir)
    print_judge_estimate(plan, args.max_cost, args.refresh)
    if not plan["items"]:
        print("No complete traces to judge yet: record the desk sets first. Nothing was called.")
        return 0
    if not proxy.confirm(args.yes):
        return 1
    verdicts, spent, cap_hit = judge_all(plan["items"], store_dir, mode="record", port=args.port, approved=True,
                                         max_cost=args.max_cost, transport=transport, refresh=args.refresh)
    print(f"Judged {sum(v is not None for v in verdicts.values())} of {len(plan['items'])}; spent ${spent:.4f}.")
    if cap_hit:
        print("Stopped by the cost cap; the judge set is incomplete. Recordings so far are saved.")
        return 3
    print("Now rescore: .venv-agents/bin/python agents/evals/run_evals.py")
    return 0
