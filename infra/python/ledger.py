#!/usr/bin/env python3
"""
ledger: what a call may cost and what it did cost, for the gateway.

The prices are the LLMs lab's table (llms/python/triage_lab.py), read, never copied,
so the gateway's ledger and the agents track's ledger price the same tokens the same
way. The cost formula is the agents proxy's ledger_cost (R64): uncached input at the
input price, cache writes at 1.25x, cache reads at 0.10x, output at the output price.
Standard library only; nothing here calls a model.
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

# ── 1. Prices ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
LLMS_PYTHON = ROOT / "llms" / "python"

# The API's usage fields, stored verbatim and apart. input_tokens is the uncached input only.
USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
# Prompt-cache prices as multiples of the model's input price, as the agents proxy prices them.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


@lru_cache(maxsize=1)
def _lab_table() -> tuple[tuple[str, tuple[float, float]], ...]:
    if str(LLMS_PYTHON) not in sys.path:
        sys.path.insert(0, str(LLMS_PYTHON))
    from triage_lab import PRICES  # read-only; triage_lab imports only the standard library at load
    return tuple((m, (float(p[0]), float(p[1]))) for m, p in PRICES.items())


def lab_prices() -> dict[str, tuple[float, float]]:
    """The lab's dollars per million tokens, (input, output), by model. A fresh dict each call."""
    return dict(_lab_table())


def resolve_prices(cfg) -> dict[str, tuple[float, float]]:
    """The config's price per model: the literal 'lab' becomes the lab's pair, [in, out] a tuple.
    A model priced 'lab' that the lab does not list is an error naming it."""
    lab, out = lab_prices(), {}
    for name, m in cfg.models.items():
        if m.price == "lab":
            if name not in lab:
                raise ValueError(f"models.{name}.price is lab, but the lab's table has no {name}")
            out[name] = lab[name]
        else:
            out[name] = (float(m.price[0]), float(m.price[1]))
    return out


# ── 2. Cost ─────────────────────────────────────────────────────────────────
def cost_of(model: str | None, usage: dict, prices: dict[str, tuple[float, float]]) -> float | None:
    """Dollars for one call from its usage, cache tokens at their multipliers; None when the model
    has no price or a usage field is missing. The same arithmetic, in the same order, as the proxy."""
    if model not in prices or any(usage.get(k) is None for k in USAGE_FIELDS):
        return None
    p_in, p_out = prices[model]
    return (usage["input_tokens"] * p_in + usage["cache_creation_input_tokens"] * CACHE_WRITE_MULTIPLIER * p_in
            + usage["cache_read_input_tokens"] * CACHE_READ_MULTIPLIER * p_in
            + usage["output_tokens"] * p_out) / 1_000_000


def worst_case_usd(model: str, in_tokens: int, out_tokens: int, prices: dict[str, tuple[float, float]]) -> float:
    """Dollars if the call used its estimated input and every output token it may, no caching (the
    conservative side). 0.0 for a model with no price: the route stage denies or passes it, not the budget."""
    if model not in prices:
        return 0.0
    p_in, p_out = prices[model]
    return (in_tokens * p_in + out_tokens * p_out) / 1_000_000


# ── 3. Ledger store ─────────────────────────────────────────────────────────
# One JSON line per call, appended and never rewritten (the agents proxy's store, one row per call).
# A last line cut off by a crash is skipped with a warning; a bad line anywhere else is corruption.
REFUSED_STATUSES = (400, 402, 404, 429)  # the gateway's own refusals: parse, budget, deny, limit


def read_jsonl(path: Path) -> list[dict]:
    """Every row of a JSONL file. A last line that does not parse is skipped with one warning on
    stderr (the writer was interrupted); an unparseable line before it raises ValueError naming its number."""
    path = Path(path)
    if not path.exists():
        return []
    lines = [(n, s) for n, s in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if s.strip()]
    out = []
    for i, (n, s) in enumerate(lines):
        try:
            row = json.loads(s)
            if not isinstance(row, dict):
                raise ValueError("not an object")
        except ValueError as e:
            if i == len(lines) - 1:
                print(f"warning: {path}: skipped a half-written last line", file=sys.stderr)
                continue
            raise ValueError(f"{path}: line {n} is not a JSON object ({e})") from e
        out.append(row)
    return out


class Ledger:
    """The append-only ledger file and its rows in memory."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._rows: list[dict] = []
        self.load()

    def load(self) -> None:
        self._rows = read_jsonl(self.path)

    def append(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._rows.append(dict(row))

    def rows(self) -> list[dict]:
        return list(self._rows)

    # ── 4. Budget state ─────────────────────────────────────────────────────
    def spend_by_team(self) -> dict[str, float]:
        """Dollars per team; a row with no cost (None) adds nothing."""
        out: dict[str, float] = {}
        for r in self._rows:
            if r.get("cost_usd") is not None:
                out[r["team"]] = out.get(r["team"], 0.0) + r["cost_usd"]
        return out


def summary(rows: list[dict]) -> dict:
    """teams: {requests, cost_usd, cached, refused}; tags: cost by tag; top_tags: the 10 dearest,
    cost descending then tag. Rows with cost_usd None count as requests but add no cost."""
    teams: dict[str, dict] = {}
    tags: dict[str, float] = {}
    for r in rows:
        t = teams.setdefault(r["team"], {"requests": 0, "cost_usd": 0.0, "cached": 0, "refused": 0})
        t["requests"] += 1
        t["cached"] += bool(r.get("cached"))
        t["refused"] += r.get("status") in REFUSED_STATUSES
        if r.get("cost_usd") is not None:
            t["cost_usd"] += r["cost_usd"]
            tags[r["tag"]] = tags.get(r["tag"], 0.0) + r["cost_usd"]
    top = sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return {"teams": teams, "tags": tags, "top_tags": [[k, v] for k, v in top]}
