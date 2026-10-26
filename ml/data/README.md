# ML track data: CFPB consumer complaints

`complaints.jsonl` is a fixed sample of real consumer complaints sent to the US Consumer Financial
Protection Bureau (CFPB), 2019–2024, one JSON object per line:

```json
{"id": "1234567", "date": "2021-03-14", "product": "<current name>", "product_raw": "<as published>", "narrative": "..."}
```

- `id`: CFPB complaint ID. `date`: date the CFPB received the complaint.
- `product_raw`: the product as published. `product`: mapped to the current CFPB name (`category_map.json`).
- `narrative`: the consumer's own description of what happened.

Nothing else is kept. Company, state, ZIP code, tags, issue and response fields are dropped.

## Where it comes from, and why the sample is committed

The live Consumer Complaint Database stopped publishing narratives in August 2026 (CFPB announcement of
2026-08-14). On 2026-10-26 the search API (`https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/`)
returned no narrative field, `has_narrative` was not a parameter any more (cfpb/ccdb5-api PR #256 "Remove narratives,
trends, geo", merged 2026-08-18), and the bulk `complaints.csv.zip` had no "Consumer complaint narrative" column.

The narratives survive in the CFPB's official FOIA **Narratives Archive**:
<https://www.consumerfinance.gov/foia-requests/foia-electronic-reading-room/cfpb-consumer-complaint-database-narratives-archive/>.
It holds "consumer complaints received December 1, 2011 through August 14, 2026 that were previously published in the
Consumer Complaint Database", as 21 ZIP files named "CCDB Export …". Each ZIP holds one CSV with these columns:

`Date received, Product, Sub-product, Issue, Sub-issue, Consumer complaint narrative, Company public response, Company,
State, ZIP code, Tags, Submitted via, Date sent to company, Company response to consumer, Timely response?, Complaint ID`

A source can disappear, and a model's numbers are only reproducible if its data stays put. That is why the sample is
committed, not fetched on demand.

### Files used (the seven exports covering 2019–2024)

Downloaded on 2026-10-26 from `https://files.consumerfinance.gov/f/documents/<name>`, with User-Agent `ai-engineering-course-sample/1.0`.

| File | Bytes | SHA-256 |
|---|---|---|
| [`CCDB_Export_2_May_2018_through_April_2021.zip`](https://files.consumerfinance.gov/f/documents/CCDB_Export_2_May_2018_through_April_2021.zip) | 164,598,156 | `4b33ffc92fa0236836228c5618b7247f0f59ffef35760a45175c858a0d5aaf45` |
| [`CCDB_Export_3_May_2021_through_October_2022.zip`](https://files.consumerfinance.gov/f/documents/CCDB_Export_3_May_2021_through_October_2022.zip) | 128,680,448 | `b8b195ff7d6b8e137bd0f37d13d90c0efb4f216c08dff3c4af91f02af3756130` |
| [`CCDB_Export_4_November_2022_through_August_2023.zip`](https://files.consumerfinance.gov/f/documents/CCDB_Export_4_November_2022_through_August_2023.zip) | 115,518,181 | `547e8bdb07961861d4bacabff74fd2dd7358c04d225b492b286c37920cb39921` |
| [`CCDB_Export_5_September_2023_through_March_2024.zip`](https://files.consumerfinance.gov/f/documents/CCDB_Export_5_September_2023_through_March_2024.zip) | 100,880,669 | `993747363e52239e4762b804662d7f604f976a139de4f729f613329d3d8c7206` |
| [`CCDB_Export_6_April_2024_through_July_2024.zip`](https://files.consumerfinance.gov/f/documents/CCDB_Export_6_April_2024_through_July_2024.zip) | 77,142,558 | `c8a6fb0d98287958e2c19b47e1a4211c4e43225d27123a3ca08d730a2b4ae0a5` |
| [`CCDB_Export_7_August_2024_through_October_2024.zip`](https://files.consumerfinance.gov/f/documents/CCDB_Export_7_August_2024_through_October_2024.zip) | 71,365,591 | `f053000c342bfd08bc6f8f382290f623e34b166cb56760c0726ed15e296a6d74` |
| [`CCDB_Export_8_November_2024_through_December_2024.zip`](https://files.consumerfinance.gov/f/documents/CCDB_Export_8_November_2024_through_December_2024.zip) | 52,563,546 | `24b3326a954c0edeff33318b157d12eef5a59547f6f274f9236f1ceaba988a74` |
| **Total** | 710,749,149 | |

The archive page was last modified 2026-09-14, and the files' `Last-Modified` dates are all 2026-09-14. Each ZIP holds one CSV named like the ZIP. The 2026-10-26 downloads matched the sizes from the HEAD requests made just before them.

## How the sample is drawn

1. Stream each ZIP's CSV (nothing is extracted). Keep rows with a non-empty narrative, received 2019-01-01 to
   2024-12-31, whose product maps to one of the six categories below. Drop duplicate complaint IDs (there were none),
   and drop unparseable dates or IDs (there were none). The two passes keep memory small: pass 1 keeps everything but the
   narrative, and pass 2 reads back only the sampled narratives.
2. Map published product names to current ones (`category_map.json`, with a note per rename). This is real
   label drift: in 2019 credit-reporting complaints were filed under "Credit reporting, credit repair services, or
   other personal consumer reports", and "Credit card or prepaid card" was later split in two.
3. Draw 600 complaints per year, 50 per month, with seed 7. Within each month every category gets at least
   5 (so at least 60 per year), and the rest follow that month's real mix. The class balance therefore shifts over
   time the way the real complaint stream does.
   Credit reporting is 72% of the available narratives but 41% of the sample, because the floors take half of
   each month's 50. Its share still rises from 35% (2019) to 47% (2024), so the label mix drifts in the
   same direction as the real stream.

### Why these six categories

Real narrative counts for 2019–2024 (after mapping) are shown below. Every candidate product has thousands per year,
so the "at least 60 per year in every year" rule ruled nothing out. The choice is the six largest consumer-finance
products that exist in every year under one current name, each with a clear story:

- **Credit reporting** is the giant (58k narratives in 2019, 631k in 2024). It shows imbalance, and it carries the 2023 rename.
- **Debt collection**, **Checking or savings account** and **Mortgage** have names that never changed and steady volume.
- **Credit card** comes from the 2023 split of "Credit card or prepaid card". It is a mapping example that needs
  the sub-product, because the prepaid sub-products went to "Prepaid card" and are left out.
- **Money transfer, virtual currency, or money service** is the smallest (2.8k in 2019) and grows 3.7x by 2024
  (mobile wallets, virtual currency). That makes it a natural source of drift.

Considered and left out: *Vehicle loan or lease* (2.9k–7.6k per year, a near tie with money transfer; left out to keep six
classes), *Student loan* (2.2k–8.0k), *Payday loan, title loan, or personal loan* (renamed in 2023; 2.3k–5.2k per year across both names),
and *Prepaid card* and *Debt or credit management* (these exist only from 2023).

Reproduce it (standard library only):

```bash
python ml/data/fetch_sample.py download --dir /tmp/ccdb   # ~711 MB, checks sizes and SHA-256
python ml/data/fetch_sample.py sample   --dir /tmp/ccdb   # rewrites complaints.jsonl and the counts below
```

<!-- generated:start -->
Generated by `python fetch_sample.py sample` on 2026-10-26.

**Sample:** 3,600 complaints, `complaints.jsonl` = 4,609,144 bytes.

Sample, per year x category:

| Category | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | Total |
|---|---|---|---|---|---|---|---|
| Credit reporting or other personal consumer reports | 211 | 231 | 225 | 260 | 264 | 280 | 1,471 |
| Debt collection | 105 | 92 | 96 | 76 | 72 | 74 | 515 |
| Credit card | 79 | 74 | 72 | 71 | 72 | 65 | 433 |
| Checking or savings account | 72 | 71 | 72 | 70 | 72 | 61 | 418 |
| Mortgage | 73 | 72 | 72 | 63 | 60 | 60 | 400 |
| Money transfer, virtual currency, or money service | 60 | 60 | 63 | 60 | 60 | 60 | 363 |
| **Total** | 600 | 600 | 600 | 600 | 600 | 600 | 3,600 |

Available narratives (2019-2024, chosen categories, after mapping):

| Category | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | Total |
|---|---|---|---|---|---|---|---|
| Credit reporting or other personal consumer reports | 58,184 | 95,617 | 108,791 | 233,169 | 348,115 | 630,670 | 1,474,546 |
| Debt collection | 23,945 | 25,831 | 34,478 | 31,380 | 35,607 | 70,291 | 221,532 |
| Credit card | 11,736 | 14,906 | 14,659 | 19,611 | 32,544 | 35,451 | 128,907 |
| Checking or savings account | 7,351 | 9,530 | 12,401 | 18,032 | 28,017 | 29,485 | 104,816 |
| Mortgage | 10,035 | 12,467 | 14,077 | 12,289 | 12,883 | 12,209 | 73,960 |
| Money transfer, virtual currency, or money service | 2,755 | 4,709 | 7,819 | 7,708 | 8,268 | 10,239 | 41,498 |
| **Total** | 114,006 | 163,060 | 192,225 | 322,189 | 465,434 | 788,345 | 2,045,259 |

The same narratives by product as published (before mapping):

| Published product | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | Total |
|---|---|---|---|---|---|---|---|
| Checking or savings account | 7,351 | 9,530 | 12,401 | 18,032 | 28,017 | 29,485 | 104,816 |
| Credit card | 0 | 0 | 0 | 0 | 12,905 | 35,451 | 48,356 |
| Credit card or prepaid card | 11,736 | 14,906 | 14,659 | 19,611 | 19,639 | 0 | 80,551 |
| Credit reporting or other personal consumer reports | 0 | 0 | 0 | 0 | 124,058 | 630,670 | 754,728 |
| Credit reporting, credit repair services, or other personal consumer reports | 58,184 | 95,617 | 108,791 | 233,169 | 224,057 | 0 | 719,818 |
| Debt collection | 23,945 | 25,831 | 34,478 | 31,380 | 35,607 | 70,291 | 221,532 |
| Money transfer, virtual currency, or money service | 2,755 | 4,709 | 7,819 | 7,708 | 8,268 | 10,239 | 41,498 |
| Mortgage | 10,035 | 12,467 | 14,077 | 12,289 | 12,883 | 12,209 | 73,960 |
| **Total** | 114,006 | 163,060 | 192,225 | 322,189 | 465,434 | 788,345 | 2,045,259 |

Rows not used (all seven exports):

| Reason | Rows |
|---|---|
| no narrative | 3,991,163 |
| product not in the chosen categories | 96,519 |
| outside 2019-2024 | 74,619 |
<!-- generated:end -->

## Licence and privacy

The Consumer Complaint Database is a work of the US government and is in the public domain (the API reports
licence `CC0`). Narratives were published only when the consumer consented, and the CFPB scrubbed personal
information before publishing: names, account numbers, dates and similar details appear as `XXXX` (42,622 times in
this sample), and dates often appear as `XX/XX/XXXX`. Dollar amounts are published in braces, like `{$250.00}`. The narratives
are kept exactly as published, apart from trimming leading and trailing whitespace. Consumers sometimes name a company
in their own words, but the sample adds no company, state, ZIP code or tag fields.

Narrative length is 10 to 23,091 characters. One narrative is longer than the prediction server's 20,000-character limit.
