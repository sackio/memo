# Knowledge-base audit and proposal

**Author:** `memo` seat · **Date:** 2026-08-05 · **Requested by:** Ben
**Sources:** memo `00cf9e56` (fleet survey) · `a662e90f` (open-source survey + scout pass) · `4d50bbdf` (migration record)

---

## 0. Scope, and what I did not check

This audits **five vector corpora across four hosts**, the open-source field, and memo's own
retrieval. It proposes changes to memo, and separately notes what each other seat said it
needed — **those are their calls, not mine.**

⛔ **Explicitly not verified**, so nobody reads more into this than it supports:

- `embeddings` states their own inventory covers *"everything I've confirmed, not everything
  there is"* — `hi-score` and `quantum-feed` are **unaudited either way**.
- I have not read `vectara/open-rag-eval`'s method, only its README claim.
- Every retrieval number below comes from memo's own bench. **No other seat has a baseline**,
  so nothing here compares corpora.
- HN coverage is **the current front page only**. The `hn` tool cannot search history.

---

## 1. The estate

| corpus | model / dims | size | hosted? |
|---|---|---|---|
| memo v1 `:8000` | OpenRouter 3-small / 1536 | ~8k docs | **yes — paid** |
| memo v2 `:8091` | OpenRouter 3-large / 3072 | ~8k docs | **yes — paid** |
| groton | qwen3-4b / 2560 | 8.7k docs / 41k chunks, growing | local |
| quantum-data | qwen3-4b / 2560 | ~97% of endpoint traffic | local |
| mind | 3-large/3072 **and** qwen3/2560 | ~2.2M news chunks + ~475k | local |
| dojo | 3-large / 3072 | 305,657 docs, **frozen** | local |

⇒ **Only memo is on a paid API.** Fleet-wide "embedding spend" is almost entirely this project,
which is the opposite of what the phrase suggests.

---

## 2. The finding

**All four seats answered "how would you know if retrieval got worse?" with *I wouldn't*.**
No noise floor ⇒ a regression and normal variance are the same observation.

The decisive datum: **the one time anybody measured retrieval quality, it reversed a completed
migration.** memo-v2 had already moved to local qwen3 and was running fine on throughput and
cost; measurement moved it back — **63.7% vs 54.3% rank-1**. `groton`, `quantum-data` and `mind`
migrated on throughput and cost with no before/after eval.

⭐ **And every failure any of us reported looks exactly like success from the inside:**

- `mind` can flip one `is_active` flag and make **2.28M news articles invisible** — right
  latency, right result count, plausible ranked passages, nothing raised.
- `dojo` has manifests that reliably detect *change* — **a change detector wearing a quality
  detector's clothes.** Silent when the pipeline is stable and the answers are bad.
- `groton` has a sentinel that is **red, has been, and has been walked past.**
- `memo` (me, this week) shipped a read-back check that proved the **transport**, not the read:
  two files stored partial behind 200s and byte-identical read-backs.
- `embeddings`: *"degradation here is silent by construction. There is no exception to catch."*

⇒ **The operational test, from `embeddings` and `mind`, which I propose as the standard:**

> **If the fact that currently makes this safe changed, would anything in the thing's own path
> notice?**

A control you can point at in the execution path is a control. A fact about the world that
happens to hold is a coincidence with good manners.

---

## 3. Proposals for memo

Ranked by value ÷ cost. **P1–P2 are Ben's stated efficiency directive; P3 is the one I would
argue for hardest.**

### P1 — Hybrid keyword + vector retrieval · *small, low risk*
Dense vectors are **worst on literals**, and literals — IPs, ports, model names, codes — are
most of what memo is asked for. sqlite FTS5 is already available in-process.
📌 **Read `groton`'s working RRF first** (`/mnt/nas/data/code/projects/groton/src/search.py`)
rather than writing a second one. ⚠️ Their mechanics are sound; their **weights are unvalidated
by their own account**, so take the structure and not the constants.
⚠️ Must be measured against the pinned sample — **noise floor ~2.4pt, never quote a smaller
delta.**

### P2 — Adaptive context budget · *small, low risk, directly saves money*
The right memo is **rank-1 ~96% of the time**; the packer still fills 4k with ~6 memos. Stop
when the top hit dominates.
⛔ **Do not trim inside memos — R-25 measured that at −8.8pt. Return FEWER MEMOS, not smaller
pieces.**

### P3 — An evaluation harness that can come out negative · *medium; the one that matters*
`dojo`'s framing is the argument, and it is better than mine: build the fixed query set **not
because it measures quality well — it measures it badly — but because it can come out negative,
and nothing any of us currently has can.**

Requirements, each earned from a specific objection:
1. **Questions must be time-invariant** — what a document says, not what is currently true.
   `groton`: on a corpus with validity windows the *ground truth decays*, so a pinned "what's
   the current rule" turns world-drift into apparent model-drift **and looks exactly like a real
   regression**.
2. **Pinned by someone who does not own the corpus**, or it measures the owner's assumptions
   back at them.
3. **A measured noise floor**, from repeat runs — without it a regression cannot be told from
   variance.
4. **A positive control.** ⭐ A detector never run against a known failure is not a detector.
   `mind` offers the perfect one: flip `is_active` in a test config and confirm the score
   collapses. **If it does not, the harness is worthless and we know in a day.**
5. **Evaluate `vectara/open-rag-eval`** (390★, Apache-2.0) first — *"RAG evaluation without the
   need for golden answers"* would remove requirement 1 rather than mitigate it. ⚠️ Unread;
   "without golden answers" may mean LLM-as-judge, which has its own drift.

### P4 — Bi-temporal validity, NOT recency weighting · *medium; schema change*
⛔ **Do not implement supersession as recency decay.** `groton` killed that: a dated record stays
true *as a record* and becomes wrong *as an answer*; ageing it down breaks *"when did they
decide"* in order to fix *"what's the rule"*. **Two different queries against one chunk; no
scalar separates them.**
⚠️ **Counter-evidence worth respecting:** Mem0's v3 (April 2026) moved *away* from
update/delete-at-write-time to add-only with entity linking. The largest player shipped automatic
supersession and backed off.
⇒ Proposal: **additive metadata** — `valid_from` / `valid_to` / `supersedes` — with
invalidate-and-keep (Graphiti's model), never delete. Retrieval gains an optional *as-of* filter;
default behaviour unchanged.
⇒ **`mind`'s complementary move, which I rate higher than any weighting scheme:** refuse to store
the decaying quantity at all. Their pin now says *"measure before you quote a number"* instead of
carrying the number — costs a query, cannot go stale. Portable as: **mark fields as decaying and
refuse to serve them without a freshness check.**

### P5 — Provenance and lifecycle vocabulary · *small; mostly done*
Already landed via the migration: `source_path`, `source_host`, `source_mtime`, `chunk_index`.
⇒ Add the lifecycle half from `embeddings`' ledger fix: **`status` / `parked_by` /
`owner_moved_to` / `reverses_when`, with `unaudited` as a value that must be written, never left
blank.** Their diagnosis is the point — they had *an activity column doing duty as an
authorization column*, so "authorized but parked" and "unauthorized and idle" emitted identical
evidence: nothing.

### P6 — Scheduled drift check · *trivial*
`memdir-drift-check` exists and runs one `stat` per file. Nothing schedules it. memo holds a
**point-in-time snapshot, not a mirror**, and one file has already drifted.

### ✅ P7 — Phantom-parameter logging · **DONE 2026-08-05, v0.4.1**
Unknown fields are accepted (Ben's call — live callers) and **logged**. ⚠️ HTTP path only; FastMCP
drops unknown kwargs upstream, so **an empty log is not evidence nobody is passing them.**

---

## 4. For the other projects — their calls, recorded not directed

- **groton** — the red sentinel needs triage; it is currently worse than no harness because it
  looks like coverage. They said the eval harness is the piece they would adopt, and that they
  cannot bootstrap a noise floor alone. **I can pin their sample precisely because I am not
  tuning their retrieval.**
- **mind** — the `is_active` flag has no control in its own execution path; their defence is *"a
  line in a rewarm pin telling me not to flip it. That is not a control, it's a note."* Their two
  stores also do not cover the same material (the 2560 collection holds **zero news vectors**).
- **dojo** — nothing checks that a generated label still matches its cell after a re-cluster.
  Once *"dots, dot, pos, ptr, apt"* named a theme covering **34,200 strategies**. Their own
  proposal — a fixed query set with hand-marked expected cells, run on every rebuild — is right.
- **embeddings** — ledger fields adopted 2026-08-05, including `unaudited` as a written value.

---

## 5. What I recommend against

| | why |
|---|---|
| ⛔ **Making memo a configurable platform for all corpora** | The needs are different *data models*, not configurations: validity windows (groton), decaying fields (mind), cluster-label integrity (dojo). No flag spans those. Scale differs by 250× (8k / 305k / 2.2M). memo is live fleet infra; it should not change shape because dojo wants a taxonomy feature. **Stood down by Ben 2026-08-05.** |
| ⛔ **Replatforming onto Mem0 / Zep / Letta / Cognee** | All four **own ingestion** — raw text in, their own extraction and embedding. "Here are my chunks and vectors, you do the weighting" is not an offered mode. Adopting one re-platforms ingestion rather than adding a layer. Their benchmarks (LongMemEval, LoCoMo) measure *conversational* memory; ours is a document corpus. |
| ⛔ **Moving memo to Qdrant** | Its advantages are scale advantages at ~8k docs. Today a restore is `cp memo.db`. The recency scoring that made it attractive is ~20 lines over our own ranking. Reconsider at ~100k docs or real multi-tenancy. |
| ⛔ **Rejecting unknown API parameters** | Ben's call and the right one: four hosts of live callers plus an MCP layer, to punish a typo that costs nothing to tolerate. Log instead. |
| ⚠️ **Adopting the small bitemporal repos** | `inite-ai/inite-brain-service` (32★) is **AGPL** — copyleft, a real constraint on lifting code. `douglasjordan2/c0` (20★, MIT) is a month old. **Read for design; `xtdb` (3k★, MPL-2.0) is the mature reference.** |

---

## 6. Worth watching, not adopting

**`Zero-Mem`** (arXiv 2607.29377, on HN today): **no LLM call anywhere in the memory path** —
only the final answer invokes a model — reporting **−57.6% memory-operation time** vs the fastest
baseline. ⛔ **Code not released** pending peer review.
⇒ This is Ben's cost directive from the opposite side: P1 and P2 reduce tokens *per retrieval*;
this removes the model from the retrieval machinery altogether. Worth weighing before starting
either.

---

## 7. Decisions I need

1. **Order:** P1+P2 (Ben's stated directive, small, measurable) before P3 (the harness), or P3
   first so P1/P2 can be measured properly? ⭐ **I lean P3-lite first** — without a baseline,
   "we improved retrieval" is unfalsifiable, which is the whole complaint in §2.
2. **P4 bi-temporal fields** — additive and low risk, but it is schema surface on live infra.
   Worth it, or park until something needs it?
3. **Cross-seat work** (pinning groton's sample) — that is another project's time as well as
   mine.

⚠️ All of it is currently **parked under the token drought** except what Ben has explicitly
released.
