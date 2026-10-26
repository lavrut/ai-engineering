#!/usr/bin/env python3
"""
bench — the same requests against the FastAPI server and the Go server, one machine, one client.

Both servers load the same production model from ml/registry.json and must give the same
answers (checked here: same label and low_confidence, every score within 1e-9). Then each gets
the same fixed request set: the valid texts from predictions.json, cycled to REQUESTS requests,
sent one at a time over one keep-alive connection. The servers alternate request by request so a
busy moment on the machine hits both alike.

Only the ratio is meaningful: the absolute milliseconds belong to this machine, this load and
this client (Python's http.client is part of every measurement, equally for both servers).

Run (from ml/python; needs Go and the Python requirements, no network beyond localhost):
    python bench.py                   # print the numbers
    python bench.py --write-reports   # also write ml/reports/latency.json
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

ML_DIR = Path(__file__).resolve().parent.parent

# ── 1. Configuration ────────────────────────────────────────────────────────
REQUESTS = 500          # timed requests per server
WARMUP = 20             # untimed requests per server first (imports, caches, JIT-free but lazy paths)
MAX_CHARS = 20_000      # the servers reject longer texts, so the request set leaves them out
STARTUP_TIMEOUT = 60.0  # seconds to wait for /health
PATHS = {"fixtures": ML_DIR / "data" / "fixtures" / "predictions.json",
         "serve": ML_DIR / "serve", "go": ML_DIR / "go", "reports": ML_DIR / "reports"}


def request_texts() -> list[str]:
    """The valid predictions.json inputs (the servers reject empty and > 20,000 characters)."""
    cases = json.loads(PATHS["fixtures"].read_text(encoding="utf-8"))["cases"]
    return [c["input"] for c in cases if 1 <= len(c["input"]) <= MAX_CHARS]


# ── 2. Starting and stopping the servers ────────────────────────────────────
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(cmd: list[str], port: int, stack: ExitStack, cwd: Path) -> subprocess.Popen:
    """Start a server and register its shutdown, so it stops even if the benchmark fails."""
    env = {**os.environ, "ML_ROOT": str(ML_DIR)}
    log = stack.enter_context(tempfile.TemporaryFile())   # a file, not a pipe: it never fills up
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=log)
    stack.callback(stop, proc)
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.seek(0)
            raise RuntimeError(f"{cmd[0]} exited: {log.read().decode(errors='replace')}")
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/health")
            if conn.getresponse().status == 200:
                conn.close()
                return proc
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"{cmd} did not answer /health within {STARTUP_TIMEOUT:.0f}s")


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def python_server(port: int) -> list[str]:
    return [sys.executable, "-m", "uvicorn", "server:app", "--app-dir", str(PATHS["serve"]),
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning", "--no-access-log"]


def go_server(port: int, build_dir: str) -> list[str]:
    """Build once, then run the binary itself (`go run` would leave a child process behind)."""
    binary = str(Path(build_dir) / "ml-serve")
    subprocess.run(["go", "build", "-o", binary, "."], cwd=PATHS["go"], check=True)
    return [binary, "serve", "-port", str(port)]


# ── 3. Measuring ────────────────────────────────────────────────────────────
def post(conn: http.client.HTTPConnection, body: bytes) -> tuple[float, dict]:
    """One request; returns (milliseconds, parsed reply)."""
    t0 = time.perf_counter()
    conn.request("POST", "/predict", body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = resp.read()
    ms = (time.perf_counter() - t0) * 1000
    if resp.status != 200:
        raise RuntimeError(f"status {resp.status}: {data[:200]!r}")
    return ms, json.loads(data)


def same_answer(a: dict, b: dict, tol: float = 1e-9) -> bool:
    return (a["label"] == b["label"] and a["low_confidence"] == b["low_confidence"]
            and a["model_version"] == b["model_version"] and a["scores"].keys() == b["scores"].keys()
            and all(abs(a["scores"][k] - b["scores"][k]) <= tol for k in a["scores"]))


def summarize(ms: list[float]) -> dict:
    """p50 / p95 by linear interpolation between order statistics (numpy's default)."""
    xs = sorted(ms)

    def pct(p: float) -> float:
        pos = (len(xs) - 1) * p / 100
        lo = int(pos)
        hi = min(lo + 1, len(xs) - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
    return {"n": len(xs), "p50_ms": pct(50), "p95_ms": pct(95), "mean_ms": statistics.fmean(xs),
            "min_ms": xs[0], "max_ms": xs[-1]}


def run(texts: list[str], ports: dict[str, int]) -> dict:
    bodies = [json.dumps({"text": t}, ensure_ascii=False).encode("utf-8") for t in texts]
    conns = {name: http.client.HTTPConnection("127.0.0.1", port, timeout=30) for name, port in ports.items()}
    try:
        for body in bodies:                          # parity: both servers, every text
            answers = [post(c, body)[1] for c in conns.values()]
            if not same_answer(*answers):
                raise RuntimeError(f"servers disagree on {body[:80]!r}: {answers}")
        for i in range(WARMUP):
            for c in conns.values():
                post(c, bodies[i % len(bodies)])
        times = {name: [] for name in conns}
        for i in range(REQUESTS):                     # alternate request by request
            for name, c in conns.items():
                times[name].append(post(c, bodies[i % len(bodies)])[0])
    finally:
        for c in conns.values():
            c.close()
    return {name: summarize(ms) for name, ms in times.items()}


# ── 4. Report ───────────────────────────────────────────────────────────────
def _version(module: str) -> str:
    from importlib.metadata import version
    return version(module)


def machine() -> dict:
    cpu = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                             capture_output=True, text=True).stdout.strip() or cpu
    go = subprocess.run(["go", "version"], capture_output=True, text=True).stdout.strip()
    return {"cpu": cpu, "cores": os.cpu_count(), "os": platform.platform(),
            "python": platform.python_version(), "go": go,
            "fastapi": _version("fastapi"), "uvicorn": _version("uvicorn")}


def build_report(results: dict, texts: list[str], generated_by: str) -> dict:
    py, go = results["python"], results["go"]
    return {
        "generated_by": generated_by,
        "note": "Numbers from ONE machine (see `machine`), one client, sequential requests over "
                "localhost. Absolute milliseconds will differ elsewhere; only the ratio between "
                "the two servers is meaningful.",
        "request_set": {"source": "ml/data/fixtures/predictions.json, the valid inputs (1 to "
                                  "20,000 characters), cycled", "distinct_texts": len(texts),
                        "requests_per_server": REQUESTS, "warmup_per_server": WARMUP,
                        "order": "alternating python, go, request by request; one keep-alive "
                                 "connection per server"},
        "parity": "both servers gave the same label, low_confidence and scores (within 1e-9) "
                  "for every distinct text before timing",
        "machine": machine(),
        "servers": {
            "python": {"command": "uvicorn server:app (FastAPI), 1 worker, access log off", **py},
            "go": {"command": "ml-serve serve (go build; net/http)", **go}},
        "ratio_python_over_go": {"p50": py["p50_ms"] / go["p50_ms"], "p95": py["p95_ms"] / go["p95_ms"],
                                 "mean": py["mean_ms"] / go["mean_ms"]},
    }


# ── 5. Entry point ──────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="Latency of the FastAPI and Go servers, same requests.")
    ap.add_argument("--write-reports", action="store_true", help="also write ml/reports/latency.json")
    args = ap.parse_args(argv)
    texts = request_texts()
    ports = {"python": free_port(), "go": free_port()}
    with tempfile.TemporaryDirectory() as build_dir, ExitStack() as stack:   # servers stop first
        start(python_server(ports["python"]), ports["python"], stack, PATHS["serve"])
        start(go_server(ports["go"], build_dir), ports["go"], stack, PATHS["go"])
        results = run(texts, ports)
    report = build_report(results, texts, " ".join(["python bench.py", *argv]))
    print(f"{'server':<8}{'n':>5}{'p50 ms':>9}{'p95 ms':>9}{'mean ms':>9}")
    for name, r in results.items():
        print(f"{name:<8}{r['n']:>5}{r['p50_ms']:>9.3f}{r['p95_ms']:>9.3f}{r['mean_ms']:>9.3f}")
    ratio = report["ratio_python_over_go"]
    print(f"python / go: p50 {ratio['p50']:.1f}x, p95 {ratio['p95']:.1f}x (one machine; only the ratio means anything)")
    if args.write_reports:
        path = PATHS["reports"] / "latency.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
