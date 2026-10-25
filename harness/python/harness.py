#!/usr/bin/env python3
"""
mini_harness — a complete, teaching-sized AI agent harness.

The model only ever returns text or a request to call a tool. Everything
else — the loop, the tools, permissions, context limits, logging — is the
harness. This file is all of it, in about 200 lines.

Run:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python harness.py
"""
from __future__ import annotations

import json
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
    decision = input("    Allow? [y/N] ").strip().lower() == "y"
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
def main() -> None:
    WORKSPACE.mkdir(exist_ok=True)
    print(f"mini-harness · {MODEL} · workspace {WORKSPACE}\nType a task, or 'exit'.")
    while True:
        try:
            task = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if task in {"exit", "quit"}:
            break
        if not task:
            continue
        try:
            print("\nagent >", run_agent(task))
        except anthropic.RateLimitError:
            print("Rate limited even after retries. Wait a minute and try again.")
        except anthropic.APIStatusError as e:
            print(f"API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            print("Network error: could not reach the API.")


if __name__ == "__main__":
    main()
