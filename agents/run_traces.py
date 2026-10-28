"""
run_traces — the trace files agents/run.py writes: where each one lives, what it holds (the R2 records, the
run's output and the proxy's ledger), and how a replay is checked against a recorded trace.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from run_sets import APPROVE, ELICIT, MODEL, REPO

# ── 1. Traces ───────────────────────────────────────────────────────────────
def log_path(traces_dir: Path, set_name: str) -> Path:
    """The proxy's log for a set (git-ignored), so a failure's cause is kept even when stderr is not (R59)."""
    path = Path(traces_dir) / set_name / "proxy.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def trace_path(traces_dir: Path, set_name: str, engine_name: str, desk: str, ticket_id: str) -> Path:
    return Path(traces_dir) / set_name / f"{engine_name}-{desk.removeprefix('pattern:')}-{ticket_id}.json"


def trace_doc(*, set_name: str, engine_name: str, desk: str, ticket_id: str, mode: str, result: dict,
              spent: float, paid_calls: int, proxy_calls: list[dict] | None = None) -> dict:
    """R2 records plus output (the run's own result: a desk's reply, stop, calls, tool_calls...; a pattern's
    {result, calls}), errors, cost_usd, the proxy's spend and its ledger (proxy_calls, R62). In replay every
    record's ms is None: it is the engine's wall clock, the local proxy's in replay, and leaving it out keeps
    replayed traces byte-stable. The recorded upstream latency is in proxy_calls instead."""
    result = dict(result)
    trace = result.pop("trace")
    records = trace.to_list() if hasattr(trace, "to_list") else [dict(r) for r in trace]
    if mode == "replay":
        records = [{**r, "ms": None} for r in records]
    errors = result.pop("errors", [])
    cost = result.pop("cost_usd", None)
    return {"set": set_name, "engine": engine_name, "desk": desk, "ticket": ticket_id, "mode": mode, "model": MODEL,
            "approve": APPROVE, "elicit": ELICIT, "trace": records, "output": result, "errors": errors,
            "cost_usd": cost, "spent": round(spent, 6), "paid_calls": paid_calls,
            "proxy_calls": [dict(c) for c in proxy_calls or []]}


def comparable(doc: dict) -> dict:
    """A trace without what differs between a recording run and its replay: mode, the engine's latency, spend,
    and whether each ledger call was replayed. The ledger is compared as a multiset (sorted): calls a desk makes
    concurrently may be served in another order."""
    out = {k: v for k, v in doc.items() if k not in ("mode", "spent", "paid_calls")}
    out["trace"] = [{**r, "ms": None} for r in doc["trace"]]
    if "proxy_calls" in doc:
        out["proxy_calls"] = sorted(({**c, "replayed": None} for c in doc["proxy_calls"]),
                                    key=lambda c: json.dumps(c, sort_keys=True))
    return out


def dump(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def write_trace(path: Path, doc: dict) -> str:
    """Write the trace. A replay never replaces a record-mode trace (it holds the real spend): it checks the run
    matches it instead. A record-mode trace written before the ledger existed (R62) gets the replay's
    proxy_calls added when the rest matches. Returns what happened, for the run's line."""
    if doc["mode"] == "replay" and path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("mode") == "record":
            if "proxy_calls" not in old and comparable(old) == comparable({k: v for k, v in doc.items()
                                                                           if k != "proxy_calls"}):
                _write(path, {**old, "proxy_calls": doc["proxy_calls"]})
                return "matches the recorded trace (kept); added its proxy_calls"
            if comparable(old) == comparable(doc):
                return "matches the recorded trace (kept)"
            raise RuntimeError(f"the replayed run differs from the recorded trace {rel(path)}; kept the recording's")
    _write(path, doc)
    return f"wrote {rel(path)}"


def _write(path: Path, doc: dict) -> None:
    """Atomically: a temporary file, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump(doc), encoding="utf-8")
    os.replace(tmp, path)


def rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)
