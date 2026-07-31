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

> ⚠️ **The 36% figure is a small-sample artifact — see R-06.** Re-measured
> 2026-07-31 over the **entire** 63-memo 2000+ band rather than a 14-memo sample, the
> same build on the same corpus scores **37/63 = 58.7%**. The verdict is unchanged
> (SC-101 still fails an 80% bar) but the magnitude was understated by ~23 points, and
> every per-config comparison in the T253 sweep that rested on n=14 was inside the
> noise. Prefer R-06's numbers to this table's for the 2000+ band.

Chunking is still at the untuned default (384 tokens, 15% overlap). **T253** — the
{256, 384, 512} × {0, 15, 25}% sweep — is the designed way to close 36% → 80%, and is
exactly what the spec said to measure rather than guess. The mid-document fact set (R-04,
SC-103) has not yet been re-run against the passage path.

Reproduce:

```
memo-retrieval-bench --url http://localhost:8091 --per-band 14 --both-indexed-only --path document
memo-retrieval-bench --url http://localhost:8091 --per-band 14 --both-indexed-only --path passages
```

---

## R-06 — The chunk-size sweep, and why its answer is "not this lever" (2026-07-31) [002/FR-112]

T253 asked which `(target, overlap)` closes SC-101. The sweep ran, and the honest
answer is **none of them — and the question was being asked at a sample size that
could not have answered it either way.**

### The sweep as specified

Nine configs, `{256, 384, 512} × {0, 15, 25}%`, each a full re-index of the same
fixed 408-document control set, then the own-title bench at `--per-band 14 --seed 7`
(the same instrument and seed as R-05). Driver: `scripts/memo-chunk-sweep`.

| config | passages | /doc | median tok | 500–1k | 1k–2k | **2000+** | **2000+ %** |
|---|---|---|---|---|---|---|---|
| 256/0% | 2515 | 6.16 | 244 | 10/14 | 9/14 | 5/14 | 35.7 |
| 256/15% | 2515 | 6.16 | 244 | 10/14 | 9/14 | 6/14 | 42.9 |
| 256/25% | 2515 | 6.16 | 244 | 10/14 | 9/14 | 6/14 | 42.9 |
| 384/0% | 1758 | 4.31 | 332 | 9/14 | 10/14 | 5/14 | 35.7 |
| 384/15% *(default)* | 1758 | 4.31 | 332 | 10/14 | 10/14 | 5/14 | 35.7 |
| 384/25% | 1758 | 4.31 | 332 | 10/14 | 9/14 | 5/14 | 35.7 |
| 512/0% | 1373 | 3.37 | 424 | 10/14 | 11/14 | 5/14 | 35.7 |
| 512/15% | 1373 | 3.37 | 424 | 10/14 | 10/14 | 5/14 | 35.7 |
| 512/25% | 1373 | 3.37 | 424 | 10/14 | 10/14 | 6/14 | 42.9 |

**The entire spread of the gating metric is 5 or 6 documents out of 14.** Nothing here
is an effect. Declaring 512/25% the winner on a one-document margin — which the
driver's own `best` line does, and which is why that line is advisory and not written
into the spec — would be reading noise as signal.

### The re-measurement that settles it

The 2000+ band contains **63** memos in the control set, so it does not have to be
sampled at all. Measuring the whole band:

| config | 500–1k | 1k–2k | **2000+ rank-1** | **2000+ %** | 2000+ top-5 | fact set |
|---|---|---|---|---|---|---|
| 512/25% | 49/63 | 43/63 | **37/63** | **58.7** | 56/63 (88.9%) | 4/8 |
| 384/15% *(default)* | 49/63 | 42/63 | **37/63** | **58.7** | — | 5/8 |

**Identical on the gating metric.** The two configs furthest apart in the sweep — one
at 3.37 passages/doc, the other at 6.16 — retrieve exactly the same number of long
memos at rank 1. The 1k–2k and fact-set columns differ by one case each, which at
these denominators is nothing.

### What this changes

1. **SC-101 still fails, and the verdict is unchanged.** 58.7% against an 80% bar.
   The bar was not moved; the default was not flipped.
2. **The measured gap was never 36%.** That was 14 samples of a 63-memo population.
   The true value for the same build is 58.7% — the passage path was ~23 points
   better than R-05 recorded. R-05 has been annotated in place rather than rewritten,
   so the error stays legible.
3. **T253's premise is disproved.** The sweep was designed as "the way to close 36% →
   80%". Chunk geometry does not move this number at all, so the remaining 21 points
   are somewhere else: query formulation, the scoring/ranking step, or the embedding
   model (which is T203's open question about `text-embedding-3-large`, still with
   the operator). Continuing to tune chunk sizes would be work that this measurement
   has already shown cannot pay.
4. **Top-5 is 88.9%.** The answer is nearly always retrieved; it is the rank-1
   ordering that misses. That is a re-ranking problem, not a chunking problem, and it
   is a much better-shaped one — it points at T201's still-open result-shape decision.

### A defect found in the instrument, again

`--both-indexed-only` restricted the own-title sample but was **never applied to the
fact set**. 22 of the 30 fact cases target memos with no passages, so on the passage
path they are unreachable by construction and scored `absent`: the fact set read
**5/30 (17%)** when the retrieval number among cases the path can actually reach is
**5/8**. That is the third appearance of one confound — coverage wearing retrieval's
clothes — after the original document-vs-passage error and the R-05 restriction.
Fixed in `memo-retrieval-bench`, which now applies the restriction to both sets and
**prints the excluded count on every run**, because a silently reduced denominator is
how a partial index flatters itself.

**SC-103 therefore still has no fair measurement.** 5/8 is honest but n=8 is too small
to gate on, and the fix is coverage, not chunking: passage-index the 22 fact-set
targets (~66k tokens) so the criterion can be judged at n=30. Tracked as T253a.

### Reproduce

```
docker cp scripts/memo-chunk-sweep memo-v2:/tmp/ && \
docker cp scripts/memo-retrieval-bench memo-v2:/tmp/ && \
docker cp specs/002-passage-retrieval/factset-mid-document.json memo-v2:/tmp/

# the 3x3 sweep (9 full re-indexes of the control set, ~620k embedded tokens each)
docker compose exec memo-v2 python /tmp/memo-chunk-sweep \
    --factset /tmp/factset-mid-document.json --out /tmp/chunk-sweep.json

# the full-band measurement that supersedes it
docker compose exec memo-v2 python /tmp/memo-retrieval-bench \
    --path passages --both-indexed-only --per-band 63 \
    --factset /tmp/factset-mid-document.json
```

The sweep re-asserts that the control set is byte-identical between rounds and
**aborts** if it changed, so a coverage shift can never be reported as a retrieval
difference. The passage index is left at the documented default, 384/15%.
