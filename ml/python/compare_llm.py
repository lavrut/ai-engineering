#!/usr/bin/env python3
"""
compare_llm — lesson 6's comparison: the trained model (v1) against Claude on the same complaints.

Both read the same 200 complaints from the 2022 test split and name one of six categories.
The Claude answers are SAVED, so everything here replays them for free; only `record` and
`live` can spend money, and both print an estimate and stop unless you say yes.

Run:
    python compare_llm.py sample                 # pick (or check) the 200 complaints: free
    python compare_llm.py compare                # replay saved answers, write reports/llm_compare.json: free
    export ANTHROPIC_API_KEY=sk-ant-...          # only to record or run live
    python compare_llm.py record                 # prints the estimate, asks first (or add --yes)
    python compare_llm.py live --limit 5         # calls, prints, saves nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic

import train

# ── 1. Configuration ────────────────────────────────────────────────────────
SEED = 7                                    # the same seed as the rest of the track
MODEL = "claude-haiku-5-5"
PRICES = {"claude-haiku-5-5": (0.10, 0.50)}  # dollars per million tokens: (input, output)
MAX_TOKENS = 300                            # the most output one call may use. Any thinking is billed
                                            # as output and counts here; the answer itself is ~20 tokens
# Haiku 5.5 thinks by default, and thinking tokens count toward max_tokens and are billed as output.
# A 300-token cap with thinking on would often stop with stop_reason "max_tokens" and no answer, so
# the classifier call turns thinking off. These settings are part of every cache key.
THINKING = {"type": "disabled"}
SAMPLE_SIZE = 200
DEFAULT_MAX_COST = 10.0                     # the --max-cost default: a run stops once it has spent this
EST_TOKENS = (1100, 25)                     # typical (input, output) per call: ~600-word narrative + prompt
WORST_INPUT_TOKENS = 3000                   # input of the longest narrative we expect to send (about 12,000 chars)
ML_DIR = Path(__file__).resolve().parent.parent   # holds data/, models/, reports/; not the working directory
# Replay is the default of the CLI and of every function that reads saved answers; there is
# deliberately no module-level "mode" setting that a library call could inherit.

CLASSES = json.loads((ML_DIR / "models" / "v1.json").read_text())["classes"]
DEFINITIONS = {   # one line each, in the spirit of the CFPB product names (the labels are CFPB's own)
    "Checking or savings account": "bank checking, savings or prepaid account problems: deposits, holds, fees, closures",
    "Credit card": "credit card problems: charges, disputes, fees, rewards, payments, the card company",
    "Credit reporting or other personal consumer reports": "credit bureau reports, scores and background reports: errors, "
                                                           "disputes, identity theft on a report",
    "Debt collection": "a collector contacting someone about a debt, or a debt that is not theirs",
    "Money transfer, virtual currency, or money service": "wire or app transfers, money orders, cryptocurrency, "
                                                          "payment apps and money services",
    "Mortgage": "home loans: payments, servicing, escrow, modification, foreclosure, refinancing",
}
SYSTEM = ("You classify consumer complaints sent to the US Consumer Financial Protection Bureau. "
          "Read the complaint and choose the one product category it is about:\n"
          + "\n".join(f"- {c}: {DEFINITIONS[c]}" for c in CLASSES)
          + "\nAnswer with the category only.")
SCHEMA = {"type": "object", "properties": {"category": {"type": "string", "enum": CLASSES}},
          "required": ["category"], "additionalProperties": False}


# ── 2. Sample ───────────────────────────────────────────────────────────────
def pick_sample(test_rows: list[dict], n: int = SAMPLE_SIZE, seed: int = SEED) -> list[str]:
    """n complaint ids from the test split, stratified: each category gets its share of n
    (largest remainder, so the shares add up to exactly n), drawn with a seeded shuffle."""
    by_class = {c: sorted(r["id"] for r in test_rows if r["product"] == c) for c in CLASSES}
    total = len(test_rows)
    if n > total:
        raise ValueError(f"asked for {n} complaints but the test split has {total}")
    shares = {c: n * len(ids) / total for c, ids in by_class.items()}
    take = {c: int(s) for c, s in shares.items()}
    by_remainder = sorted(CLASSES, key=lambda c: -(shares[c] - take[c]))   # stable: ties keep class order
    for c in by_remainder[: n - sum(take.values())]:
        take[c] += 1
    rng = random.Random(seed)
    return sorted(i for c in CLASSES for i in rng.sample(by_class[c], take[c]))


def load_sample_ids(ml: Path = ML_DIR) -> list[str]:
    return json.loads((Path(ml) / "data" / "llm_sample.json").read_text())["ids"]


def load_sample_rows(ml: Path = ML_DIR) -> list[dict]:
    """The sampled complaints, in the order the sample file lists them."""
    by_id = {r["id"]: r for r in train.load_complaints(Path(ml) / "data" / "complaints.jsonl")}
    return [by_id[i] for i in load_sample_ids(ml)]


# ── 3. Request ──────────────────────────────────────────────────────────────
def build_request(narrative: str, model: str = MODEL) -> dict:
    """Structured output makes the reply `{"category": <one of the six>}` every time. No
    temperature, top_p or top_k (the newest models reject them) and no forced tool_choice.
    Thinking is disabled so the 300 output tokens are all for the answer."""
    return {"model": model, "max_tokens": MAX_TOKENS, "thinking": THINKING, "system": SYSTEM,
            "messages": [{"role": "user", "content": narrative}],
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}}}


# ── 4. Model call with saved responses ──────────────────────────────────────
class ReplayMiss(Exception):
    """Replay found no saved response. It never falls back to a paid call."""


class CostLimit(Exception):
    """The spend passed --max-cost. `lines` holds what finished; `spent` is the dollars."""
    def __init__(self, lines: list[dict], spent: float):
        super().__init__(f"spent ${spent:.4f}, over the limit")
        self.lines, self.spent = lines, spent


def cache_key(model: str, system: str, narrative: str, rep: int, settings: dict | None = None) -> str:
    """sha256 of the canonical JSON of everything that shapes the answer, including the thinking
    setting, max_tokens and the schema, so changing any of them can never replay an old answer."""
    settings = settings or {"thinking": THINKING, "max_tokens": MAX_TOKENS, "schema": SCHEMA}
    request = {"model": model, "prompt_text": system, "input": narrative, "rep": rep, "settings": settings}
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def request_key(row: dict, model: str = MODEL, rep: int = 0) -> str:
    return cache_key(model, SYSTEM, row["narrative"], rep)


def response_path(ml: Path = ML_DIR, model: str = MODEL) -> Path:
    return Path(ml) / "data" / "llm_responses" / f"{model}.jsonl"


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def latest(path: Path, key: str) -> dict | None:
    """The newest saved line for key: re-recording an errored request fixes it."""
    return next((x for x in reversed(_lines(path)) if x["key"] == key), None)


_WRITE_LOCK = threading.Lock()


def call_model(params: dict, *, key: str, case_id: str, mode: str = "replay", client=None,
               ml: Path = ML_DIR, rep: int = 0) -> dict:
    """One saved-response line. replay: read it (a miss raises). record: call and append it,
    unless the latest line for the key succeeded. live: call, save nothing."""
    path = response_path(ml, params["model"])
    if mode == "replay":
        line = latest(path, key)
        if line is None:
            raise ReplayMiss(f"no saved answer for complaint {case_id}. Record it: python compare_llm.py record")
        return line
    if mode not in ("record", "live"):
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "record":
        saved = latest(path, key)
        if saved and not saved.get("error"):
            return {**saved, "skipped": True}          # never pay twice for the same request
    client = client or anthropic.Anthropic(max_retries=4)   # built only here, for a paid call
    line = {"key": key, "model": params["model"], "case_id": case_id, "rep": rep, "output": None,
            "stop_reason": None, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "error": None}
    start = time.monotonic()
    try:
        response = client.messages.create(**params)
        line.update(stop_reason=response.stop_reason, input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens)
        block = next((b for b in response.content if b.type == "text"), None)
        if response.stop_reason in ("refusal", "max_tokens") or block is None:
            line["error"] = f"no usable reply (stop_reason {response.stop_reason})"
        else:
            try:
                line["output"] = json.loads(block.text)
            except json.JSONDecodeError as e:
                line["error"] = f"invalid JSON: {e.msg}"
    except anthropic.APIError as e:
        line["error"] = str(e) or type(e).__name__
    line["latency_ms"] = round((time.monotonic() - start) * 1000)
    if mode == "record":
        path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def classify(row: dict, mode: str = "replay", client=None, ml: Path = ML_DIR, model: str = MODEL) -> dict:
    return call_model(build_request(row["narrative"], model), key=request_key(row, model), case_id=row["id"],
                      mode=mode, client=client, ml=ml)


def call_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def estimate_cost(n_calls: int, model: str = MODEL) -> float:
    return n_calls * call_cost(model, *EST_TOKENS)


def worst_case_cost(n_calls: int, model: str = MODEL) -> float:
    """Every call reads a long narrative and uses all of MAX_TOKENS for output."""
    return n_calls * call_cost(model, WORST_INPUT_TOKENS, MAX_TOKENS)


def run_calls(rows: list[dict], *, mode: str, client=None, ml: Path = ML_DIR, max_cost: float | None = None,
              workers: int = 8) -> list[dict]:
    """Paid path: classify every row on a thread pool. Once real spend passes max_cost no new call
    starts, calls in flight finish, and CostLimit carries the finished lines."""
    todo = [r for r in rows if mode == "live" or not _recorded(r, ml)]
    if not todo:
        return []
    client = client or anthropic.Anthropic(max_retries=4)   # after confirmation, only if a call is due
    stop, lock, spent = threading.Event(), threading.Lock(), [0.0]

    def work(row):
        if stop.is_set():
            return None
        line = classify(row, mode=mode, client=client, ml=ml)
        with lock:
            spent[0] += call_cost(line["model"], line["input_tokens"], line["output_tokens"])
            if max_cost is not None and spent[0] > max_cost:
                stop.set()
        return line

    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        lines = [x for x in pool.map(work, todo) if x is not None]
    if stop.is_set():
        raise CostLimit(lines, spent[0])
    return lines


def _recorded(row: dict, ml: Path) -> bool:
    saved = latest(response_path(ml), request_key(row))
    return bool(saved) and not saved.get("error")


# ── 5. Compare ──────────────────────────────────────────────────────────────
def compare(ml: Path = ML_DIR, model: str = MODEL) -> dict | None:
    """Score v1 and Claude on the complaints that have a saved Claude answer. Replay only:
    this function has no client and no mode, so it cannot call the API. None if nothing is saved."""
    v1 = json.loads((Path(ml) / "models" / "v1.json").read_text())
    latency = json.loads((Path(ml) / "reports" / "latency.json").read_text())["servers"]
    rows, lines = [], []
    for row in load_sample_rows(ml):
        try:
            lines.append(classify(row, mode="replay", ml=ml, model=model))
            rows.append(row)
        except ReplayMiss:
            continue
    if not lines:
        return None
    classes = v1["classes"]
    answered = [bool(x.get("output")) and not x.get("error") for x in lines]
    # A refused, truncated or errored reply is a wrong answer, not a missing row: it is scored
    # as "no answer" (an extra class that is never the true one), over every replayed complaint.
    guess = [x["output"]["category"] if ok else NO_ANSWER for x, ok in zip(lines, answered)]
    y_true = [r["product"] for r in rows]
    mine = train.per_class_metrics(y_true, [train.score(v1, r["narrative"])["label"] for r in rows], classes)
    theirs = _six_class_metrics(y_true, guess, classes)
    keep = [i for i, ok in enumerate(answered) if ok]
    only = train.per_class_metrics([y_true[i] for i in keep], [guess[i] for i in keep], classes) if keep else None
    mine_only = train.per_class_metrics([y_true[i] for i in keep],
                                        [train.score(v1, rows[i]["narrative"])["label"] for i in keep], classes) if keep else None
    cost = sum(call_cost(model, x["input_tokens"], x["output_tokens"]) for x in lines)   # every paid line, errors too
    return {
        "n": len(rows), "sample_size": len(load_sample_ids(ml)), "no_answer": answered.count(False),
        "band": mine["band"], "split": "test (2022)", "classes": classes,
        "scoring": "headline = all replayed complaints; a Claude reply with no usable answer counts as wrong",
        "answered_only": {"n": len(keep), "v1_accuracy": mine_only["accuracy"] if keep else None,
                          "claude_accuracy": only["accuracy"] if keep else None},
        "v1": {"version": v1["version"], "accuracy": mine["accuracy"], "macro_f1": mine["macro"]["f1"],
               "per_class_f1": {c: m["f1"] for c, m in mine["per_class"].items()},
               "latency_p50_ms": latency["python"]["p50_ms"], "latency_p50_ms_go": latency["go"]["p50_ms"],
               "latency_note": "local CPU inference: one request to the model served on this machine (reports/latency.json)",
               "cost_per_1000": 0.0},
        "claude": {"model": model, "accuracy": theirs["accuracy"], "macro_f1": theirs["macro_f1"],
                   "per_class_f1": theirs["per_class_f1"],
                   "latency_p50_ms": statistics.median(x["latency_ms"] for x in lines),
                   "latency_note": "remote API round trip: network, SDK retries and queueing behind 8 concurrent "
                                   "workers while recording; not comparable to the local inference above",
                   "cost_per_1000": cost / len(lines) * 1000},
        "latency_caveat": "The two latencies measure different things (local CPU inference vs a remote API call).",
    }


NO_ANSWER = "no answer"


def _six_class_metrics(y_true, y_pred, classes) -> dict:
    """Accuracy and macro-F1 over the six real categories; "no answer" is a seventh, never-true prediction."""
    m = train.per_class_metrics(y_true, y_pred, classes + [NO_ANSWER])
    f1 = {c: m["per_class"][c]["f1"] for c in classes}
    return {"accuracy": m["accuracy"], "macro_f1": sum(f1.values()) / len(classes), "per_class_f1": f1}


# ── 6. Entry point ──────────────────────────────────────────────────────────
def _confirm(n_calls: int, cap: float, yes: bool) -> bool:
    """Print the estimate, the worst case and the cap. True only after --yes or an interactive y."""
    typical, worst = estimate_cost(n_calls), worst_case_cost(n_calls)
    print(f"{n_calls} calls to {MODEL} (${PRICES[MODEL][0]:.2f} / ${PRICES[MODEL][1]:.2f} per million tokens in / out).")
    print(f"Estimated cost ${typical:.2f} (worst case ${worst:.2f} if every reply uses max_tokens={MAX_TOKENS}). "
          f"Cap: ${cap:.2f}.")
    if worst > cap:
        print(f"The worst case is over the cap; the run stops at ${cap:.2f} and keeps what finished.")
    if yes:
        return True
    answer = ""
    if sys.stdin.isatty():
        try:
            answer = input("Make these paid calls? [y/N] ")
        except EOFError:
            pass
    if answer.strip().lower() not in ("y", "yes"):
        print("Not confirmed. Nothing was called. Add --yes to approve.")
        return False
    return True


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["sample", "compare", "record", "live"])
    ap.add_argument("--ml-dir", type=Path, default=ML_DIR, help="folder holding data/, models/ and reports/")
    ap.add_argument("--limit", type=int, default=None, help="record/live: only the first N sampled complaints")
    ap.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST, help="stop a paid run after this many dollars")
    ap.add_argument("--yes", action="store_true", help="approve the printed estimate without asking")
    args = ap.parse_args(argv)
    ml = args.ml_dir

    if args.command == "sample":
        test = train.split_by_time(train.load_complaints(ml / "data" / "complaints.jsonl"))["test"]
        ids, path = pick_sample(test), ml / "data" / "llm_sample.json"
        if path.exists() and load_sample_ids(ml) == ids:
            print(f"{path.name}: the {len(ids)} saved ids match a fresh draw (seed {SEED}).")
        else:
            _write_json(path, {"generated_by": "python compare_llm.py sample", "seed": SEED, "split": "test (2022)",
                               "stratified_by": "category", "ids": ids})
            print(f"Wrote {len(ids)} ids to {path}.")
        return 0

    if args.command == "compare":
        report = compare(ml)
        if report is None:
            print("No recorded run yet. Record one (needs ANTHROPIC_API_KEY; prints the cost first):\n"
                  "  python compare_llm.py record")
            return 0
        _write_json(ml / "reports" / "llm_compare.json", {"generated_by": "python compare_llm.py compare", **report})
        print(f"{report['n']} complaints: v1 accuracy {report['v1']['accuracy']:.3f}, "
              f"{MODEL} {report['claude']['accuracy']:.3f} (noise band ±{report['band']:.3f}); "
              f"{report['no_answer']} Claude replies had no usable answer, counted wrong.")
        return 0

    rows = load_sample_rows(ml)[: args.limit]
    todo = rows if args.command == "live" else [r for r in rows if not _recorded(r, ml)]
    if len(todo) < len(rows):
        print(f"{len(rows) - len(todo)} complaints are already recorded and will be skipped.")
    if not todo:
        print("Nothing to record.")
        return 0
    if not _confirm(len(todo), args.max_cost, args.yes):
        return 1
    try:
        lines = run_calls(todo, mode=args.command, ml=ml, max_cost=args.max_cost)
    except CostLimit as e:
        print(f"Stopped: spent ${e.spent:.4f}, over --max-cost {args.max_cost}. "
              f"{'Finished calls are saved.' if args.command == 'record' else 'Live mode saves nothing.'}")
        return 1
    for x in (lines if args.command == "live" else []):
        print(x["case_id"], x["output"] or x["error"])
    print(f"Done: {len(lines)} calls" + (f", saved under {response_path(ml).parent}." if args.command == "record" else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
