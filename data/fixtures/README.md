# Validator fixtures

`cases.jsonl` holds the cases both validators (Python now, Go in Task 2) must reproduce exactly.
One JSON object per line:

| Key | Meaning |
|---|---|
| `name` | unique case name |
| `contract` | a loaded contract (the five sections, as `load_contract` returns it) |
| `rows` | the dataset as a list of objects, or `null` when `dataset` is given |
| `dataset` | optional raw text, read the way a file is read: JSONL, a JSON array, or one JSON object |
| `age_days` | the file age the caller computed, or `null` (freshness unknown) |
| `expect` | the verdicts, sorted by rule then field |

`gen_cases.py` writes the file from Python; the `expect` lists are written by hand there, not computed by the validator.
`gen_cases.py --check` regenerates in memory and fails if `cases.jsonl` differs.
Row numbers in details are 1-based and count parsed rows.
