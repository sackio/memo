# Final proposal: agentic memory for memo v2

2026-07-30. Written after three parallel surveys of the codebase, spec 001's 54 FRs, and the
constitution. **Recommendation first, then what the surveys changed, then the path.**

## Recommendation

**Adopt the agent architecture. Four layers, with a hard line between judgment and constraint.**

1. **memo server (Python)** — corpus, bi-temporal integrity, passage index, and the invariants an
   agent must not be able to reason around. Exposes a **narrow MCP tool surface** and a fast
   search endpoint.
2. **Agents** — `memo-recall` and `memo-memorize`, declarative, session-spawned, minimal tools.
   They hold *judgment*.
3. **Method skills** — the procedure, single-sourced so a fix lands once rather than across ~50
   stale copies.
4. **Injection** — stays deterministic: a hook reads a **precomputed** cache. The memo session
   (already standing, already has MCP tools and crons) refreshes that cache. Agent quality,
   ~100ms on the critical path, no new infrastructure.

**Sequence: finish the index → wire it → build the agents → migrate once.** Not migrating twice
is why we stopped the backfill; the same logic says don't migrate before the agents exist either.

## What the surveys changed — four safety holes, two of them mine

The architecture is right. These are the things that would have gone wrong quietly.

### 1. The refutation gate evaporates (highest risk)

Principle II says only operators refute facts. Its **only** implementation is a branch in
`mediators/store.py:247`, reached after an LLM refutation check. Verified independently:
`db.supersede` accepts `operator_directive_ref` as an **optional** argument and never requires
it. So the moment the mediator stops being the sole write path, any agent calling
`POST /supersede` deletes a fact with no authority check at all.

Worse — and this one is **my error**: `memo_update` mutates content **in place**, with no
supersede edge, no `valid_until`, no lineage. I put it in `memo-memorize`'s tool list in the
draft. That is a hole straight through bi-temporal integrity, handed to the agent.

**Fix, before any of store.py is deleted:** move the authority check into
`db.supersede`/`repositories/documents.py`, and restrict `memo_update` to tags/metadata/title
only. Content changes must route through supersede.

### 2. `verbatim-critical` has zero enforcement — also my error

My README claimed it is "enforced in the result assembler." **There is no result assembler.**
Verified: the string appears only as a class literal, an injection membership test, and a
proposals layer name. The protection exists solely as a sentence in a skill file — which is
precisely the kind of constraint an agent can reason around.

**Fix:** if it is on the constraints list, it needs code. Otherwise remove the claim.

### 3. "Every write is reconciled" stops being a property

FR-015g's `bypass_mediator` flag existed because mediation was the server-side default, which
made "every write went through reconcile" provable from the audit log. Under agent-side
mediation, `memo_store` **is** the raw path, held open by design. `/memorize`'s "do not write
directly" is a **request, not a constraint**, and any of ~50 sessions can ignore it.

By the constitution's own governance rule — *"a principle is in force only if a live gate
enforces it"* — **Principle VII is arguably already unenforced** the moment we ship this.

**Fix:** either accept it and mark Principle VII `UNENFORCED` honestly at the next audit, or add
an identity-scoped gate (writes from a non-mediator identity rejected). I recommend deciding
deliberately rather than discovering it later.

### 4. Observability collapse

Today one call in, one answer out: query, answer, latency, caller, chosen action — all logged.
With retrieval inside a subagent, memo sees scattered uncorrelated `memo_search` calls under the
*subagent's* identity, and **the answer never crosses memo's boundary**. `/answer-loop-audit`
becomes an endpoint over an empty table, taking C-05's learning loop and SC-004's ≥95%
measurement with it.

**Fix:** the agent reports back at completion — answer, citations, action, correlation id
threaded from the calling session. Cheap, and it is also the only thing that makes a wrong
synthesis *correctable* rather than merely catchable.

## What gets deleted — and a pleasant surprise

**`providers/llm/` has never run.** `memo_llm_provider` defaults to `"null"` and the `memo-llm`
session was never created on this fleet. Every LLM path in production has been taking its
degrade branch this whole time. Deleting the standing-session provider, its escalation
machinery, and its 252 lines of tests **breaks nothing** — they test an adapter that never
served a request.

Also deleted: `recall.py`'s LLM fallback and `_compose_answer`, the similarity bands as
*deciders*, `clarify.py`'s 409 round-trip, `_looks_compound`.

**One genuine exception: `auto_store.py`.** It is driven by a hook with **no agent waiting** —
nobody to spawn a subagent. Three coherent answers, materially different: delete it and accept
memo only captures what an agent deliberately hands it; have the hook spawn `memo-memorize` and
eat a ~2s / 15–41k-token floor per exchange; or keep exactly one LLM provider alive for
unattended inference. **My recommendation: have the hook spawn the agent.** It is the only
option that keeps auto-capture working without resurrecting the architecture we are removing.

## The blocker nobody flagged

**The passage index is built and wired to nothing.** `chunking.py` + `passages.py` — 361 lines,
303 test lines, four commits — are imported by **no production code path**. The entire value of
an iterating subagent is cheap paragraph-level search. Until `document_chunks` is wired into a
search path, the agents would be iterating over the same diluted document vectors that are
failing today at 17% rank-1.

**This is the first thing to build.** Everything else is downstream.

## Path forward

| # | step | why it is in this position |
|---|---|---|
| 1 | Wire the passage index into search (`memo_search` → passages, bi-temporal + scope filters always on) | Agents need a good index; nothing else matters until this works |
| 2 | Move the constraints into code: supersede authority, `memo_update` restriction, `verbatim-critical`, constitutional-write rejection | Must land **before** store.py is deleted, or the gates disappear with it |
| 3 | Measure — run the bench + fact set on the passage path against the committed baselines | SC-101/103 exist; flip only if they clear |
| 4 | Build the agents + skills, narrow MCP surface, agent report-back for observability | Now they have something worth iterating over |
| 5 | Delete `providers/llm/`, the mediator judgment code, `clarify.py` | Last, so nothing is removed before its replacement is proven |
| 6 | One clean v1→v2 migration on the finished system | Migrate once |

## The one decision I need

**Do you want a usable v2 corpus sooner, or one clean build?**

- **(a) Migrate after step 3.** You can kick the tires on a real 7,500-memo corpus with working
  passage retrieval in maybe a day. Costs a second migration later.
- **(b) Finish all six steps, migrate once.** Cleaner, and the corpus you eventually touch is the
  real thing. Longer before you can poke at it.

I recommend **(b)** — a migration is ~90 minutes and $0.13, but a corpus migrated twice means
two sets of ids, two audit trails, and a window where nobody is sure which is authoritative. But
you explicitly wanted to kick the tires, so this is genuinely yours.

Everything else above I will proceed on without asking.
