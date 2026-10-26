#!/usr/bin/env python3
"""
fetch_sample — build the course's complaint sample from the CFPB narratives archive.

In August 2026 the CFPB stopped publishing complaint narratives in the live
Consumer Complaint Database (API and bulk CSV). The narratives survive in the
CFPB's official FOIA "Narratives Archive": ZIP exports of every complaint that
was published, received December 2011 through August 14, 2026. This script
downloads the exports that cover 2019-2024, checks their SHA-256, streams the
CSV inside each ZIP (nothing is extracted to disk), keeps five fields, and
writes a fixed, seeded sample to complaints.jsonl. Only the sample is committed.

Run (standard library only, about 711 MB of downloads):
    python fetch_sample.py download --dir /tmp/ccdb      # skips files already there
    python fetch_sample.py counts   --dir /tmp/ccdb      # narratives per year x product
    python fetch_sample.py sample   --dir /tmp/ccdb      # writes complaints.jsonl + README counts
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

# ── 1. Archive files ────────────────────────────────────────────────────────
ARCHIVE_PAGE = ("https://www.consumerfinance.gov/foia-requests/foia-electronic-reading-room/"
                "cfpb-consumer-complaint-database-narratives-archive/")
BASE_URL = "https://files.consumerfinance.gov/f/documents/"
USER_AGENT = "ai-engineering-course-sample/1.0"   # generic: no personal data in requests

# (file name, size in bytes, SHA-256) as downloaded on 2026-10-26. A change means the archive moved or changed.
FILES: list[tuple[str, int, str]] = [
    ("CCDB_Export_2_May_2018_through_April_2021.zip", 164598156,
     "4b33ffc92fa0236836228c5618b7247f0f59ffef35760a45175c858a0d5aaf45"),
    ("CCDB_Export_3_May_2021_through_October_2022.zip", 128680448,
     "b8b195ff7d6b8e137bd0f37d13d90c0efb4f216c08dff3c4af91f02af3756130"),
    ("CCDB_Export_4_November_2022_through_August_2023.zip", 115518181,
     "547e8bdb07961861d4bacabff74fd2dd7358c04d225b492b286c37920cb39921"),
    ("CCDB_Export_5_September_2023_through_March_2024.zip", 100880669,
     "993747363e52239e4762b804662d7f604f976a139de4f729f613329d3d8c7206"),
    ("CCDB_Export_6_April_2024_through_July_2024.zip", 77142558,
     "c8a6fb0d98287958e2c19b47e1a4211c4e43225d27123a3ca08d730a2b4ae0a5"),
    ("CCDB_Export_7_August_2024_through_October_2024.zip", 71365591,
     "f053000c342bfd08bc6f8f382290f623e34b166cb56760c0726ed15e296a6d74"),
    ("CCDB_Export_8_November_2024_through_December_2024.zip", 52563546,
     "24b3326a954c0edeff33318b157d12eef5a59547f6f274f9236f1ceaba988a74"),
]

YEARS = range(2019, 2025)       # 2019-2024: train 2019-2021, test 2022, stream 2023-2024
PER_YEAR = 600                  # target complaints per year (50 per month)
MIN_PER_YEAR = 60               # every category gets at least this many per year
SEED = 7

# The current CFPB product names the course classifies into, chosen from real counts (see README).
CATEGORIES = [
    "Credit reporting or other personal consumer reports",
    "Debt collection",
    "Credit card",
    "Checking or savings account",
    "Mortgage",
    "Money transfer, virtual currency, or money service",
]

DATA_DIR = Path(__file__).resolve().parent
MAP_FILE = DATA_DIR / "category_map.json"
OUT_FILE = DATA_DIR / "complaints.jsonl"
README = DATA_DIR / "README.md"
NARRATIVE = "Consumer complaint narrative"   # the column the live database no longer has


class ArchiveError(RuntimeError):
    """The archive moved, changed, or could not be read. The committed sample still works."""


# ── 2. Download with retries and a checksum ─────────────────────────────────
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path, tries: int = 5) -> None:
    """Stream one file to dest. Retries 429, 5xx and network errors with backoff; fails clearly otherwise."""
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:
                while block := resp.read(1 << 20):
                    out.write(block)
            part.rename(dest)
            return
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise ArchiveError(f"{url}: HTTP {e.code}. Has the archive moved? See {ARCHIVE_PAGE}") from e
            error: Exception = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            error = e
        wait = 2 ** attempt
        print(f"  {dest.name}: {error}; retry {attempt}/{tries} in {wait}s", file=sys.stderr)
        time.sleep(wait)
    raise ArchiveError(f"{url}: gave up after {tries} tries ({error})")


def check(path: Path, size: int, digest: str) -> None:
    """Size, then SHA-256 (when recorded). A mismatch means a different file from the one the sample came from."""
    if path.stat().st_size != size:
        raise ArchiveError(f"{path.name}: {path.stat().st_size} bytes, expected {size}")
    if digest and sha256(path) != digest:
        raise ArchiveError(f"{path.name}: SHA-256 differs from the recorded one")


def download_all(folder: Path, delay: float = 2.0) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, size, digest in FILES:
        dest = folder / name
        if not dest.exists():
            print(f"downloading {name} ({size / 1e6:.0f} MB)")
            download(BASE_URL + name, dest)
            time.sleep(delay)                   # one file at a time, with a pause: be polite
        check(dest, size, digest)


# ── 3. Parse ────────────────────────────────────────────────────────────────
def read_export(path: Path) -> Iterator[dict]:
    """Stream the CSV rows inside one ZIP export, without extracting it."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ArchiveError(f"{path.name}: expected one CSV inside, found {names}")
        with z.open(names[0]) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
            if NARRATIVE not in (reader.fieldnames or []):
                raise ArchiveError(f"{path.name}: no '{NARRATIVE}' column; columns are {reader.fieldnames}")
            yield from reader


def parse_date(text: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_row(row: dict, mapping: dict[str, str]) -> tuple[dict | None, str]:
    """One CSV row -> (complaint line, "kept") or (None, why it was dropped). Only five fields survive."""
    narrative = (row.get(NARRATIVE) or "").strip()
    if not narrative:
        return None, "no narrative"
    day = parse_date(row.get("Date received") or "")
    if day is None:
        return None, "bad date"
    if day.year not in YEARS:
        return None, "outside 2019-2024"
    product = map_product(row.get("Product") or "", mapping, row.get("Sub-product") or "")
    if product is None:
        return None, "product not in the chosen categories"
    cid = (row.get("Complaint ID") or "").strip()
    if not cid.isdigit():
        return None, "bad complaint id"
    return {"id": cid, "date": day.isoformat(), "product": product,
            "product_raw": row["Product"], "narrative": narrative}, "kept"


def parse_rows(csv_rows: Iterable[dict], mapping: dict[str, str] | None = None) -> list[dict]:
    """CSV rows as published -> complaint lines (id, date, product, product_raw, narrative)."""
    mapping = load_category_map() if mapping is None else mapping
    return [line for line, _ in (parse_row(r, mapping) for r in csv_rows) if line]


# ── 4. Map categories ───────────────────────────────────────────────────────
def load_category_map(path: Path = MAP_FILE) -> dict[str, str]:
    """category_map.json -> {published name: current name}. Keys may be "Product•Sub-product"."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {raw: entry["to"] for raw, entry in data["map"].items()}


def map_product(raw: str, mapping: dict[str, str], sub_product: str = "") -> str | None:
    """The current name for a published product, or None if it is not one of ours.
    A "Product•Sub-product" key wins, so a split category (credit card vs prepaid) can be told apart."""
    return mapping.get(f"{raw}•{sub_product}", mapping.get(raw))


# ── 5. Sample ───────────────────────────────────────────────────────────────
def month_floor(per_year: int, n_categories: int) -> int:
    """Complaints per category per month that every category is guaranteed (when it has them)."""
    return min(-(-MIN_PER_YEAR // 12), per_year // 12 // max(n_categories, 1))


def allocate(per_month: int, available: dict[str, int], floor: int) -> dict[str, int]:
    """Split one month's quota: a floor for every category, the rest in proportion to real volume."""
    take = {c: min(n, floor) for c, n in available.items()}
    left = per_month - sum(take.values())
    while left > 0:
        spare = {c: available[c] - take[c] for c in available if available[c] > take[c]}
        if not spare:
            break                                         # the month has fewer than per_month: take them all
        total = sum(spare.values())
        shares = {c: left * n / total for c, n in spare.items()}
        extra = {c: min(spare[c], int(s)) for c, s in shares.items()}
        if not any(extra.values()):                       # hand out the last few by largest share
            extra = {max(shares, key=lambda c: (shares[c], c)): 1}
        for c, n in extra.items():
            take[c] += n
        left -= sum(extra.values())
    return take


def stratified_sample(rows: list[dict], per_year: int = PER_YEAR, seed: int = SEED) -> list[dict]:
    """per_year rows for each year, spread evenly over its months; within a month every category
    gets a floor and the rest follows the real mix. Same rows + same seed -> same sample, in any order."""
    rng = random.Random(seed)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["date"][:7], r["product"])].append(r)
    categories = sorted({r["product"] for r in rows})
    floor = month_floor(per_year, len(categories))
    out: list[dict] = []
    for month in sorted({m for m, _ in groups}):
        m = int(month[5:7])
        per_month = per_year // 12 + (1 if m <= per_year % 12 else 0)
        available = {c: len(groups[(month, c)]) for c in categories if groups.get((month, c))}
        for c, n in sorted(allocate(per_month, available, floor).items()):
            pool = sorted(groups[(month, c)], key=lambda r: int(r["id"]))
            out.extend(rng.sample(pool, n))
    return sorted(out, key=lambda r: (r["date"], int(r["id"])))


# ── 6. Write ────────────────────────────────────────────────────────────────
def scan(folder: Path, mapping: dict[str, str]) -> tuple[list[dict], Counter, Counter]:
    """Pass 1: every eligible complaint without its narrative (to keep memory small), plus drop reasons."""
    slim, dropped, raw_counts, seen = [], Counter(), Counter(), set()
    for name, _, _ in FILES:
        for row in read_export(folder / name):
            line, why = parse_row(row, mapping)
            if line and line["id"] in seen:
                line, why = None, "duplicate complaint id"
            if line is None:
                dropped[why] += 1
                continue
            seen.add(line["id"])
            raw_counts[(line["date"][:4], line["product_raw"])] += 1
            del line["narrative"]
            slim.append(line)
    return slim, dropped, raw_counts


def collect_narratives(folder: Path, ids: set[str]) -> dict[str, str]:
    """Pass 2: the narratives of the sampled complaints only."""
    found: dict[str, str] = {}
    for name, _, _ in FILES:
        for row in read_export(folder / name):
            cid = (row.get("Complaint ID") or "").strip()
            if cid in ids and cid not in found:
                found[cid] = row[NARRATIVE].strip()
    return found


def write_sample(folder: Path, out: Path = OUT_FILE, readme: Path = README) -> list[dict]:
    mapping = load_category_map()
    slim, dropped, raw_counts = scan(folder, mapping)
    sample = stratified_sample(slim, PER_YEAR, SEED)
    texts = collect_narratives(folder, {r["id"] for r in sample})
    with out.open("w", encoding="utf-8") as f:
        for r in sample:
            line = {"id": r["id"], "date": r["date"], "product": r["product"],
                    "product_raw": r["product_raw"], "narrative": texts[r["id"]]}
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    update_readme(readme, report(slim, sample, dropped, raw_counts, out))
    return sample


def table(counts: Counter, rows: list[str], label: str) -> list[str]:
    years = [str(y) for y in YEARS]
    lines = [f"| {label} | " + " | ".join(years) + " | Total |", "|---" * (len(years) + 2) + "|"]
    for r in rows:
        n = [counts[(y, r)] for y in years]
        lines.append(f"| {r} | " + " | ".join(f"{v:,}" for v in n) + f" | {sum(n):,} |")
    tot = [sum(counts[(y, r)] for r in rows) for y in years]
    return lines + ["| **Total** | " + " | ".join(f"{v:,}" for v in tot) + f" | {sum(tot):,} |"]


def report(slim, sample, dropped, raw_counts, out: Path) -> str:
    sample_counts = Counter((r["date"][:4], r["product"]) for r in sample)
    pop = Counter((r["date"][:4], r["product"]) for r in slim)
    raws = sorted({p for _, p in raw_counts})
    parts = [f"Generated by `python fetch_sample.py sample` on {date.today().isoformat()}.", "",
             f"**Sample:** {len(sample):,} complaints, `{out.name}` = {out.stat().st_size:,} bytes.", "",
             "Sample, per year x category:", "", *table(sample_counts, CATEGORIES, "Category"), "",
             "Available narratives (2019-2024, chosen categories, after mapping):", "",
             *table(pop, CATEGORIES, "Category"), "",
             "The same narratives by product as published (before mapping):", "", *table(raw_counts, raws, "Published product"), "",
             "Rows not used (all seven exports):", "", "| Reason | Rows |", "|---|---|",
             *[f"| {why} | {n:,} |" for why, n in dropped.most_common()]]
    return "\n".join(parts)


START, END = "<!-- generated:start -->", "<!-- generated:end -->"


def update_readme(readme: Path, block: str) -> None:
    text = readme.read_text(encoding="utf-8") if readme.exists() else f"{START}\n{END}\n"
    if START not in text:
        text += f"\n{START}\n{END}\n"
    head, rest = text.split(START, 1)
    readme.write_text(f"{head}{START}\n{block}\n{END}{rest.split(END, 1)[1]}", encoding="utf-8")


def counts(folder: Path) -> None:
    """Narratives per year x product as published (and sub-product), to choose the categories."""
    c: Counter = Counter()
    for name, _, _ in FILES:
        for row in read_export(folder / name):
            day = parse_date(row.get("Date received") or "")
            if (row.get(NARRATIVE) or "").strip() and day and day.year in YEARS:
                c[(day.year, row["Product"], row.get("Sub-product") or "")] += 1
    json.dump([[y, p, s, n] for (y, p, s), n in sorted(c.items())], sys.stdout, indent=0)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["download", "counts", "sample"])
    ap.add_argument("--dir", type=Path, required=True, help="folder for the downloaded ZIP exports")
    args = ap.parse_args(argv)
    try:
        if args.command == "download":
            download_all(args.dir)
        elif args.command == "counts":
            counts(args.dir)
        else:
            for name, size, digest in FILES:
                check(args.dir / name, size, digest)
            print(f"wrote {len(write_sample(args.dir)):,} complaints to {OUT_FILE}")
    except FileNotFoundError as e:
        sys.exit(f"fetch_sample: {e.filename} is missing; run `download --dir {args.dir}` first")
    except ArchiveError as e:
        sys.exit(f"fetch_sample: {e}")


if __name__ == "__main__":
    main()
