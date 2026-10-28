"""A stand-in for the Claude Code CLI, for the Agent SDK desk's offline tests (test code, not a recording).

`FakeCLI` is a claude_agent_sdk Transport. Passed to the SDK's own `query(..., transport=FakeCLI(script))`, it
speaks the CLI's side of the control protocol, so the SDK's real code runs: the initialize handshake hands it the
hook callback ids, every tool call goes through the desk's PreToolUse hooks (a deny becomes the CLI's error text,
an updatedInput replaces the call's input), an MCP call is a JSON-RPC `tools/call` through the SDK's in-process
server bridge (so the desk's handlers and run_tool really run), and an Agent call runs a scripted subagent whose
messages carry parent_tool_use_id. The shapes copy what the bundled CLI 2.1.310 sent in an offline probe against a
local stub of the API: one AssistantMessage per content block (sharing the response's message id), a hook denial
as "PreToolUse:<tool> hook error: <reason>", and the Agent tool's PostToolUse response {status, content, ...}.

As the real CLI does, consecutive Agent calls in one response run concurrently (verified: R42); every other
tool runs alone, in order.

No subprocess, no network, no key. The script is per agent: "main" for the desk's own agent, then a worker
name; each entry is one model response: a string (text), {"text", "delay"} (text after `delay` seconds, to
order concurrent subagents), a tool call {"name", "input"}, or a list of these.
A command-line option (model, tools, permission mode) never reaches a custom transport, so the tests assert those
on the ClaudeAgentOptions instead.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
from typing import Any

import anyio
from claude_agent_sdk import ProcessError, Transport
from mcp.types import LATEST_PROTOCOL_VERSION

MODEL = "claude-haiku-5-5"


def use(name: str, **args: Any) -> dict:
    return {"name": name, "input": args}


class FakeCLI(Transport):
    def __init__(self, script: dict[str, list], *, max_turns: int = 8, worker_turns: dict[str, int] | None = None,
                 result: dict | None = None) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.max_turns = max_turns
        self.worker_turns = {"research": 3, **(worker_turns or {})}
        self.result_override = result          # a canned result frame (e.g. an API error), sent instead of a run
        self.hooks: dict[str, list[dict]] = {}
        self.prompts: list[tuple[str, str]] = []        # (agent, the prompt it was given)
        self.agent_inputs: list[dict] = []              # every Agent call's input as it ran (after the hooks)
        self.contexts: list[str] = []                   # PostToolUse additionalContext the desk returned
        self.hook_errors = 0                            # hook callbacks that failed (the CLI then runs the tool)
        self._send, self._recv = anyio.create_memory_object_stream[dict](1000)
        self._pending: dict[str, list] = {}
        self._ids = itertools.count(1)
        self._mcp_ready: set[str] = set()
        self._task: asyncio.Task | None = None
        self._ready = False
        self._error = False

    # ── the Transport interface ──
    async def connect(self) -> None:
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    async def write(self, data: str) -> None:
        for line in data.splitlines():
            if not line.strip():
                continue
            msg = json.loads(line)
            kind = msg.get("type")
            if kind == "control_request" and msg["request"].get("subtype") == "initialize":
                self.hooks = msg["request"].get("hooks") or {}
                await self._send.send({"type": "control_response", "response": {
                    "subtype": "success", "request_id": msg["request_id"], "response": {}}})
            elif kind == "control_response":
                slot = self._pending.get(msg["response"]["request_id"])
                if slot is not None:
                    slot[1] = msg["response"]
                    slot[0].set()
            elif kind == "user":
                self._task = asyncio.get_running_loop().create_task(self._main(msg["message"]["content"]))

    async def read_messages(self):
        async for item in self._recv:
            yield item
        if self._error:   # the CLI exits non-zero after an error result; the SDK turns that into ResultError
            raise ProcessError("Command failed", exit_code=1, stderr="")

    async def end_input(self) -> None:
        if self._task is not None:
            await self._task
        self._send.close()

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._send.close()

    # ── the CLI's side ──
    async def _emit(self, msg: dict) -> None:
        await self._send.send({"session_id": "fake", **msg})

    async def _request(self, request: dict) -> dict:
        rid = f"cli_{next(self._ids)}"
        slot = [anyio.Event(), None]
        self._pending[rid] = slot
        await self._send.send({"type": "control_request", "request_id": rid, "request": request})
        with anyio.fail_after(30):
            await slot[0].wait()
        response = self._pending.pop(rid)[1]
        if response.get("subtype") == "error":
            raise RuntimeError(f"control request failed: {response.get('error')}")
        return response.get("response") or {}

    async def _hooks(self, event: str, tool_name: str, data: dict, tool_use_id: str) -> list[dict]:
        outs = []
        for matcher in self.hooks.get(event) or []:
            pattern = matcher.get("matcher")
            if pattern and not re.fullmatch(pattern, tool_name):
                continue
            for cid in matcher.get("hookCallbackIds", []):
                try:
                    outs.append(await self._request({"subtype": "hook_callback", "callback_id": cid,
                                                     "input": data, "tool_use_id": tool_use_id}))
                except RuntimeError:   # as the real CLI: a hook that errors makes no decision, and the tool runs
                    self.hook_errors += 1
        return outs

    async def _mcp(self, server: str, method: str, params: dict | None, notify: bool = False) -> dict:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            message["id"] = next(self._ids)
        response = await self._request({"subtype": "mcp_message", "server_name": server, "message": message})
        return response.get("mcp_response") or {}

    async def _call_mcp(self, server: str, tool: str, args: dict) -> tuple[Any, bool]:
        if server not in self._mcp_ready:
            await self._mcp(server, "initialize", {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                                                   "clientInfo": {"name": "fake-cli", "version": "0"}})
            await self._mcp(server, "notifications/initialized", None, notify=True)
            self._mcp_ready.add(server)
        reply = await self._mcp(server, "tools/call", {"name": tool, "arguments": args})
        if "error" in reply:
            return reply["error"].get("message", "error"), True
        result = reply["result"]
        return result.get("content", []), bool(result.get("isError"))

    async def _tool(self, agent: str, parent: str | None, block: dict) -> tuple[Any, bool]:
        name, args, tid = block["name"], block["input"], block["id"]
        base = {"session_id": "fake", "transcript_path": "", "cwd": "", "tool_name": name, "tool_use_id": tid}
        who = {} if parent is None else {"agent_id": f"agent-{parent}", "agent_type": agent}
        for out in await self._hooks("PreToolUse", name, {**base, **who, "hook_event_name": "PreToolUse",
                                                          "tool_input": args}, tid):
            spec = out.get("hookSpecificOutput") or {}
            if spec.get("permissionDecision") == "deny":
                return f"PreToolUse:{name} hook error: {spec.get('permissionDecisionReason')}", True
            if "updatedInput" in spec:
                args = spec["updatedInput"]
        if name.startswith("mcp__"):
            _, server, tool = name.split("__", 2)
            content, is_error = await self._call_mcp(server, tool, args)
            response: Any = content
        elif name in ("Agent", "Task") and parent is None:
            self.agent_inputs.append(dict(args))
            worker = args["subagent_type"]
            text, _ = await self._agent(worker, tid, args["prompt"], self.worker_turns.get(worker, 1))
            content, is_error = [{"type": "text", "text": text}], False
            response = {"status": "completed", "agentType": worker, "content": content}
        else:
            return f"No such tool available: {name}", True
        for out in await self._hooks("PostToolUse", name, {**base, **who, "hook_event_name": "PostToolUse",
                                                           "tool_input": args, "tool_response": response,
                                                           "duration_ms": 3}, tid):
            context = (out.get("hookSpecificOutput") or {}).get("additionalContext")
            if context:
                self.contexts.append(context)
        return content, is_error

    async def _agent(self, agent: str, parent: str | None, prompt: str, cap: int) -> tuple[str, bool]:
        """One agent loop: scripted responses until one has no tool call, or `cap` responses."""
        self.prompts.append((agent, prompt))
        text = ""
        for _ in range(cap):
            if not self.script.get(agent):
                raise AssertionError(f"the script for {agent!r} ran out")
            reply = self.script[agent].pop(0)
            if isinstance(reply, dict) and "delay" in reply:
                await anyio.sleep(reply["delay"])
                reply = reply["text"]
            entries = reply if isinstance(reply, list) else [reply]
            mid = f"msg_{next(self._ids)}"
            blocks = []
            if next(self._ids) % 2 == 0:   # R59: half the responses lead with a thinking block, as Haiku 5.5's often do
                blocks.append({"type": "thinking", "thinking": "", "signature": f"sig_{mid}"})
            for e in entries:
                if isinstance(e, str):
                    blocks.append({"type": "text", "text": e})
                else:
                    blocks.append({"type": "tool_use", "id": f"toolu_{next(self._ids):03d}", "name": e["name"], "input": e["input"]})
            for b in blocks:      # the CLI sends one assistant message per block, all with the response's id
                await self._emit({"type": "assistant", "parent_tool_use_id": parent, "message": {
                    "id": mid, "model": MODEL, "role": "assistant", "content": [b],
                    "usage": {"input_tokens": 100, "output_tokens": 1}}})
            text = "".join(b["text"] for b in blocks if b["type"] == "text") or text
            uses = [b for b in blocks if b["type"] == "tool_use"]
            if not uses:
                return text, False
            groups: list[list[dict]] = []      # consecutive Agent calls together, any other tool alone
            for b in uses:
                if groups and b["name"] in ("Agent", "Task") and groups[-1][0]["name"] in ("Agent", "Task"):
                    groups[-1].append(b)
                else:
                    groups.append([b])
            for group in groups:
                done = await asyncio.gather(*(self._tool(agent, parent, b) for b in group))
                for b, (content, is_error) in zip(group, done):
                    await self._emit({"type": "user", "parent_tool_use_id": parent, "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": b["id"], "content": content, "is_error": is_error}]}})
        return text, True

    async def _main(self, prompt: str) -> None:
        try:
            if self.result_override is not None:
                frame = self.result_override
            else:
                text, capped = await self._agent("main", None, prompt, self.max_turns)
                frame = {"subtype": "error_max_turns" if capped else "success", "is_error": capped,
                         "result": None if capped else text}
            self._error = bool(frame.get("is_error"))
            await self._emit({"type": "result", "duration_ms": 5, "duration_api_ms": 4, "num_turns": 1,
                              "total_cost_usd": 0.0042, "usage": {"input_tokens": 100, "output_tokens": 5},
                              "modelUsage": {MODEL: {"inputTokens": 100, "outputTokens": 5, "costUSD": 0.0042}},
                              **frame})
        except BaseException as e:   # surface a broken script as the CLI's crash, never a hang
            self._error = True
            await self._emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                              "duration_ms": 0, "duration_api_ms": 0, "num_turns": 0, "result": f"fake CLI: {e!r}"})
