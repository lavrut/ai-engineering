# Lab data

Everything the triage lab reads. Each folder here is a data root: `tickets.jsonl`, `prompts.json`, `reply_labels.jsonl` and `responses/`.

- `tickets.jsonl` has 60 support tickets for Lumen, a fictional cloud notes app. Each line has an id, the ticket text, the right category and priority, tags for tricky cases and a split, `dev` (tune on it) or `test` (report a final number only).
- `prompts.json` has the three triage prompt versions (`v1_basic`, `v2_rules`, `v3_examples`), the judge prompt with a `{rubric}` placeholder, and the rubric: `addresses_issue`, `tone`, `no_false_promises`.
- `reply_labels.jsonl` has 30 draft replies (r001 to r030) to dev tickets, with a true or false label for each rubric item. They are the human answers the judge is compared against. **These labels are Claude-drafted and pending your review.** Re-read each reply against the rubric and change any label you disagree with before you trust the judge agreement numbers.
- `responses/` holds saved model responses, one `.jsonl` file per configuration, named `kind__model__prompt_version__effort.jsonl`. They come only from real API runs (`record`); replay reads them for free.
- `labeling_guide.md` is the rule set behind every `category` and `priority` in `tickets.jsonl`: a decision order, the $100 threshold for wrongful charges, tie-breakers for the known ambiguities (outage vs bug, how_to vs account, feature wishes) and invented examples. `v2_rules` and `v3_examples` state the same rules in a shorter form; `v1_basic` is deliberately left minimal.
- `label_changes.md` lists every ticket whose label changed when the guide was applied, with the rule that decided it.
- `label_audits/` holds label audits. `round1_blind.jsonl` is a blind relabel by an independent labeller given only the ticket text and the old `v2_rules` definitions. It agreed on category 57/60 and on priority only 33/60, which is why the guide was written. `round2_blind.jsonl` is a second blind labeller using only the guide. It agreed 60/60 before the round-2 tightening and marked 5 tickets ambiguous. The guide was then tightened on those points; see `label_changes.md`.
- **Round 3 (tightened guide):** a third blind labeller, given only the current guide and the ticket text, matched the labels on 60/60 categories and 60/60 priorities (`label_audits/round3_blind.jsonl`). It still marked 5 tickets ambiguous (t018, t034, t037, t047, t057), so those labels are the guide's best reading, not the only defensible one. All three labellers were Claude; a human labeller would likely agree less.
- **Limits of these labels.** The labels come from the same rules that `v2_rules` and `v3_examples` embed, so v2/v3 beating v1 partly measures how well a prompt follows the guide, not only triage skill. The guide's author and both blind labellers were Claude, so their agreement probably overstates what independent human labellers would reach.
- `fixtures/` is a tiny root used only by the tests: 8 tickets, a small prompts file, 4 labelled replies, hand-written responses and `expected_scores.json` with the numbers worked out by hand. It is never real recorded data.

## Recording run (2026-10-25)

The files in `responses/` were recorded on 2026-10-25 with `python triage_lab.py record --plan pilot`, then `--plan all`, both approved before they ran.

- Pilot: 3 Opus triage calls at high effort and 3 judge calls, $0.05. Its first attempt failed with "Connection error" on every call because the local `brotli` package was older than 1.2; nothing was billed.
- Full plan: 537 calls, $1.59, estimated $4.06 beforehand. Every call ended with `end_turn`; the longest reply used 659 of its 2,000 output tokens.
- *Superseded, scored on the pre-guide labels:* Phase 2 found all three prompts tied at 55.0% `both` accuracy on dev, so the first one, `v1_basic`, became the "best" prompt for the Sonnet and Opus runs and the judged test replies. This result and the choice of best prompt no longer hold, pending a re-record against the guide's labels.
- Some files contain an earlier failed line for the same key; replay uses the latest line.
- The `v2_rules` and `v3_examples` recordings predate `labeling_guide.md`. Those prompts were rewritten after the run, so their cache keys changed and replaying them now misses. They will be re-recorded after approval. The `v1_basic` recordings are still valid, but they are graded against the new labels.
