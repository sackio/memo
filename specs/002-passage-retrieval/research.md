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

## R-04 — Mid-document fact set: 17% rank-1 against the current implementation

Built 2026-07-30 (`factset-mid-document.json`, 30 cases). Method: take memos ≥2000 tokens, pull
a distinctive prose sentence from the **middle third** (skipping headings, tables, code fences
and boilerplate), and use ~18 words of it as the query. The correct answer is the memo it came
from.

These are close to best-case queries — near-verbatim text lifted straight out of the target
document. Against the current document-level index:

| metric | result |
|---|---|
| rank-1 | **5/30 (17%)** |
| in top-5 | 8/30 (27%) |
| absent from top-10 | **16/30 (53%)** |

**The set is valid precisely because it fails.** FR-111 requires a set that the pre-change
implementation cannot pass; one that passed today would be measuring something other than the
defect. Over half the time, quoting a memo's own middle back at it does not retrieve it.

This is the sharpest statement of the problem so far. The own-title measurement (R-01) shows
long memos are hard to find *by name*; this shows their **contents are not addressable at all**.
For a system whose entire purpose is recalling specific facts, that is the finding that matters.

Runnable: `memo-retrieval-bench --factset specs/002-passage-retrieval/factset-mid-document.json`.
SC-103 requires ≥75% rank-1 here after the change.

---

## R-05 — First document-vs-passage comparison, 2026-07-30 (SC-101 does NOT pass yet)

Both retrieval paths are live at once (FR-113), so this is the same corpus, the same
queries and the same sample scored twice — once through `/search`, once through
`/search-passages`.

**Restricted to the 408 memos present in BOTH indexes** (`--both-indexed-only`). This
restriction is load-bearing: passage coverage is currently 408/1655 (24.7%) of the corpus,
so an unrestricted passage run scores every un-indexed memo as `absent` and reports
**indexing coverage while looking exactly like a retrieval result**. A first pass at this
measurement made that mistake and showed the passage path doing *worse*; the number below
is the corrected one.

Own-title set, `--per-band 14 --seed 7`, n=59:

| band | n | document rank-1 | **passage rank-1** | document top-5 | passage top-5 |
|---|---|---|---|---|---|
| 0–200 | 3 | 3 | 3 | 3 | 3 |
| 200–500 | 14 | 11 | 11 | 11 | 11 |
| 500–1000 | 14 | 9 | **10** | 11 | 11 |
| 1000–2000 | 14 | 9 | **10** | 10 | **12** |
| **2000+** | 14 | **0** | **5** | 3 | **11** |
| absent from top-10 (all bands) | 59 | **12** | **4** | | |

**The 2000+ band goes from 0/14 to 5/14 rank-1, and from 3/14 to 11/14 in the top 5.** That
is the defect this feature exists for, moving in the right direction and by a lot. Absent
-from-top-10 drops 12 → 4 across the board. No band regresses, so **SC-102 holds**.

**SC-101 does NOT pass.** It requires ≥80% rank-1 for memos ≥2000 tokens; this is 36%.
Reported as measured rather than adjusting the bar.

Chunking is still at the untuned default (384 tokens, 15% overlap). **T253** — the
{256, 384, 512} × {0, 15, 25}% sweep — is the designed way to close 36% → 80%, and is
exactly what the spec said to measure rather than guess. The mid-document fact set (R-04,
SC-103) has not yet been re-run against the passage path.

Reproduce:

```
memo-retrieval-bench --url http://localhost:8091 --per-band 14 --both-indexed-only --path document
memo-retrieval-bench --url http://localhost:8091 --per-band 14 --both-indexed-only --path passages
```
