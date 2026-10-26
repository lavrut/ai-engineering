#!/usr/bin/env python3
"""
monitor — has the world moved away from what the model learned? PSI drift, month by month.

The Population Stability Index compares two distributions over the same buckets:

    PSI = sum over buckets of (a_i - e_i) * ln(a_i / e_i)

e_i is the expected (training) proportion and a_i the actual (this month's). Both are
proportions = count / total, and epsilon = 1e-4 is ADDED TO EACH PROPORTION AFTER DIVIDING and
before the log (not renormalised), so an empty bucket gives a finite number:

    e_i = expected_i / sum(expected) + 1e-4        a_i = actual_i / sum(actual) + 1e-4

Each term is >= 0, so PSI >= 0, and 0 only when the distributions match. Rule of thumb: below
0.1 stable, 0.1-0.2 watch, 0.2 or more alert. Go repeats this to 1e-12 (psi_cases.json).

Two signals per stream month, against the training split (2019-2021), with the production model:
  (a) predicted-category mix: what v1 says the month's complaints are about;
  (b) input words: which top-K training words the text uses, plus an "other" bucket.

Run (from ml/python):
    python monitor.py                   # print the table, write nothing
    python monitor.py --write-reports   # also write ml/reports/drift.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import train

ML_DIR = train.ML_DIR
sys.path.insert(0, str(ML_DIR / "data"))      # for fetch_sample (offline: we only use its shares())
import fetch_sample                            # noqa: E402

# ── 1. Configuration ────────────────────────────────────────────────────────
EPSILON = 1e-4          # added to every proportion before the log
ALERT_AT = 0.2          # PSI at or above this is an alert
MIN_N = 30              # a month with fewer complaints is shown but never alerted: PSI on a few
                        # dozen texts is mostly noise
# K = 50: the top 50 words hold roughly a third of all training tokens, so every bucket expects
# real mass (>= 0.3%), while the 50+1 buckets stay few enough for a month of 50 complaints
# (~30,000 tokens) to fill them. Chosen for readability, not tuned to alerts.
TOP_K = 50
SHOWN_WORDS = 5         # words listed per month in top_shifted_words
PATHS = {"registry": ML_DIR / "registry.json", "readme": ML_DIR / "data" / "README.md",
         "reports": ML_DIR / "reports"}


# ── 2. PSI ──────────────────────────────────────────────────────────────────
def proportions(counts: list[float], eps: float = EPSILON) -> list[float]:
    total = sum(counts)
    return [(c / total if total else 0.0) + eps for c in counts]


def contributions(expected_counts: list[float], actual_counts: list[float],
                  eps: float = EPSILON) -> list[float]:
    """Each bucket's share of the PSI: (a - e) * ln(a / e). Never negative."""
    if len(expected_counts) != len(actual_counts):
        raise ValueError("expected and actual need the same buckets")
    e, a = proportions(expected_counts, eps), proportions(actual_counts, eps)
    return [(ai - ei) * math.log(ai / ei) for ei, ai in zip(e, a)]


def psi(expected_counts: list[float], actual_counts: list[float], eps: float = EPSILON) -> float:
    return sum(contributions(expected_counts, actual_counts, eps))


# ── 3. Buckets ──────────────────────────────────────────────────────────────
def top_words(train_rows: list[dict], model_json: dict, k: int = TOP_K) -> list[str]:
    """The k most frequent words of the training text (ties alphabetical), words the model knows."""
    known = set(model_json["vocabulary"])
    freq = Counter(t for r in train_rows for t in train.tokenize(r["narrative"], model_json["tokenizer"])
                   if t in known)
    return [t for t, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def word_counts(rows: list[dict], words: list[str], model_json: dict) -> list[int]:
    """Token counts per bucket: one per top word, then "other" (every remaining token)."""
    index = {w: i for i, w in enumerate(words)}
    counts = [0] * (len(words) + 1)
    for r in rows:
        for t in train.tokenize(r["narrative"], model_json["tokenizer"]):
            counts[index.get(t, len(words))] += 1
    return counts


def label_counts(rows: list[dict], model_json: dict, index: dict) -> list[int]:
    """How many complaints the model puts in each category (classes in model order)."""
    labels = Counter(train.score(model_json, r["narrative"], index)["label"] for r in rows)
    return [labels[c] for c in model_json["classes"]]


# ── 4. Monthly drift ────────────────────────────────────────────────────────
def monthly_drift(stream_rows: list[dict], model_json: dict, train_rows: list[dict]) -> list[dict]:
    """One dict per month in the stream: n, psi_predictions, psi_words, alert, top_shifted_words."""
    index = {t: i for i, t in enumerate(model_json["vocabulary"])}
    words = top_words(train_rows, model_json)
    exp_words = word_counts(train_rows, words, model_json)
    exp_labels = label_counts(train_rows, model_json, index)
    e_prop = proportions(exp_words)
    by_month: dict[str, list[dict]] = {}
    for r in stream_rows:
        by_month.setdefault(r["date"][:7], []).append(r)
    out = []
    for month in sorted(by_month):
        rows = by_month[month]
        act_words = word_counts(rows, words, model_json)
        parts = contributions(exp_words, act_words)
        a_prop = proportions(act_words)
        shifted = sorted(range(len(words)), key=lambda i: (-parts[i], words[i]))[:SHOWN_WORDS]
        psi_pred = psi(exp_labels, label_counts(rows, model_json, index))
        psi_words = sum(parts)
        out.append({
            "month": month, "n": len(rows),
            "psi_predictions": psi_pred, "psi_words": psi_words,
            "alert": len(rows) >= MIN_N and (psi_pred >= ALERT_AT or psi_words >= ALERT_AT),
            "top_shifted_words": [{"word": words[i], "contribution": parts[i],
                                   "direction": "up" if a_prop[i] > e_prop[i] else "down"}
                                  for i in shifted],
        })
    return out


# ── 5. Report ───────────────────────────────────────────────────────────────
def real_counts() -> Counter:
    """(year, category) -> real narratives, read from the README's recorded archive table."""
    text = PATHS["readme"].read_text(encoding="utf-8")
    block = text.split("Available narratives", 1)[1].split("Share of each year", 1)[0]
    years = None
    counts: Counter = Counter()
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] == "Category":
            years = cells[1:7]
        elif years and cells[0] in fetch_sample.CATEGORIES:
            for y, c in zip(years, cells[1:7]):
                counts[(y, cells[0])] = int(c.replace(",", ""))
    return counts


def class_shares(counts: Counter) -> dict[str, dict[str, float]]:
    """year -> category -> share of that year (same arithmetic as fetch_sample.shares)."""
    out = {}
    for y in (str(y) for y in fetch_sample.YEARS):
        total = sum(counts[(y, c)] for c in fetch_sample.CATEGORIES)
        out[y] = {c: counts[(y, c)] / total for c in fetch_sample.CATEGORIES}
    return out


def production_model() -> dict:
    reg = json.loads(PATHS["registry"].read_text(encoding="utf-8"))["versions"]
    version = next(v for v, info in reg.items() if info["stage"] == "production")
    return json.loads((ML_DIR / reg[version]["path"]).read_text(encoding="utf-8"))


def build_report(rows: list[dict], generated_by: str) -> dict:
    splits = train.split_by_time(rows)
    model = production_model()
    sample = Counter((r["date"][:4], r["product"]) for r in rows)
    return {"generated_by": generated_by,
            "settings": {"alert_at": ALERT_AT, "epsilon": EPSILON, "min_n": MIN_N,
                         "top_k": TOP_K, "model_version": model["version"]},
            "months": monthly_drift(splits["stream"], model, splits["train"]),
            # The sample is stratified, so its class mix barely moves (README); production's did.
            "real_class_shares": class_shares(real_counts()),
            "sample_class_shares": class_shares(sample)}


# ── 6. Entry point ──────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="PSI drift of the 2023-2024 stream against training.")
    ap.add_argument("--write-reports", action="store_true", help="also write ml/reports/drift.json")
    args = ap.parse_args(argv)
    rows = train.load_complaints(train.PATHS["complaints"])
    report = build_report(rows, " ".join(["python monitor.py", *argv]))
    print(f"{'month':<9}{'n':>4}{'psi_pred':>10}{'psi_words':>11}  alert  top shifted words")
    for m in report["months"]:
        top = ", ".join(f"{w['word']}{'+' if w['direction'] == 'up' else '-'}" for w in m["top_shifted_words"])
        print(f"{m['month']:<9}{m['n']:>4}{m['psi_predictions']:>10.4f}{m['psi_words']:>11.4f}  "
              f"{'ALERT' if m['alert'] else '-':<6} {top}")
    if args.write_reports:
        path = PATHS["reports"] / "drift.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
