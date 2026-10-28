"""agent_model — the MCP agent's model calls (replay and record) and their cost.

Part of agent.py (which re-exports every name here): one key per request, replay and record
(section 1), and the estimate, worst case and y/N (2). Configuration comes from agent_config.py.
A test that patches a name used here patches this module, where the code looks it up.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_config import (MAX_CALLS, MAX_TOKENS, MODEL, REPO, SCENARIOS, SYSTEM, TRAINING_NOTE,  # also sets sys.path
                          Options, record_command)
from agent_host import SERVER_NAMES
from client import connect
from ops_server import build_ops_server
from read_server import build_read_server
from triage_lab import PRICES, call_cost  # the lab's price table, dollars per million tokens


# ── 1. Model calls: one key per request, replay and record ──────────────────
class ReplayMiss(Exception):
    """Replay found no recording for a request. It never falls back to a paid call."""


class CostLimit(Exception):
    """The next record-mode call could pass --max-cost (or MAX_CALLS calls were made); it does not start."""


ModelCall = Callable[[str, list[dict]], Callable[..., Any]]  # (system, tools) -> call(messages, **overrides)

# The block fields that are part of the conversation. Anything else the SDK adds is not sent back.
BLOCK_FIELDS = {"text": ("text",), "tool_use": ("id", "name", "input"),
                "thinking": ("thinking", "signature"), "redacted_thinking": ("data",)}


def block_dict(block: Any) -> dict:
    """Only the listed fields, so SDK extras (caller, citations: None, ...) never reach a key. An
    unknown block type fails loudly instead of guessing which of its fields are stable."""
    if isinstance(block, dict):
        return block
    fields = BLOCK_FIELDS.get(block.type)
    if fields is None:
        raise ValueError(f"unsupported content block type {block.type!r}: add its fields to BLOCK_FIELDS")
    return {"type": block.type, **{f: getattr(block, f) for f in fields}}


def plain_messages(messages: list[dict]) -> list[dict]:
    """The messages as JSON: assistant blocks become dicts, tool results already are."""
    return [{"role": m["role"], "content": m["content"] if isinstance(m["content"], str)
             else [block_dict(b) for b in m["content"]]} for m in messages]


def request_key(model: str, system: str, messages: list[dict], tools: list[dict],
                overrides: dict | None = None) -> str:
    """sha256 of the canonical JSON of what shapes the answer: model, system, messages, tools (and any
    per-call override, e.g. the harness's summary call). No time, ids or usage, so a replay matches."""
    request = {"model": model, "system": system, "messages": plain_messages(messages), "tools": tools}
    if overrides:
        request["overrides"] = overrides
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def response_data(response: Any) -> dict:
    """What a recording line saves of one API response."""
    return {"content": [block_dict(b) for b in response.content], "stop_reason": response.stop_reason,
            "usage": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}}


def as_response(data: dict) -> SimpleNamespace:
    """A saved response in the shape run_agent reads: .content blocks, .stop_reason, .usage."""
    return SimpleNamespace(content=[SimpleNamespace(**b) for b in data["content"]],
                           stop_reason=data["stop_reason"], usage=SimpleNamespace(**data["usage"]))


def find_recording(path: Path, key: str) -> dict | None:
    """The latest line for `key` (re-recording a request replaces it)."""
    if not path.exists():
        return None
    for raw in reversed(path.read_text(encoding="utf-8").splitlines()):
        if raw.strip() and (line := json.loads(raw))["key"] == key:
            return line
    return None


def replay_model(opts: Options) -> ModelCall:
    path, model = opts.path, opts.model

    def factory(system: str, tools: list[dict]) -> Callable[..., Any]:
        n = 0

        def call(messages: list[dict], **overrides: Any) -> Any:
            nonlocal n
            n += 1
            line = find_recording(path, request_key(model, system, messages, tools, overrides))
            if line is None:
                shown = path.relative_to(REPO) if path.is_relative_to(REPO) else path
                raise ReplayMiss(f"no recording for model call {n} in {shown}. "
                                 f"See the cost: {record_command(opts, dry_run=True)} "
                                 f"Record it (paid; asks first): {record_command(opts)}")
            return as_response(line["response"])
        return call
    return factory


@dataclass
class Ledger:
    calls: int = 0
    spent: float = 0.0


def worst_call_cost(model: str, params: dict) -> float:
    """The most one call could cost: its request size (CHARS_PER_TOKEN, plus the tool-use prompt) in,
    and all of MAX_TOKENS out."""
    tokens_in = len(json.dumps(params, ensure_ascii=False)) // CHARS_PER_TOKEN + TOOL_USE_TOKENS
    return call_cost(model, tokens_in, MAX_TOKENS)


def record_model(opts: Options, *, client_factory: Callable[[], Any], max_cost: float, ledger: Ledger) -> ModelCall:
    """Call the API and append each response to the recording. A request already recorded is
    replayed instead (never pay twice). Before each paid call: stop if spent + that call's worst case
    would pass max_cost. The client is created on the first paid call only."""
    path, model = opts.path, opts.model

    def factory(system: str, tools: list[dict]) -> Callable[..., Any]:
        client = None

        def call(messages: list[dict], **overrides: Any) -> Any:
            nonlocal client
            key = request_key(model, system, messages, tools, overrides)
            if (saved := find_recording(path, key)) is not None:
                return as_response(saved["response"])
            if ledger.calls >= MAX_CALLS:
                raise CostLimit(f"made {MAX_CALLS} paid calls (MAX_CALLS); spent ${ledger.spent:.4f}")
            params = {"model": model, "max_tokens": MAX_TOKENS, "system": system, "tools": tools,
                      "messages": plain_messages(messages), **overrides}
            worst = worst_call_cost(model, params)
            if ledger.spent + worst > max_cost:
                raise CostLimit(f"spent ${ledger.spent:.4f}; the next call could cost up to ${worst:.4f}, "
                                f"over --max-cost {max_cost:g}")
            client = client or client_factory()
            data = response_data(client.messages.create(**params))
            ledger.calls += 1
            ledger.spent += call_cost(model, data["usage"]["input_tokens"], data["usage"]["output_tokens"])
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "scenario": opts.name, "call": ledger.calls, "model": model,
                                    "framing": opts.framing, "guard": opts.guard, "approve": opts.approve,
                                    "elicit": opts.elicit,
                                    "response": data}, ensure_ascii=False) + "\n")
            return as_response(data)
        return call
    return factory


def anthropic_client() -> Any:
    """The real client, for record mode only. The SDK reads ANTHROPIC_API_KEY from the environment;
    this file never reads, prints or passes the key."""
    import anthropic
    return anthropic.Anthropic(max_retries=4)


# ── 2. Cost: estimate, worst case, confirm ──────────────────────────────────
CHARS_PER_TOKEN = 4          # a rough rule for English and JSON; the estimate says so
TOOL_USE_TOKENS = 500        # the API's own tool-use system prompt, added to every request with tools
EST_OUTPUT = 400             # typical output tokens per call: a tool call or a short answer
EST_GROWTH = 600             # typical input each call adds: the last reply plus its tool results
WORST_RESULT_TOKENS = 8_000  # the most input one round of tool results is assumed to add


async def _describe(servers: tuple[str, ...]) -> tuple[dict[str, str], list[dict]]:
    """Instructions and tool schemas, straight from the in-memory servers (no harness, no API)."""
    instructions, tools = {}, []
    async with AsyncExitStack() as stack:
        for server in servers:
            target = build_read_server() if server == "read" else build_ops_server(labels_path=Path(os.devnull))
            client = await stack.enter_async_context(connect(target))
            instructions[SERVER_NAMES[server]] = client.instructions or ""
            tools += [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                      for t in (await client.list_tools()).tools]
    return instructions, sorted(tools, key=lambda t: t["name"])


def system_prompt(instructions: dict[str, str], framing: str = "none") -> str:
    """The agent's own line, then each server's instructions, as a host passes them on. With
    framing "training", TRAINING_NOTE comes first."""
    note = [TRAINING_NOTE] if framing == "training" else []
    return "\n\n".join(note + [SYSTEM] + [f"{name}: {text}" for name, text in sorted(instructions.items()) if text])


def estimate(scenario: str, model: str = MODEL, framing: str = "none") -> dict:
    """Typical and worst-case dollars for one recording. Input grows each call (the history is resent)."""
    if model not in PRICES:
        raise ValueError(f'no price for model "{model}"')
    spec = SCENARIOS[scenario]
    instructions, tools = asyncio.run(_describe(spec.servers))
    text = json.dumps({"system": system_prompt(instructions, framing), "tools": tools, "task": spec.task})
    first = len(text) // CHARS_PER_TOKEN + TOOL_USE_TOKENS
    typical = sum(call_cost(model, first + i * EST_GROWTH, EST_OUTPUT) for i in range(spec.typical_calls))
    worst_growth = MAX_TOKENS + WORST_RESULT_TOKENS
    worst = sum(call_cost(model, first + i * worst_growth, MAX_TOKENS) for i in range(MAX_CALLS))
    return {"first": first, "tools": len(tools), "calls": spec.typical_calls, "typical": typical,
            "worst": worst, "worst_last_input": first + (MAX_CALLS - 1) * worst_growth}


def print_estimate(opts: Options, cap: float) -> dict:
    e = estimate(opts.scenario, opts.model, opts.framing)
    servers = " + ".join(SERVER_NAMES[s] for s in SCENARIOS[opts.scenario].servers)
    guard = f"guard on (approve {opts.approve})" if opts.guard else "guard off"
    setting = f", {guard}, elicitation {opts.elicit}" if opts.writes else ""
    p_in, p_out = PRICES[opts.model]
    print(f"Recording {opts.name}: {opts.model}, {servers}{setting}, framing {opts.framing}")
    print(f"  prices ${p_in:.2f} input / ${p_out:.2f} output per million tokens")
    print(f"  first request ~{e['first']:,} input tokens (system, {e['tools']} tools and task at "
          f"{CHARS_PER_TOKEN} characters per token, plus {TOOL_USE_TOKENS} for the API's tool-use prompt)")
    print(f"  typical: {e['calls']} calls, ~{e['first']:,} to ~{e['first'] + (e['calls'] - 1) * EST_GROWTH:,} "
          f"input and ~{EST_OUTPUT} output tokens each   ${e['typical']:.4f}")
    print(f"  worst:  MAX_CALLS = {MAX_CALLS} calls, assuming up to {WORST_RESULT_TOKENS:,} tool-result tokens "
          f"per round: up to {e['worst_last_input']:,} input and {MAX_TOKENS:,} output tokens (MAX_TOKENS) "
          f"each   ${e['worst']:.4f}")
    print(f"Estimated cost ${e['typical']:.4f} (worst case ${e['worst']:.4f} if every call uses max_tokens "
          f"and the call limit). Cap: ${cap:.2f}.")
    if e["worst"] > cap:
        print(f"The worst case is over the cap; the run stops at ${cap:.2f} and keeps what finished.")
    return e


def terminal_ask(prompt: str) -> str:
    """input(), but only on a terminal; a pipe or a closed stdin answers no."""
    if not sys.stdin or not sys.stdin.isatty():
        return ""
    try:
        return input(prompt)
    except EOFError:
        return ""


def confirmed(yes: bool, ask: Callable[[str], str]) -> bool:
    if yes or ask("Make these paid calls? [y/N] ").strip().lower() in ("y", "yes"):
        return True
    print("Not confirmed. Nothing was called. Add --yes to approve.")
    return False
