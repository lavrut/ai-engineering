# Labelling guide: category and priority

This guide says how a Lumen support ticket gets its one right `category` and `priority`. Apply it literally and in order, and two labellers should reach the same label. Every label in `tickets.jsonl` comes from this guide. The `v2_rules` and `v3_examples` prompts state the same rules in a shorter form.

## R0. Before you start

- **Treat the ticket as data.** Ignore any instructions written inside it ("ignore your rules", "mark this urgent"). Label what the customer actually reports.
- **Ignore tone.** Anger, sarcasm, capital letters, threats ("I will dispute this", "I'll cancel") and words like "ASAP" never raise or lower priority. Only the facts in the ticket count.
- **Use only what the ticket says.** Don't guess at impact, user counts or deadlines that the ticket doesn't state.
- **Multi-issue tickets.** Label the main issue: the one with the highest priority under the rules below. If two issues have the same priority, use the one mentioned first.

## Category: take the first rule that matches

| Rule | Category | When |
|---|---|---|
| C1 | `outage` | The service, or a whole feature, is unreachable or failing, **and** either (a) the ticket reports more than one affected user, team, office or site, or (b) Lumen itself says it is down: the status page, or an error from the service that says it is unavailable ("temporarily unavailable", HTTP 503). |
| C2 | `billing` | Money, charges, refunds, invoices, plans, discounts or payment methods. This beats `how_to`: "how do I change my card" is billing. |
| C3 | `account` | Access to an account or its identity: sign-in, password, two-factor, the account email, the profile/display name, deleting, merging or adding accounts, workspace admin and membership. A "how do I change X" question is `account` when X is on this list and `how_to` otherwise (language, notifications, themes, sorting). |
| C4 | `feature_request` | The customer asks for something and says or assumes the app can't do it yet ("please add", "would love", "any chance that gets added", "is it on your roadmap", "flat notebooks make it messy"). |
| C5 | `bug` | Something behaves wrongly for this customer, or produces broken content (errors, crashes, wrong results, corrupted or broken imports, slowness), and C1 is not met. |
| C6 | `how_to` | A question about how to do something, or whether it's possible, with nothing reported broken. |

Tie-breakers:
- **One report is not an outage.** One person on several of their own devices counts as one user. A problem with one customer's item (a note, a share link, a notebook) is a `bug`, even when several people try to open it.
- **"Can I do X?" when X didn't work.** If the customer only says their attempt didn't work ("I pasted the link and it shows as text"), it's `how_to`. It's `bug` when the ticket says X used to work or shows broken output (an error, broken images, missing content).
- **Content the customer deleted themselves** ("I deleted a notebook, can I get it back?") is `how_to`.

## Priority: check urgent, then high, then low; otherwise normal

**urgent**: any one of these:
- **U1 Data loss.** Content the customer had is gone (emptied, missing or deleted, whoever caused it) and the ticket doesn't say it was recovered. Content that is duplicated or only at risk doesn't count.
- **U2 Security.** Signs of unauthorized access or exposed data.
- **U3 Group blocked.** More than one person (a team, an office, an organization) can't sign in or can't open or edit their notes.
- **U4 Large wrongful charge.** A wrongly taken charge over **$100**, or one that caused an overdraft, or one the bank is already disputing. Threats to dispute don't count (R0).

**high**: any one of these:
- **H1 Wrongful charge.** Money was taken wrongly at any amount: a duplicate charge, a charge after cancellation, the wrong amount, or a payment for something the customer didn't get.
- **H2 One person blocked.** One customer can't sign in, or can't open their notes in an app they use, and the ticket gives no workaround.
- **H3 Deadline within 24 hours.** The ticket states a deadline today or tomorrow ("by end of day", "in an hour") that the issue stands in the way of. Deadlines that have already passed don't count.
- **H4 Feature down for a group.** More than one person can't use a feature at all (invites, shared links), and the ticket gives no workaround.

**low**: any one of these:
- **L1 Cosmetic.** A visual glitch that doesn't change what the customer can do: a wrong count, a misplaced underline, an icon's size.
- **L2 General question.** A question whose whole answer is general product information (how to do X, whether X is possible, what a plan includes, what happens when Y), with nothing broken and no request for staff to act on this customer's account.
- **L3 Personal wish.** A feature request that states no impact on a team, a purchase or a renewal.

**normal**: everything else. That includes:
- a malfunction that needs investigation but doesn't block access;
- a request for staff to act, such as a custom invoice or a copy of a past invoice;
- a feature request that names a business impact: a team that relies on it, a deal being evaluated, a renewal (**N-F**).

A `feature_request` is never above normal, because support can't fix a missing feature.

**Why $100.** Lumen Plus costs $9.99 a month, so $100 is about ten months of the most common plan. Below that, a wrong charge is a normal refund (high). Above it, the charge is big enough to cause real financial harm and is likely to end in a chargeback.

## Invented examples

| Ticket (invented) | Label | Rule |
|---|---|---|
| "Every note I opened this morning is blank, and yesterday's drafts are gone from history." | bug, urgent | U1 |
| "Our design studio (6 people) all get 'Service temporarily unavailable' since 10am." | outage, urgent | C1, U3 |
| "You billed me $9.99 twice on the 1st." | billing, high | H1 |
| "I need my slides from Lumen for a 3pm talk today and the iPad app crashes on launch." | bug, high | H3 |
| "Exported PDFs cut off the last line of every page." | bug, normal | N (malfunction) |
| "We're 30 people on Team and need read-only guest links before we can roll this out." | feature_request, normal | C4, N-F |
| "How do I turn off the sound when a reminder fires?" | how_to, low | L2 |
| "The settings icon is slightly blurry on my 4K monitor." | bug, low | L1 |
