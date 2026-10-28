#!/usr/bin/env python3
"""
mini_harness — a complete, teaching-sized AI agent harness.

The model only ever returns text or a request to call a tool. Everything
else — the loop, the tools, permissions, context limits, logging — is the
harness. This file is all of it, in about 300 lines.

Run (from the repo root; the key setup is in lesson 2):
    python3 -m venv .venv-harness && .venv-harness/bin/pip install -r harness/python/requirements.txt
    cd harness/python && ../../.venv-harness/bin/python harness.py
Every task costs money: the REPL prints an estimate and asks y/N first (section 9).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import anthropic

# ── 1. Configuration ────────────────────────────────────────────────────────
MODEL = "claude-opus-5-5"
MAX_TOKENS = 16_000          # cap on a single model response
MAX_TURNS = 25               # hard stop for a runaway loop
CONTEXT_BUDGET = 150_000     # input tokens before we roll over to a summary
USE_FALLBACKS = True         # retry on a fallback model if a safety classifier declines
WORKSPACE = Path("./workspace").resolve()
TRACE_FILE = Path("trace.jsonl")
PRICE_PER_MTOK = (4.00, 20.00)  # MODEL's dollars per million input / output tokens (PRICES in llms/python/triage_lab.py)
MAX_COST = 10.00                # default --max-cost: the most one REPL session may spend, in dollars
TYPICAL_TASK = (4, 4_000, 400)  # for the estimate: model calls, input tokens and output tokens per call
CHARS_PER_TOKEN = 3             # sizes a request before it is sent; low, so the guess runs high

SYSTEM_PROMPT = f"""You are a careful coding assistant working inside {WORKSPACE}.
Look at files before you change them. Keep your final answer short."""

client = anthropic.Anthropic(max_retries=4)  # SDK retries 429 / 5xx / network errors


# ── 2. Observability: every event flows through one function ────────────────
def emit(event: str, **data: Any) -> None:
    record = {"ts": round(time.time(), 3), "event": event, **data}
    with TRACE_FILE.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


# ── 3. Tool registry ────────────────────────────────────────────────────────
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., str]
    needs_approval: bool = False

    def schema(self) -> dict:
        """The part the model sees. The function itself never leaves the harness."""
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict, required: list[str],
         needs_approval: bool = False):
    """Decorator: turn a plain Python function into a tool the model can call."""
    def register(fn: Callable[..., str]) -> Callable[..., str]:
        schema = {"type": "object", "properties": properties,
                  "required": required, "additionalProperties": False}
        REGISTRY[name] = Tool(name, description, schema, fn, needs_approval)
        return fn
    return register


def safe_path(rel: str) -> Path:
    """Sandbox rule: tools may only touch files inside WORKSPACE."""
    p = (WORKSPACE / rel).resolve()
    if not p.is_relative_to(WORKSPACE):
        raise PermissionError(f"{rel} is outside the workspace")
    return p


@tool("list_files", "List the files in a workspace directory.",
      {"path": {"type": "string", "description": "Directory relative to the workspace, e.g. '.'"}},
      ["path"])
def list_files(path: str) -> str:
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in safe_path(path).iterdir())
    return "\n".join(entries) or "(empty directory)"


@tool("read_file", "Read a UTF-8 text file from the workspace.",
      {"path": {"type": "string", "description": "File path relative to the workspace"}},
      ["path"])
def read_file(path: str) -> str:
    return safe_path(path).read_text()[:20_000]  # cap what one tool can push into context


@tool("write_file", "Create or overwrite a text file in the workspace.",
      {"path": {"type": "string", "description": "File path relative to the workspace"},
       "content": {"type": "string", "description": "The full new file contents"}},
      ["path", "content"], needs_approval=True)
def write_file(path: str, content: str) -> str:
    p = safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"Wrote {len(content)} characters to {path}"


# ── 4. Permission gate ──────────────────────────────────────────────────────
def approve(name: str, args: dict) -> bool:
    print(f"\n  ! The agent wants to run {name}({json.dumps(args)[:200]})")
    try:
        decision = input("    Allow? [y/N] ").strip().lower() == "y"
    except EOFError:  # no more input: treat it as no
        decision = False
    emit("permission", tool=name, allowed=decision)
    return decision


# ── 5. Tool executor ────────────────────────────────────────────────────────
def run_one(block: Any, allowed: bool) -> dict:
    start = time.perf_counter()
    t = REGISTRY.get(block.name)
    print(f"  -> {block.name}({json.dumps(block.input)[:80]})")
    if t is None:
        content, is_error = f"Unknown tool: {block.name}", True
    elif not allowed:
        content, is_error = "The user denied this action.", True
    else:
        try:
            content, is_error = t.fn(**block.input), False
        except Exception as e:  # a tool failure is information for the model, not a crash
            content, is_error = f"{type(e).__name__}: {e}", True
    emit("tool_result", tool=block.name, is_error=is_error,
         ms=round((time.perf_counter() - start) * 1000))
    return {"type": "tool_result", "tool_use_id": block.id,
            "content": content, "is_error": is_error}


def execute_tools(blocks: list[Any],
                  approver: Callable[[str, dict], bool] = approve) -> list[dict]:
    # Ask for approvals one at a time on the main thread (stdin isn't thread-safe) ...
    allowed = {}
    for b in blocks:
        t = REGISTRY.get(b.name)
        allowed[b.id] = t is None or not t.needs_approval or approver(b.name, b.input)
    # ... then run the approved calls in parallel. map() keeps the original order.
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda b: run_one(b, allowed[b.id]), blocks))


# ── 6. Model call ───────────────────────────────────────────────────────────
def call_model(messages: list[dict], **overrides: Any) -> anthropic.types.Message:
    params: dict[str, Any] = dict(
        model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
        tools=[t.schema() for t in REGISTRY.values()], messages=messages,
    )
    if USE_FALLBACKS:
        params["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
        params["extra_body"] = {"fallbacks": "default"}
    params.update(overrides)
    emit("model_request", messages=len(messages))
    response = client.messages.create(**params)
    emit("model_response", stop_reason=response.stop_reason,
         input_tokens=response.usage.input_tokens,
         output_tokens=response.usage.output_tokens)
    return response


# ── 7. Context management: roll over to a summary ───────────────────────────
def compact(messages: list[dict], call: Callable[..., Any] = call_model) -> list[dict]:
    """Start a fresh conversation from a summary. We never edit old turns in
    place: current models reject edited history, so the harness stays append-only."""
    emit("compact", messages=len(messages))
    ask = {"role": "user", "content": "Summarize this session for a fresh start: the goal, "
           "what you did, files touched, and what is left to do."}
    summary = call(messages + [ask], tool_choice={"type": "none"}, max_tokens=4_000)
    text = "".join(b.text for b in summary.content if b.type == "text")
    return [{"role": "user", "content": f"Summary of earlier work:\n{text}\n\nContinue the task."}]


# ── 8. The agent loop ───────────────────────────────────────────────────────
def run_agent(task: str, call: Callable[..., Any] = call_model,
              approver: Callable[[str, dict], bool] = approve) -> str:
    messages: list[dict] = [{"role": "user", "content": task}]
    emit("session_start", task=task)

    for turn in range(1, MAX_TURNS + 1):
        response = call(messages)
        messages.append({"role": "assistant", "content": response.content})
        text = "".join(b.text for b in response.content if b.type == "text")

        if response.stop_reason == "tool_use":
            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            # All results go back in ONE user message, matched by tool_use_id.
            messages.append({"role": "user", "content": execute_tools(tool_blocks, approver)})
            if response.usage.input_tokens > CONTEXT_BUDGET:
                messages = compact(messages, call)
            continue

        emit("session_end", turns=turn, stop_reason=response.stop_reason)
        if response.stop_reason == "max_tokens":
            return text + "\n[stopped: the response hit max_tokens]"
        if response.stop_reason == "refusal":
            return "[the model declined this request]"
        return text  # end_turn: the model is done

    emit("session_end", turns=MAX_TURNS, stop_reason="max_turns")
    return "[stopped: reached MAX_TURNS without finishing]"


# ── 9. Entry point: a tiny REPL ─────────────────────────────────────────────
def dollars(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * PRICE_PER_MTOK[0] + output_tokens * PRICE_PER_MTOK[1]) / 1_000_000


class CostLimit(Exception):
    """The next model call could take the session past --max-cost, so it was not made."""


@dataclass
class Ledger:
    cap: float = MAX_COST
    spent: float = 0.0


def metered(call: Callable[..., Any], ledger: Ledger) -> Callable[..., Any]:
    """The money gate on every model call: stop if spent + this call's worst case would pass the cap."""
    def gated(messages: list[dict], **overrides: Any) -> Any:
        sent = json.dumps([SYSTEM_PROMPT, [t.schema() for t in REGISTRY.values()], messages],
                          default=lambda o: getattr(o, "__dict__", str(o)))
        worst = dollars(len(sent) // CHARS_PER_TOKEN, overrides.get("max_tokens", MAX_TOKENS))
        if ledger.spent + worst > ledger.cap:
            raise CostLimit(f"spent ${ledger.spent:.4f}; the next call could cost up to ${worst:.4f}, "
                            f"over --max-cost {ledger.cap:.2f}")
        response = call(messages, **overrides)
        ledger.spent += dollars(response.usage.input_tokens, response.usage.output_tokens)
        return response
    return gated


def estimate_lines(ledger: Ledger) -> list[str]:
    calls, tokens_in, tokens_out = TYPICAL_TASK
    typical, worst = calls * dollars(tokens_in, tokens_out), MAX_TURNS * dollars(CONTEXT_BUDGET, MAX_TOKENS)
    return [f"Estimate: typical ${typical:.2f} a task ({calls} calls of ~{tokens_in:,} input and ~{tokens_out:,} "
            f"output tokens); worst case ${worst:.2f} if every call stays at CONTEXT_BUDGET (MAX_TURNS {MAX_TURNS} calls of {CONTEXT_BUDGET:,} input "
            f"and {MAX_TOKENS:,} output tokens, plus any compaction summaries), at ${PRICE_PER_MTOK[0]:.2f} / "
            f"${PRICE_PER_MTOK[1]:.2f} per million tokens; the hard stop is --max-cost ${ledger.cap:.2f}.",
            f"Spent ${ledger.spent:.4f} this session; each call is checked against the cap first, "
            f"so the session stops at about ${ledger.cap:.2f}."]


def confirmed(yes: bool) -> bool:
    """y on a terminal, or --yes for a scripted run you approved. Anything else is no."""
    if yes:
        return True
    if not sys.stdin.isatty():
        print("Not run: no terminal to ask. Nothing was spent.")
        return False
    try:
        answer = input("Run it? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() == "y":
        return True
    print("Not run. Nothing was spent.")
    return False


def main(argv: list[str] | None = None, call: Callable[..., Any] = call_model) -> int:
    ap = argparse.ArgumentParser(description="mini-harness: a REPL that calls the Claude API (paid).")
    ap.add_argument("--max-cost", type=float, default=MAX_COST, help="dollars this session may spend")
    ap.add_argument("--yes", action="store_true", help="scripted runs only: approve every task without asking")
    args = ap.parse_args(argv)
    if not math.isfinite(args.max_cost) or args.max_cost <= 0:  # nan would make every "over the cap?" test false
        print("--max-cost must be a finite number above 0. Nothing was called.")
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY first (lesson 2). Nothing was called.")
        return 1
    WORKSPACE.mkdir(exist_ok=True)
    ledger = Ledger(cap=args.max_cost)
    print(f"mini-harness · {MODEL} · workspace {WORKSPACE}\nType a task, or 'exit'.")
    while True:
        try:
            task = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if task in {"exit", "quit"}:
            return 0
        if not task:
            continue
        print("\n".join(estimate_lines(ledger)))
        if not confirmed(args.yes):
            continue
        before = ledger.spent
        try:
            print("\nagent >", run_agent(task, call=metered(call, ledger)))
        except CostLimit as e:
            print(f"Stopped: {e}")
        except anthropic.RateLimitError:
            print("Rate limited even after retries. Wait a minute and try again.")
        except anthropic.APIStatusError as e:
            print(f"API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            print("Network error: could not reach the API.")
        except Exception as e:  # anything else: report it and keep the REPL, as Go's main does
            print(f"Error: {e}")
        print(f"  [${ledger.spent - before:.4f} this task, ${ledger.spent:.4f} this session]")


if __name__ == "__main__":
    sys.exit(main())
