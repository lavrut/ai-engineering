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
A case whose contract fails to load expects a single `contract` verdict (`ok: false`), which is what both command lines print.

## Pattern grammar (ruling D7)

A contract `pattern` is a full match (`re.fullmatch` in Python, `^(?:…)$` in Go).
Before compiling it, both validators run the same small tokenizer (`pattern_tokens` in Python, `patternTokens` in Go), which accepts only this allow-list:

| Form | Allowed |
|---|---|
| literals | any character except `\ . ^ $ * + ? ( ) [ ] { } \|` |
| any character | `.` (not a newline, in both languages) |
| escaped metacharacters | `\. \* \+ \? \( \) \[ \] \{ \} \| \^ \$ \\ \-` |
| class escapes | `\d \D \w \W \s \S`, which are rewritten to ASCII before compiling: `[0-9]`, `[A-Za-z0-9_]`, `[ \t\n\r\f]` and their negations (so `\s` never matches `\v` or a no-break space) |
| bracket classes | `[...]` and `[^...]` holding literal characters, escaped metacharacters, ranges `a-z` (low end first), and the six class escapes; `-` is literal only as the first or last item; no `[` inside, no empty class |
| groups | `(...)` and `(?:...)` |
| alternation | `\|` |
| quantifiers | `* + ? {n} {n,} {n,m}` after an atom, with n ≤ m ≤ 1000; no `{,n}`, no lazy `?` or possessive `+` suffix; a counted repeat never follows a group that holds one, at any depth (`(a{2}){3}` is refused; `(a{2})+` is fine), because RE2 refuses the product and Python does not (ruling D9) |
| anchors | `^` only as the first token and `$` only as the last; both are redundant under a full match and dropped before compiling |

Anything else is refused when the contract loads, with the detail `schema.fields[i].pattern: invalid pattern: <offending token>`. Refused forms include inline flags, lookarounds, named groups, comments, backreferences, `\u`, `\N{}`, `\p`, `\b`, `\B`, `\A`, `\Z`, `[[:alpha:]]` and `[\b]`. The text is identical in both languages, and it never includes an engine's error text.
A contract file that cannot be parsed fails with `<file> is not a valid contract file` in both languages.
