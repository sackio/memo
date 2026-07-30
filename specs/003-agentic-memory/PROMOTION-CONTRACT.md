# ATC → memo promotion: the candidate contract

Ben's D16 (2026-07-30): ATC offers candidates, **memo's auditor decides**. ATC proposes, memo
disposes. This is memo's half — what a candidate must carry for memo to judge it, and what memo
returns.

## What ATC sends

memo cannot re-derive any of this, which is the whole reason it must be on the offer:

| field | why memo needs it |
|---|---|
| `content` | the thing itself |
| `origin` | **who said it** — `operator` \| `agent:<name>` \| `system`. The single most load-bearing field: operator-originated content is far more likely to be behavioral or constitutional, and memo has no way to tell after the fact. |
| `provenance` | where it came from — session id, message id, timestamp, zone. **Never invented.** Under R-18 an unattributed fact is still stored, tagged `provenance-pending`; but a real locator here is worth more than anything memo can reconstruct later. |
| `first_seen` / `last_seen` | age and liveness |
| `reference_count` + window | ATC's actual signal — "this keeps coming up" |
| `referencing_sessions` | scope. Referenced by one session = probably session-local; by five = probably fleet-wide. Feeds memo's `scope` field directly. |
| `ttl_expired` | whether it outlived its intended lifetime, which is itself evidence of durability |

## What memo decides, and on what basis

The core judgment, and the reason it's memo's: **is this a durable fact, or working state that
merely persisted?** Something can be referenced twenty times and still be ephemeral — a
long-running task's status is discussed constantly and worth nothing tomorrow.

memo's criteria, in the order they're applied:

1. **Reconcile first.** A candidate that duplicates or contradicts an existing memo is not a
   write — it's a merge, a supersede, or a rejection. This is the same reconcile-before-write
   rule that governs every other path into the corpus; promotion is not an exception.
2. **State vs. finding** (operator rule, 2026-07-30). Facts about what IS get replaced when they
   change; findings about what HAPPENED get kept. A candidate describing current state is
   promotable only if that state is *durable* — "the router is a VyOS box" yes, "the migration is
   at 40%" no.
3. **Class inference**, which is where `origin` earns its place. Operator-originated + prohibition
   language → behavioral. Operator + standing-rule language → **proposal only, never enacted**
   (Principle V). Agent-originated → fact or episodic.
4. **Would anyone ask for this?** The test that kills most candidates. Reference count measures
   how often it came *up*, not whether anyone would ever go *looking*. Status chatter scores high
   on the first and zero on the second.

## What memo returns

`accept` (+ memo id, + action taken: write-new / merge / supersede) · `reject` (+ reason) ·
`defer` (+ what would change the answer).

**Rejections carry a reason** so ATC can stop offering that shape, rather than re-offering the
same candidate forever. A promotion path that silently drops candidates is one nobody can tune.

## Two constraints from the other rulings

**Asynchronous by construction** (D15). ATC's maintainer degrades and never blocks, so promotion
offers are **enqueued, never awaited**. Symmetrically, memo must not block on ATC: an offer that
arrives is processed when memo gets to it, and neither side waits for the other. Nothing on
either critical path.

**Neither side can report its own absence.** If promotion silently stops — ATC never offering, or
memo never deciding — both sides look completely normal, and the corpus simply stops growing
from this source. That is the agent coordinator's to observe, same as compaction. Worth naming in
the spec rather than discovering in three weeks.

## Deliberately NOT in memo's half

- **What's worth offering.** ATC's signal, ATC's call. memo shouldn't reach into ATC's retention.
- **Deletion from ATC after promotion.** memo accepting a candidate says nothing about whether
  ATC should drop it; those are different lifetimes and coupling them would make each side's
  retention depend on the other's.
