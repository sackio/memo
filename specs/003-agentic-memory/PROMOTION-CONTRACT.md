# ATC → memo promotion: the candidate contract

Ben's D16 (2026-07-30): ATC offers candidates, **memo's auditor decides**. ATC proposes, memo
disposes. This is memo's half — what a candidate must carry for memo to judge it, and what memo
returns.

## What ATC sends

memo cannot re-derive any of this, which is the whole reason it must be on the offer:

| field | why memo needs it |
|---|---|
| `content` | the thing itself |
| `authored_by` | **who said it** — `operator` \| `agent:<name>` \| `system`. The single most load-bearing field: operator-originated content is far more likely to be behavioral or constitutional, and memo has no way to tell after the fact. **NOT named `origin`** — see the collision note below. |
| `provenance` | where it came from — session id, message id, timestamp, zone. **Never invented.** Under R-18 an unattributed fact is still stored, tagged `provenance-pending`; but a real locator here is worth more than anything memo can reconstruct later. |
| `first_seen` / `last_seen` | age and liveness |
| `reference_count` + window | raw frequency. Necessary but **not sufficient** — see below. |
| **gap structure** (reference → silence → reference) | the signal that actually predicts durability. ATC's, computed by ATC. |
| `referencing_sessions` | scope. Referenced by one session = probably session-local; by five = probably fleet-wide. Feeds memo's `scope` field directly. |
| `ttl_expired` | whether it outlived its intended lifetime, which is itself evidence of durability |

## ⚠️ `authored_by` vs `origin` — two fields, never one

ATC had already specced `origin` as the **transport trust class** (`mcp` / `http` / `system`,
server-set), and agentkit will **gate destructive verbs on it** — refusing respawn/kill on a
caller-asserted HTTP `from`. memo's field is **semantic authorship**. They look like the same
field and are orthogonal.

The proof case: **an operator directive from Ben arrives via the Slack bridge, which posts over
HTTP.** Semantically `operator`; transport-wise `http`. Collapse them into one name and either
Ben's directive reads as untrusted, or an HTTP-injected message reads as operator-authored. One
of those is an outage; the other is a privilege escalation.

So: `authored_by` = who wrote it (memo's). `origin` = how much the sender field can be trusted
(ATC's, agentkit gates on it). Neither is derivable from the other.

## The signal: recurrence after dormancy, not frequency

memo's objection to raw reference count — *it measures how often something came UP, not whether
anyone would go LOOKING for it* — killed the original signal, and ATC's replacement is better:

**Working state is referenced continuously and then never again. A durable fact is referenced,
goes quiet for days or weeks, and is referenced AGAIN — because someone went looking.**

So the promotable shape is `referenced → silent → referenced`, and **the length of the silence is
the strength of the signal**. ATC offers both the counts and the gap structure so memo tunes on
evidence rather than on either side's heuristic.

Worth noting this is the same measurement memo already collects for its own corpus and has never
used: `doc_access` records every get/search, and `GET /admin/access-stats` computes hot vs cold
(built ~2026-07-05, zero callers, found in the 2026-07-30 archaeology). Two systems, one idea —
*does anyone come back for this?* — arrived at independently. That convergence is itself evidence
the signal is the right one.

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
