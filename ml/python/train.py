#!/usr/bin/env python3
"""
train — a teaching-sized text classifier for CFPB consumer complaints.

The task: read a complaint narrative and name the product it is about (credit
reporting, debt collection, ...). This file is everything from the data to the
exported model: splits by time, our own tokenizer and TF-IDF, three models,
metrics, and a JSON export that Go and the browser can read.

Run:
    python train.py            # sections 4-7 arrive in later lessons
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import re
import scipy.sparse as sp

# ── 1. Configuration ────────────────────────────────────────────────────────
SEED = 7                             # every random choice in the lab uses this seed

ML_DIR = Path(__file__).resolve().parent.parent   # resolved from this file, not the working directory
PATHS = {
    "complaints": ML_DIR / "data" / "complaints.jsonl",
    "models": ML_DIR / "models",
    "reports": ML_DIR / "reports",
}

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


# ── 2. Data and time-based splits ───────────────────────────────────────────
def load_complaints(path: str | Path) -> list[dict]:
    """Read one complaint per line."""
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def split_by_time(rows: list[dict]) -> dict[str, list[dict]]:
    """Train on the past, test on the next year, keep the rest as the live stream.
    A random split would let the model peek at the future; this one cannot."""
    out: dict[str, list[dict]] = {name: [] for name in SPLITS}
    for r in rows:
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
        # Keep the `limit` terms with the highest total count. scikit-learn does exactly this
        # call on the alphabetical column order, so ties break the same way.
        counts_arr = np.array([tf[t] for t in terms])
        keep = (-counts_arr).argsort()[:limit]
        terms = sorted(terms[i] for i in keep)
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

# ── 5. Metrics ──────────────────────────────────────────────────────────────

# ── 6. Export ───────────────────────────────────────────────────────────────

# ── 7. Entry point ──────────────────────────────────────────────────────────
