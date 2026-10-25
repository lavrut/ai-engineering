# Lab data

Everything the triage lab reads. Each folder here is a data root: `tickets.jsonl`, `prompts.json`, `reply_labels.jsonl` and `responses/`.

- `tickets.jsonl` has 60 support tickets for Lumen, a fictional cloud notes app. Each line has an id, the ticket text, the right category and priority, tags for tricky cases and a split, `dev` (tune on it) or `test` (report a final number only).
- `prompts.json` has the three triage prompt versions (`v1_basic`, `v2_rules`, `v3_examples`), the judge prompt with a `{rubric}` placeholder, and the rubric: `addresses_issue`, `tone`, `no_false_promises`.
- `reply_labels.jsonl` has 30 draft replies (r001 to r030) to dev tickets, with a true or false label for each rubric item. They are the human answers the judge is compared against. **These labels are Claude-drafted and pending your review.** Re-read each reply against the rubric and change any label you disagree with before you trust the judge agreement numbers.
- `responses/` holds saved model responses, one `.jsonl` file per configuration, named `kind__model__prompt_version__effort.jsonl`. They come only from real API runs (`record`); replay reads them for free.
- `fixtures/` is a tiny root used only by the tests: 8 tickets, a small prompts file, 4 labelled replies, hand-written responses and `expected_scores.json` with the numbers worked out by hand. It is never real recorded data.
