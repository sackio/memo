# Research: Passage-Level Retrieval

## R-01 — Baseline: memo cannot find its own memos above ~1000 tokens

`scripts/memo-retrieval-bench`, v1 @ 7,511 memos, seed 7, limit 10. Query is
each memo's **own exact title**, so rank 1 is the only correct answer.

| band | n | rank-1 | top-5 | absent from top-10 | median score |
|---|---|---|---|---|---|
| 0–200 | 14 | 14 | 14 | 0 | 0.732 |
| 200–500 | 14 | 9 | 12 | 2 | 0.716 |
| 500–1000 | 14 | 10 | 13 | 0 | 0.668 |
| 1000–2000 | 14 | 5 | 8 | 5 | 0.650 |
| 2000+ | 14 | **2** | 4 | **9** | 0.596 |

Raw JSON: `baseline-2026-07-30-v1.json`. 56.4% of corpus content is in memos
≥1000 tokens, 25.1% in memos ≥2000.

**Mechanism confirmed as dilution, not a stale vector.** Nonce probe: a memo
created containing NONCE_A retrieves on NONCE_A; after PATCHing the body to
NONCE_B, NONCE_B retrieves it (0.512) and NONCE_A no longer does. `PATCH`
re-embeds correctly. The competing hypothesis was tested and **rejected**.

## R-02 — Passage counts over the real corpus, measured 2026-07-30

Ran the Phase-A chunker over all 7,515 v1 memos.

| target | passages | ×corpus | memos yielding exactly 1 passage | max passages in one memo |
|---|---|---|---|---|
| 256 | 30,873 | 4.1× | 1,867 (24.8%) | 43 |
| 384 | 21,155 | 2.8× | 3,056 (**40.7%**) | 30 |
| 512 | 16,555 | 2.2× | 3,837 (**51.1%**) | 22 |

**This corrected an assumption in `plan.md`.** The plan's risk section claimed
passage-count inflation was "mitigated by FR-103 — most memos yield one
passage." That is **false at 256 and 384**: at the planned 384-token target only
40.7% of memos stay single-passage, and a quarter of the corpus splits into 4+.
Only at 512 does it cross half. The corpus averages 745 tokens per memo, so the
median memo is comfortably over any target we would reasonably pick.

Nothing about the design changes — FR-103 promises that a memo *short enough*
yields one passage, which holds — but the *rationale* offered in the plan was
wrong and is corrected there. Passage-count inflation is a real 2–4× and must
be justified on its retrieval benefit, not waved away as rare.

**Overlap does not affect passage count**, by construction: `_apply_overlap`
prepends a neighbour's tail to an existing passage rather than creating a new
one. So overlap is purely a retrieval-quality dimension in the FR-112 sweep, and
its cost is embedding tokens rather than row count.

Cost at 384/15%: ~21k passages, ~$0.13 for a full re-embed. Still not a
constraint.

## R-03 — Open: chunk size is NOT yet chosen

FR-112 requires selection by measurement against retrieval quality, which needs
Phases B and C. R-02 sizes the *cost* of each option; it says nothing about
which retrieves better. **Do not read R-02 as a recommendation.** The temptation
is to pick 512 because it produces the fewest rows and the most single-passage
memos — that is an argument about storage, and storage was never the problem.
