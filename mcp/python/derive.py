"""derive.py — small JSON files for the app's lessons, made by running the real servers in memory.

Writes mcp/data/derived/{messages,tool_budget,elicitation,app_snapshot,injection,lesson_numbers}.json. They are
committed; `.venv-mcp/bin/python mcp/python/derive.py --check` fails if any is out of date (CI-style, writes nothing).

How messages are captured: mcp 2.3.0 has no message hook on the high-level Client, so a wrapping
in-memory transport (TapTransport) sits between the client and the server's stream loop and records
every JSON-RPC message in both directions as it crosses, as the SDK serialises it (model_dump with
by_alias and exclude_none), not as raw bytes. The client runs with mode="auto", so it
opens with a real `server/discover` and every later request carries the 2026-07-28 `_meta`. The
file holds real wire messages; nothing is typed by hand.

Made deterministic: request ids stay as the client numbered them (1, 2, ...). The only volatile
value on these paths is the sealed `requestState` (a fresh nonce on every run); it is replaced by
the placeholder "<sealed state>" everywhere. Label rows (which carry a time) are written to a tmp
file and never copied in. tool_budget's token figure is an ESTIMATE: ceil(characters / 4) of the
compact `tools/list` tools array, not a tokenizer count.

injection.json is built from the two capstone recordings (mcp/data/recordings/capstone-{noguard,
guard}.jsonl), which exist only after the user approves the paid runs. Until then it is
{"recorded": false, "command": [...]}. Once they exist, re-running this file fills in the real
outcome, one entry per recording (keyed by name, with its model and framing): the tool calls the model
made, whether label_ticket was attempted, and the rows written (found by replaying the recording with
its own settings against the real servers on a tmp labels file). Never fabricated.

injection.json also has a separate `simulated` block (ruling R41), built the same way from
mcp/data/simulated/ (written by simulate.py: a SCRIPTED model that obeys t061, run through the real
agent and servers). Every line there must say "simulated": true, and no line in recordings/ may;
either mix-up stops the build. Each simulated entry carries "simulated": true.

lesson_numbers.json holds the figures the lessons' Try-it sections quote that no other file holds, from
the same real code: the client's output (and its md5) for lesson 4's search, lesson 2's classification,
lesson 9's one-hit search, and the free record-mode estimates of lessons 5 and 11. app/tests/mcplessons.test.ts
pins the lesson prose against it.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import math
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp.client._memory import InMemoryTransport
from mcp_types import ElicitResult

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from ops_server import APP_PATH, APP_URI, build_ops_server  # noqa: E402  (puts mcp/core/python on sys.path)
from read_server import build_read_server  # noqa: E402

DERIVED_DIR = REPO / "mcp" / "data" / "derived"
RECORDINGS_DIR = REPO / "mcp" / "data" / "recordings"
SIMULATED_DIR = REPO / "mcp" / "data" / "simulated"
SIM_MODEL = "simulated-compromised"  # simulate.SIM_MODEL; a test pins that they agree
FILES = ("messages", "tool_budget", "elicitation", "app_snapshot", "injection", "lesson_numbers")
SEALED = "<sealed state>"
SAMPLE_INPUT = {"id": "t001", "label": "billing"}


# ── 1. Capturing the wire ───────────────────────────────────────────────────
class Tap:
    """A stream that passes everything through and copies each JSON-RPC message into `log`."""

    def __init__(self, inner: Any, log: list[dict], direction: str) -> None:
        self._inner, self._log, self._direction = inner, log, direction

    def _record(self, item: Any) -> None:
        message = getattr(item, "message", None)  # an Exception from the transport has none
        if message is not None:
            self._log.append({"direction": self._direction,
                              "message": message.model_dump(by_alias=True, exclude_none=True, mode="json")})

    async def receive(self) -> Any:
        item = await self._inner.receive()
        self._record(item)
        return item

    async def send(self, item: Any, /) -> None:
        self._record(item)
        await self._inner.send(item)

    async def aclose(self) -> None:
        await self._inner.aclose()

    def __aiter__(self) -> "Tap":
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            raise StopAsyncIteration from None

    async def __aenter__(self) -> "Tap":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


class TapTransport:
    """The SDK's in-memory transport, with a Tap on each side. The Client takes it as a Transport."""

    def __init__(self, server: Any, log: list[dict]) -> None:
        self._transport, self._log = InMemoryTransport(server), log

    async def __aenter__(self) -> tuple[Tap, Tap]:
        read, write = await self._transport.__aenter__()
        return Tap(read, self._log, "server->client"), Tap(write, self._log, "client->server")

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._transport.__aexit__(*exc)


def scrub(value: Any) -> Any:
    """Replace every requestState string with the placeholder (the one volatile value)."""
    if isinstance(value, dict):
        return {k: SEALED if k == "requestState" and isinstance(v, str) else scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def pair_up(log: list[dict]) -> list[dict]:
    """Join each request with its response by id, in request order. Notifications are left out."""
    replies = {m["message"]["id"]: m["message"] for m in log
               if m["direction"] == "server->client" and "id" in m["message"] and "method" not in m["message"]}
    out = []
    for m in log:
        msg = m["message"]
        if m["direction"] == "client->server" and "method" in msg and "id" in msg:
            reply = replies.get(msg["id"])
            out.append({"method": msg["method"], "request": scrub(msg),
                        "response": scrub(reply) if reply is not None else None})
    return out


async def capture(server: Any, drive: Callable[[Client], Any], **client_kw: Any) -> list[dict]:
    """Run `drive` against `server` over the tapped transport; return the paired exchanges."""
    log: list[dict] = []
    async with Client(TapTransport(server, log), mode="auto", **client_kw) as client:
        await drive(client)
    return pair_up(log)


# ── 2. The five files ───────────────────────────────────────────────────────
def tools_json(exchanges: list[dict]) -> list[dict]:
    return next(x["response"]["result"]["tools"] for x in exchanges if x["method"] == "tools/list")


async def derive_messages() -> dict:
    async def drive(c: Client) -> None:
        await c.list_tools()
        await c.call_tool("get_ticket", {"id": "t001"})
        await c.read_resource("lumen://tickets/t001")
        await c.get_prompt("review_ticket", {"id": "t001"})
    exchanges = await capture(build_read_server(), drive)
    return {"server": "lumen-read", "protocolVersion": "2026-07-28",
            "captured_with": "wrapping in-memory transport (TapTransport); client mode=auto",
            "exchanges": exchanges}


def estimate_tokens(tools: list[dict]) -> dict:
    chars = len(json.dumps(tools, separators=(",", ":"), ensure_ascii=False))
    return {"tools": len(tools), "chars": chars, "tokens_estimate": math.ceil(chars / 4)}


async def derive_tool_budget() -> dict:
    async def listing(server: Any) -> list[dict]:
        async def drive(c: Client) -> None:
            await c.list_tools()
        return tools_json(await capture(server, drive))
    with tempfile.TemporaryDirectory() as tmp:
        read = estimate_tokens(await listing(build_read_server()))
        ops = estimate_tokens(await listing(build_ops_server(Path(tmp) / "labels.jsonl")))
    return {"note": "tokens_estimate = ceil(chars / 4) of the compact tools/list tools array: an estimate, "
                    "not a tokenizer count", "servers": {"lumen-read": read, "lumen-ops": ops}}


async def one_round(answer: ElicitResult) -> dict:
    """label_ticket(t001, billing) once, with the person's `answer`; all its real round trips."""
    async def cb(context: Any, params: Any) -> ElicitResult:
        return answer

    async def drive(c: Client) -> None:
        await c.call_tool("label_ticket", SAMPLE_INPUT)
    with tempfile.TemporaryDirectory() as tmp:  # the label row (it has a time) never leaves this folder
        path = Path(tmp) / "labels.jsonl"
        exchanges = await capture(build_ops_server(path), drive, elicitation_callback=cb)
        rows = path.read_text(encoding="utf-8").count("\n") if path.exists() else 0
    return {"answer": answer.model_dump(exclude_none=True, mode="json"), "rows_written": rows,
            "exchanges": [x for x in exchanges if x["method"] == "tools/call"]}


async def derive_elicitation() -> dict:
    return {"server": "lumen-ops", "tool": "label_ticket", "arguments": SAMPLE_INPUT,
            "note": "requestState is sealed and unique per run; shown as a placeholder",
            "accepted": await one_round(ElicitResult(action="accept", content={"confirm": True})),
            "declined": await one_round(ElicitResult(action="decline"))}


async def derive_app_snapshot() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        async with Client(build_ops_server(Path(tmp) / "labels.jsonl")) as c:
            read = await c.read_resource(APP_URI)
    entry = read.contents[0]
    html = APP_PATH.read_text(encoding="utf-8")
    assert entry.text == html, "the served App differs from app/label.html"
    return {"uri": APP_URI, "mimeType": entry.mime_type, "html": html,
            "sample_tool_input": {"name": "label_ticket", "arguments": SAMPLE_INPUT}}


LESSON_SEARCH = {"query": "charged twice", "limit": 2}  # lesson 4's parity check (lesson 2 shows the ids)
LESSON_CLASSIFY = {"text": "I was charged twice for my subscription"}  # lesson 2


async def derive_lesson_numbers() -> dict:
    """What the client prints for the lessons' free stdio commands (run in memory, same code), and the
    record-mode estimates the lessons quote (agent.estimate: no API client, no call)."""
    import hashlib

    import agent
    import client as lumen_client
    async with lumen_client.connect(build_read_server()) as c:
        search = await lumen_client.run(c, "call", ["search_tickets", json.dumps(LESSON_SEARCH)])
        one = await lumen_client.run(c, "call", ["search_tickets", json.dumps({**LESSON_SEARCH, "limit": 1})])
        classified = await lumen_client.run(c, "call", ["classify_complaint", json.dumps(LESSON_CLASSIFY)])
    printed = lumen_client.dump(search) + "\n"  # what `client.py ... call search_tickets` writes to stdout

    def est(scenario: str, model: str) -> dict:
        e = agent.estimate(scenario, model)
        return {"model": model, "first": e["first"], "calls": e["calls"], "typical": round(e["typical"], 4),
                "max_calls": agent.MAX_CALLS, "worst": round(e["worst"], 4)}
    lesson5, capstone = await asyncio.to_thread(lambda: (est("lesson5", agent.MODEL),
                                                         est("capstone", "claude-sonnet-5-5")))
    c = classified["structured"]
    return {"search_charged_twice": {"arguments": LESSON_SEARCH,
                                     "ids": [r["id"] for r in search["structured"]["results"]],
                                     "md5": hashlib.md5(printed.encode()).hexdigest()},
            "search_charged_twice_1": [{"id": r["id"], "category": r["category"]}
                                       for r in one["structured"]["results"]],
            "classify": {"arguments": LESSON_CLASSIFY, "label": c["label"], "low_confidence": c["low_confidence"],
                         "scores": len(c["scores"]), "model_version": c["model_version"]},
            "estimates": {"lesson5": lesson5, "capstone-noguard-sonnet-5-5": capstone}}


# ── 3. The injection outcome (needs the paid recordings) ────────────────────
def record_commands() -> list[str]:
    import agent
    return [agent.record_command(agent.Options("capstone", elicit="accept")),
            agent.record_command(agent.Options("capstone", guard=True, approve="decline", elicit="accept"))]


def replay_rows(settings: dict, recordings: Path) -> int:
    """Replay one recording against the real servers on a tmp labels file, with the settings stored
    in its own lines; return the rows written. Replay never calls the API: a missing or changed
    request stops with ReplayMiss."""
    import agent
    opts = agent.Options(settings["scenario"], guard=settings["guard"], approve=settings["approve"],
                         elicit=settings["elicit"], model=settings["model"], framing=settings["framing"],
                         recordings=recordings)
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):  # the harness prints each call
        path = Path(tmp) / "labels.jsonl"
        agent.run_scenario(settings["scenario"], guard=settings["guard"], elicit=settings["elicit"],
                           labels_path=path, model_call=agent.replay_model(opts), approve=settings["approve"],
                           framing=settings["framing"])
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def recording_settings(path: Path) -> dict:
    """The run's settings, from the first line of the recording itself (older lines have no framing)."""
    first = json.loads(next(l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()))
    return {"scenario": "capstone", "model": first["model"], "framing": first.get("framing", "none"),
            "guard": first["guard"], "approve": first["approve"], "elicit": first["elicit"]}


def summarize(path: Path, *, settings: dict, rows_written: int) -> dict:
    """What one recording says: the model and framing, every tool the model called (in order),
    whether it tried label_ticket, what became of it, and its last text answer. The outcome is
    read from the replayed label rows."""
    calls: list[dict] = []
    final = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            content = json.loads(raw)["response"]["content"]
            for block in content:
                if block.get("type") == "tool_use":
                    calls.append({"name": block["name"], "input": block["input"]})
            final = "".join(b["text"] for b in content if b.get("type") == "text") or final
    attempted = any(c["name"].endswith("label_ticket") for c in calls)
    outcome = ("not attempted" if not attempted
               else "written" if rows_written > 0
               else "guard denied" if settings["guard"] else "declined or refused")
    return {"model": settings["model"], "framing": settings["framing"], "guard": settings["guard"],
            "tool_calls": calls, "label_ticket_attempted": attempted,
            "outcome": outcome, "rows_written": rows_written, "final_answer": final}


def simulated_flags(path: Path) -> set:
    """The set of "simulated" values on the file's lines (absent counts as False)."""
    return {json.loads(l).get("simulated", False) is True
            for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def derive_injection(recordings: Path = RECORDINGS_DIR,
                     replay: Callable[[dict, Path], int] = replay_rows,
                     simulated: Path | None = None) -> dict:
    """One entry per capstone recording present (capstone-*.jsonl), keyed by recording name, so
    recordings from other models or framings are summarised as they appear. A recording with a
    simulated line is refused: real and simulated runs never share a table. With `simulated`, the
    separate block from that folder is added."""
    files = sorted(recordings.glob("capstone-*.jsonl")) if recordings.is_dir() else []
    for path in files:
        if True in simulated_flags(path):
            raise ValueError(f"{path.name} has a simulated line, but {recordings} holds only real recordings; "
                             f"simulated transcripts go in {SIMULATED_DIR.relative_to(REPO)}")
    if not files:
        out: dict = {"recorded": False, "command": record_commands()}
    else:
        runs = {}
        for path in files:
            settings = recording_settings(path)
            runs[path.stem] = summarize(path, settings=settings, rows_written=replay(settings, recordings))
        out = {"recorded": True, "recordings": runs}
    if simulated is not None:
        out["simulated"] = derive_simulated(simulated, replay)
    return out


def derive_simulated(folder: Path = SIMULATED_DIR,
                     replay: Callable[[dict, Path], int] = replay_rows) -> dict:
    """The SIMULATED runs (simulate.py), summarised like recordings and replayed through the real
    agent for the rows written. Every line must say "simulated": true and name SIM_MODEL; each entry
    says simulated too."""
    out = {}
    for path in sorted(folder.glob("capstone-*.jsonl")) if folder.is_dir() else []:
        if simulated_flags(path) != {True}:
            raise ValueError(f"{path.name}: every line in {folder} must say \"simulated\": true")
        models = {json.loads(l).get("model") for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}
        if models != {SIM_MODEL}:
            raise ValueError(f"{path.name}: a simulated run must name the model {SIM_MODEL!r}, never a real one; "
                             f"found {sorted(map(str, models))}")
        settings = recording_settings(path)
        out[path.stem] = {"simulated": True,
                          **summarize(path, settings=settings, rows_written=replay(settings, folder))}
    return out


def missing_recordings(committed: Path, recordings: Path, simulated: Path = SIMULATED_DIR) -> list[str]:
    """Recordings (and simulated transcripts) the committed injection.json was built from that are
    no longer there."""
    if not committed.exists():
        return []
    claimed = json.loads(committed.read_text(encoding="utf-8"))
    names = claimed.get("recordings", {}) if claimed.get("recorded") else {}
    gone = [f"{n}.jsonl" for n in names if not (recordings / f"{n}.jsonl").exists()]
    return gone + [f"{n}.jsonl (simulated)" for n in claimed.get("simulated", {})
                   if not (simulated / f"{n}.jsonl").exists()]


# ── 4. Writing and checking ─────────────────────────────────────────────────
async def build_all(recordings: Path = RECORDINGS_DIR, simulated: Path = SIMULATED_DIR) -> dict[str, dict]:
    return {"messages": await derive_messages(), "tool_budget": await derive_tool_budget(),
            "elicitation": await derive_elicitation(), "app_snapshot": await derive_app_snapshot(),
            "injection": await asyncio.to_thread(derive_injection, recordings, replay_rows, simulated),
            "lesson_numbers": await derive_lesson_numbers()}


def render(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write (or --check) the derived lesson data.")
    ap.add_argument("--check", action="store_true", help="fail if a derived file is stale; write nothing")
    ap.add_argument("--out", type=Path, default=DERIVED_DIR)
    ap.add_argument("--recordings", type=Path, default=RECORDINGS_DIR)
    ap.add_argument("--simulated", type=Path, default=SIMULATED_DIR)
    args = ap.parse_args(argv)
    if args.check and (gone := missing_recordings(args.out / "injection.json", args.recordings, args.simulated)):
        print(f"derive: injection.json was built from recordings that are missing from {args.recordings} "
              f"or {args.simulated}: "
              f"{', '.join(gone)}. Restore them (they are committed); not comparing against recorded:false.",
              file=sys.stderr)
        return 1
    built = asyncio.run(build_all(args.recordings, args.simulated))
    stale = [n for n, d in built.items()
             if not (args.out / f"{n}.json").exists() or (args.out / f"{n}.json").read_text(encoding="utf-8") != render(d)]
    if args.check:
        if stale:
            print(f"derive: stale or missing: {', '.join(stale)}. Run: .venv-mcp/bin/python mcp/python/derive.py", file=sys.stderr)
            return 1
        print("derive: up to date")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in built.items():
        (args.out / f"{name}.json").write_text(render(data), encoding="utf-8")
    print(f"derive: wrote {len(built)} files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
