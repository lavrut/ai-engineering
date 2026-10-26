# Lab data

Everything the triage lab reads. Each folder here is a data root: `tickets.jsonl`, `prompts.json`, `reply_labels.jsonl` and `responses/`.

- `tickets.jsonl` has 60 support tickets for Lumen, a fictional cloud notes app. Each line has an id, the ticket text, the right category and priority, tags for tricky cases and a split, `dev` (tune on it) or `test` (report a final number only).
- `prompts.json` has the three triage prompt versions (`v1_basic`, `v2_rules`, `v3_examples`), the judge prompt with a `{rubric}` placeholder, and the rubric: `addresses_issue`, `tone`, `no_false_promises`.
- `reply_labels.jsonl` has 30 draft replies (r001 to r030) to dev tickets, with a true or false label for each rubric item. They are the human answers the judge is compared against. **These labels are Claude-drafted and pending your review.** Re-read each reply against the rubric and change any label you disagree with before you trust the judge agreement numbers.
- `responses/` holds saved model responses, one `.jsonl` file per configuration, named `kind__model__prompt_version__effort.jsonl`. They come only from real API runs (`record`); replay reads them for free.
- `fixtures/` is a tiny root used only by the tests: 8 tickets, a small prompts file, 4 labelled replies, hand-written responses and `expected_scores.json` with the numbers worked out by hand. It is never real recorded data.

## Recording run (2026-10-25)

The files in `responses/` were recorded on 2026-10-25 with `python triage_lab.py record --plan pilot`, then `--plan all`, both approved before they ran.

- Pilot: 3 Opus triage calls at high effort and 3 judge calls, $0.05. Its first attempt failed with "Connection error" on every call because the local `brotli` package was older than 1.2; nothing was billed.
- Full plan: 537 calls, $1.59, estimated $4.06 beforehand. Every call ended with `end_turn`; the longest reply used 659 of its 2,000 output tokens.
- Phase 2 found all three prompts tied at 55.0% `both` accuracy on dev, so the first one, `v1_basic`, became the "best" prompt for the Sonnet and Opus runs and the judged test replies.
- Some files contain an earlier failed line for the same key; replay uses the latest line.
