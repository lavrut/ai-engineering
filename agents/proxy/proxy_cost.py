"""
proxy_cost — what a call may cost and what it did cost, for the record/replay proxy (proxy.py): the request
check, the cap's worst case, the reply's usage, the ledger (R62, R64), the up-front estimate and the y/N.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from proxy import DEFAULT_MAX_COST, DEFAULT_MODEL, EST_INPUT_TOKENS, MAX_CALLS, MAX_TOKENS, MAX_TOKENS_LIMIT
from triage_lab import PRICES, call_cost  # the LLMs lab's prices (proxy.py puts llms/python on the path)

# ── 1. Cost ─────────────────────────────────────────────────────────────────
class BadRequest(ValueError):
    """The request is refused before any key lookup or call (answered 400)."""


def check_body(body: dict) -> None:
    """model must be a string; max_tokens, when present, an int in 1..MAX_TOKENS_LIMIT.
    Checked up front so a bad value can never make a negative or absurd reservation."""
    if "model" in body and not isinstance(body["model"], str):
        raise BadRequest("model must be a string")
    mt = body.get("max_tokens")
    if mt is not None and (isinstance(mt, bool) or not isinstance(mt, int) or not 1 <= mt <= MAX_TOKENS_LIMIT):
        raise BadRequest(f"max_tokens must be an integer from 1 to {MAX_TOKENS_LIMIT}")


def estimate_input_tokens(body: dict) -> int:
    """About four characters per token over the request JSON: rough, and on the high side."""
    return len(json.dumps(body, ensure_ascii=False)) // 4 + 1


def worst_call(body: dict) -> float:
    """Dollars if this call used every output token it may: input estimate at the input price plus
    max(request max_tokens, MAX_TOKENS) at the output price (ruling R14), never above the API limit."""
    model = body.get("model") or DEFAULT_MODEL
    out = min(max(int(body.get("max_tokens") or 0), MAX_TOKENS), MAX_TOKENS_LIMIT)
    return call_cost(model, estimate_input_tokens(body), out)


def _sse_data(events: list[str]) -> list[dict]:
    out = []
    for ev in events:
        for line in ev.splitlines():
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:])
                except ValueError:
                    continue
                if isinstance(data, dict):
                    out.append(data)
    return out


def _sse_event_names(events: list[str]) -> list[str]:
    return [line[6:].strip() for ev in events for line in ev.splitlines() if line.startswith("event:")]


def sse_complete(events: list[str]) -> bool:
    """A stream is a good recording only if it has no error event and reaches message_stop."""
    names = _sse_event_names(events)
    types = [d.get("type") for d in _sse_data(events)]
    return "error" not in names and "error" not in types and ("message_stop" in names or "message_stop" in types)


def usage_tokens(response: dict) -> tuple[int, int] | None:
    """(input, output) tokens from the reply's own usage (JSON body, or SSE message_start then message_delta), for
    the run's spend and so the cap: cache writes and reads count as full-price input (the conservative side; the
    ledger prices them apart, R64). None if the reply carries no usage."""
    usage_in = usage_out = None
    if "sse_events" in response:
        for data in _sse_data(response["sse_events"]):
            if data.get("type") == "message_start":
                u = data.get("message", {}).get("usage", {})
                usage_in = sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens"))
                usage_out = u.get("output_tokens") or 0
            elif data.get("type") == "message_delta" and isinstance(data.get("usage"), dict):
                usage_out = data["usage"].get("output_tokens", usage_out)
    elif isinstance(response.get("body"), dict) and isinstance(response["body"].get("usage"), dict):
        u = response["body"]["usage"]
        usage_in = sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                               "cache_read_input_tokens"))
        usage_out = u.get("output_tokens") or 0
    if usage_in is None:
        return None
    return usage_in, usage_out or 0


def usage_cost(model: str, response: dict) -> float | None:
    """Dollars from the reply's own usage (JSON body or SSE events); None if it carries none."""
    used = usage_tokens(response)
    return None if used is None else call_cost(model, *used)


# R62: what one served call cost and took, from the recording. Traces carry these (proxy_calls) so the eval's
# cost and latency come from the recording, never from what a particular run happened to pay (a store hit is
# free and takes ~1 ms; the work it stands for was not).
MESSAGES_PATHS = ("/v1/messages", "/v1/messages?beta=true")


# R64: the API's usage fields, stored verbatim and apart. input_tokens is the uncached input only.
USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
# R64: prompt-cache prices as multiples of the model's input price (Anthropic's published rates: a cache write
# costs 1.25x, a cache read 0.10x). The ledger prices a recording's cached input with these, so an engine whose
# requests hit the cache (the Agent SDK's CLI does, on most calls) is costed as it is billed. The estimates and the
# cap do not use them: they assume no caching, the conservative side.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def upstream_usage(response: dict) -> dict[str, int]:
    """The reply's four usage fields (JSON body, or SSE message_start then message_delta); a missing one is 0."""
    usage: dict[str, Any] = {}
    if "sse_events" in response:
        for data in _sse_data(response["sse_events"]):
            if data.get("type") == "message_start":
                usage.update(data.get("message", {}).get("usage") or {})
            elif data.get("type") == "message_delta" and isinstance(data.get("usage"), dict):
                usage.update({k: v for k, v in data["usage"].items() if v is not None})
    elif isinstance(response.get("body"), dict) and isinstance(response["body"].get("usage"), dict):
        usage = response["body"]["usage"]
    return {k: int(usage.get(k) or 0) for k in USAGE_FIELDS}


def ledger_cost(model: str | None, usage: dict) -> float | None:
    """Dollars for one recorded call at the lab's PRICES, cache tokens at their multipliers (R64); None when the
    model has no price or a field is unknown."""
    if model not in PRICES or any(usage.get(k) is None for k in USAGE_FIELDS):
        return None
    p_in, p_out = PRICES[model]
    return (usage["input_tokens"] * p_in + usage["cache_creation_input_tokens"] * CACHE_WRITE_MULTIPLIER * p_in
            + usage["cache_read_input_tokens"] * CACHE_READ_MULTIPLIER * p_in
            + usage["output_tokens"] * p_out) / 1_000_000


def ledger_entry(key: str, replayed: bool, upstream: dict | None) -> dict:
    """One ledger row: the recording's upstream ms and usage (all None when it predates them), priced with
    ledger_cost whether the call was replayed or live."""
    up = upstream or {}
    usage = {k: up.get(k) for k in USAGE_FIELDS}
    return {"key": key, "replayed": replayed, "ms": up.get("ms"), "model": up.get("model"), **usage,
            "cost_usd": ledger_cost(up.get("model"), usage)}


def estimate_line(model: str = DEFAULT_MODEL, calls: int = MAX_CALLS, max_cost: float = DEFAULT_MAX_COST) -> str:
    """Print and return the up-front estimate for one record run."""
    typical = calls * call_cost(model, EST_INPUT_TOKENS, 700)
    worst = calls * call_cost(model, EST_INPUT_TOKENS, MAX_TOKENS)
    line = (f"Estimated cost ${typical:.2f} for up to {calls} calls on {model} (worst case ${worst:.2f} "
            f"if every reply uses {MAX_TOKENS} tokens). Cap: ${max_cost:.2f}, checked before every call.")
    print(line, flush=True)
    return line


class NotConfirmed(Exception):
    """Record mode was not approved; no server was built and nothing was called."""


def confirm(yes: bool) -> bool:
    """True only after --yes or an interactive y. Stdin that is not a terminal counts as no."""
    if yes:
        return True
    answer = ""
    if sys.stdin.isatty():
        try:
            answer = input("Make these paid calls? [y/N] ")
        except EOFError:
            pass
    if answer.strip().lower() not in ("y", "yes"):
        print("Not confirmed. Nothing was called. Run it in a terminal and answer y after reading the estimate.", flush=True)
        return False
    return True
