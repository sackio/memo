# Retrieval eval set

**Why this exists.** Every claim anyone has made about memo's retrieval quality —
including mine — has been an anecdote. On 2026-09-10 I reported to Ben that two
seats independently re-derived a fact the corpus already held twice, and that
three seats read a deleted id as "renamed". That is a reason to *look*. It is not
a number, and you cannot tell whether a change helped without one.

⛔ **This is the precondition for the reranker question (ask `a2`), not a side
quest.** A reranker reorders candidates that recall already returned. If the
right memo is not in the candidate set at all, reranking cannot help, and the
two failure modes are indistinguishable from the outside. Measuring first tells
you which one you have — and may show the answer is "neither, retrieval is fine",
which saves the build entirely.

## The shape

`queries.jsonl`, one object per line:

```json
{"id": "q001",
 "query": "what a seat actually types to reach memo's transcript search",
 "expect_ids": ["4a605183-..."],
 "source": "session 11c817c8, 2026-09-09 — seat measured the filesystem instead of asking",
 "note": "the memo predates the failure by 35 days"}
```

- `query` — **what the seat actually asked, or would have asked.** ⛔ Not a
  rephrasing that happens to contain the memo's title words. That is the trap
  this whole file exists to avoid: a query written by someone who already knows
  the answer tests nothing, and it will make any retrieval system look good.
- `expect_ids` — memos that genuinely answer it. More than one is fine and
  common; order does not matter.
- `source` — where the query came from, so a successor can judge whether it is
  representative. A query with no provenance is a guess wearing a schema.

## Harvest from real failures, in this order

1. **Known re-derivations.** A seat re-derived something the corpus held: the
   question it was actually answering is the query, and the memo it missed is
   the expectation. These are the highest-value entries — a failure that already
   happened is not hypothetical.
2. **`/search` traffic** with no follow-up read, which suggests nothing in the
   result set was worth opening.
3. **Corrections.** Where a memo-writer found an existing memo the caller did
   not, the caller's framing is the query.

⚠️ **Do not pad this with synthetic queries to reach a round number.** A set of
20 real ones supports a conclusion; 50 with 30 invented does not, and the
invented ones will quietly dominate whatever metric you compute.

## Scoring

`score.py` reports recall@k and MRR against the live store. ⛔ Report the
denominator and the date every time: the corpus changes daily, so a number
without them cannot be compared to the next run.

## Status

**EMPTY — deliberately.** Seeded 2026-09-10 with the structure and the rules,
not with contents. Filling it is the next step, and it needs the real failures
harvested rather than invented in one sitting.
