#!/usr/bin/env python3
"""
config: load and validate the gateway config (infra/gateway.yaml).

Run (from the repo root):
    python3 -m venv .venv-infra && .venv-infra/bin/pip install -r infra/requirements.txt
    .venv-infra/bin/python infra/python/config.py                 # validate gateway.yaml
    .venv-infra/bin/python infra/python/config.py --export        # rewrite infra/data/fixtures/config.json
    .venv-infra/bin/python infra/python/config.py X.yaml --export --out X.json   # export elsewhere (the Go twin's test)
    .venv-infra/bin/python infra/python/config.py --check         # fail if config.json drifted
Free and offline: no model calls.

Every rule raises ConfigError with the offending key in the message, and the server
refuses to start on it. The upstream must be loopback unless live=True.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

# ── 1. Shape ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
GATEWAY_YAML = ROOT / "infra" / "gateway.yaml"
CONFIG_JSON = ROOT / "infra" / "data" / "fixtures" / "config.json"

SECTIONS = ("upstream", "models", "routes", "cache", "retry", "limit", "budgets", "traces")
LOOPBACK = ("127.0.0.1", "localhost", "::1")


class ConfigError(ValueError):
    """A malformed config; the message names the key."""


@dataclass(frozen=True)
class ModelCfg:
    rpm: int
    tpm: int
    price: tuple[float, float] | str  # (input, output) per million tokens, or the literal "lab"


@dataclass(frozen=True)
class Route:
    to: str
    fallback: list[str]


@dataclass(frozen=True)
class CacheCfg:
    ttl_s: int
    skip_tools: bool


@dataclass(frozen=True)
class RetryCfg:
    attempts: int
    base_ms: int
    max_ms: int


@dataclass(frozen=True)
class LimitCfg:
    max_wait_ms: int


@dataclass(frozen=True)
class Budget:
    limit_usd: float
    warn_usd: float | None


@dataclass(frozen=True)
class Config:
    upstream: str
    models: dict[str, ModelCfg]
    routes: dict[str, Route]
    deny_unknown: bool
    cache: CacheCfg
    retry: RetryCfg
    limit: LimitCfg
    budgets: dict[str, Budget]
    untagged: str  # "refuse", or the name of the budget untagged requests are booked to
    traces_dir: Path


# ── 2. Rules ────────────────────────────────────────────────────────────────
def is_loopback(url: str) -> bool:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    return host in LOOPBACK


def _map(x: Any, where: str, keys: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict:
    if not isinstance(x, dict):
        raise ConfigError(f"{where} must be a mapping")
    for k in x:
        if k not in keys and k not in optional:
            raise ConfigError(f"unknown key {k!r} in {where}")
    for k in keys:
        if k not in x:
            raise ConfigError(f"{where}.{k} is missing")
    return x


def _num(x: Any, where: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0:
        raise ConfigError(f"{where} must be a finite number above zero, got {x!r}")
    return float(x)


def _int(x: Any, where: str) -> int:
    if isinstance(x, bool) or not isinstance(x, int) or x <= 0:
        raise ConfigError(f"{where} must be an integer above zero, got {x!r}")
    return x


def _name(x: Any, where: str) -> str:
    if not isinstance(x, str) or not x:
        raise ConfigError(f"{where} must be a non-empty string")
    return x


def _price(x: Any, where: str) -> tuple[float, float] | str:
    if x == "lab":
        return "lab"
    if isinstance(x, list) and len(x) == 2:
        return (_num(x[0], f"{where}[0]"), _num(x[1], f"{where}[1]"))
    raise ConfigError(f"{where} must be 'lab' or [input, output] per million tokens")


# ── 3. Load ─────────────────────────────────────────────────────────────────
def load(path: Path, live: bool = False) -> Config:
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: not valid YAML ({e})") from e
    return build(raw, live)


def from_dict(d: dict, live: bool = False) -> Config:
    """A Config from the JSON export's shape (config.json, and a fixture case's merged config):
    turned back into the YAML's shape, then validated by the same rules as load."""
    if not isinstance(d, dict):
        raise ConfigError("config must be a mapping")
    keys = ("upstream", "models", "routes", "deny_unknown", "cache", "retry", "limit", "budgets",
            "untagged", "traces_dir")
    for k in d:
        if k not in keys:
            raise ConfigError(f"unknown key {k!r} at top level")
    raw = {k: d[k] for k in ("upstream", "models", "cache", "retry", "limit") if k in d}
    if isinstance(d.get("routes"), dict):
        raw["routes"] = {**d["routes"], **({"*": "deny"} if d.get("deny_unknown") else {})}
    if isinstance(d.get("budgets"), dict):
        raw["budgets"] = {t: ({k: v for k, v in b.items() if not (k == "warn_usd" and v is None)}
                              if isinstance(b, dict) else b) for t, b in d["budgets"].items()}
        if "untagged" in d:
            raw["budgets"]["untagged"] = d["untagged"]
    if "traces_dir" in d:
        raw["traces"] = {"dir": d["traces_dir"]}
    return build(raw, live)


def build(raw: Any, live: bool = False) -> Config:
    """Every rule, over the YAML's shape."""
    if not isinstance(raw, dict):
        raise ConfigError("config must be a mapping")
    for k in raw:
        if k not in SECTIONS:
            raise ConfigError(f"unknown key {k!r} at top level")
    for k in SECTIONS:
        if k not in raw:
            raise ConfigError(f"missing section: {k}")

    upstream = _name(raw["upstream"], "upstream")
    if not live and not is_loopback(upstream):
        raise ConfigError(f"upstream {upstream} is not loopback; a remote host such as "
                          f"{urlsplit(upstream).hostname} needs live mode")

    if not isinstance(raw["models"], dict) or not raw["models"]:
        raise ConfigError("models must be a non-empty mapping")
    models = {}
    for name, m in raw["models"].items():
        w = f"models.{name}"
        m = _map(m, w, ("rpm", "tpm", "price"))
        models[name] = ModelCfg(_int(m["rpm"], f"{w}.rpm"), _int(m["tpm"], f"{w}.tpm"),
                                _price(m["price"], f"{w}.price"))

    if not isinstance(raw["routes"], dict):
        raise ConfigError("routes must be a mapping")
    routes, deny_unknown = {}, False
    for name, r in raw["routes"].items():
        if name == "*":
            if r != "deny":
                raise ConfigError("routes.'*' must be deny")
            deny_unknown = True
            continue
        w = f"routes.{name}"
        r = _map(r, w, ("to",), ("fallback",))
        fb = r.get("fallback", [])
        if not isinstance(fb, list):
            raise ConfigError(f"{w}.fallback must be a list")
        for t in [r["to"], *fb]:
            if t not in models:
                raise ConfigError(f"{w} names {t}, which is not under models")
        routes[name] = Route(r["to"], list(fb))

    c = _map(raw["cache"], "cache", ("ttl_s", "skip_tools"))
    if not isinstance(c["skip_tools"], bool):
        raise ConfigError("cache.skip_tools must be true or false")
    cache = CacheCfg(_int(c["ttl_s"], "cache.ttl_s"), c["skip_tools"])

    r = _map(raw["retry"], "retry", ("attempts", "base_ms", "max_ms"))
    retry = RetryCfg(_int(r["attempts"], "retry.attempts"), _int(r["base_ms"], "retry.base_ms"),
                     _int(r["max_ms"], "retry.max_ms"))
    if retry.max_ms < retry.base_ms:
        raise ConfigError("retry.max_ms must not be below retry.base_ms")

    l = _map(raw["limit"], "limit", ("max_wait_ms",))
    limit = LimitCfg(_int(l["max_wait_ms"], "limit.max_wait_ms"))

    if not isinstance(raw["budgets"], dict):
        raise ConfigError("budgets must be a mapping")
    budgets, untagged = {}, None
    for team, b in raw["budgets"].items():
        if team == "untagged":
            untagged = _name(b, "budgets.untagged")
            continue
        w = f"budgets.{team}"
        b = _map(b, w, ("limit_usd",), ("warn_usd",))
        lim = _num(b["limit_usd"], f"{w}.limit_usd")
        warn = _num(b["warn_usd"], f"{w}.warn_usd") if "warn_usd" in b else None
        if warn is not None and warn > lim:
            raise ConfigError(f"{w}.warn_usd ({warn}) is above limit_usd ({lim})")
        budgets[team] = Budget(lim, warn)
    if untagged is None:
        raise ConfigError("budgets.untagged is missing (refuse, or a team name)")
    if untagged != "refuse" and untagged not in budgets:
        raise ConfigError(f"budgets.untagged names {untagged}, which is not a team under budgets")

    t = _map(raw["traces"], "traces", ("dir",))
    traces_dir = Path(_name(t["dir"], "traces.dir"))

    return Config(upstream, models, routes, deny_unknown, cache, retry, limit, budgets,
                  untagged, traces_dir)


# ── 4. Export and command line ──────────────────────────────────────────────
def to_json(cfg: Config) -> dict:
    """The shape the Go twin reads; price keeps the literal 'lab'."""
    return {
        "upstream": cfg.upstream,
        "models": {n: {"rpm": m.rpm, "tpm": m.tpm,
                       "price": list(m.price) if isinstance(m.price, tuple) else m.price}
                   for n, m in cfg.models.items()},
        "routes": {n: {"to": r.to, "fallback": r.fallback} for n, r in cfg.routes.items()},
        "deny_unknown": cfg.deny_unknown,
        "cache": {"ttl_s": cfg.cache.ttl_s, "skip_tools": cfg.cache.skip_tools},
        "retry": {"attempts": cfg.retry.attempts, "base_ms": cfg.retry.base_ms,
                  "max_ms": cfg.retry.max_ms},
        "limit": {"max_wait_ms": cfg.limit.max_wait_ms},
        "budgets": {n: {"limit_usd": b.limit_usd, "warn_usd": b.warn_usd}
                    for n, b in cfg.budgets.items()},
        "untagged": cfg.untagged,
        "traces_dir": cfg.traces_dir.as_posix(),
    }


def export_text(path: Path = GATEWAY_YAML) -> str:
    return json.dumps(to_json(load(path)), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate the gateway config and export its JSON.")
    ap.add_argument("path", nargs="?", type=Path, default=GATEWAY_YAML)
    ap.add_argument("--live", action="store_true", help="allow a remote upstream")
    ap.add_argument("--export", action="store_true", help=f"write {CONFIG_JSON.relative_to(ROOT)}")
    ap.add_argument("--out", type=Path, help=f"with --export: write here instead of {CONFIG_JSON.relative_to(ROOT)}")
    ap.add_argument("--check", action="store_true", help="fail if the export drifted from the yaml")
    a = ap.parse_args(argv)
    try:
        cfg = load(a.path, live=a.live)
    except (ConfigError, OSError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    rel = CONFIG_JSON.relative_to(ROOT)
    if a.export:
        out = a.out or CONFIG_JSON
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(export_text(a.path))
        print(f"wrote {rel if a.out is None else out}")
    elif a.check:
        if not CONFIG_JSON.exists() or CONFIG_JSON.read_text() != export_text(a.path):
            print(f"{rel} has drifted from {a.path}; run --export", file=sys.stderr)
            return 1
        print(f"{rel} matches")
    else:
        print(f"ok: {len(cfg.models)} model(s), {len(cfg.routes)} route(s), {len(cfg.budgets)} budget(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
