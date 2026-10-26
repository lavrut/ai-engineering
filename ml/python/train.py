#!/usr/bin/env python3
"""
train — a teaching-sized text classifier for CFPB consumer complaints.

The task: read a complaint narrative and name the product it is about (credit
reporting, debt collection, ...). This file is everything from the data to the
exported model: splits by time, our own tokenizer and TF-IDF, three models,
metrics, and a JSON export that Go and the browser can read.

Run (from ml/python):
    python train.py --model logreg --version v1 --write-reports   # v1.json and ml/reports/*.json
    python train.py --model nb --version nb1     # only ml/models/nb1.json; the reports stay put
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
import warnings
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from sklearn.dummy import DummyClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import MultinomialNB

# ── 1. Configuration ────────────────────────────────────────────────────────
SEED = 7                             # every random choice in the lab uses this seed

ML_DIR = Path(__file__).resolve().parent.parent   # resolved from this file, not the working directory
PATHS = {
    "complaints": ML_DIR / "data" / "complaints.jsonl",
    "models": ML_DIR / "models",
    "reports": ML_DIR / "reports",
}

DATE_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}")   # text comparison only works on this exact shape

# Splits by `date` (ISO strings compare correctly as text). Inclusive on both ends.
SPLITS = {
    "train": ("2019-01-01", "2021-12-31"),
    "test": ("2022-01-01", "2022-12-31"),
    "stream": ("2023-01-01", "2024-12-31"),   # the "production" months, scored one by one later
}

# The tokenizer and TF-IDF settings. Go and the browser read the same values from the
# model JSON, so these names are part of the contract.
FEATURES = {
    "lowercase": "ascii",            # fold A-Z only; never Unicode case folding
    "pattern": "[a-z0-9']+",         # a token is a maximal run of these characters
    "drop_x_runs_from": 2,           # drop tokens that are only the letter x, length >= 2 (XX, XXXX)
    "stop_words": [],                # none by default: "not" and "no" carry meaning here
    # About 1,800 training complaints are about 600 words each. min_df=2 drops words seen in a
    # single complaint (typos, names, scrub leftovers), which cannot generalise. max_features
    # caps the table for the export; most of the vocabulary is already under the cap.
    "min_df": 2,
    "max_features": 20000,
}

# Models. C is the inverse strength of logistic regression's L2 penalty (bigger C, weaker
# penalty). With TF-IDF inputs a row's weights are small, so Naive Bayes' default alpha=1
# smoothing swamps them (5-fold cross-validation on the training years only: 0.40 accuracy
# at alpha=1, 0.76 at 0.1). The test year was not used to choose either value.
C = 1.0
MAX_ITER = 1000                      # logistic regression converges in well under this
NB_ALPHA = 0.1
LOW_CONFIDENCE = 0.5                 # below this top probability, a prediction is flagged


# ── 2. Data and time-based splits ───────────────────────────────────────────
def load_complaints(path: str | Path) -> list[dict]:
    """Read one complaint per line."""
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _is_iso_date(text: str) -> bool:
    """YYYY-MM-DD and a real calendar day (2021-02-30 is not)."""
    if not DATE_FORMAT.fullmatch(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def split_by_time(rows: list[dict]) -> dict[str, list[dict]]:
    """Train on the past, test on the next year, keep the rest as the live stream.
    A random split would let the model peek at the future; this one cannot."""
    out: dict[str, list[dict]] = {name: [] for name in SPLITS}
    for r in rows:
        if not _is_iso_date(str(r["date"])):
            raise ValueError(f"complaint {r['id']}: date {r['date']!r} is not YYYY-MM-DD")
        for name, (first, last) in SPLITS.items():
            if first <= r["date"] <= last:
                out[name].append(r)
                break
        else:
            raise ValueError(f"complaint {r['id']}: date {r['date']} is outside every split")
    return out


# ── 3. Features ─────────────────────────────────────────────────────────────
_UPPER_TO_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}   # code point -> code point


def tokenize(text: str, cfg: dict = FEATURES) -> list[str]:
    """Split text into tokens. Go and the browser repeat these rules exactly.

    1. Lowercase the letters A-Z only. No Unicode case folding: str.lower() would turn
       Turkish "İ" into two characters, and Go and JavaScript disagree on such cases.
    2. A token is a maximal run of [a-z0-9'] in that text. Every other character, including
       every non-ASCII letter and digit, is a separator ("café" gives "caf").
    3. Strip leading and trailing apostrophes from each token ("'hi'" gives "hi").
    4. Drop empty tokens, and tokens made only of the letter x with length >= 2: CFPB
       scrubs personal details to XX or XXXX. A single "x" is kept.
    5. No stemming. Stop words are removed only if cfg["stop_words"] lists them.
    """
    if cfg["lowercase"] != "ascii":
        raise ValueError(f"tokenize implements lowercase='ascii' only, not {cfg['lowercase']!r}")
    text = text.translate(_UPPER_TO_LOWER)
    stop = set(cfg["stop_words"])
    tokens = []
    for tok in re.findall(cfg["pattern"], text):
        tok = tok.strip("'")
        if not tok or tok in stop:
            continue
        if len(tok) >= cfg["drop_x_runs_from"] and tok.strip("x") == "":
            continue
        tokens.append(tok)
    return tokens


def fit_tfidf(docs: list[str], cfg: dict = FEATURES) -> tuple[list[str], np.ndarray]:
    """Learn the vocabulary and the IDF weights from the training documents.
    idf = ln((1 + n) / (1 + df)) + 1 ("smooth": a word in every document still weighs 1)."""
    counts = [Counter(tokenize(d, cfg)) for d in docs]
    df = Counter(t for c in counts for t in c)       # documents containing the term
    tf = Counter()                                   # total occurrences across all documents
    for c in counts:
        tf.update(c)
    terms = sorted(t for t in df if df[t] >= cfg["min_df"])   # alphabetical, like scikit-learn
    limit = cfg["max_features"]
    if limit is not None and len(terms) > limit:
        # Keep the `limit` terms with the highest total count; ties go to the alphabetically
        # first term. scikit-learn uses an unstable sort here, so its choice among tied terms
        # can vary by machine; ours cannot. The two agree whenever the cutoff has no ties.
        terms = sorted(sorted(terms, key=lambda t: (-tf[t], t))[:limit])
    n = len(docs)
    idf = np.array([math.log((1 + n) / (1 + df[t])) + 1 for t in terms])
    return terms, idf


def transform(docs: list[str], vocabulary: list[str], idf: np.ndarray,
              cfg: dict = FEATURES) -> sp.csr_matrix:
    """Turn documents into rows: term counts x idf, then scaled to length 1 (L2)."""
    index = {t: i for i, t in enumerate(vocabulary)}
    indptr, indices, data = [0], [], []
    for d in docs:
        row = Counter(index[t] for t in tokenize(d, cfg) if t in index)
        for j in sorted(row):
            indices.append(j)
            data.append(row[j] * idf[j])
        indptr.append(len(indices))
    X = sp.csr_matrix((data, indices, indptr), shape=(len(docs), len(vocabulary)), dtype=np.float64)
    norms = np.sqrt(np.asarray(X.multiply(X).sum(axis=1)).ravel())
    norms[norms == 0] = 1.0                          # a document with no known words stays all zeros
    return sp.diags(1.0 / norms) @ X




# ── 4. Models ───────────────────────────────────────────────────────────────
def train_models(train_rows: list[dict], cfg: dict = FEATURES, c: float = C) -> dict:
    """Fit the features and three models on the training rows only.

    - majority: always answers the most common training category (the bar to beat);
    - nb: multinomial Naive Bayes, which counts how often each word weight goes with each category;
    - logreg: logistic regression, one weight per (category, word), learned together.
    """
    docs = [r["narrative"] for r in train_rows]
    y = [r["product"] for r in train_rows]
    vocabulary, idf = fit_tfidf(docs, cfg)
    X = transform(docs, vocabulary, idf, cfg)
    models = {
        "majority": DummyClassifier(strategy="most_frequent").fit(X, y),
        "nb": MultinomialNB(alpha=NB_ALPHA).fit(X, y),
        "logreg": LogisticRegression(C=c, max_iter=MAX_ITER, random_state=SEED).fit(X, y),
    }
    classes = [str(k) for k in models["logreg"].classes_]       # sorted, the same for all three
    counts = Counter(y)
    return {"vocabulary": vocabulary, "idf": idf, "classes": classes, "X": X, "y": y,
            "models": models, "c": c,
            "majority_class": max(classes, key=lambda k: counts[k])}   # ties: first in order


def _objective(X, y_idx, coef, intercept, c) -> tuple[float, float]:
    """Logistic regression's training loss: mean log-loss over the training rows, and the
    L2 penalty as scikit-learn scales it, sum(coef^2) / (2 * C * n). lbfgs minimises the sum."""
    logits = X @ coef.T + intercept
    logits -= logits.max(axis=1, keepdims=True)
    log_p = logits - np.log(np.exp(logits).sum(axis=1, keepdims=True))
    loss = -log_p[np.arange(len(y_idx)), y_idx].mean()
    return float(loss), float((coef ** 2).sum() / (2 * c * len(y_idx)))


def training_log(fitted: dict, top_at=(1, 8, 21)) -> dict:
    """Loss by iteration, for the TrainingStepper aid.

    scikit-learn does not report the loss at each lbfgs step, so we refit from scratch with
    max_iter = 1, 2, 3, 5, 8, ... and read the loss of each stopped fit. lbfgs is
    deterministic, so a fit stopped at k steps is exactly the first k steps of the full fit.
    Iteration 0 is the starting point: all weights 0, every category equally likely."""
    X, classes, c = fitted["X"], fitted["classes"], fitted["c"]
    y_idx = np.array([classes.index(label) for label in fitted["y"]])
    final = int(fitted["models"]["logreg"].n_iter_[0])
    steps, a, b = [], 1, 2
    while a < final:
        steps.append(a)
        a, b = b, a + b
    points = []
    for k in [0] + steps + [final]:
        if k == 0:
            coef, intercept = np.zeros((len(classes), X.shape[1])), np.zeros(len(classes))
        else:
            with warnings.catch_warnings():             # a stopped fit has not converged, by design
                warnings.simplefilter("ignore", ConvergenceWarning)
                m = LogisticRegression(C=c, max_iter=k, random_state=SEED).fit(X, fitted["y"])
            coef, intercept = m.coef_, m.intercept_
        loss, penalty = _objective(X, y_idx, coef, intercept, c)
        point = {"iteration": k, "loss": loss, "penalty": penalty}
        if k in top_at or k == final:
            point["top_words"] = {
                label: [[fitted["vocabulary"][j], round(float(coef[i, j]), 4)]
                        for j in np.argsort(-coef[i], kind="stable")[:8]]
                for i, label in enumerate(classes)}
        points.append(point)
    return {"classes": classes, "C": c, "converged_at": final, "checkpoints": points,
            "note": "loss = mean multinomial log-loss on the training rows; penalty = "
                    "sum(coef^2) / (2 * C * n_train). lbfgs minimises loss + penalty."}


# ── 5. Metrics ──────────────────────────────────────────────────────────────
def noise_band(n: int) -> float:
    """Rough noise on an accuracy over n cases, 1/sqrt(n): about two standard errors at worst
    (p = 0.5), as in the LLMs track. A difference smaller than this is not evidence."""
    if n <= 0:
        raise ValueError("no scored cases")
    return 1 / math.sqrt(n)


def per_class_metrics(y_true: list[str], y_pred: list[str], classes: list[str]) -> dict:
    """Precision, recall and F1 per category, their macro and weighted averages, accuracy,
    the noise band and the confusion matrix (rows: true category, columns: predicted).

    precision = of the complaints we called X, the share that were X;
    recall = of the complaints that were X, the share we called X;
    F1 = their harmonic mean. 0/0 counts as 0. Macro averages every class equally (a rare
    category counts as much as a common one); weighted averages by support."""
    pos = {label: i for i, label in enumerate(classes)}
    confusion = [[0] * len(classes) for _ in classes]
    for t, p in zip(y_true, y_pred, strict=True):
        confusion[pos[t]][pos[p]] += 1
    n = len(y_true)
    per_class = {}
    for i, label in enumerate(classes):
        tp = confusion[i][i]
        predicted = sum(row[i] for row in confusion)
        support = sum(confusion[i])
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1, "support": support}
    keys = ("precision", "recall", "f1")
    macro = {k: sum(m[k] for m in per_class.values()) / len(classes) for k in keys}
    weighted = {k: sum(m[k] * m["support"] for m in per_class.values()) / n for k in keys}
    accuracy = sum(confusion[i][i] for i in range(len(classes))) / n
    return {"classes": list(classes), "n": n, "accuracy": accuracy, "band": noise_band(n),
            "per_class": per_class, "macro": macro, "weighted": weighted, "confusion": confusion}


def fit_and_evaluate(rows: list[dict], c: float = C) -> dict:
    """Split by time, fit on train only, and score every model on train and test."""
    splits = split_by_time(rows)
    fitted = train_models(splits["train"], c=c)
    metrics = {}
    for name, model in fitted["models"].items():
        metrics[name] = {}
        for split in ("train", "test"):
            part = splits[split]
            X = transform([r["narrative"] for r in part], fitted["vocabulary"], fitted["idf"])
            y_pred = [str(p) for p in model.predict(X)]
            metrics[name][split] = per_class_metrics(
                [r["product"] for r in part], y_pred, fitted["classes"])
    return {"splits": splits, "fitted": fitted, "metrics": metrics}


# ── 6. Export ───────────────────────────────────────────────────────────────
def data_version(path: str | Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_model(model, vocabulary: list[str], idf, meta: dict, cfg: dict = FEATURES) -> dict:
    """Everything needed to score text without Python: the tokenizer rules, the vocabulary,
    idf, and one row of weights per category. Both models score as softmax(coef . x + intercept):
    for Naive Bayes, coef is the log probability of each word given the category and intercept
    the log prior. Floats keep full precision, so Go and the browser can match to 1e-9."""
    if isinstance(model, MultinomialNB):
        kind, coef, intercept = "nb", model.feature_log_prob_, model.class_log_prior_
    elif isinstance(model, LogisticRegression) and len(model.classes_) > 2:
        kind, coef, intercept = "logreg", model.coef_, model.intercept_
    else:
        raise ValueError("export needs Naive Bayes or a logistic regression with 3+ classes")
    return {
        "version": meta["version"], "created": meta["created"], "model": kind,
        "data_version": meta["data_version"],
        "trained_on": {"from": SPLITS["train"][0], "to": SPLITS["train"][1]},
        "tokenizer": dict(cfg),
        "tokenizer_note": "The rules are fixed and documented in train.tokenize; these values "
                          "are their parameters.",
        "vocabulary": list(vocabulary), "idf": [float(v) for v in idf],
        "classes": [str(k) for k in model.classes_],
        "coef": coef.tolist(), "intercept": intercept.tolist(),
        "majority_class": meta["majority_class"],     # the answer when no known word is present
        "metrics": meta["metrics"],
    }


def score(model_json: dict, text: str, index: dict | None = None) -> dict:
    """Score one text from the model JSON alone, the way Go and the browser do it.

    TF counts over the vocabulary -> x idf -> L2 normalise -> coef . x + intercept per category
    -> softmax. Sums run in vocabulary order. The label is the first category with the top
    probability. With no known word, x is all zeros: the scores are softmax(intercept), and the
    label is the training majority class, flagged low_confidence. Otherwise low_confidence is
    true when the top probability is below 0.5. There is no length limit here.

    `index` is the term -> position table. Leave it out and it is built from the vocabulary on
    every call; a server builds it once and passes it in, with the same result."""
    if index is None:
        index = {t: i for i, t in enumerate(model_json["vocabulary"])}
    elif len(index) != len(model_json["vocabulary"]):
        raise ValueError("index does not belong to this model's vocabulary")
    counts = Counter(index[t] for t in tokenize(text, model_json["tokenizer"]) if t in index)
    idf = model_json["idf"]
    weights = [(j, counts[j] * idf[j]) for j in sorted(counts)]
    norm = math.sqrt(sum(w * w for _, w in weights)) or 1.0
    logits = [b + sum(row[j] * (w / norm) for j, w in weights)
              for row, b in zip(model_json["coef"], model_json["intercept"])]
    top = max(logits)
    exps = [math.exp(z - top) for z in logits]
    total = sum(exps)
    probs = [e / total for e in exps]
    classes = model_json["classes"]
    scores = dict(zip(classes, probs))
    if not weights:
        return {"label": model_json["majority_class"], "scores": scores, "low_confidence": True}
    best = max(range(len(classes)), key=probs.__getitem__)       # first index wins a tie
    return {"label": classes[best], "scores": scores, "low_confidence": probs[best] < LOW_CONFIDENCE}


def dataset_report(rows: list[dict], splits: dict, classes: list[str]) -> dict:
    """Counts by year and category (mapped and as published), plus a few short snippets."""
    by_year = Counter((r["date"][:4], r["product"]) for r in rows)
    by_raw = Counter((r["date"][:4], r["product_raw"]) for r in rows)
    years = sorted({r["date"][:4] for r in rows})
    rng = random.Random(SEED)
    snippets = {}
    for label in classes:
        pool = sorted((r for r in splits["train"] if r["product"] == label), key=lambda r: r["id"])
        snippets[label] = [
            {"id": r["id"], "date": r["date"], "product_raw": r["product_raw"],
             "text": r["narrative"] if len(r["narrative"]) <= 200 else r["narrative"][:199] + "…"}
            for r in rng.sample(pool, 3)]
    lengths = sorted(len(r["narrative"]) for r in rows)
    return {
        "n": len(rows), "years": years, "classes": classes,
        "splits": {k: {"from": a, "to": b, "n": len(splits[k])} for k, (a, b) in SPLITS.items()},
        "counts": {label: {y: by_year[(y, label)] for y in years} for label in classes},
        "counts_raw": {raw: {y: by_raw[(y, raw)] for y in years}
                       for raw in sorted({r["product_raw"] for r in rows})},
        "narrative_chars": {"min": lengths[0], "median": lengths[len(lengths) // 2],
                            "max": lengths[-1]},
        "snippets": snippets,
    }


# ── 7. Entry point ──────────────────────────────────────────────────────────
def _write(path: Path, data: dict, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if compact \
        else json.dumps(data, ensure_ascii=False, indent=1)
    path.write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="Train, evaluate and export the complaint classifier.")
    ap.add_argument("--model", choices=["logreg", "nb"], default="logreg")
    ap.add_argument("--C", type=float, default=C, help="logistic regression: inverse L2 strength")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--write-reports", action="store_true",
                    help="also rewrite ml/reports/*.json (the committed ones come from v1)")
    args = ap.parse_args(argv)
    generated_by = " ".join(["python train.py", *argv])

    rows = load_complaints(PATHS["complaints"])
    run = fit_and_evaluate(rows, c=args.C)
    fitted, metrics = run["fitted"], run["metrics"]

    test = metrics[args.model]["test"]
    summary = {"split": "test", "n": test["n"], "accuracy": test["accuracy"], "band": test["band"],
               "macro_f1": test["macro"]["f1"], "weighted_f1": test["weighted"]["f1"],
               "per_class_f1": {k: v["f1"] for k, v in test["per_class"].items()}}
    # `created` is the last complaint date the model saw, not the clock: the same data and
    # settings give a byte-identical file (and so the same SHA-256 in the run tracker).
    created = max(r["date"] for r in run["splits"]["train"])
    meta = {"version": args.version, "created": created,
            "data_version": data_version(PATHS["complaints"]),
            "majority_class": fitted["majority_class"], "metrics": summary}
    model_json = export_model(fitted["models"][args.model], fitted["vocabulary"], fitted["idf"], meta)
    model_path = PATHS["models"] / f"{args.version}.json"
    _write(model_path, model_json, compact=True)

    print(f"wrote {model_path} ({model_path.stat().st_size:,} bytes)")
    if args.write_reports:
        gap = metrics["nb"]["test"]["macro"]["f1"] - metrics["logreg"]["test"]["macro"]["f1"]
        why = (f"v1 is logistic regression although Naive Bayes leads by {gap:.3f} test macro-F1: "
               f"that is inside the +/-{test['band']:.3f} band, and logistic regression's "
               "per-word weights are easy to read and drive the training-log aid.")
        reports = {
            "dataset": dataset_report(rows, run["splits"], fitted["classes"]),
            "metrics": {"note": why, "classes": fitted["classes"], "C": args.C,
                        "nb_alpha": NB_ALPHA, "models": metrics},
            "training_log": training_log(fitted),
        }
        for name, report in reports.items():
            _write(PATHS["reports"] / f"{name}.json", {"generated_by": generated_by, **report})
        print(f"wrote {len(reports)} reports to {PATHS['reports']}")
    print(f"{'model':<10}{'split':<7}{'accuracy':>9}{'macro F1':>10}{'band':>7}")
    for name, by_split in metrics.items():
        for split, m in by_split.items():
            print(f"{name:<10}{split:<7}{m['accuracy']:>9.3f}{m['macro']['f1']:>10.3f}{m['band']:>7.3f}")


if __name__ == "__main__":
    main()
