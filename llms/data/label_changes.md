# Label changes (labelling guide, round 1)

Every ticket whose `category` or `priority` changed when `labeling_guide.md` was applied literally to all 60 tickets. Rule codes refer to the guide; R0 = tone and injected text are ignored. Text, ids, tags and splits are unchanged.

**Summary: 24 of 60 tickets changed — 2 category changes, 23 priority changes.**

| id | split | category | priority | deciding rule |
|---|---|---|---|---|
| t003 | dev | account | normal → high | priority H2 |
| t010 | dev | how_to | normal → low | priority L2 |
| t016 | dev | how_to | normal → low | priority L2 (sarcasm ignored, R0) |
| t017 | dev | feature_request | normal → low | priority L3 |
| t018 | dev | outage | normal → high | priority H2 |
| t021 | dev | account | normal → high | priority H2 |
| t027 | dev | account | urgent → high | priority H2 (single customer; not U) |
| t028 | dev | how_to → bug | normal | category C5 (broken content after import beats how_to) |
| t030 | dev | billing | normal → low | priority L2 |
| t032 | dev | bug | low → normal | priority N (slowness is not cosmetic) |
| t033 | dev | account | normal → low | priority L2 |
| t035 | dev | feature_request | normal → low | priority L3 (sarcasm ignored, R0) |
| t037 | dev | bug | normal → low | priority L1 (anger ignored, R0) |
| t038 | dev | outage → bug | high → normal | category C1 not met (one user, generic error) -> C5; priority N |
| t040 | dev | how_to | normal → low | priority L2 |
| t041 | test | billing | normal → low | priority L2 |
| t043 | test | billing | low → normal | priority N (staff action, not L2) |
| t044 | test | bug | high → normal | priority N |
| t046 | test | bug | high → normal | priority N (no content lost, not U1) |
| t048 | test | account | normal → low | priority L2 |
| t050 | test | how_to | normal → low | priority L2 |
| t052 | test | how_to | high → urgent | priority U1 |
| t053 | test | feature_request | normal → low | priority L3 |
| t059 | test | feature_request | normal → low | priority L3 |

New distribution: category billing 10, bug 15, account 10, how_to 9, feature_request 10, outage 6; priority low 24, normal 16, high 11, urgent 9 (test split: low 8, normal 7, high 2, urgent 3).

## Round 2 (guide tightened after the second blind audit)

The second blind labeller (`label_audits/round2_blind.jsonl`) matched every label (60/60) but marked t019, t031, t033, t047 and t057 ambiguous. The guide now settles each one. **None of the 60 labels changes under the tightened rules: 0 category changes, 0 priority changes.**

| id | label (unchanged) | now decided by |
|---|---|---|
| t019 | billing, high | R0 order: two listed issues; charge without the upgrade = H1, teammates can't see newly shared notebooks = H4 (not U3, which is only for notes people already had). Tie at high, so the first mentioned (billing) wins |
| t031 | bug, high | H4 explicitly covers a share link that several people can't open; category stays bug (one customer's item) |
| t033 | account, low | L2: "I'd like to delete my account" followed by what/how questions is a question, not a request for staff to act |
| t047 | account, high | H4 explicitly covers a workspace with no admin (nobody can invite or manage the plan) |
| t057 | outage, normal | Workaround definition: "until we reload" is a workaround, so H4 is not met |

Also rechecked under the stated R0 order (pick the issue first, then categorize it): t012 (sync and search, both bug normal, so the first issue gives bug normal) and t040 (three L2 questions, so the first one, offline mode, gives how_to low). Both are unchanged.
