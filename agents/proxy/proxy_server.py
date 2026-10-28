"""
proxy_server — the record/replay proxy's server (proxy.py): what one proxy run holds (the cap's accounting,
the ledger, the open connections), make_server (the record-mode gate) and serve. Each request is a
proxy_handler.Handler.
"""
from __future__ import annotations

import os
import re
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx

from proxy import DEFAULT_MAX_COST, DEFAULT_PORT, HOST, MAX_CALLS, RECORD_COMMAND, STORE_DIR, UPSTREAM, Store
from proxy_cost import NotConfirmed, confirm, estimate_line
from proxy_handler import Handler

# ── 1. The server ───────────────────────────────────────────────────────────
class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, *, mode: str, store: Store, set_name: str | None, max_cost: float,
                 max_calls: int, api_key: str | None, client: httpx.Client | None, record_command: str | None = None,
                 log_file: str | Path | None = None, refresh: bool = False):
        super().__init__((HOST, port), Handler)
        self.mode, self.store, self.set_name = mode, store, set_name
        self.max_cost, self.max_calls = max_cost, max_calls
        self._api_key = api_key           # held in memory only; never printed, never stored
        self.client = client
        self.lock = threading.Lock()
        self.log_lock = threading.Lock()   # proxy.log appends only: disk I/O never holds up the accounting lock
        self.key_locks: dict[str, threading.Lock] = {}
        self.spent = self.reserved = 0.0
        self.paid_calls = 0
        self.misses = 0                    # replay misses this run answered (a caller may have absorbed one, R59)
        self.cap_hit = False
        self._record_command = record_command
        self.log_file = Path(log_file) if log_file else None
        self.closed = False
        self.connections: dict[socket.socket, "Handler"] = {}   # open client connections, dropped on close (R59)
        self.calls: list[dict] = []        # R62: the ledger, one row per served /v1/messages call, in service order
        self.refresh = refresh             # R62: record mode goes upstream even on a store hit (re-record)
        # R65: a refreshed run's new recordings, held until the caller commits them (its run finished ok) or
        # discards them (it failed), so a failed refresh never leaves the store half-overwritten
        self.staged: dict[str, tuple[dict, dict, dict | None]] = {}

    def server_close(self) -> None:
        """Stop listening AND drop every idle open connection; a connection mid-request answers, then closes. A
        client that pools connections (langchain-anthropic keeps one httpx client per base URL) would otherwise send
        its next request, meant for the next proxy run on this port, down a kept-alive connection to this one,
        whose upstream client is closed (R59). A busy handler's reply is only guaranteed if the caller leaves
        `client` open until it finishes: run.py waits (bounded, wait_idle) before closing it; serve() does not wait."""
        self.closed = True
        with self.lock:
            conns = [c for c, h in self.connections.items() if not h.busy]
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        super().server_close()

    def commit_staged(self) -> int:
        """Write a refreshed run's staged recordings over the store (R65). Returns how many."""
        with self.lock:
            staged, self.staged = self.staged, {}
        for key, (summary, response, upstream) in staged.items():
            self.store.add(key, summary, response, upstream)
        return len(staged)

    def discard_staged(self) -> int:
        """Drop a failed refreshed run's staged recordings (R65); the store keeps the old ones. Returns how many."""
        with self.lock:
            n, self.staged = len(self.staged), {}
        return n

    def record_command(self) -> str:
        """What a miss tells the user to run. run.py names its own command (one proxy run per ticket)."""
        if self._record_command:
            return self._record_command
        return RECORD_COMMAND + (f" --set {self.set_name}" if self.set_name else "")

    def key_lock(self, key: str) -> threading.Lock:
        """One lock per key, so two identical concurrent misses pay once: the second waits,
        then finds the first one's recording."""
        with self.lock:
            return self.key_locks.setdefault(key, threading.Lock())


def make_server(mode: str = "replay", port: int = DEFAULT_PORT, store_dir: str | Path = STORE_DIR,
                set_name: str | None = None, max_cost: float = DEFAULT_MAX_COST, max_calls: int = MAX_CALLS,
                transport: httpx.BaseTransport | None = None, yes: bool = False,
                record_command: str | None = None, estimate: bool = True,
                log_file: str | Path | None = None, refresh: bool = False) -> ProxyServer:
    """Build (not start) a proxy bound to 127.0.0.1. Record mode reads the key from the environment,
    prints the estimate and asks y/N here, before anything listens, so no caller can start a paying
    server unapproved (ruling R13); `yes` is the only bypass. `transport` is for tests only.
    `record_command` replaces the command a replay miss names (run.py passes its own). `estimate=False` skips the
    printed per-run estimate (run.py prints one for the whole set); the y/N gate still applies. `log_file` also
    appends every log line there (run.py keeps one per set). `refresh` (record mode only, R62) sends even a
    request already recorded upstream; its new recording is staged (served again within the run) and written over
    the store only by commit_staged(), when the caller's run finished ok (R65)."""
    if mode not in ("replay", "record"):
        raise ValueError(f"unknown mode {mode!r}")
    if refresh and mode != "record":
        raise ValueError("refresh re-records, so it needs record mode")
    if set_name is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", set_name):
        raise ValueError("--set takes letters, digits, '-' and '_' only")
    store = Store(Path(store_dir) / set_name if set_name else store_dir)
    api_key = client = None
    if mode == "record":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("record mode needs ANTHROPIC_API_KEY in the environment")
        if estimate:
            estimate_line(max_cost=max_cost, calls=max_calls)
        if not confirm(yes):
            raise NotConfirmed()
        client = httpx.Client(base_url=UPSTREAM, transport=transport,
                              timeout=httpx.Timeout(600.0, connect=10.0))
    return ProxyServer(port, mode=mode, store=store, set_name=set_name, max_cost=max_cost,
                       max_calls=max_calls, api_key=api_key, client=client, record_command=record_command,
                       log_file=log_file, refresh=refresh)


def serve(server: ProxyServer) -> int:
    """Serve until stopped. Returns 3 if the cost cap stopped it, else 0."""
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if server.client is not None:
            server.client.close()
    return 3 if server.cap_hit else 0
