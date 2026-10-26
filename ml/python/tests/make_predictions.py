#!/usr/bin/env python3
"""Write ml/data/fixtures/predictions.json: 30 inputs and what train.score gives on v1.

The Go server and the FastAPI server must return the same label, scores (to 1e-9) and
low_confidence for every input. Run from ml/python after training v1:
    python tests/make_predictions.py
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import train  # noqa: E402

ML = Path(__file__).resolve().parent.parent.parent
OUT = ML / "data" / "fixtures" / "predictions.json"

LONG = ("They keep reporting a late payment on my credit report that is not mine. " * 400)[:25000]

EDGE = [
    ("empty", ""),
    ("whitespace only", "   \n\t "),
    ("all unknown words", "zzqx blorptag frobnicatory quuxle"),
    ("scrubbed placeholders only", "XXXX XX/XX/XXXX XXXX xx"),
    ("25,000 characters (servers reject over 20,000; scoring has no limit)", LONG),
    ("non-ASCII, Spanish", "Mi tarjeta de crédito fue cobrada dos veces — ¿por qué?"),
    ("non-ASCII, Turkish dotted I and emoji", "İnternet banking locked my account 😡 since Monday"),
    ("digits and amounts", "Charged {$250.00} twice on XX/XX/2022, fee 35, account 12345"),
    ("case and apostrophes", "I DON'T owe this DEBT; it's NOT mine!!! 'Collector' keeps calling"),
    ("one word: mortgage", "mortgage"),
    ("one word: overdraft", "overdraft"),
    ("one word: zelle", "Zelle"),
    ("one known word among unknown", "zzqx blorptag escrow"),
    ("vague, likely low confidence", "I called them and nobody helped me."),
    ("single letter x is a token", "x"),
]


def main() -> None:
    v1 = json.loads((ML / "models" / "v1.json").read_text(encoding="utf-8"))
    test = train.split_by_time(train.load_complaints(train.PATHS["complaints"]))["test"]
    short = sorted((r for r in test if len(r["narrative"]) <= 300), key=lambda r: r["id"])
    real = random.Random(train.SEED).sample(short, 30 - len(EDGE))
    cases = [{"name": name, "input": text} for name, text in EDGE]
    cases += [{"name": f"test split complaint {r['id']} ({r['product']})", "input": r["narrative"]}
              for r in real]
    for c in cases:
        c["expected"] = train.score(v1, c["input"])
    OUT.write_text(json.dumps({
        "generated_by": "python tests/make_predictions.py",
        "model": "ml/models/v1.json",
        "note": "expected = train.score(v1, input). Go and FastAPI must match: same label and "
                "low_confidence, every score within 1e-9.",
        "cases": cases}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(cases)} cases)")


if __name__ == "__main__":
    main()
