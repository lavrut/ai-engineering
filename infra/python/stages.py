#!/usr/bin/env python3
"""
stages: the gateway's chain, one function per stage, run in order on every request:
tag -> budget -> cache -> route -> limit -> retry/upstream.
Each stage returns a decision {stage, verdict, detail} plus keys of its own; run_chain stops
at the first refusal and maps verdicts to statuses (400 parse, 402 budget, 404 deny, 429
limit, 502 upstream). Bodies go upstream byte for byte; a fallback changes where, never what.
A stage that raises is a 500 and a decision {stage, verdict: error, detail: <exception>}.

Run (from the repo root; free and offline, the cases' upstream is a script of statuses):
    .venv-infra/bin/python infra/python/stages.py --cases infra/data/fixtures/cases.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import ledger
from responses import (cache_key, canonical, error_body, json_body, messages_text, response_usage,
                       retry_after_ms, stop_reason)

# ── 1. Request and state ────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
CONFIG_JSON = ROOT / "infra" / "data" / "fixtures" / "config.json"
Decision = dict  # {stage, verdict, detail} as strings, plus the stage's own keys
JSON_HEADERS = {"content-type": "application/json"}


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]  # lower-cased names
    body: bytes              # forwarded as is
    json: dict | None        # the parsed body, None when it is not a JSON object


def make_request(method: str, path: str, headers: dict[str, str], body: bytes) -> Request:
    return Request(method.upper(), path, {k.lower(): v for k, v in headers.items()}, body, json_body(body))


@dataclass
class CacheEntry:
    status: int
    headers: dict[str, str]
    body: bytes
    stop_reason: str | None
    stored_at: float
    tools: bool = False


@dataclass
class Bucket:
    tokens: float    # tokens left this minute
    requests: float  # requests left this minute
    at: float        # clock reading of the last refill


@dataclass
class State:
    spend: dict[str, float] = field(default_factory=dict)       # dollars per team
    cache: dict[str, CacheEntry] = field(default_factory=dict)  # by cache_key
    buckets: dict[str, Bucket] = field(default_factory=dict)    # by target model
    clock: Callable[[], float] = time.time
    rng: random.Random = field(default_factory=random.Random)


@dataclass
class Upstream:
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    error: str | None = None  # set when no answer came back (a dropped connection)


@dataclass
class Outcome:
    status: int
    headers: dict[str, str]
    body: bytes
    decisions: list[Decision]
    team: str
    tag: str
    usage: dict | None
    cost_usd: float | None
    ms: float
    cached: bool
    attempts: list[dict]  # {model, status, error, backoff_ms} per upstream call


def decision(stage: str, verdict: str, detail: str, **extra) -> Decision:
    return {"stage": stage, "verdict": verdict, "detail": detail, **extra}


# ── 2. Tag ──────────────────────────────────────────────────────────────────
TICKET_ID = re.compile(r"\bt\d{3}\b")


def tag(req: Request, cfg) -> Decision:
    """verdict: the team from x-team, or untagged. tag: x-request-tag, else the first ticket id
    (t and three digits) in the messages' text, else none."""
    team = req.headers.get("x-team") or "untagged"
    t, src = req.headers.get("x-request-tag"), "x-request-tag"
    if not t:
        m = TICKET_ID.search(messages_text(req.json or {}))
        t, src = (m.group(0), "the messages") if m else ("none", "nowhere")
    who = "x-team" if team != "untagged" else "no x-team header"
    return decision("tag", team, f"team from {who}; tag {t} from {src}", tag=t)


# ── 3. Budget ───────────────────────────────────────────────────────────────
def worst_case_tokens(req: Request) -> tuple[int, int]:
    """(input, output): about four characters a token over the canonical JSON, as the proxy
    estimates; output is max_tokens, or 0 when the request sets none."""
    body = req.json or {}
    mt = body.get("max_tokens")
    out = mt if isinstance(mt, int) and not isinstance(mt, bool) and mt > 0 else 0
    return len(canonical(body).decode()) // 4 + 1, out


def budget(req: Request, team: str, state: State, cfg, prices: dict) -> Decision:
    """Spend so far plus this request's worst case against the team's limit: allow, warn
    (over warn_usd) or refuse (over limit_usd, or no budget for the team)."""
    booked = cfg.untagged if team == "untagged" and cfg.untagged != "refuse" else team
    model = (req.json or {}).get("model")
    priced = cfg.routes[model].to if model in cfg.routes else model  # the target's price
    worst = ledger.worst_case_usd(priced, *worst_case_tokens(req), prices)
    spent = state.spend.get(booked, 0.0)
    b = cfg.budgets.get(booked)
    extra = {"team": booked, "worst_usd": worst, "spent_usd": spent, "limit_usd": b.limit_usd if b else None}
    if b is None:
        why = "untagged requests are refused" if booked == "untagged" else f"team {booked} has no budget"
        return decision("budget", "refuse", why, **extra)
    line = f"${spent:.6f} spent + ${worst:.6f} worst case"
    if spent + worst > b.limit_usd:
        return decision("budget", "refuse", f"{line} > ${b.limit_usd:.2f} limit", **extra)
    if b.warn_usd is not None and spent + worst > b.warn_usd:
        return decision("budget", "warn", f"{line} > ${b.warn_usd:.2f} warn line", **extra)
    return decision("budget", "allow", f"{line} within ${b.limit_usd:.2f}", **extra)


# ── 4. Cache ────────────────────────────────────────────────────────────────
def cache_lookup(req: Request, state: State, cfg) -> Decision:
    """hit (a fresh end_turn answer is stored), miss, or skip (not a Messages call, or it carries
    tools while skip_tools is on). The key hashes the path and the body bytes."""
    key = cache_key(req.path, req.body)
    if req.method != "POST" or req.path.split("?")[0] != "/v1/messages":
        return decision("cache", "skip", "only Messages calls are cached", key=key)
    if cfg.cache.skip_tools and (req.json or {}).get("tools"):
        return decision("cache", "skip", "the request carries tools", key=key)
    e = state.cache.get(key)
    if e is None:
        return decision("cache", "miss", "not stored", key=key)
    age = state.clock() - e.stored_at
    if age >= cfg.cache.ttl_s:
        del state.cache[key]
        return decision("cache", "miss", f"expired: stored {age:.0f}s ago, ttl {cfg.cache.ttl_s}s", key=key)
    if e.stop_reason != "end_turn":
        return decision("cache", "miss", f"stored answer stopped on {e.stop_reason}", key=key)
    return decision("cache", "hit", f"stored {age:.0f}s ago", key=key)


# ── 5. Route ────────────────────────────────────────────────────────────────
def route(req: Request, cfg) -> Decision:
    """The body's model through routes to a target and its fallbacks; '*': deny refuses the rest."""
    model = (req.json or {}).get("model")
    r = cfg.routes.get(model)
    if r:
        fb = f", then {', '.join(r.fallback)}" if r.fallback else ""
        return decision("route", "route", f"{model} -> {r.to}{fb}", to=r.to, fallback=list(r.fallback))
    if cfg.deny_unknown:
        return decision("route", "deny", f"{model} has no route and '*' is deny", to="", fallback=[])
    return decision("route", "route", f"{model} has no route; sent as itself", to=model, fallback=[])


# ── 6. Limit ────────────────────────────────────────────────────────────────
def limit(target: str, tokens: int, state: State, cfg) -> Decision:
    """A token bucket per target model, refilled continuously at rpm and tpm per minute. A request
    takes one request and its worst-case tokens: allow now, wait (the bucket is debited now and the
    caller sleeps wait_ms, at most max_wait_ms), or refuse."""
    m = cfg.models.get(target)
    if m is None:
        return decision("limit", "allow", f"no rate set for {target}", wait_ms=0)
    now = state.clock()
    b = state.buckets.get(target) or Bucket(float(m.tpm), float(m.rpm), now)
    elapsed = max(0.0, now - b.at)
    b.tokens = min(float(m.tpm), b.tokens + elapsed * m.tpm / 60)
    b.requests = min(float(m.rpm), b.requests + elapsed * m.rpm / 60)
    b.at = now
    state.buckets[target] = b
    if tokens > m.tpm:
        return decision("limit", "refuse", f"needs {tokens} tokens, more than tpm {m.tpm}", wait_ms=0)
    wait_s = max(0.0, (1 - b.requests) / (m.rpm / 60), (tokens - b.tokens) / (m.tpm / 60))
    wait_ms = math.ceil(round(wait_s * 1000, 6))
    if wait_ms > cfg.limit.max_wait_ms:
        return decision("limit", "refuse", f"needs a {wait_ms} ms wait, over max_wait_ms {cfg.limit.max_wait_ms}",
                        wait_ms=wait_ms)
    b.requests -= 1
    b.tokens -= tokens
    if wait_ms == 0:
        return decision("limit", "allow", f"within {m.rpm} rpm and {m.tpm} tpm", wait_ms=0)
    return decision("limit", "wait", f"waits {wait_ms} ms for the bucket to refill", wait_ms=wait_ms)


# ── 7. Retry and upstream ───────────────────────────────────────────────────
def retry_plan(cfg, attempt: int, rng: random.Random) -> int:
    """Backoff in ms before the next try after failed attempt number `attempt` (from 0):
    exponential, capped at max_ms, plus jitter in [0, base_ms)."""
    return min(cfg.retry.max_ms, cfg.retry.base_ms * 2 ** attempt) + rng.randrange(cfg.retry.base_ms)


def forward(req: Request, targets: list[str], state: State, cfg, send, sleep) -> tuple[Upstream | None, Decision, list]:
    """Each target in order, up to `attempts` tries each, backing off between tries on 429, 5xx or a
    dropped connection. ok, retried (answered by the route's target), fallback (by a later one), or
    fail. A 404 is not retried: from the replay proxy it means no recording, so it fails at once."""
    attempts: list[dict] = []
    for i, model in enumerate(targets):
        for n in range(cfg.retry.attempts):
            up = send(model, req)
            attempts.append({"model": model, "status": up.status or None, "error": up.error, "backoff_ms": 0})
            if up.error is None and up.status == 404:
                return up, decision("retry", "fail", f"{model} answered 404, not retried", to=model,
                                    attempts=len(attempts)), attempts
            if not (up.error or up.status == 429 or up.status >= 500):  # answered: no retry
                verdict = "ok" if len(attempts) == 1 else "retried" if i == 0 else "fallback"
                return up, decision("retry", verdict, f"{model} answered {up.status} on try {len(attempts)}",
                                    to=model, attempts=len(attempts)), attempts
            if n + 1 < cfg.retry.attempts:
                wait = max(retry_plan(cfg, n, state.rng), retry_after_ms(up.headers))
                attempts[-1]["backoff_ms"] = wait
                sleep(wait / 1000)
    what = ", ".join(str(a["status"] or a["error"]) for a in attempts)
    return None, decision("retry", "fail", f"{len(attempts)} tries over {', '.join(targets)} failed: {what}",
                          to="", attempts=len(attempts)), attempts


# ── 8. The chain ────────────────────────────────────────────────────────────
def run_chain(req: Request, state: State, cfg, send: Callable[[str, Request], Upstream],
              prices: dict | None = None, sleep: Callable[[float], None] = time.sleep) -> Outcome:
    """parse -> tag -> budget -> cache -> route -> limit -> retry/upstream -> usage and cost.
    send(model, req) is the upstream call; sleep(seconds) is injected so tests never wait."""
    t0 = state.clock()
    out = Outcome(500, dict(JSON_HEADERS), b"", [], "untagged", "none", None, None, 0.0, False, [])

    def finish(status: int, body: bytes, headers: dict | None = None) -> Outcome:
        out.status, out.body = status, body
        out.headers.update(headers or {})
        out.ms = (state.clock() - t0) * 1000
        return out

    stage = "parse"
    try:
        body = req.json
        if body is None or not isinstance(body.get("model"), str):
            why = "the body is not a JSON object" if body is None else "the body names no model"
            out.decisions.append(decision("parse", "refuse", why))
            return finish(400, error_body("invalid_request_error", why))

        stage = "tag"
        d = tag(req, cfg)
        out.decisions.append(d)
        out.team, out.tag = d["verdict"], d["tag"]

        stage = "budget"
        prices = ledger.resolve_prices(cfg) if prices is None else prices
        d = budget(req, out.team, state, cfg, prices)
        out.decisions.append(d)
        booked = d["team"]
        if d["verdict"] == "refuse":
            lim = f" of ${d['limit_usd']:.2f}" if d["limit_usd"] is not None else ""
            return finish(402, error_body("budget_exceeded", f"team {booked}: budget{lim} refused: {d['detail']}"))

        stage = "cache"
        d = cache_lookup(req, state, cfg)
        out.decisions.append(d)
        key, cache_verdict = d["key"], d["verdict"]
        if cache_verdict == "hit":
            e = state.cache[key]
            out.cached, out.usage, out.cost_usd = True, response_usage(e.headers, e.body), 0.0
            return finish(e.status, e.body, {**e.headers, "x-cache": "hit"})

        stage = "route"
        r = route(req, cfg)
        out.decisions.append(r)
        if r["verdict"] == "deny":
            return finish(404, error_body("not_found_error", r["detail"]))

        stage = "limit"
        d = limit(r["to"], sum(worst_case_tokens(req)), state, cfg)
        out.decisions.append(d)
        if d["verdict"] == "refuse":
            after = str(max(1, math.ceil(d["wait_ms"] / 1000)))
            return finish(429, error_body("rate_limit_error", d["detail"]), {"retry-after": after})
        if d["verdict"] == "wait":
            sleep(d["wait_ms"] / 1000)

        stage = "retry"
        up, d, out.attempts = forward(req, [r["to"], *r["fallback"]], state, cfg, send, sleep)
        out.decisions.append(d)
        if d["verdict"] == "fail":
            said = f": {up.body.decode('utf-8', 'replace')[:500]}" if up is not None else ""
            return finish(502, error_body("upstream_error", d["detail"] + said))

        stage = "ledger"
        out.usage = response_usage(up.headers, up.body)
        out.cost_usd = ledger.cost_of(d["to"], out.usage, prices) if out.usage else None
        if out.cost_usd is not None:
            state.spend[booked] = state.spend.get(booked, 0.0) + out.cost_usd
        if cache_verdict == "miss" and up.status == 200 and stop_reason(up.headers, up.body) == "end_turn":
            state.cache[key] = CacheEntry(200, dict(up.headers), up.body, "end_turn", state.clock())
        out.headers = {}
        return finish(up.status, up.body, {**up.headers, "x-cache": cache_verdict})
    except Exception as e:  # a stage that raises is a bug: answered 500 and named, never raised
        out.decisions.append(decision(stage, "error", type(e).__name__))
        out.headers = dict(JSON_HEADERS)
        return finish(500, error_body("api_error", f"gateway stage {stage} failed: {type(e).__name__}"))


# ── 9. Cases and command line ───────────────────────────────────────────────
CASE_CLOCK = 1000.0
CASE_REPLY = canonical({"id": "msg_case", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                        "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                        "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}})


def merge_config(base: dict, overrides: dict) -> dict:
    """Deep merge, overrides win: a case's config overrides over config.json."""
    out = dict(base)
    for k, v in overrides.items():
        out[k] = merge_config(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def scripted(script: list) -> Callable[[str, Request], Upstream]:
    """An upstream that answers from a script: 200 (the fixed end_turn reply), 429 (retry-after 0),
    another status (an error body), or "drop" (no answer)."""
    queue = list(script)

    def send(model: str, req: Request) -> Upstream:
        if not queue:
            return Upstream(error="the script has no answer left")
        s = queue.pop(0)
        if s == "drop":
            return Upstream(error="connection dropped")
        if s == 200:
            return Upstream(200, dict(JSON_HEADERS), CASE_REPLY)
        return Upstream(s, {**JSON_HEADERS, **({"retry-after": "0"} if s == 429 else {})},
                        error_body("rate_limit_error" if s == 429 else "api_error", f"scripted {s}"))
    return send


def case_state(req: Request, st: dict) -> State:
    """A case's state at CASE_CLOCK: spend per team; stored cache entries (key "self" is this case's
    own request), each with stop_reason, tools flag and age_s; bucket levels per model."""
    s = State(spend=dict(st.get("spend", {})), clock=lambda: CASE_CLOCK, rng=random.Random(0))
    for e in st.get("cache", []):
        key = cache_key(req.path, req.body) if e["key"] == "self" else e["key"]
        s.cache[key] = CacheEntry(200, dict(JSON_HEADERS), CASE_REPLY, e.get("stop_reason"),
                                  CASE_CLOCK - e.get("age_s", 0), bool(e.get("tools", False)))
    for model, b in st.get("bucket", {}).items():
        s.buckets[model] = Bucket(float(b["tokens"]), float(b["requests"]), CASE_CLOCK)
    return s


def run_case(case: dict, base: dict) -> dict:
    import config  # the config loader (pyyaml) stays off the request path
    cfg = config.from_dict(merge_config(base, case.get("config", {})))
    r = case["request"]
    req = make_request(r["method"], r["path"], r.get("headers", {}), canonical(r["body"]))
    out = run_chain(req, case_state(req, case.get("state", {})), cfg, scripted(case.get("upstream", [])),
                    sleep=lambda _s: None)
    return {"id": case["id"], "decisions": [{"stage": d["stage"], "verdict": d["verdict"]} for d in out.decisions],
            "status": out.status}


def run_cases(path: Path | str) -> list[dict]:
    return [run_case(c, json.loads(CONFIG_JSON.read_text())) for c in json.loads(Path(path).read_text())]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the gateway's stages over the shared fixture cases.")
    ap.add_argument("--cases", type=Path, required=True, help="a cases.json; prints one JSON line per case")
    for row in run_cases(ap.parse_args(argv).cases):
        print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
