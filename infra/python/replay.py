#!/usr/bin/env python3
"""
replay: the capstone. It sends the course's recorded traffic through the gateway, in the order the
agents track's runs made it (each trace's proxy_calls, then the judge set's recordings), checks the
gate and writes the three derived files the lessons quote: infra/data/derived/capstone.json,
ledger.json and traces.sample.json.

Run (from the repo root; free and offline, everything replays from agents/data/recordings):
    .venv-infra/bin/python infra/python/replay.py --start --gate     # proxy + gateway in-process, write
    .venv-infra/bin/python infra/python/replay.py --start --check    # fail if a derived file drifted
--start (the default) runs one agents replay proxy per set and one gateway on free ports in this process,
the gateway's upstream switched to the set's proxy; its ledger, cache and traces start empty under --out
(default infra/data/derived/run/, git-ignored). --gateway URL drives a gateway started by hand: see run().
"""
from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import random
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import config
import gateway
import ledger
import traces
from responses import canonical, messages_text
from stages import TICKET_ID, State

# ── 1. Calls ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "agents"
DERIVED = ROOT / "infra" / "data" / "derived"
RUN_DIR = DERIVED / "run"
TRACED_SETS = ("desk-hand", "desk-hand-go", "desk-langgraph", "desk-sdk", "patterns", "patterns-go")
SETS = (*TRACED_SETS, "judge")
DESK_SETS = TRACED_SETS[:4]
STAGES = ("tag", "budget", "cache", "route", "limit", "retry", "ledger", "trace")


@dataclass(frozen=True)
class Call:
    set: str
    team: str
    order: int
    key: str                       # the recording's key (agents/data/recordings/<set>/<key>.json)
    request: tuple[str, str, bytes]  # method, path, body bytes (canonical JSON of the recorded body)
    tag: str                       # the ticket id in the messages' text, or none
    recorded_ms: int               # the recorded call's wall clock (upstream.ms)
    recorded_usage: dict
    cost_recorded: float | None    # the trace's proxy_calls cost_usd; for judge, the recording priced the same way


def team_of(set_name: str) -> str:
    return "desk" if set_name.startswith("desk-") else "patterns" if set_name.startswith("patterns") else set_name


def trace_calls(root: Path, set_name: str) -> list[dict]:
    """Every proxy_calls entry of a set's agents traces, trace files sorted, calls in run order."""
    return [c for f in sorted((root / "agents" / "data" / "traces" / set_name).glob("*.json"))
            for c in json.loads(f.read_text(encoding="utf-8"))["proxy_calls"]]


def recording(root: Path, set_name: str, key: str) -> dict:
    return json.loads((root / "agents" / "data" / "recordings" / set_name / f"{key}.json").read_text(encoding="utf-8"))


def calls(root: Path = ROOT, sets: tuple[str, ...] = SETS) -> list[Call]:
    """The capstone's traffic: per set, each agents trace's proxy_calls in order (repeats kept), the
    judge set (no traces) from its recordings in file order. 957 calls for all seven sets."""
    out: list[Call] = []
    prices = ledger.lab_prices()
    for s in sets:
        entries = ([(c["key"], c["cost_usd"]) for c in trace_calls(root, s)] if s in TRACED_SETS else
                   [(p.stem, None) for p in sorted((root / "agents" / "data" / "recordings" / s).glob("*.json"))])
        for key, cost in entries:
            rec = recording(root, s, key)
            rs, up = rec["request_summary"], rec["upstream"]
            usage = {k: up[k] for k in ledger.USAGE_FIELDS}
            m = TICKET_ID.search(messages_text(rs["body"]))
            out.append(Call(s, team_of(s), len(out), key, (rs["method"], rs["path"], canonical(rs["body"])),
                            m.group(0) if m else "none", up["ms"], usage,
                            cost if cost is not None else ledger.cost_of(up["model"], usage, prices)))
    return out


# ── 2. Run ──────────────────────────────────────────────────────────────────
@dataclass
class ReplayClock:
    """Replay time for the gateway's State: it starts at 0 and moves by each call's recorded wall clock
    and by any limiter wait (sleep advances it; nothing really sleeps), so the limiter and the cache's
    ttl see the pace the recorded runs had, one call after another."""
    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Result:
    call: Call
    status: int
    cache: str              # the answer's x-cache: hit, miss or skip
    trace_id: str | None    # the answer's x-gateway-trace


@dataclass
class Capstone:
    cfg: config.Config
    results: list[Result]
    traces: list[dict]      # the run's trace file, in order
    rows: list[dict]        # the run's ledger rows, in order (one per call)


def run(cfg_path: Path, gateway_url: str, out_dir: Path, calls: list[Call], *, before=None,
        ledger_path: Path | None = None, traces_path: Path | None = None) -> Capstone:
    """Send each call to the gateway with x-team and x-request-tag; collect status, x-cache and the trace
    id. `before(call)` runs before each send (--start switches the upstream and moves the clock there).
    Without the stores' paths (a gateway started by hand) the ledger rows are those its default ledger
    gained during the run, and the traces are found by id under the config's traces dir. That gateway's
    agents proxy serves one set, so only that set hits (the rest answer 502), and its limiter runs on the
    wall clock, so it refuses (429) much of a fast replay: --start runs on replay time instead."""
    cfg = config.load(cfg_path) if not isinstance(cfg_path, config.Config) else cfg_path
    lpath = Path(ledger_path) if ledger_path else gateway.LEDGER_PATH
    skip = 0 if ledger_path else len(ledger.read_jsonl(lpath))
    u = urlsplit(gateway_url)
    results = []
    for c in calls:
        if before:
            before(c)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=600)
        try:
            conn.request(*c.request, headers={"content-type": "application/json", "x-team": c.team,
                                              "x-request-tag": c.tag})
            r = conn.getresponse()
            r.read()
        finally:
            conn.close()
        results.append(Result(c, r.status, r.getheader("x-cache") or "", r.getheader("x-gateway-trace")))
    rows = ledger.read_jsonl(lpath)[skip:]
    if traces_path:
        found = traces.read_run(traces_path)
    else:
        ids = {r.trace_id for r in results}
        tdir = cfg.traces_dir if cfg.traces_dir.is_absolute() else ROOT / cfg.traces_dir
        found = [t for p in sorted(tdir.glob("*.jsonl")) for t in traces.read_run(p) if t["id"] in ids]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.jsonl").write_text("".join(json.dumps({"set": r.call.set, "key": r.call.key, "status": r.status,
        "cache": r.cache, "trace": r.trace_id, "recorded_ms": r.call.recorded_ms}) + "\n" for r in results))
    return Capstone(cfg, results, found, rows)


def serve(server):
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server


@contextlib.contextmanager
def started(cfg: config.Config, out_dir: Path, sets, run_name: str = "capstone"):
    """One replay proxy per set and one gateway on replay time, stores fresh under out_dir. Yields the
    gateway and the `before` hook that points it at a call's set and moves the clock."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"ledger": out_dir / "ledger.jsonl", "cache": out_dir / "cache.json", "traces": out_dir / f"{run_name}.jsonl"}
    for p in paths.values():
        p.unlink(missing_ok=True)
    from capstone_extras import replay_proxy
    clock, servers = ReplayClock(), []
    try:
        for s in sets:
            servers.append(serve(replay_proxy(AGENTS / "data" / "recordings" / s)))
        gw = serve(gateway.make_server(cfg, 0, State(clock=clock, rng=random.Random(0)), paths["ledger"], out_dir,
                                       run_name, cache_path=paths["cache"], sleep=clock.sleep))
        servers.append(gw)
        sends = {s: gateway.make_send(f"http://127.0.0.1:{px.server_address[1]}") for s, px in zip(sets, servers)}
        last: list[Call] = []

        def before(c: Call) -> None:
            if last:
                clock.sleep(last[0].recorded_ms / 1000)
            gw.send = sends[c.set]
            last[:] = [c]
        yield gw, before
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()


def replay_sets(cfg: config.Config, out_dir: Path, calls: list[Call], run_name: str = "capstone") -> Capstone:
    """The --start path: proxies and gateway in-process, every call through, then both stopped."""
    with started(cfg, out_dir, sorted({c.set for c in calls}), run_name) as (gw, before):
        return run(cfg, gw.url, out_dir, calls, before=before, ledger_path=gw.ledger.path,
                   traces_path=gw.traces.path)


# ── 3. Gate ─────────────────────────────────────────────────────────────────
def gate(cap: Capstone, agents_root: Path = ROOT) -> list[tuple[str, bool, str]]:
    """The four conditions, each (name, ok, detail with its figures)."""
    lines = []
    joined = len(cap.rows) == len(cap.results)
    parts, ok1 = [], joined
    for s in [s for s in DESK_SETS if any(r.call.set == s for r in cap.results)]:
        pairs = [(r, row) for r, row in zip(cap.results, cap.rows) if r.call.set == s]
        gw = sum(row["cost_usd"] or 0.0 for _, row in pairs)
        hits = [r for r, _ in pairs if r.cache == "hit"]
        saved = sum(r.call.cost_recorded or 0.0 for r in hits)
        agents = sum(c["cost_usd"] or 0.0 for c in trace_calls(agents_root, s))
        ok = abs(gw + saved - agents) <= 1e-9
        ok1 = ok1 and ok
        cached = f" + ${saved:.7f} for {len(hits)} cache hit{'s' * (len(hits) != 1)}" if hits else ""
        parts.append(f"{s} ${gw:.7f}{cached} {'=' if ok else '!='} agents ${agents:.7f}")
    lines.append(("ledger", ok1, ("; ".join(parts) or "no desk set in this run")
                  + ("" if joined else f"; {len(cap.rows)} ledger rows for {len(cap.results)} calls")))

    spend: dict[str, float] = {}
    for row in cap.rows:
        spend[row["team"]] = spend.get(row["team"], 0.0) + (row["cost_usd"] or 0.0)
    over = [t for t, v in spend.items() if t not in cap.cfg.budgets or v > cap.cfg.budgets[t].limit_usd]
    ids = {t["id"] for t in cap.traces}
    refusals = [r for r in cap.results if r.status in ledger.REFUSED_STATUSES]
    untraced = [r for r in refusals if r.trace_id not in ids]
    teams = ", ".join(f"{t} ${v:.4f} of ${cap.cfg.budgets[t].limit_usd:.2f}" if t in cap.cfg.budgets
                      else f"{t} ${v:.4f} with no budget" for t, v in sorted(spend.items()))
    lines.append(("budgets", not over and not untraced,
                  f"{teams}; {len(refusals)} refusals, {len(refusals) - len(untraced)} traced"
                  + (f"; over the limit: {', '.join(over)}" if over else "")))

    expected = {"ok": list(STAGES), "cached": ["tag", "budget", "cache", "ledger", "trace"]}
    bad, counts = [], {"ok": 0, "cached": 0, "stopped": 0}
    for t in cap.traces:                  # a refusal stops at the stage that refused (parse alone, or a prefix)
        names, kind = [d["stage"] for d in t["decisions"]], t["outcome"] if t["outcome"] in expected else "stopped"
        good = names == expected[kind] if kind in expected else names in (["parse"], list(STAGES[:len(names)]))
        counts[kind] += good
        bad += [] if good else [t["id"]]
    parents = [t["parent_id"] for t in cap.traces if t.get("parent_id")]
    orphans = [p for p in parents if p not in ids]
    lines.append(("stages", not bad and not orphans and len(cap.traces) == len(cap.results),
                  f"{len(cap.traces)} traces: {counts['ok']} answered with all eight stages, "
                  f"{counts['cached']} cache hits stop after cache, {counts['stopped']} refusals stop at the "
                  f"refusing stage, {len(bad)} out of order; {len(parents)} parent ids named, {len(orphans)} missing"))

    figs = figures(cap.results, cap)
    hits = sum(t["outcome"] == "cached" for t in cap.traces)
    rate = round(hits / len(cap.traces), 4) if cap.traces else 0.0
    p95 = percentile([recording(agents_root, r.call.set, r.call.key)["upstream"]["ms"] for r in cap.results], 0.95)
    ok4 = rate == figs["hit_rate"] and p95 == figs["p95_ms"]
    lines.append(("figures", ok4, f"hit rate {figs['hit_rate']} ({figs['cache_hits']} of {figs['calls']}) and "
                  f"p95 {figs['p95_ms']} ms; from the traces and the recordings: {rate} and {p95} ms"))
    return lines


# ── 4. Derived files ────────────────────────────────────────────────────────
FIGURES = ("calls", "cache_hits", "hit_rate", "retries", "refusals", "cost_usd", "p50_ms", "p95_ms",
           "prompt_cache_read_tokens", "prompt_cache_write_tokens")
VOLATILE = ("id", "ts", "ms")   # a sample trace's random id and the gateway's own clock: not compared


def percentile(values: list[int], p: float) -> int:
    """Nearest rank: the smallest recorded value with at least p of the calls at or below it."""
    v = sorted(values)
    return v[max(0, -(-int(p * 1000) * len(v) // 1000) - 1)] if v else 0


def figures(results: list[Result], cap: Capstone) -> dict:
    by_id = {t["id"]: t for t in cap.traces}
    row_of = dict(zip((id(r) for r in cap.results), cap.rows))
    rows = [row_of.get(id(r)) or {} for r in results]
    paid = [row for row in rows if not row.get("cached")]
    hits = sum(r.cache == "hit" for r in results)
    ms = [r.call.recorded_ms for r in results]
    return {"calls": len(results), "cache_hits": hits, "hit_rate": round(hits / len(results), 4) if results else 0.0,
            "retries": sum(len((by_id.get(r.trace_id) or {}).get("attempts") or []) > 1 for r in results),
            "refusals": sum(r.status in ledger.REFUSED_STATUSES for r in results),
            "cost_usd": round(sum(row.get("cost_usd") or 0.0 for row in rows), 10),
            "p50_ms": percentile(ms, 0.5), "p95_ms": percentile(ms, 0.95),
            "prompt_cache_read_tokens": sum(row.get("cache_read_input_tokens") or 0 for row in paid),
            "prompt_cache_write_tokens": sum(row.get("cache_creation_input_tokens") or 0 for row in paid)}


def capstone_json(cap: Capstone, lines, cases: list, injector: dict) -> dict:
    from capstone_extras import CAPSTONE_ABOUT
    groups: dict[str, dict[str, list[Result]]] = {"sets": {}, "teams": {}}
    for r in cap.results:
        groups["sets"].setdefault(r.call.set, []).append(r)
        groups["teams"].setdefault(r.call.team, []).append(r)
    return {"about": CAPSTONE_ABOUT, "config": config.to_json(cap.cfg), "calls": len(cap.results),
            "all": figures(cap.results, cap),
            "sets": {k: figures(v, cap) for k, v in groups["sets"].items()},
            "teams": {k: figures(v, cap) for k, v in groups["teams"].items()},
            "cases": cases, "injector": injector,
            "gate": {"ok": all(ok for _, ok, _ in lines), "lines": [list(x) for x in lines]}}


def ledger_json(cap: Capstone, root: Path = ROOT) -> dict:
    from capstone_extras import LEDGER_ABOUT
    sets = [s for s in TRACED_SETS if any(r.call.set == s for r in cap.results)]
    return {"about": LEDGER_ABOUT, **ledger.summary(cap.rows),
            "agents": {s: round(sum(c["cost_usd"] or 0.0 for c in trace_calls(root, s)), 10) for s in sets}}


def write_files(files: dict[str, dict], d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for name, obj in files.items():
        (d / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def comparable(name: str, obj):
    if name != "traces.sample.json" or not isinstance(obj, dict):
        return obj
    return {**obj, "traces": [{**x, "trace": {k: v for k, v in x["trace"].items() if k not in VOLATILE}}
                              for x in obj.get("traces", [])]}


def drift(files: dict[str, dict], d: Path) -> list[str]:
    """The names of the files under d that differ from these (a sample trace's id, ts and ms aside)."""
    def old(name: str):
        try:
            return json.loads((d / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return [n for n, obj in files.items() if old(n) is None or comparable(n, old(n)) != comparable(n, json.loads(json.dumps(obj)))]


def produce(cfg_path: Path, out_dir: Path, sets: tuple[str, ...] = SETS) -> tuple[Capstone, dict[str, dict]]:
    """The capstone run, the gate, the cases, the injector timelines and the demo refusal: the three files."""
    import capstone_extras as extras
    cfg = config.load(cfg_path)
    cap = replay_sets(cfg, out_dir, calls(ROOT, sets))
    timelines, flaky = extras.injector_timelines(cfg, out_dir)
    return cap, {"capstone.json": capstone_json(cap, gate(cap), extras.cases(), timelines),
                 "ledger.json": ledger_json(cap),
                 "traces.sample.json": extras.sample(cap, flaky, extras.demo_refusal(cfg, out_dir))}


# ── 5. Command line ─────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Replay the course's recorded traffic through the gateway; the gate.")
    p.add_argument("--config", type=Path, default=config.GATEWAY_YAML)
    how = p.add_mutually_exclusive_group()
    how.add_argument("--gateway", metavar="URL", help="a gateway started by hand (the proxy already running)")
    how.add_argument("--start", action="store_true", help="start the proxies and the gateway in-process (the default)")
    p.add_argument("--sets", help=f"comma-separated, from {','.join(SETS)} (default: all)")
    p.add_argument("--gate", action="store_true", help="print the gate's lines")
    p.add_argument("--check", action="store_true", help="fail if the derived files drifted (writes nothing there)")
    p.add_argument("--out", type=Path, default=RUN_DIR, help="the run's ledger, cache and traces")
    a = p.parse_args(argv)
    sets = tuple(a.sets.split(",")) if a.sets else SETS
    if any(s not in SETS for s in sets):
        p.error(f"--sets takes {', '.join(SETS)}")
    try:
        cfg = config.load(a.config)
    except (config.ConfigError, OSError) as e:
        print(f"replay: config error: {e}. Nothing was sent.")
        return 2
    full = not a.gateway and sets == SETS and Path(a.config).resolve() == config.GATEWAY_YAML.resolve()
    if a.check and not full:
        print("replay: --check needs --start over every set with the course's config")
        return 2
    try:
        if a.gateway:
            cap = run(cfg, a.gateway, a.out, calls(ROOT, sets))
            lines = gate(cap)
            files = {"capstone.json": capstone_json(cap, lines, [], {}), "ledger.json": ledger_json(cap)}
        else:
            cap, files = produce(a.config, a.out, sets)
            lines = files["capstone.json"]["gate"]["lines"]
    except (OSError, ValueError) as e:
        print(f"replay: could not start or reach the gateway or the proxy: {e}")
        return 2
    figs = files["capstone.json"]["all"]
    print(f"replayed {figs['calls']} calls: {figs['cache_hits']} cache hits, {figs['refusals']} refusals, "
          f"${figs['cost_usd']:.6f}")
    if a.gate:
        for name, ok, detail in lines:
            print(f"gate {name}: {'ok' if ok else 'FAILED'}: {detail}")
    if a.check:
        stale = drift(files, DERIVED)
        rel = DERIVED.relative_to(ROOT) if DERIVED.is_relative_to(ROOT) else DERIVED
        print(f"{rel}: up to date" if not stale else f"drifted: {', '.join(f'{rel}/{n}' for n in stale)}; rerun "
              "without --check")
        return 1 if stale else 0
    target = DERIVED if full else a.out
    write_files(files, target)
    shown = target.relative_to(ROOT) if target.is_relative_to(ROOT) else target
    print(f"wrote {', '.join(files)} to {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
