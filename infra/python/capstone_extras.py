"""
capstone_extras: what replay.py needs beside the recorded traffic, kept apart so replay.py stays under
400 lines: the quiet replay proxy, the derived files' about text, the shared cases' decisions, one
request through the gateway against each injector script, the demo run whose tight budget gives a 402,
and the trace sample. Free and offline: the injector is a loopback fixture, the demo replays a set.
"""
from __future__ import annotations

import dataclasses
import json
import random
import sys
import threading
from pathlib import Path

import config
import gateway
import injector
import replay
import stages
import traces
from responses import canonical, stop_reason

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "infra" / "data" / "fixtures"
SCRIPTS = ("flaky", "dead", "ratelimited")
REQUEST = canonical({"model": "claude-haiku-5-5", "max_tokens": 64,
                     "messages": [{"role": "user", "content": "Say ok. t008"}]})
DEMO_LIMIT_USD = 0.01    # the demo run's patterns budget: some calls pass, then the set runs into it

CAPSTONE_ABOUT = {
    "traffic": "each agents trace's proxy_calls in order (desk-hand, desk-hand-go, desk-langgraph, desk-sdk, patterns, "
               "patterns-go; repeats kept), then the judge set's recordings in file order: 957 calls, one at a time, "
               "with x-team (desk, patterns, judge) and x-request-tag (the ticket id in the messages, or none)",
    "latency": "P6: p50_ms and p95_ms are nearest-rank percentiles over each call's recorded wall clock (upstream.ms in "
               "agents/data/recordings), joined by the driver; a trace's ms is the gateway's own clock (about a "
               "millisecond in replay) and is not used for them",
    "time": "replay time: the gateway's clock starts at 0 and moves by each call's recorded ms and any limiter wait, "
            "so the limiter and the cache ttl see the recorded pace; nothing sleeps",
    "cost": "cost_usd is the gateway's ledger: the recorded usage priced with the LLMs lab's table; a cache hit books "
            "$0; prompt_cache_* are the recorded prompt-cache tokens of the calls that reached the upstream",
    "counts": "retries: traces with more than one upstream attempt; refusals: status 400, 402, 404 or 429",
    "all": "the figures over every call; sets and teams split them",
    "cases": "infra/data/fixtures/cases.json through stages.run_cases: each case's decisions and status",
    "injector": "one request through the gateway against injector.make_server on each script in "
                "infra/data/fixtures/injector, retry.base_ms 1, rng Random(0): its status, outcome, decisions "
                "and attempts (model, status, backoff_ms)",
    "gate": "replay.py --gate: ledger (per desk set, the gateway's ledger plus what its cache hits were recorded "
            "at equals the agents traces' cost within 1e-9), budgets, stages, figures",
}
LEDGER_ABOUT = {
    "ledger": "ledger.summary over the capstone run's ledger rows (one per call; refused = status 400, 402, 404 or "
              "429; a cache hit books $0)",
    "agents": "per traced set, the sum of its agents traces' proxy_calls cost_usd (the agents track's ledger); the "
              "judge set has no traces",
}


def replay_proxy(store_dir: Path):
    """The agents replay proxy for one set (a read-only import), its per-request log line silenced: a
    replay miss still shows, as a 502 in the results and the trace."""
    if str(ROOT / "agents" / "proxy") not in sys.path:
        sys.path.insert(0, str(ROOT / "agents" / "proxy"))
    import proxy_server
    px = proxy_server.make_server(mode="replay", port=0, store_dir=store_dir)
    px.RequestHandlerClass = type("Quiet", (px.RequestHandlerClass,), {"_log": lambda self, text: None})
    return px


def recorded_stop(call: "replay.Call") -> str | None:
    """Why the recorded answer stopped (end_turn, tool_use, ...), read from the recording itself."""
    r = replay.recording(ROOT, call.set, call.key)["response"]
    body = ("".join(r["sse_events"]).encode() if "sse_events" in r else
            r["body_text"].encode() if "body_text" in r else json.dumps(r["body"]).encode())
    return stop_reason(dict(r.get("headers_allowlist") or {}), body)


def cases() -> list[dict]:
    """[{id, decisions, status}] for the shared cases, as stages.py --cases prints them."""
    return stages.run_cases(FIXTURES / "cases.json")


def one_through(cfg: config.Config, script: list, out_dir: Path, run_name: str) -> dict:
    """One request through a gateway whose upstream is the injector on `script`; its trace."""
    inj = injector.make_server(script, port=0)
    cfg = dataclasses.replace(cfg, upstream=inj.url, retry=config.RetryCfg(cfg.retry.attempts, 1, cfg.retry.max_ms))
    for name in (f"{run_name}.jsonl", f"{run_name}-ledger.jsonl", f"{run_name}-cache.json"):
        (out_dir / name).unlink(missing_ok=True)
    gw = gateway.make_server(cfg, 0, stages.State(rng=random.Random(0)), out_dir / f"{run_name}-ledger.jsonl",
                             out_dir, run_name, cache_path=out_dir / f"{run_name}-cache.json")
    for s in (inj, gw):
        threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        call = replay.Call("injector", "desk", 0, run_name, ("POST", "/v1/messages", REQUEST), "t008", 0, {}, None)
        replay.run(cfg, gw.url, out_dir / run_name, [call], ledger_path=gw.ledger.path, traces_path=gw.traces.path)
        return traces.read_run(gw.traces.path)[-1]
    finally:
        for s in (inj, gw):
            s.shutdown()
            s.server_close()


def injector_timelines(cfg: config.Config, out_dir: Path) -> tuple[dict, dict]:
    """{script: {script, status, outcome, decisions, attempts}} and the flaky request's full trace."""
    out, flaky = {}, None
    for name in SCRIPTS:
        script = injector.load_script(FIXTURES / "injector" / f"{name}.json")
        t = one_through(cfg, script, out_dir, f"injector-{name}")
        out[name] = {"script": script, "status": t["status"], "outcome": t["outcome"],
                     "decisions": [[d["stage"], d["verdict"]] for d in t["decisions"]],
                     "attempts": [{"model": a["model"], "status": a["status"], "backoff_ms": a["backoff_ms"]}
                                  for a in t["attempts"]]}
        flaky = t if name == "flaky" else flaky
    return out, flaky


def demo_refusal(cfg: config.Config, out_dir: Path) -> dict | None:
    """The first 402 of a short second run: the patterns set with its budget cut to DEMO_LIMIT_USD."""
    budgets = {**cfg.budgets, "patterns": config.Budget(DEMO_LIMIT_USD, None)}
    cap = replay.replay_sets(dataclasses.replace(cfg, budgets=budgets), out_dir / "demo",
                             replay.calls(ROOT, ("patterns",)), run_name="demo")
    return next((t for t in cap.traces if t["status"] == 402), None)


def capped(x, n: int = 200):
    """Any string over n characters cut to n (a trace carries no body; this only guards it)."""
    if isinstance(x, dict):
        return {k: capped(v, n) for k, v in x.items()}
    if isinstance(x, list):
        return [capped(v, n) for v in x]
    return x[:n] if isinstance(x, str) else x


def sample(cap: "replay.Capstone", flaky: dict | None, demo: dict | None) -> dict:
    """traces.sample.json: three whole traces the lessons quote, picked by a stable rule."""
    first_hit = next((t for t in cap.traces if t["outcome"] == "cached"), None)
    picks = (("cached", "capstone", first_hit), ("retried", "flaky", flaky), ("refused", "demo", demo))
    return {"about": {"pick": "cached: the capstone run's first cache hit in file order; retried: the flaky injector "
                              "request; refused: the first 402 of the demo run (the patterns set replayed alone with "
                              f"budgets.patterns.limit_usd {DEMO_LIMIT_USD})",
                      "volatile": "id, ts and ms (the gateway's own clock) change every run; --check ignores them",
                      "bodies": "a trace carries no request or response body; any string is capped at 200 characters"},
            "traces": [{"label": label, "run": run, "trace": capped(t)} for label, run, t in picks if t is not None]}
