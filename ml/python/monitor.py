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

Two signals per stream month (and per quarter), with the production model, against a REFERENCE:
  (a) predicted-category mix: what v1 says the period's complaints are about;
  (b) input words: which top-K training words the text uses, plus an "other" bucket.

The reference is the 2022 test split: data v1 did not train on, the year before deployment.
Scoring the training rows themselves would be in-sample (v1 is 90% right there, 77-79% on unseen
months), which makes every unseen month look drifted; that variant is kept in drift.json only as
`reference_training_in_sample`, to show why it misleads.

0.2 is an industry convention for coarse, decile-style bins, not a law. So each signal also gets
a null (what PSI looks like with no drift), two ways:
  - leave-out, stratified like the sample: each draw picks one 2022 month (or quarter) at random
    and draws, WITHOUT replacement from the 2022 reference, as many complaints of each category as
    that period has, then compares them with the remaining rows (the stream month is independent
    of the reference, so must the sample be). The sample's months are quota-stratified by
    category (ml/data/README.md, step 3), so a real month's class mix is nearly fixed; a simple
    random draw of 50 would add class-mix variance real months never have. A noise model must
    match how the data was sampled.
  - within-2022: each 2022 month / quarter against the rest of 2022, which includes real
    month-to-month variation that the draws miss.
The calibrated alert asks for PSI above max(leave-out p95, largest within-2022 value), per signal
and period length. The unstratified draws (simple random samples of n) are kept in drift.json as
`null_unstratified`, for comparison only: that is the noise an unstratified production stream of
50 complaints would see, and it pushes the predictions PSI past 0.2 in about 15% of months, while
the stratified draws do in about 1.4%. Expected chance alerts: the p95 is exceeded 5% of the time by
construction, so 24 months x 5% = at most 1.2 per signal (at most 2.4 for either of the two: the
union bound, whatever their correlation); 8 quarters x 5% = at most 0.4 per signal (at most 0.8).
The within-2022 maximum makes the real rate lower. A count above that is something to
investigate, not proof of drift.

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

import numpy as np

import train

ML_DIR = train.ML_DIR
sys.path.insert(0, str(ML_DIR / "data"))      # for fetch_sample (offline: we only use its shares())
import fetch_sample                            # noqa: E402

# ── 1. Configuration ────────────────────────────────────────────────────────
EPSILON = 1e-4          # added to every proportion before the log
ALERT_AT = 0.2          # the conventional PSI alert level
MIN_N = 30              # a period with fewer complaints is shown but never alerted
# K = 50: with stop words out, the top 50 words hold 23% of the remaining training tokens (the
# smallest bucket 0.25%); a month of 50 complaints has about 5,000 such tokens, enough to fill
# 51 buckets. Chosen for readability, not tuned to alerts. (Before the stop-word list the top 50
# held 46.8%; those were function words.)
TOP_K = 50
SHOWN_WORDS = 5         # words listed per period in top_shifted_words
DRAWS = 5000            # bootstrap draws per null band
# Common English function words, left out of the word buckets (drift only; the model's tokenizer
# is unchanged). "not" and "no" stay: they carry meaning here.
STOP_WORDS = frozenset("""a about after all also am an and any are as at be been but by can did do
does for from had has have he her his i if in into is it its me my of on or our out she so than
that the their them then there they this to up us was we were what when which who will with would
you your""".split())
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
    """The k most frequent training words the model knows, stop words excluded (ties alphabetical)."""
    known = set(model_json["vocabulary"]) - STOP_WORDS
    freq = Counter(t for r in train_rows for t in train.tokenize(r["narrative"], model_json["tokenizer"])
                   if t in known)
    return [t for t, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def featurize(rows: list[dict], words: list[str], model_json: dict, index: dict):
    """Per complaint: a one-hot predicted category, and token counts per word bucket
    (the top words, then "other"; stop words are not counted). Two integer matrices."""
    slot = {w: i for i, w in enumerate(words)}
    classes = {c: i for i, c in enumerate(model_json["classes"])}
    labels = np.zeros((len(rows), len(classes)), dtype=np.int64)
    counts = np.zeros((len(rows), len(words) + 1), dtype=np.int64)
    for i, r in enumerate(rows):
        labels[i, classes[train.score(model_json, r["narrative"], index)["label"]]] = 1
        for t in train.tokenize(r["narrative"], model_json["tokenizer"]):
            if t not in STOP_WORDS:
                counts[i, slot.get(t, len(words))] += 1
    return labels, counts


def _psi_vs_rest(m, mask) -> float:
    return psi((m.sum(axis=0) - m[mask].sum(axis=0)).tolist(), m[mask].sum(axis=0).tolist())


def _leave_out_values(m, chosen) -> list[float]:
    """PSI of each drawn sample (rows of `chosen`: summed counts) against the rows left over."""
    total = m.sum(axis=0)
    return [psi((total - c).tolist(), c.tolist()) for c in chosen]


def _summary(values: list[float]) -> dict:
    p50, p95 = np.percentile(values, [50, 95])
    return {"p50": float(p50), "p95": float(p95),
            "share_at_or_above_alert_at": sum(v >= ALERT_AT for v in values) / len(values)}


def _scale_counts(counts, size: int) -> list[int]:
    """`counts` rescaled to add up to `size`, by largest remainder (ties to the earlier category).
    Only for a period size the reference periods do not have (the report's are all 50 / 150)."""
    exact = [c * size / sum(counts) for c in counts]
    out = [math.floor(x) for x in exact]
    for i in sorted(range(len(exact)), key=lambda i: (out[i] - exact[i], i))[:size - sum(out)]:
        out[i] += 1
    return out


def stratified_picks(categories: list[str], periods, size: int, draws: int,
                     rng: np.random.Generator):
    """A (draws x rows) boolean mask. Each draw picks one reference period at random and takes, without
    replacement from the whole reference, as many rows of each category as that period has: a draw
    matches the sample's design (every period's class mix is fixed by quota, README step 3). For a
    size the periods do not have, that period's counts are rescaled to `size`."""
    keys = sorted(set(periods))
    cats = sorted(set(categories))
    cat = np.array([cats.index(c) for c in categories])
    need = np.array([[int(np.sum((periods == p) & (cat == j))) for j in range(len(cats))] for p in keys])
    if (need.sum(axis=1) != size).any():
        need = np.array([_scale_counts(row, size) for row in need])
    choice = rng.integers(len(keys), size=draws)
    noise = rng.random((draws, len(categories)))
    rank = np.empty((draws, len(categories)), dtype=np.int64)
    for j in range(len(cats)):
        cols = np.flatnonzero(cat == j)
        rank[:, cols] = np.argsort(np.argsort(noise[:, cols], axis=1), axis=1)
    return rank < need[choice][:, cat]


def null_band(ref: tuple, dates: list[str], categories: list[str], size: int, unit: str,
              draws: int = DRAWS, seed: int = train.SEED) -> dict:
    """What PSI looks like with NO drift, per signal, two ways (see the docstring).
    leave-out: `draws` stratified samples (one 2022 period's category counts, drawn without replacement
    from the reference) vs the remaining rows (p50, p95, share at or above 0.2). within_2022: each
    2022 period vs the rest of 2022 (values and max). threshold = max(p95, within-2022 max)."""
    rng = np.random.default_rng(seed)
    periods = np.array([_period(d, unit) for d in dates])
    picks = stratified_picks(categories, periods, size, draws, rng).astype(np.int64)
    out = {"n": size, "draws": draws, "seed": seed}
    for name, m in (("predictions", ref[0]), ("words", ref[1])):
        band = _summary(_leave_out_values(m, picks @ m))
        within = {p: _psi_vs_rest(m, periods == p) for p in sorted(set(periods))}
        out[name] = {**band, "within_2022": within, "within_2022_max": max(within.values()),
                     "threshold": max(band["p95"], max(within.values()))}
    return out


def null_band_unstratified(ref: tuple, n_rows: int, size: int, draws: int = DRAWS,
                           seed: int = train.SEED) -> dict:
    """For comparison only: simple random samples of `size` (no regard to category) vs the rest.
    This is the noise an unstratified stream would see; it does not match this sample's design."""
    rng = np.random.default_rng(seed)
    picks = np.vstack([np.argsort(rng.random((250, n_rows)), axis=1)[:, :size]
                       for _ in range(draws // 250)])
    out = {"n": size, "draws": draws, "seed": seed}
    for name, m in (("predictions", ref[0]), ("words", ref[1])):
        out[name] = _summary(_leave_out_values(m, np.stack([m[p].sum(axis=0) for p in picks])))
    return out


# ── 4. Monthly and quarterly drift ──────────────────────────────────────────
def _period(date: str, unit: str) -> str:
    return date[:7] if unit == "month" else f"{date[:4]}-Q{(int(date[5:7]) - 1) // 3 + 1}"


def drift(stream_rows: list[dict], model_json: dict, *, reference_rows: list[dict],
          unit: str = "month", train_rows: list[dict] | None = None,
          nulls: dict | None = None, calibrate: bool = True) -> list[dict]:
    """One dict per period of the stream: n, psi_predictions, psi_words, alert_convention
    (PSI >= 0.2, n >= 30), alert_calibrated (PSI above that signal's null threshold, n >= 30) and
    top_shifted_words. Words are chosen from `train_rows` (default: the reference). `nulls` maps
    (unit, n) to a ready null band; `calibrate=False` skips the null (alert_calibrated is then False)."""
    index = {t: i for i, t in enumerate(model_json["vocabulary"])}
    words = top_words(train_rows or reference_rows, model_json)
    ref = featurize(reference_rows, words, model_json, index)
    exp_pred, exp_words = ref[0].sum(axis=0).tolist(), ref[1].sum(axis=0).tolist()
    e_prop = proportions(exp_words)
    groups: dict[str, list[dict]] = {}
    for r in stream_rows:
        groups.setdefault(_period(r["date"], unit), []).append(r)
    nulls = {} if nulls is None else dict(nulls)
    dates = [r["date"] for r in reference_rows]
    categories = [r["product"] for r in reference_rows]
    out = []
    for key in sorted(groups):
        rows = groups[key]
        n = len(rows)
        labels, counts = featurize(rows, words, model_json, index)
        act_pred, act_words = labels.sum(axis=0).tolist(), counts.sum(axis=0).tolist()
        parts = contributions(exp_words, act_words)
        a_prop = proportions(act_words)
        shifted = sorted(range(len(words)), key=lambda i: (-parts[i], words[i]))[:SHOWN_WORDS]
        p_pred, p_words = psi(exp_pred, act_pred), sum(parts)
        big = n >= MIN_N
        if big and calibrate and (unit, n) not in nulls:
            nulls[(unit, n)] = null_band(ref, dates, categories, n, unit)
        null = nulls.get((unit, n))
        out.append({
            "period": key, "n": n, "psi_predictions": p_pred, "psi_words": p_words,
            "alert_convention": big and (p_pred >= ALERT_AT or p_words >= ALERT_AT),
            "alert_calibrated": bool(big and calibrate and (p_pred > null["predictions"]["threshold"]
                                                            or p_words > null["words"]["threshold"])),
            "top_shifted_words": [{"word": words[i], "contribution": parts[i],
                                   "direction": "up" if a_prop[i] > e_prop[i] else "down"}
                                  for i in shifted],
        })
    return out


def monthly_drift(stream_rows, model_json, *, reference_rows, train_rows=None, **kw) -> list[dict]:
    return [{"month": p.pop("period"), **p} for p in
            drift(stream_rows, model_json, reference_rows=reference_rows, unit="month",
                  train_rows=train_rows, **kw)]


def quarterly_drift(stream_rows, model_json, *, reference_rows, train_rows=None, **kw) -> list[dict]:
    return [{"quarter": p.pop("period"), **p} for p in
            drift(stream_rows, model_json, reference_rows=reference_rows, unit="quarter",
                  train_rows=train_rows, **kw)]


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
            years = cells[1:1 + len(fetch_sample.YEARS)]
        elif years and cells[0] in fetch_sample.CATEGORIES:
            for y, c in zip(years, cells[1:]):
                counts[(y, cells[0])] = int(c.replace(",", ""))
    if len(counts) != len(fetch_sample.CATEGORIES) * len(fetch_sample.YEARS):
        raise ValueError(f"README count table incomplete: {len(counts)} cells")
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
    if model["trained_on"]["to"] >= train.SPLITS["test"][0]:     # the reference must be unseen data
        raise ValueError(f"{model['version']} trained on data up to {model['trained_on']['to']}, so the "
                         f"2022 reference is in-sample for it; monitor a model trained before 2022")
    index = {t: i for i, t in enumerate(model["vocabulary"])}
    words = top_words(splits["train"], model)
    ref = featurize(splits["test"], words, model, index)
    sample = Counter((r["date"][:4], r["product"]) for r in rows)
    in_sample = [{k: m[k] for k in ("month", "n", "psi_predictions", "psi_words", "alert_convention")}
                 for m in monthly_drift(splits["stream"], model, reference_rows=splits["train"],
                                        calibrate=False)]
    dates = [r["date"] for r in splits["test"]]
    categories = [r["product"] for r in splits["test"]]
    nulls = {("month", 50): null_band(ref, dates, categories, 50, "month"),
             ("quarter", 150): null_band(ref, dates, categories, 150, "quarter")}
    kw = dict(reference_rows=splits["test"], train_rows=splits["train"])
    return {"generated_by": generated_by,
            "settings": {"alert_at": ALERT_AT, "epsilon": EPSILON, "min_n": MIN_N,
                         "top_k": TOP_K, "model_version": model["version"],
                         "reference": f"2022 test split ({model['version']} predictions and tokens); words chosen "
                                      "from the training split, stop words excluded",
                         "stop_words": sorted(STOP_WORDS)},
            "null": {"reference": "2022 test split. leave-out, stratified like the sample: each "
                                  "draw picks one 2022 period at random and draws, without "
                                  "replacement, as many complaints of each category as that period "
                                  "has, vs the remaining rows; within_2022: each 2022 period vs "
                                  "the rest of 2022. threshold = max of p95 and the within-2022 "
                                  "maximum",
                     "expected_chance_alerts": {
                         "note": "p95 is exceeded 5% of the time by construction; the within-2022 "
                                 "maximum makes the real rate lower. either_signal_max is the union "
                                 "bound (twice per_signal), true however the two signals correlate. "
                                 "These are averages under the null (upper bounds); a count above "
                                 "them is something to investigate, not proof of drift.",
                         "month": {"per_signal": round(24 * 0.05, 9), "either_signal_max": round(2 * 24 * 0.05, 9)},
                         "quarter": {"per_signal": round(8 * 0.05, 9), "either_signal_max": round(2 * 8 * 0.05, 9)}},
                     "month": nulls[("month", 50)], "quarter": nulls[("quarter", 150)]},
            "null_unstratified": {
                "note": "For comparison only, not used for alerts: simple random samples of n from "
                        "the 2022 reference, with no regard to category, vs the remaining rows. "
                        "This is the noise an unstratified production stream would see; the "
                        "sample's periods are quota-stratified by category, so it overstates "
                        "this sample's noise.",
                "month": null_band_unstratified(ref, len(dates), 50),
                "quarter": null_band_unstratified(ref, len(dates), 150)},
            "months": monthly_drift(splits["stream"], model, nulls=nulls, **kw),
            "quarters": quarterly_drift(splits["stream"], model, nulls=nulls, **kw),
            "reference_training_in_sample": {
                "note": f"{model['version']} scoring its own training rows (in-sample) as the reference; unseen "
                        "months then look drifted. Shown to explain why it misleads.",
                "months": in_sample},
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
    for unit, key in (("month", "months"), ("quarter", "quarters")):
        null = report["null"][unit]
        print(f"null threshold at n={null['n']}: predictions {null['predictions']['threshold']:.4f}, "
              f"words {null['words']['threshold']:.4f}")
        print(f"{unit:<9}{'n':>4}{'psi_pred':>10}{'psi_words':>11}  conv  calib  top shifted words")
        for m in report[key]:
            top = ", ".join(f"{w['word']}{'+' if w['direction'] == 'up' else '-'}"
                            for w in m["top_shifted_words"])
            print(f"{m[unit]:<9}{m['n']:>4}{m['psi_predictions']:>10.4f}{m['psi_words']:>11.4f}  "
                  f"{'YES' if m['alert_convention'] else '-':<6}{'YES' if m['alert_calibrated'] else '-':<7}{top}")
    if args.write_reports:
        path = PATHS["reports"] / "drift.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
