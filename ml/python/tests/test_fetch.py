"""Tests for ml/data/fetch_sample.py. No network: they use fixtures/archive_rows.csv,
a few rows copied as published from a real CCDB archive export."""
import csv
import random
from collections import Counter
from pathlib import Path

import fetch_sample as fs

FIXTURE = Path(fs.__file__).resolve().parent / "fixtures" / "archive_rows.csv"
CONTRACT = {"id", "date", "product", "product_raw", "narrative"}


def fixture_rows() -> list[dict]:
    with FIXTURE.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_parse_rows_keeps_required_fields():
    raw = fixture_rows()
    lines = fs.parse_rows(raw)
    with_narrative = [r for r in raw if r["Consumer complaint narrative"].strip()]
    assert 0 < len(lines) <= len(with_narrative) < len(raw)  # blank narratives are dropped
    for line in lines:
        assert set(line) == CONTRACT                          # nothing else: no company, state, ZIP, tags
        assert line["id"].isdigit()
        assert len(line["date"]) == 10 and line["date"][4] == "-" and line["date"][7] == "-"
        assert line["narrative"].strip() == line["narrative"] and line["narrative"]
        assert line["product"] in fs.CATEGORIES
    # product_raw is exactly as published
    published = {r["Complaint ID"]: r["Product"] for r in raw}
    assert all(published[line["id"]] == line["product_raw"] for line in lines)


def test_map_product_merges_renamed_categories():
    mapping = {"Credit reporting, credit repair services, or other personal consumer reports":
               "Credit reporting or other personal consumer reports",
               "Credit reporting or other personal consumer reports":
               "Credit reporting or other personal consumer reports"}
    old, new = list(mapping)
    assert fs.map_product(old, mapping) == fs.map_product(new, mapping) == mapping[new]
    assert fs.map_product("Bank account or service of the moon", mapping) is None
    # The committed map does the same for a real rename.
    real = fs.load_category_map()
    assert fs.map_product(old, real) == fs.map_product(new, real)
    assert set(real.values()) - {None} == set(fs.CATEGORIES)
    assert fs.map_product("Credit card or prepaid card", real, "Gift card") is None      # became "Prepaid card"
    assert fs.map_product("Credit card or prepaid card", real, "Store credit card") == "Credit card"


def synthetic_rows(n_per_month: int = 40) -> list[dict]:
    """Two years, uneven months and categories, in a shuffled order."""
    rng = random.Random(1)
    rows, i = [], 0
    for year in (2019, 2020):
        for month in range(1, 13):
            for k in range(n_per_month + month):        # later months are bigger
                i += 1
                cat = fs.CATEGORIES[0] if k % 3 else fs.CATEGORIES[1 + k % (len(fs.CATEGORIES) - 1)]
                rows.append({"id": str(i), "date": f"{year}-{month:02d}-{1 + k % 28:02d}",
                             "product": cat, "product_raw": cat, "narrative": "x"})
    rng.shuffle(rows)
    return rows


def test_stratified_sample_is_deterministic_and_balanced_by_month():
    rows = synthetic_rows()
    a = fs.stratified_sample(rows, per_year=120, seed=7)
    b = fs.stratified_sample(list(reversed(rows)), per_year=120, seed=7)
    assert [r["id"] for r in a] == [r["id"] for r in b]              # same seed, any input order
    assert [r["id"] for r in a] != [r["id"] for r in fs.stratified_sample(rows, 120, seed=8)]
    months = Counter(r["date"][:7] for r in a)
    assert len(months) == 24 and set(months.values()) == {10}       # 120 / 12 per month
    assert len({r["id"] for r in a}) == len(a)                       # no duplicates
    # every category reaches its yearly floor, and the big one keeps the largest share
    per_cat = Counter((r["date"][:4], r["product"]) for r in a)
    floor = fs.month_floor(120, len(fs.CATEGORIES)) * 12
    assert all(per_cat[(y, c)] >= floor for y in ("2019", "2020") for c in fs.CATEGORIES)
    assert per_cat[("2019", fs.CATEGORIES[0])] == max(per_cat.values())


def test_stratified_sample_takes_what_exists_in_a_thin_month():
    rows = [r for r in synthetic_rows(2) if not (r["date"].startswith("2019-01") and int(r["id"]) % 2)]
    out = fs.stratified_sample(rows, per_year=600, seed=7)
    have = Counter(r["date"][:7] for r in rows)
    got = Counter(r["date"][:7] for r in out)
    assert got == have                                                # fewer than 50 → take them all
