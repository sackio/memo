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
3. **Class inference**, which is where `authored_by` earns its place. Operator-originated + prohibition
   language → behavioral. Operator + standing-rule language → **proposal only, never enacted**
   (Principle V). Agent-originated → fact or episodic.
4. **Would anyone ask for this?** The test that kills most candidates. Reference count measures
   how often it came *up*, not whether anyone would ever go *looking*. Status chatter scores high
   on the first and zero on the second.

## How much `authored_by` is allowed to buy — the strength ceiling

ATC's V2 spec says out loud that attribution is **sound against confusion, soft against intent**:
all seats share a uid, so key custody is unachievable and a determined co-resident agent can post
as a bridge. That is the right call, and memo must not build anything on a stronger reading of it.

So, explicitly: **`authored_by` may influence WORTH and CLASS INFERENCE. It may never, by itself,
confer PRIVILEGE.**

- ✅ allowed: rank a candidate higher, infer behavioral-vs-episodic class, weight it in recall.
- ❌ never: create or amend a **constitutional-class** memo, or add anything to the **forcible
  injection set**, on the strength of `authored_by: operator` alone.

Both prohibitions already hold by construction — Principle V reserves constitutional-class to the
operator (agents may only PROPOSE), and the injection set carries constitutional class only. This
section exists so that stays true by intent rather than by accident, because the drift is one
plausible edit away: the moment a promoted behavioral rule becomes injectable, a spoofed
authorship becomes a fleet-wide standing rule.

**Residual risk, accepted:** a spoofed `authored_by: operator` can bias ranking and get junk
stored as behavioral class. That is confusion-grade damage — retrievable noise, not enacted rules
— and the cure (Q-D's signed-assent lane) costs more than the disease. Revisit only if the threat
model changes from confusion to malice.

## What memo returns

`accept` (+ memo id, + action taken: write-new / merge / supersede) · `reject` (+ reason code) ·
`defer` (+ what would change the answer).

**Rejections carry a reason** so ATC can stop offering that shape, rather than re-offering the
same candidate forever. A promotion path that silently drops candidates is one nobody can tune.

The reason must come from a **closed set** — free text cannot tune a heuristic, because nothing on
ATC's side can aggregate it:

| code | meaning | terminal? |
|---|---|---|
| `working_state` | true when said, worthless now — the dominant rejection | no |
| `already_durable` | duplicate of an existing memo (id returned) | **yes** |
| `merged` | folded into an existing memo rather than stored (id returned) | **yes** |
| `too_thin` | has no standalone meaning outside its thread | no |
| `scope_too_narrow` | one session's local detail, not fleet knowledge | no |
| `previously_deleted` | memo deleted this content deliberately — see below | **yes** |
| `unverifiable` | a claim memo cannot check and would be storing as fact | no |

**Terminal means ATC must not re-offer that content**, in any later pass, on any new recurrence.
Non-terminal means the shape was wrong *this time* and stronger evidence may change the answer.

## Deletion is now a first-class rejection reason

Ben's 2026-07-30 ruling changed memo's half of this: **superseded state gets DELETED, not
rewritten** — the test is *"does anyone need this after it stopped being true?"*, and "we used to
have X" is the worst option, carrying all of the retrieval cost and none of the value.

That collides with ATC's Q-A if the disk tier is retained indefinitely, which memo thinks it
should be. **ATC's log will outlive memos memo deliberately deleted** — correctly, because ATC
records *what was said* and memo records *what is true*. But it means the same content can recur
in the log and be re-offered forever, and memo's auditor has no memory of having killed it.

Both sides need one thing each:

- **memo:** check the **deletion log** (FR-028a, content snapshots) at candidate *intake*, before
  reconcile. A hit returns `previously_deleted` — memo must not re-store what it decided to drop.
- **ATC:** treat terminal rejections as sticky. Re-offering a `previously_deleted` candidate is
  the promotion path's version of a retry loop.

This is the only place the two retention models genuinely disagree, and it is cheap to settle now
and expensive to discover as corpus churn later.

## Volume: bounded batches, memo-paced

Each candidate costs memo a full reconcile pass — passage search plus auditor judgment. Measured
floor for a subagent invocation is **~2s and 15–41k tokens**, so an unbounded offer stream is a
real cost, not a theoretical one.

So promotion is **memo-paced**: ATC ranks and holds candidates, memo drains a bounded batch when
its auditor runs. This also keeps FR-020 clean — memo draining its own queue is housekeeping, not
fleet scheduling — and means an ATC outage cannot push work at memo, nor a memo outage back up
into ATC.

**Each side publishes a counter the other can read** — ATC: last promotion pass, candidates
offered, queue depth. memo: last drain, candidates decided. Neither party counts its own absences;
the coordinator compares the two, per Principle I's corollary.

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
