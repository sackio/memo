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

---

## R-07 — The measurement R-05 should have been: whole bands, both paths (2026-07-31)

R-05 compared the two paths at `--per-band 14`. R-06 showed that sample was too small
to support the conclusions drawn from it. This is the same comparison run properly —
**every eligible memo in each band, both paths, one sample** — plus the first SC-103
number that is not confounded by coverage.

Corpus: the 412 memos with passages (408 + the 4 fact-set targets indexed by T253a).
Chunking at the documented default, 384/15%.

| band | n | doc rank-1 | **passage rank-1** | doc top-5 | passage top-5 | doc absent | passage absent |
|---|---|---|---|---|---|---|---|
| 200–500 | 65 | 54 (83.1%) | 55 (84.6%) | 58 | 58 | 4 | 5 |
| 500–1000 | 67 | 56 (83.6%) | 56 (83.6%) | 59 | 60 | 3 | 4 |
| 1000–2000 | 67 | 28 (41.8%) | **46 (68.7%)** | 42 | **62** | 18 | **3** |
| **2000+** | 66 | **10 (15.2%)** | **38 (57.6%)** | 22 | **56** | **36** | **8** |

**The defect and the fix, both larger than previously recorded.** On the band this
feature exists for, the document path retrieves the right memo at rank 1 **15% of the
time** and cannot find it at all in **55%** of cases. The passage path takes that to
57.6% rank-1 and 12% absent — a 3.8× improvement in rank-1 and a 4.5× reduction in
outright misses. The 1000–2000 band, which R-05 read as a wash (9/14 → 10/14), is
actually 41.8% → 68.7%.

### SC-103, measured without the confound

| path | rank-1 | top-5 | absent |
|---|---|---|---|
| document | **1/12 (8%)** | 3/12 | 8/12 |
| passages | **8/12 (67%)** | 11/12 | 1/12 |

Quoting a memo's own middle third back at it retrieves it 8% of the time through the
document index and 67% through passages. This is the sharpest statement of the
feature's value in the whole research record — and it is the number R-04 predicted
would move.

### The criteria, stated as measured

- **SC-102 holds.** No band regresses on rank-1 (500–1000 is exactly equal, 200–500
  gains one). Worth saying plainly, since it is the one criterion that passes: the
  `absent` column ticks up by one in each of the two short bands, which is noise at
  these denominators but is recorded rather than smoothed.
- **SC-101 FAILS** — 57.6% against ≥80%.
- **SC-103 FAILS** — 67% against ≥75%, at n=12.

**The default is not flipped.** Two criteria miss; that is the whole point of having
them.

### ⚠️ SC-103 cannot reach its designed size on this corpus — and that is not a bug

The fact set names **30** memos. Only **12 exist in the v2 corpus at all.** It was
built against v1 (7,511 memos) while v2 currently holds a partially-migrated 1,655.
T253a indexed the 4 targets that were present-but-unindexed (32 passages; the coverage
gap is now zero), but the missing 18 cannot be indexed because they were never
migrated.

So **SC-103 is capped at n=12 until the v2 corpus is complete**, which makes it
dependent on **T202** — the open question of what to do with the partial corpus (roll
back and re-migrate, or backfill in place). T202 was previously understood to block
only T270. It also gates whether a Phase-E success criterion can be evaluated at its
intended power, and that should be weighed in the decision.

Reproduce:

```
docker compose exec memo-v2 python /tmp/memo-retrieval-bench \
    --path document --both-indexed-only --per-band 67 \
    --factset /tmp/factset-mid-document.json
docker compose exec memo-v2 python /tmp/memo-retrieval-bench \
    --path passages --both-indexed-only --per-band 67 \
    --factset /tmp/factset-mid-document.json
```

---

## R-08 — The duplicate thresholds, checked (2026-07-31) [002/FR-114]

T254 asked whether passage vectors change the dedup calibration. Two answers, and the
first one narrows the question.

### The read-path "0.80 bar" is not a vector measure at all

The task was written as though `DUPLICATE_CONTENT_THRESHOLD = 0.80` were a cosine bar
calibrated on document vectors. It is **Jaccard over content words**
(`mediators/filters.py`), and `DedupFilter`'s own docstring already says why: on the
read path we have each candidate's similarity to the *query*, never to each other, and
fetching every pair's embedding "would cost more than the dedup saves." **Passage
vectors cannot affect it, so there is nothing to recalibrate.**

That leaves only the migration-time rule — `DUP_COSINE = 0.90` **and** title-4gram
`>= 0.60` (`migrate/backfill.py`) — which compares whole-document embeddings. Passage
retrieval adds an index; it does not replace those embeddings. So both thresholds are
**unchanged, and the reason they are unchanged is structural rather than empirical.**
Recorded here so the next reader knows it was checked rather than skipped.

### What the rule actually does on this corpus

Measured anyway, because "unchanged" is a claim about the rule and not about the data.
400-memo random sample (seed 11), each memo's single nearest neighbour by cosine, 384
distinct pairs:

| | pairs |
|---|---|
| cosine ≥ 0.90 **and** title-4gram ≥ 0.60 → **would collapse** | **0** |
| cosine ≥ 0.90 but title-4gram < 0.60 → gate prevents collapse | **87** (22.7%) |

**The rule collapses nothing on this corpus.** Nearly a quarter of memos have a
near-neighbour above the cosine bar, and the 4-gram gate stops every one of them. The
gate is not a tiebreaker here; it is the entire decision.

### And the near-duplicates are ours

Fifteen of the top eighteen pairs are **memo-minder's own backfill checkpoints** —
124 of the 1,655 live memos (7.5%). The extreme case:

```
0.9998  ng=0.25   'Backfill checkpoint — server5 — 2026-07-30 07:13'
                  'Backfill checkpoint — server4 — 2026-07-30 07:13'
```

Cosine 0.9998 is as close to identical as this corpus gets, and these are *genuinely*
redundant: office/server4/server5 all proxy to one database since the 2026-06-29
single-global refactor, so three per-host checkpoints were three copies of one fact
written into one store, daily. The 4-gram gate scores them 0.25 and keeps all three —
**the titles differ in exactly the token that carries no information** (the hostname),
which is a weak signal for machine-generated titles built from a template.

Two things follow, and they point in opposite directions, which is why neither was
acted on here:

- **For the migration rule this is arguably correct behaviour.** Checkpoints carry
  dedup keys; collapsing them would destroy idempotency state, and a rule that fused
  operational logs because their prose is similar would be worse than one that fuses
  nothing.
- **But it means the cosine bar is untested on this corpus.** Zero collapses is not
  evidence that 0.90 is well-placed; it is evidence that the gate fires first every
  time. A must-collapse/must-not-collapse pair set drawn from *content* memos rather
  than logs would be needed to calibrate the cosine bar itself, and this corpus —
  partially migrated, 7.5% machine logs — is not the corpus to draw it from. That is
  another dependency on **T202**.

The source of the duplication was fixed upstream on 2026-07-31: memo-minder now writes
one checkpoint per cycle instead of three, with the reason recorded in the memo itself.
The 124 historical ones are left alone.

Reproduce: `scripts/memo-dup-threshold-check`.

## R-09 — The re-migration onto text-embedding-3-large, and the measurement that follows (2026-08-01) [002/FR-111 002/FR-110]

> ⚠️ **STILL THE 3-large REFERENCE POINT, AND NO LONGER THE CURRENT BUILD.** The corpus has
> since moved to `qwen3-embedding-4b` (R-10) and then to qwen3 **with the query instruct
> prefix** (R-13). **R-13 is the current document-path record.** Against these numbers it
> loses the short bands (−5.3 / −11.1 / −7.9) and wins the long ones (+11.5, **+28.1**),
> taking 2000+ from **18.8% → 46.9%**. ⛔ **Quoting R-09's totals as "the baseline" without
> saying which comparison is meant is the drift R-13's coda documents.**


**Every number in R-01 through R-08 was measured on `text-embedding-3-small` at 1536
dimensions against a partially-migrated corpus. None of them carry over.** Ben ruled on
2026-07-31 to use the large model — the one everything else here uses — so the corpus was
rolled back and re-migrated from scratch. This section replaces those figures. Where it
contradicts an earlier R-number, this one is current; the earlier one is history.

Reproduce: `scripts/memo-retrieval-bench --path {document,passages} --both-indexed-only
--per-band 2500 --factset specs/002-passage-retrieval/factset-mid-document.json`.
Raw output is checked in as `bench-2026-08-01-{document,passages}.{json,txt}`.

### The migration

| | |
|---|---|
| v1 memos fetched | 7,661 |
| written | 7,336 |
| merged into a canonical (with redirect) | 325 |
| **skipped** | **0** |
| **errored** | **0** |
| legacy-unattributed | 8 (0.10%, budget 5%) |
| exit code | 0 |

Contrast the 2026-07-31 run, which exited 0 having failed 3,489 of 7,657 memos when the
provider ran out of credits and every exception was folded into `skipped`. That is why
`errored` is now a separate counter with its own exit code, and why a zero here means
something.

Verification (`memo-migrate-verify` over all 7,661 source ids) passes 4 of 5 checks:
every v1 id resolves (7,661/7,661), 0 unclassified, legacy 0.1% of a 5% budget, 0 bad
`valid_from`. The failure is `no_duplicate_clusters` — 4 exact-duplicate content groups,
0.05% of the corpus — and it is **R-08's prediction coming true, not a new defect**: all
four are pairs whose content is byte-identical and whose titles differ (two
`Backfill checkpoint — office…`/`— server4…` pairs differing in exactly the hostname
token, a Storage Taxonomy memo where one copy carries a long inline `[⚠️ STALE …]`
annotation in its *title*, and one fact stored under two phrasings). R-08 measured that
the 4-gram title gate fires first every time and named the office/server4 checkpoint pair
as the extreme case. Content-identical memos are the strongest possible case for a merge
and the gate blocks them, which says the conjunction is misplaced: an exact content match
should not need the title's permission. Deliberately **not** changed here — altering a
dedup rule to make a verifier green, on the day the corpus was rebuilt, produces a corpus
nobody can reason about.

### The measurement — full census, both paths

Not a sample. Every titled memo in every band, 7,221 queries per path. R-05's headline was
wrong because n=14 per band could not distinguish a real effect from one document of noise.

| band | n | document rank-1 | passages rank-1 | Δ |
|---|---|---|---|---|
| 0–200 | 1216 | 955 (78.5%) | 937 (77.1%) | **−1.5** |
| 200–500 | 2293 | 1763 (76.9%) | 1651 (72.0%) | **−4.9** |
| 500–1000 | 2007 | 1538 (76.6%) | 1495 (74.5%) | **−2.1** |
| 1000–2000 | 1306 | 677 (51.8%) | 895 (68.5%) | **+16.7** |
| **2000+** | **399** | **75 (18.8%)** | **190 (47.6%)** | **+28.8** |

Mid-document fact set (n=30, all 30 targets reachable — passage coverage is 100%, so the
`--both-indexed-only` restriction is a no-op here and the confound that produced three
earlier wrong answers cannot apply):

| | document | passages |
|---|---|---|
| rank-1 | 4/30 (13.3%) | **14/30 (46.7%)** |
| top-5 | 13/30 (43%) | **26/30 (87%)** |
| absent | 12/30 | **2/30** |

### Verdicts, stated plainly

- **SC-101 — FAIL.** Needs ≥80% rank-1 at ≥2000 tokens. Passages give **47.6%**. Against
  the 14% baseline in the criterion's own text this is a large gain, and it is not the bar.
- **SC-102 — FAIL.** Requires that *no* band regress. Three do: −1.5, −4.9, −2.1 points on
  the short bands. Small, consistent, and real at n=1216–2293 — not noise, which is exactly
  what a full census buys.
- **SC-103 — FAIL.** Needs ≥75% rank-1 on mid-document facts. Passages give **46.7%**.

### What the numbers actually say

**The larger embedding model alone did not fix long memos.** The document path at 2000+ is
18.8%, against R-01's 14% on the small model. Retrieving a 3,000-token memo as one vector
fails for a structural reason a better encoder does not address: the fact is diluted by
everything around it. That was the premise of this whole feature and it now has a direct
measurement rather than an inference.

**The remaining gap is ranking, not retrieval.** Top-5 on the fact set is 26/30 (87%) while
rank-1 is 14/30 (47%). The correct memo is nearly always *found* and lands at position 2–5.
No amount of further indexing moves that; a re-ranker over the candidate set does. This is
the strongest available evidence for T240–T243 being the next work, and it converts them
from a design preference into the measured bottleneck.

**The short-band regression is the honest cost.** Chunking a 200-token memo splits an
already-coherent unit, and its best passage matches a title query slightly worse than the
whole memo does. SC-102 exists to catch exactly this. It argues for routing by size rather
than replacing one path with the other — the document path is better below ~1000 tokens and
much worse above ~2000, and both are live (FR-113), so the router already has what it needs.

### T271 was not achieved as written

T271 asks for the corpus to be "chunked once rather than embedded twice". It was not: run 3
wrote document embeddings only, so the passage index was a **second** full-corpus embed
(~$0.85). Paid deliberately — a third migration costs more — but the task is recorded as
done-with-deviation rather than done, and inline chunking is still owed for the next
migration.

### One measurement caveat

One passage-path query (`ec06fbb6`) timed out at 60s and was scored `absent`, which is the
conservative direction. 1 of 7,221.

## R-10 — the move to qwen3, measured: both paths regress, and the passage path regresses everywhere (2026-08-02) [002/FR-111]

> ⛔ **SUPERSEDED ON THE DOCUMENT PATH BY R-13.** Every number in this section was measured
> against a client sending **bare queries** to an encoder trained to expect an instruction
> prefix. R-11 identifies the cause; **R-13 measures the fix on the full corpus**: document
> rank-1 **49.4% → 66.4%**, `absent` **1,507 → 601**. ⇒ **Do not quote R-10's document
> columns as qwen3's performance — they are the performance of a defect that has been fixed.**
> ⚠️ **The passage columns here are NOT yet superseded**: that half of the census was still
> unfinished and contaminated at the time of writing (90 timeouts during a host incident).
> **Until it lands, R-10 remains the only passage-path qwen3 measurement, floor and all.**


**Read R-11 with this section. Every number here was measured against a client that sends
bare queries to an encoder trained to expect an instruction prefix, so these are a floor for
qwen3, not a verdict on it.** Sections below are in write order; R-11 is the cause and R-12 is
an unrelated threshold problem found the same night.

Full census, matched to R-09: seed 7, depth 10, `--both-indexed-only`, 7,221 own-title queries
per path plus a 30-case fact set — 7,251 requests per path, confirmed against the server's own
access log rather than inferred. Document path finished 04:14:57 EDT, passages 06:44:02 EDT.
Raw output is `bench-2026-08-02-qwen-{document,passages}.{json,txt}`.

### Passages — every band worse

| band | n | rank-1 3L → qwen3 | Δpt | top-5 3L → qwen3 | Δpt | absent |
|---|---|---|---|---|---|---|
| 0–200 | 1216 | 937 → 733 | −16.8 | 1159 → 1019 | −11.5 | 35 → 139 |
| 200–500 | 2293 | 1651 → 959 | −30.2 | 2146 → 1544 | −26.3 | 98 → 575 |
| 500–1000 | 2007 | 1495 → 889 | −30.2 | 1839 → 1358 | −24.0 | 124 → 512 |
| 1000–2000 | 1306 | 895 → 520 | −28.7 | 1170 → 825 | −26.4 | 96 → 380 |
| **2000+** | 399 | **190 → 101** | **−22.3** | **268 → 185** | **−20.8** | 121 → 190 |
| **TOTAL** | 7221 | **5168 → 3202** | **−27.2** | **6582 → 4931** | **−22.9** | 474 → 1796 |

rank-1 71.6% → 44.3%; top-5 91.2% → 68.3%. Fact set: rank-1 **14 → 14**, top-5 26 → 21,
absent 2 → 7.

### Document — worse everywhere except the longest band

| band | n | rank-1 3L → qwen3 | Δpt | top-5 3L → qwen3 | Δpt | absent |
|---|---|---|---|---|---|---|
| 0–200 | 1216 | 955 → 751 | −16.8 | 1170 → 1044 | −10.4 | 32 → 126 |
| 200–500 | 2293 | 1763 → 996 | −33.4 | 2181 → 1608 | −25.0 | 69 → 523 |
| 500–1000 | 2007 | 1538 → 1019 | −25.9 | 1897 → 1431 | −23.2 | 73 → 434 |
| 1000–2000 | 1306 | 677 → 635 | −3.2 | 1012 → 941 | −5.4 | 201 → 282 |
| **2000+** | 399 | **75 → 169** | **+23.6** | **162 → 235** | **+18.3** | 215 → 142 |
| **TOTAL** | 7221 | 5008 → 3570 | −19.9 | 6422 → 5259 | −16.1 | 590 → 1507 |

rank-1 69.4% → 49.4%; top-5 88.9% → 72.8%. Fact set: rank-1 4 → 5, top-5 13 → 11, absent
12 → 15.

### The asymmetry has an explanation, and it is checkable

**The document path's single gain is in the 2000+ band. The passage path has no such band, and
that is not a coincidence — it has no long stored vectors at all.** Every passage vector is a
chunk of a few hundred tokens, so a 2000-token memo is represented in that index by short
text. The passage path is therefore *entirely* short-text retrieval regardless of how long the
underlying document is.

⇒ So the one place bare qwen3 beats `text-embedding-3-large` is the one place memo stores a
**long** vector, and the index that stores no long vectors gets no benefit anywhere. This is
consistent with R-11, which measures the missing query prefix recovering precisely the
short-text deficit: the effect is largest in the 200–1000 bands, on both paths, and the
document path's 2000+ band is the only cell in this census that never needed it.

### Success criteria

- ⛔ **SC-101 fails, and by more than in R-09.** Passages rank-1 at 2000+ is 101/399 (25.3%)
  against a ≥80% bar; it was 190/399 (47.6%).
- ⛔ **SC-102 fails outright.** It requires that *no* band regress against its pre-change
  rank-1 rate. **Nine of ten band-path cells regress**, several by 25–33 points.
- ⛔ **SC-103 fails, unchanged.** Fact-set rank-1 is 14/30 (46.7%) against a ≥75% bar —
  numerically identical to R-09, while top-5 fell 26 → 21 and absent rose 2 → 7.

### The pre-registration, judged

**Registered before the data: "rank-1 at ≥2000 tokens moves materially; top-5 barely moves."**
It was about the passage path (SC-101), so that is where it is judged.

| passages, 2000+ | 3-large | qwen3 | move |
|---|---|---|---|
| rank-1 | 47.6% | 25.3% | −22.3 pt |
| top-5 | 67.2% | 46.4% | −20.8 pt |

**The first clause holds and the second fails.** rank-1 did move materially; top-5 moved almost
exactly as much. ⇒ **And it failed the same way on the document path** (+23.6 rank-1 against
+18.3 top-5), so this is not a passages-specific miss. The prediction's underlying assumption
— that the ≥2000 band's problem was *ranking* rather than *retrievability*, so the target was
usually already in the top-5 and merely mis-ordered — **is wrong on both paths.** Retrievability
moves with the encoder about as much as rank does. Recorded because the assumption, not the
number, is what other work was leaning on.

### Conditions

Both runs: 2026-08-02, seed 7, depth 10, `--both-indexed-only`, `--per-band 2500` (no band
reaches the cap). The two bench processes ran concurrently until 04:14:57, after which passages
ran alone; the R-11 prefix experiments queried the same endpoint between 04:20 and 05:15.
**Contention affects throughput, not results** — the retrieval counts are deterministic given
the stored vectors, whose own reproduction drift is 1.18e-04 (R-11's calibration), far below
anything that could move a rank.

⚠️ Both artifacts **predate the per-band `query_failures` counter**, so the tool correctly
reports that field as UNKNOWN rather than 0 for them. Zero failures was established separately
and by measurement: both `fd/1` and `fd/2` of each run pointed at the captured output file, and
neither contains a `query failed` line. A `grep` alone would not have shown this — it reads
identically whether nothing failed or stderr was never captured.

## R-12 — the duplicate cutoffs are calibrated against an encoder the corpus no longer uses (2026-08-02) [002/FR-114]

R-08 chose duplicate thresholds by measurement — against `text-embedding-3-*`. The corpus is
now `qwen3-embedding-4b`. **A cosine compared to a literal is only stable if both texts are
the same text.** A same-text comparison measures determinism and reads ~1.0 in any space; a
**cross-text** comparison measures semantic scale, which is a property of the encoder. Every
duplicate and relevance cutoff is cross-text.

Measured here, zero endpoint calls — 400 random document pairs, whole-document vectors on
both sides, so the shape confound that invalidated the `size-routed` probe cannot apply:

| qwen3 random-pair similarity | |
|---|---|
| median | 0.3293 |
| p90 | 0.5115 |
| p99 | 0.6216 |
| p99.9 / max | 0.8252 |

| live cross-text cutoff | value | random pairs clearing it |
|---|---|---|
| `DUP_COSINE` (`backfill.py:51`) | 0.90 | 0 / 400 |
| `auto_store_similarity_threshold` (`main.py:1423`) | 0.82 | 1 / 400 |
| `memo_recall_min_score` — **dead, see below** | 0.50 | 40 / 400 |

Both live cutoffs now sit **past the 99.9th percentile of unrelated pairs**, i.e. they are
behaving as very strict rules. **Whether that is where they were chosen to sit is not answered
here** — that needs the same pairs scored in both spaces.

⚠️ **v2's own re-embed overwrote its 3-large vectors, so v2 holds one space only. But memo v1
(`:8000`) is still running `text-embedding-3-small` @1536 over substantially the same memos**,
and R-08 — which chose these cutoffs — was measured in the 3-small era. So a paired substrate
for the *original* calibration plausibly still exists, in the service least likely to be
touched. **Not confirmed:** v1's vectors live in a `vec0` virtual table that a plain read-only
`sqlite3` connection cannot open without the extension, and v1 is live fleet infrastructure
that is out of scope for changes, so this was not pursued further. Recorded as *available,
unverified* rather than *gone* — an earlier draft of this section asserted the substrate was
destroyed, which was a guess.

A sibling service that retained both encodings measured its own random-pair median falling
0.416 → 0.303 across a comparable model change; the *direction* is expected to transfer, the
magnitude is theirs and not adoptable.

⛔ **"The failure mode is under-deduplication" is NOT established, and one attempt to establish
it failed.** Random pairs bound the *false*-positive rate; whether a cutoff is too strict
depends on the *true*-positive rate, and a random-pair sample contains no positives. Scoring
the corpus's actual duplicates in qwen space:

| positive set | n | result |
|---|---|---|
| byte-identical content groups (R-09) | 4 | cosine **1.000000**, all clear 0.90 |
| `Backfill checkpoint — …` family (R-08) | 300 pairs | median 0.6797, only 11.7% clear 0.90 |

The first row is a **determinism** check and reads ~1.0 in any space, so it says nothing about
semantic scale — but it does confirm that genuine duplicates are still merged. **The second row
is not usable as stated:** the family is 125 memos selected *by title prefix*, spanning
different hosts and different dates, so most of its 300 pairs are **not duplicates of each
other** — a `server4` checkpoint from July and an `office` one from June are different
content. A median of 0.68 across that set measures family resemblance, not missed duplicates,
and the "265 missed" it appears to show is an artifact of asserting membership rather than
verifying it. ⇒ **Same error as the `size-routed` probe, one level along: the reference
population was assumed rather than checked.**

⇒ So: recalibration needs a set of *verified* duplicate pairs. Until then the cutoffs' current
adequacy is **unmeasured in both directions**.

**The way to get one is to construct it, not to find it.** Identifying natural near-duplicates
requires a similarity judgement, which is the thing being calibrated — the question begs
itself, and answering it by title prefix is what failed above. Instead take N memos and apply
realistic perturbations (whitespace, sentence reorder, a paragraph added, a date changed),
embed both copies, and measure `cosine(original, perturbed)`. **Membership is then true by
construction.** Cost is ~N embeddings; N=50 is minutes.

⚠️ **Its limit, which belongs next to the number it produces:** synthetic perturbations need
not match the natural near-duplicate distribution, so this yields a **detectability bound** —
*the cutoff can/cannot catch a duplicate of this severity* — not the rate at which real
duplicates are missed. **A bound from a known population is still worth more than a rate from
an assumed one.** (Method from the `embeddings` seat.)

⚖️ **What the direction of failure would be, which is worth knowing before anyone panics.** Both
live cutoffs are **floors** — merge/flag *if* cosine exceeds X. A downward shift in semantic
scale makes a floor **stricter**, so it fails toward doing *less*: fewer merges, fewer
flagged similarities. That is the noisy failure mode; it eventually surfaces as duplicates
accumulating. The dangerous shape is a cross-text **ceiling** — pass *if* cosine is *below* X —
which the same shift makes **looser**, so it fails toward a green check and is visible as
nothing, ever. **memo has no cross-text ceiling.** Worth re-checking whenever a new guard is
added, because a ceiling is exactly the shape a safety check naturally takes.

⛔ **And this table cannot tell you what to change `DUP_COSINE` to. Read the 0/400 as a blind
spot, not as a verdict.** A random-pair sample contains **no near-duplicates**, so it holds no
examples of the thing the cutoff exists to admit — and a reference population with zero
positives cannot distinguish one candidate value from another anywhere in that region. The
0/400 says only *"0.90 is above everything this instrument can see"*, which is equally true of
0.86 and of 0.99. **It is not evidence that 0.90 is too strict**, and lowering it on the
strength of this row would be acting on a measurement that has no data underneath it.

⇒ The two mid-range cutoffs are the ones this population can speak to at all, which is the
dangerous combination: **the method returns a confident-looking number for every row, and is
only actually informative for some of them.** Re-deriving the duplicate cutoff needs a
reference set that contains known near-duplicates — the corpus has some (R-08's
`Backfill checkpoint — <host>…` families, and R-09's four byte-identical content groups), so
the labelled set can be built here rather than imported. Framing owed to the `embeddings` seat.

⚑ Prefer recording an **admit rate** over a raw cutoff wherever the reference population can
support one: *"this admits 22.5% of random pairs"* is a policy about the corpus and survives an
encoder change, whereas *"0.45"* is a reading off one encoder's distribution wearing the
clothes of a policy decision. The admit rate is invariant to the encoder, which is precisely
the thing that keeps changing here — and it is not portable between corpora, so each seat
records its own.

**`memo_recall_min_score = 0.5` is dead configuration** — ⚠️ **and "dead" was the wrong
diagnosis; see the correction below.** It is defined in `config.py:76`. It looks like the
relevance floor, and an operator tuning it sees no effect and no error. Recorded rather than
removed: deleting a setting someone may be setting in an env file is its own change.

> **CORRECTED 2026-08-02 (T284), and the correction is the useful part.** The claim above
> said the setting was "read nowhere in the tree". **That is false.** The floor *is* read and
> *does* reach the server: `hooks/memo-auto-recall.sh:6` reads `$MEMO_RECALL_MIN_SCORE` and
> sends it as `min_score` on every `/context` call, which honours it. So the value was live
> the whole time — just not *this* value.
>
> The real defect is one link earlier. `hooks.py` generated `~/.memo/hooks.env` from **five
> hard-coded literals**, so the only writer of the env var was a constant, and
> `MEMO_RECALL_MIN_SCORE=0.35 memo-hooks install` reproduced `0.5`. The Python field and the
> shell consumer had the same name, the same default, and no connection.
>
> ⭐ **Why the first answer came out wrong: the grep was for the Python identifier, and the
> consumer is a shell script.** The method was fine and the population could not contain the
> answer — the same shape as three earlier failures in this file. A setting is dead when
> **nothing reads the file it is written to**, which is a question about consumers in every
> language present, not about one `grep --include=*.py`.
>
> **Resolution: WIRED, not deleted.** `cmd_install` now interpolates the five `settings`
> fields, so `MEMO_RECALL_MIN_SCORE=0.35 memo-hooks install` writes `0.35`. Covered by
> `tests/unit/test_hooks_env.py`, whose assertions deliberately use non-default values —
> written with defaults, every one of them would have passed against the bug.
>
> ⚠️ **Still open, and NOT fixed by wiring it:** `0.5` is a 3-small-era cutoff on a cosine
> scale that has since changed twice. It belongs to the same unmeasured family as
> `DUP_COSINE` and `auto_store_similarity_threshold` (R-12) — wiring made it *reachable*,
> not *correct*.

**The census is not affected by any of this.** The harness sends no `min_score`, and every
filter is guarded by `if min_score is not None`. Confirmed independently by measurement: a
direct vec0 KNN with no floor at all reproduces the census rank-1 rate (49.3% against 49.4%).

## R-11 — qwen3 needs a query instruct prefix, and memo never sent one (2026-08-02) [002/FR-111]

**Read this before R-10.** R-10's qwen3 numbers were measured against a client that was
configured wrong, so they are a floor for that model rather than a verdict on it. This
section is the cause, and it is a defect in memo, not in the model.

Qwen3-Embedding is trained for **asymmetric** retrieval: an instruction prefix on the
**query** side only, documents embedded bare. memo sends bare queries. Everything R-10
reports about the short bands is that omission.

Reproduce with the artifacts checked in beside this file:
`instruct-prefix-2026-08-02.txt` (5 bands, seed 7), `instruct-prefix-seed202-2026-08-02.txt`
(the replication), `instruct-prefix-2000plus-2026-08-02.txt` (the powered long-band run).
Every run is **paired** — each document is queried both ways against the same stored
vectors, so corpus composition cannot explain a difference — at retrieval depth 10.

### Three alternatives refuted by measurement before the prefix was proposed

This is what makes the finding a diagnosis rather than a story that happened to fit.

1. **Did the re-embed change the embedded text?** No. R-09's vectors came from
   `backfill.py:361` embedding `memo["content"]`; `memo-reembed-corpus` reads the same
   `documents.content`. Same input, so the band deltas are a model effect.
2. **Near-duplicate confusion** — the corpus holds ~179 near-identical
   `Backfill checkpoint — <host>…` memos, so perhaps qwen3 cannot pick the right family
   member. **Refuted**: failed queries return *unrelated* documents. Title overlap between
   the query and the top-1 result is a median **0.16** on misses against **1.00** on hits.
3. **Vectors attached to the wrong rows** by a batched writer pairing on position.
   **Refuted**: 25 of 25 whole-document vectors, stratified across all five bands, match a
   fresh singleton re-embed of their own content, negative control 0.2268. ⚠️ This check
   had to be *written* — `memo-verify-provenance` sampled only `document_chunks` /
   `chunk_embeddings` and had never read `document_embeddings`, the table the regressed
   path actually queries. It now checks both (commit `2369889`).

### The effect, and its replication

Band-stratified, 15 documents per band, five bands, n=75 per draw. Only the seed differs.

| | seed 7 | seed 202 | spread |
|---|---|---|---|
| rank-1 | 49.3% → 70.7% (+21.3) | 52.0% → 72.0% (+20.0) | 1.3 pt |
| top-5 | 74.7% → 84.0% (+9.3) | 76.0% → 84.0% (+8.0) | 1.3 pt |
| absent | 15 → 9 (−8.0 pp; −40% of misses) | 15 → 9 (−8.0 pp; −40%) | 0 |
| discordant rank-1 | 18 improved / 2 worsened | 15 / 0 | |
| sign test | p = 0.00020 | p = 0.00003 | |

⚠️ **Quote `absent` in both currencies or not at all.** −40% is a *relative* figure on a 20%
baseline; the absolute movement is **−8.0 percentage points**, 6 documents of 75. The two
answer different questions — *what share of the misses are recovered* versus *how many
documents become findable* — and a relative figure computed from a low baseline reads as a
much larger effect than the same absolute recovery from a high one. This exact substitution
produced a cross-seat "these are different in kind" conclusion elsewhere on 2026-08-02 that
had to be withdrawn: −40% against −11.4% is 3.5× apart in relative terms and 1.4× apart in
absolute ones, and nearly all of the gap was the denominators.

⚠️ **These figures are band-stratified and R-10's are not — do not compare them directly.**
This sample is 15 documents per band, so every band carries equal weight; the census follows
the corpus, where 2000+ is 5.5% and 200–500 is 31.8%. Since 2000+ is the one band where bare
qwen3 is strong and the prefix does nothing, stratification **under**states the prefix's effect
on the real corpus. Re-weighting each band's measured rate by the census distribution:

| | stratified bare → instruct | corpus-weighted bare → instruct |
|---|---|---|
| seed 7 | 49.3% → 70.7% (+21.3) | 51.9% → **77.7%** (+25.8) |
| seed 202 | 52.0% → 72.0% (+20.0) | 53.4% → **74.4%** (+21.0) |

⭐ Two independent checks fall out of this. **The weighted bare figures (51.9%, 53.4%) sit close
to the census's own 49.4%** on a completely separate run, which is a consistency check on the
sampling. And **the weighted prefixed figures exceed `text-embedding-3-large`'s 69.4%** — so on
the corpus's real composition the prefix does not merely recover the regression, it clears the
old baseline.

⚠️ **But re-weighting costs precision, and that belongs with the number.** Band weights are
unequal while the per-band sample sizes are equal, so effective n falls and the estimate gets
noisier: the two seeds are **1.3 points apart stratified and 4.8 points apart weighted**. The
weighted figure is the right comparison to make against R-10 and the *less* stable of the two.
Both seeds still land above 69.4%, which is the claim being made; the exact margin is not
pinned at this n.

The second draw exists because a sibling service measured ~10 points of draw-to-draw
variance on top-5 in the same design, which would have made a +9.3 point result
indistinguishable from the draw taken. It replicates to 1.3 points, and `absent` reproduces
exactly. (The likely reason memo's variance is so much lower: this path stores **one vector
per document**, so no single document can occupy several top-10 slots under resampling.)

**Pre-registration, missed.** Before running, the prediction recorded in the script was that
the prefix would help *little* — the published effect of instruction prefixes is 1–5% and
the gap to close was ~33 points. It is +21 points. **Wrong by an order of magnitude**, and
recorded here because a prediction that survives being wrong is the only kind that shows the
number was not chosen after seeing the data.

### What it does not license

**The gain is not uniform, and the long band does not share it.** The 2000+ band was re-run
at n=120 of 399 because it is the one band where bare qwen3 already *beats*
`text-embedding-3-large` (42.4% vs 18.8%) and therefore the one the prefix could plausibly
harm.

| 2000+, n=120 | bare | instruct | improved / worsened | one-sided sign p |
|---|---|---|---|---|
| rank-1 | 54/120 | 59/120 | 14 / 9 | **0.202** |
| top-5 | 73/120 | 79/120 | 9 / 3 | 0.073 |
| absent | 41/120 | 35/120 | 8 / 2 | 0.055 |

rank-1 was the **pre-registered primary** — "worsened == 0 is the only thing that supports
prefixing everywhere" — and it is null, with nine documents demoted out of rank 1. top-5 and
absent are secondaries, and among three tests on one dataset a p≈0.055 is about what noise
yields at least once; a marginal secondary does not rescue a null primary. Two documents
went from retrievable to **unretrievable**, which neither the rank-1 nor the top-5 row can
see (the nine demotions land at rank 1–2 and stay inside the top-5) and which the favourable
`absent` net prices identically to a document gained.

**This is not evidence that the prefix harms long documents** — that would overstate it in
the opposite direction. Correctly: it does not help them, and it moves nine the wrong way.

⚠️ **The same band at n=15 showed zero harm, twice.** Seed 7 gave 2 improved / 0 worsened and
seed 202 gave 3 / 0, against 14 / 9 at n=120. Those are not three results; they are one
result and two failures to resolve, and the two failures are the ones that read as
reassuring. **A small-n null in this band is no information.**

**No routing by length.** Only 2 of 5 bands are individually significant at n=15, and a
sibling service independently failed to resolve its bands too — that reproducibility makes
it a property of the effect size, not of one sample. So the choice is uniform-or-nothing
*within this corpus*; it is not a claim about corpora whose documents are all short.

### What it would take to ship

**The fix is not one line.** memo has no chokepoint: 25 call sites share a single
`embeddings.embed()`, and the query/document distinction exists only in the local variable
name at each one. Applying a query prefix means introducing `embed_query()` /
`embed_document()` and **deleting the ambiguous `embed()`**, so no site can default — which
turns ~21 silent misclassifications into ~21 forced decisions at edit time. Both directions
of a wrong decision fail silently: a document routed through the query path stores a
prefixed vector, and a query left bare is this regression.

Both directions are already detectable, **by two instruments that must disagree about the
prefix**: the write-side provenance check re-embeds documents *bare*, and the read-side
harness queries *prefixed*. Unifying them for consistency would blind both at once, and it
would look like a cleanup. The constraint is commented at both re-embed lines.

### Ranking layers: what is measured, and the one mode that is not

⛔ **Every number in this section is from the DOCUMENT path only. The prefix has never been
measured on the passage path.** All three runs are a direct KNN against `document_embeddings`;
`chunk_embeddings` is not queried by any of them. R-10's passages column measured the **bare**
arm on that path, and a bare measurement finishing there does not make the prefix measured
there — a distinction that has already been got wrong once by a reader working from a summary
of this file rather than the file.

⚠️ **Do not assume the gain transfers.** The passage path regressed *harder* than the document
path (−27.3 against −19.9 rank-1 points) and contains no long vectors at all, so the 2000+ band
that partly rescues the document path cannot exist there. Whether the prefix recovers the
passage path as well, better, or worse is **open**, and it is the measurement that should
precede any decision that touches passages. Tracked as T282's other half.

Beyond that, every number here was measured on a **single index in isolation** — a direct KNN
against `document_embeddings`.
Checked rather than assumed: **memo has no lexical leg, no BM25, no reciprocal-rank fusion
and no re-ranker anywhere in `src/`.** `/search` reads `settings.memo_retrieval_path`, which
is **`document`** — so the product surface currently serves the same pure document path these
numbers describe, and they apply to it directly.

The exception is the third mode, **`size-routed`** (`_size_routed_search`). It queries both
indexes, merges by document id, and gives each document the score from the path preferred for
its own token count — deliberately not a hybrid score, but still a **cross-path re-sort**. It
is **not active** and no measurement here covers it.

⚠️ **`size-routed` also has a suspected defect that has nothing to do with the prefix.** Short
documents keep a whole-document cosine; long ones keep a chunk-level cosine; the merged list
is then **ranked across both**. Those two scores come from different indexes with different
score distributions — a short passage tends to score higher against a query than a long
document does, for reasons of length rather than relevance — so the ordering would be biased
toward whichever path produces larger numbers, systematically, before any prefix exists.
"Scores unmodified" is the property that makes this wrong rather than the safeguard it reads
as: a fusion would at least rank-normalise, a merge preserves the incomparability. (Raised by
the `embeddings` seat, 2026-08-02.)

**Still SUSPECTED — one attempt to measure it failed, and the failure is worth recording.**
Using stored document vectors as queries costs no endpoint calls, and gave the document index
a +0.0087 median edge with the chunk index winning only 32% of pairs — a real asymmetry, and
in the *opposite* direction to the suspicion. **The pre-registered control killed it.** Re-run
with stored *chunk* vectors as queries, the advantage mirrors: chunk index +0.0172, winning
67%. ⇒ **The probe measures how closely the query's shape matches the index's, not how the two
score scales compare**, so neither run answers the question. A document-shaped query is nearer
document-shaped vectors for reasons that have nothing to do with scale.

⇒ The zero-cost trick — stored vectors as queries — is sound only when the query distribution
does not interact with the quantity being measured. Here it is the confound. **Settling this
needs real short-query vectors, which cost embeddings**, and it is owed before `size-routed`
is enabled rather than now, since the mode is off.

That mode is worth measuring before it is ever switched on, because a sibling service
measured a query prefix at **+7.5 points rank-1 vector-only and −8.0 points through RRF
fusion** on one corpus: a post-embedding ranking layer reversed the sign rather than shrinking
it. memo's merge is a weaker construct — one model, no lexical leg, scores unmodified — so
the same reversal is not expected. **"Not expected" is exactly what would have been said about
the 2000+ band at n=15**, so `size-routed` and the prefix should not be enabled together
until that combination has been measured bare-vs-prefixed the same way.

**Scope of every number above:** one fixed task string, `"Instruct: Given a search query,
retrieve relevant documents that match the query\nQuery: {q}"`, held constant across all
bands and both draws. A sibling measured a domain-specific string at 7/10 against a generic
one at 5/10 with a worse tail, so the string is a live variable and these results do not
transfer to a different one. And because every query is a document's own exact title, the
correct answer is true by construction — read `bare` before the delta; where bare is near
ceiling a null means no power rather than no effect.

## R-13 — the prefix, measured on the full corpus: +16.9 rank-1 vs bare qwen3, and a BAND TRADE against 3-large (2026-08-02) [002/FR-111]

⚠️ **DOCUMENT PATH ONLY. The passage run was paused at ~78% when server4 wedged
under an unrelated 18-hour ZFS saturation and is not in this section.** R-11's
warning still stands unanswered: `chunk_embeddings` has never been queried with a
prefixed vector, the passage path regressed harder in R-10 (−27.2 vs −19.9), and
nothing below transfers to it by assumption.

Same population as R-09 and R-10 — seed 7, depth 10, `--both-indexed-only`, 7,221
own-title queries, verified as 7,336 corpus − 115 blank-title before the run rather
than after. Raw: `bench-2026-08-02-prefix-document.{json,txt}`.

### vs R-10 (bare qwen3) — the defect is real and this is its size

| band | n | rank-1 | Δpt | top-5 | Δpt | absent |
|---|---|---|---|---|---|---|
| 0–200 | 1216 | 751 → 890 | +11.4 | 1044 → 1138 | +7.7 | 126 → 45 |
| 200–500 | 2293 | 996 → 1509 | **+22.4** | 1608 → 2077 | +20.5 | 523 → 134 |
| 500–1000 | 2007 | 1019 → 1379 | +17.9 | 1431 → 1758 | +16.3 | 434 → 169 |
| 1000–2000 | 1306 | 635 → 827 | +14.7 | 941 → 1116 | +13.4 | 282 → 130 |
| 2000+ | 399 | 169 → 187 | +4.5 | 235 → 260 | +6.3 | 142 → 123 |
| **TOTAL** | 7221 | **3570 → 4792** | **+16.9** | 5259 → 6349 | +15.1 | 1507 → 601 |

rank-1 49.4% → 66.4%; top-5 72.8% → 87.9%. **`absent` falls 1,507 → 601** — the
prefix is not re-ranking, it is making documents findable at all.

⚠️ **+16.9 is BELOW the +21.3 / +20.0 that R-11 predicted from two smaller draws.**
The prediction was not wrong by much, but it was high, and it was made on
band-stratified and corpus-weighted samples rather than the full corpus. Record the
full-corpus number as the estimate and the sample numbers as what they were.

### vs R-09 (`text-embedding-3-large`) — ⭐ THE RESULT THAT MATTERS

| band | n | rank-1 | Δpt | top-5 | Δpt |
|---|---|---|---|---|---|
| 0–200 | 1216 | 955 → 890 | −5.3 | 1170 → 1138 | −2.6 |
| 200–500 | 2293 | 1763 → 1509 | −11.1 | 2181 → 2077 | −4.5 |
| 500–1000 | 2007 | 1538 → 1379 | −7.9 | 1897 → 1758 | −6.9 |
| **1000–2000** | 1306 | 677 → 827 | **+11.5** | 1012 → 1116 | +8.0 |
| **2000+** | 399 | **75 → 187** | **+28.1** | 162 → 260 | +24.6 |
| **TOTAL** | 7221 | 5008 → 4792 | **−3.0** | 6422 → 6349 | −1.0 |

⇒ **The aggregate says qwen3 is 3.0 points worse. The aggregate is the wrong
statistic here.** Prefixed qwen3 **loses on short memos and wins decisively on long
ones**, and 002 exists because of long ones. At 2000+ tokens the document path goes
**18.8% → 46.9% rank-1** — it more than doubles, on the single band this feature was
opened to fix.

**A corpus that is 77% short memos will always let the short bands dominate a
corpus-weighted mean.** That is the same shape as R-05's coverage confound and
R-01's own-title framing: the number that reads like a verdict is a property of the
size distribution, not of the encoder.

⛔ **SC-101 still FAILS on the document path**: 46.9% against a ≥80% bar. The
document-as-one-vector ceiling is not removed by a better encoder, only raised —
which is the finding R-09 already recorded and this confirms with a second encoder.

**Fact set (SC-103)**: rank-1 7/30 (23%), top-5 14/30, absent 14/30 — up from 4/30
(R-09) and 5/30 (R-10), still far under the 75% bar.

**Query failures: 9**, reported per band (0–200 = 3, 2000+ = 6) — the first run in
this series that can state that at all. R-09 and R-10 predate the counter, so their
`absent` columns are mixtures of misses and queries that never ran and **cannot be
decomposed after the fact**. Do not read this run as the dirtier one for reporting
the number that the others could not.

### R-13 coda — ⚠️ SC-102 has quietly become ambiguous, and nobody has said so

> ✅ **RESOLVED BY R-14 (2026-08-03).** The passage half landed and SC-102 is now evaluated
> as the path-vs-path criterion this coda argued it was: **FAIL on both builds — 3 of 5
> bands on 3-large, 5 of 5 on qwen3+prefix.** The row below reading *"not yet measured"* is
> the one that mattered, and it is measured. **Read R-14 before quoting anything in this
> coda**, which is preserved as written.

SC-102 reads: *"**no** size band regresses against its **pre-change** rank-1 rate."*

That was unambiguous when written. It is not any more, because the corpus has moved
encoder **twice** since the criterion was set — 3-small → 3-large (R-09) → qwen3 (R-10) →
qwen3+prefix (R-13). "Pre-change" now names at least three different states, and the
verdict flips depending on which one is chosen:

| reading of "pre-change" | short-band verdict on the document path |
|---|---|
| vs 3-large (R-09) | **REGRESSES** — −5.3 / −11.1 / −7.9 |
| vs bare qwen3 (R-10) | **IMPROVES** — +11.4 / +22.4 / +17.9 |
| vs the passage path on the same build | **not yet measured** — that run is unfinished |

⛔ **Do not pick one and report SC-102.** The criterion exists to catch the passage index
making short memos harder to find — a comparison between the two RETRIEVAL PATHS on ONE
build. Every comparison in R-13 is between two ENCODERS on one path, which is a different
question wearing the same numbers.

⇒ **SC-102 is UNEVALUATED as of R-13, and will stay so until the passage half of the census
lands.** Recorded here because the alternative is that someone later picks whichever
baseline gives the answer they need, in complete good faith, with three defensible options
available. **A criterion whose baseline has drifted is not a criterion until the baseline is
re-stated.**

📌 The same hazard applies to SC-101 in the other direction and does not bite: its bar is an
absolute rate (≥80%), not a delta, so it survives an encoder change unchanged. **Absolute
bars are portable across encoders; delta bars are not.** Worth remembering when writing the
next one.

---

## R-14 — the passage half landed. SC-102 is evaluated, and the cutover loses on every axis measured.

Census completed **2026-08-03 01:00:06 EDT**, 7,221 queries, 103 query failures. This
closes R-13's coda and answers SC-102 for the first time since the baseline drifted.

### The headline, stated before the caveats

**The incumbent beats the proposed configuration in every band, and the one band where it
does not is inside noise.** Corrected rank-1, full corpus:

| band | **A** 3-large + passages *(live today)* | **B** qwen3+prefix + documents | **C** qwen3+prefix + passages |
|---|---|---|---|
| 0-200 | **77.1%** | 73.3% | 72.1% |
| 200-500 | **72.0%** | 65.8% | 63.8% |
| 500-1000 | **74.5%** | 68.7% | 62.1% |
| 1000-2000 | **68.5%** | 63.3% | 54.4% |
| 2000+ | 47.6% | **47.9%** | 42.4% |
| **TOTAL** | **71.6%** | 66.4% | 61.8% |

⇒ **B vs A: −5.1pt. C vs A: −9.7pt.** And B additionally costs **+51% read-path tokens**
(the query instruction prefix is 17.9 of 53 tokens/query, R-12).

⛔ **The caveat cannot flip this, only widen it.** A predates the query-failure counter, so
its `absent` is an undecomposable mixture and any failures it carried *depressed* its
measured rank-1. **A is a LOWER BOUND on the incumbent.** The asymmetry is real and it runs
against the challenger.

### ⭐ Why: passage-chunking and the qwen3 upgrade are SUBSTITUTES, and they do not stack

The whole case for qwen3 was long documents. At the 2000+ band, rank-1:

| | document path | passage path |
|---|---|---|
| **3-large** | 18.8% | **47.6%** |
| **qwen3+prefix** | **46.9%** | 42.4% |

⇒ Chunking buys **+28.8**. The encoder buys **+28.1**. Applying both buys **−5.2 against
either alone.** They are two ways of solving one problem — a long memo whose answer is a
small part of it — and the second one applied is not merely redundant, it is negative.

⭐ **A component justified by a benchmark it wins can still be worthless in a system that
already contains a different fix for the same failure.** Neither R-10 nor R-13 could have
seen this: both compared ENCODERS on ONE path, and this is visible only across all four
cells. The four-cell design was not cleverness — it is the minimum that could answer it.

### SC-102 — EVALUATED. Both builds fail, and the difference between the failures is the finding.

SC-102: *no size band regresses against its pre-change rank-1 rate* — properly read as
path-vs-path on one build (R-13 coda).

| build | verdict | per-band |
|---|---|---|
| 3-large (R-09) | **FAIL, 3 of 5** | −1.5 / −4.9 / −2.1, then **+16.7 / +28.8** |
| qwen3+prefix | **FAIL, 5 of 5** | −1.1 / −2.0 / −6.6 / −8.9 / −4.5 |

⇒ On 3-large the failure is **the trade the passage index exists to make**: a few points on
short memos, bought with +16.7 and +28.8 where documents were nearly unfindable. On qwen3
there is no trade — **the passage index loses everywhere, so on that build there is no
reason to run it at all.** Same criterion, same verdict word, opposite engineering meaning.
📌 The three 3-large deltas reproduce research.md:470 exactly, which is a check on the
instrument, not a new result.

### The 103 failures decompose into episodes, and the single-window story was wrong twice

Failures are recorded per-band, so the raw report reads *95 of 103 in the 500-1000 band* —
which invites "that band is hard." It is not: the bench's **outer** loop is over bands
(`memo-retrieval-bench:137`), so ordinal position is a clock and a stall lands in whatever
band the run is standing in. ⚠️ **A failure clustered in one band of a sequentially-scanned
corpus is a clock artefact wearing a content artefact's clothes** (`embeddings`, 2026-08-02).

But the band was not one window either. Reconstructing the exact query order (seed 7) and
timestamping it against the container access log:

| episode | n | window (EDT) | attribution |
|---|---|---|---|
| pos 466-537 | **71** | 20:16-21:50 | the 86-min hole |
| pos 1160-1168 | 9 | ~22:37 | just after the census resumed |
| 0-1 (first two queries of the run) | 2 | 17:33 | cold start |
| eleven others, 1-6 each | 21 | scattered 19:43-00:34 | background rate |

⛔ **Only 71 of 103 belong to the named incident.** The root cause of the hole is an
18.7-hour single-threaded `zfs send | gzip` starving docker, fixed at source (`gzip` →
`pigz -6`) — but **~30 failures are outside it and that fix does not touch them.** Had the
named-and-closed account been accepted whole, the correct prediction would have been a
clean census next run. It will not be clean.

⚠️ **The 86-minute hole is CONFOUNDED and I am not attributing it.** It opens at 20:16,
before my registry mirror started at 20:32:49, and persists to 21:42, an hour after the
backup cron ended at 20:40. Neither cause spans it. Running the mirror concurrently with
the census was already the choice I would take back; this neither exonerates nor convicts
it, and the honest record is that the window has two candidates and no discriminator.

### ⚠️ "Retrieval is deterministic" is FALSE at the 1-in-95 level — and it was found by accident

`memo-bench-repair` rests on three premises, the first being that retrieval is deterministic
so a query re-asked later is as good as one asked during the run. **Two byte-identical
repair runs disagreed: 500-1000 rank-1 came back 55, then 56.**

Replicated three times on the identical 95 queries, and separately probed for mechanism:

| run | rank-1 | top-5 | absent | found outside top-5 |
|---|---|---|---|---|
| 1 | 55 | 78 | 10 | 7 |
| 2 | **56** | 78 | 10 | 7 |
| 3 | **56** | 78 | **11** | 6 |

Two distinct flips, both at a **boundary**: one rank-1↔rank-2-4, one rank-9↔rank-10. The
mechanism is visible directly — asking 25 titles 5× each, **the target's rank never varied
(0/25) but the returned 10-id list varied on 11/25 (44%)**. ⇒ **Retrieval is rank-stable for
the target and not set-stable for the list**; the target only moves when it sits on a
boundary, which is why the effect is ~1-2 counts in 95 rather than pervasive.

⚠️ **That 0/25 does not license "rank is deterministic" — the probe is underpowered for the
effect it was built to chase.** A 1-in-95 rate predicts ~0.25 flipping titles in 25, so 0/25
was the expected null whether or not the effect exists. It was the n=95 replicate that
carried the information. **A null from an instrument too small to see the effect is not
evidence of absence, and it reads exactly like evidence of absence.**

⇒ So the premise is *approximately* true. The corrected figures carry a replication term of
±1-2 counts per 95 — immaterial to a 5.1-point decision, **material to any future use of
this tool on a small margin.**

📌 **Separately consequential for the product, not just the bench:** a 44% set-instability
rate means two identical `/recall` calls can return different supporting documents even when
the top hit is the same. Anything downstream that depends on the *set* — `memo_context`
budget packing, dedup, citation lists — is not reproducible run-to-run. Not evaluated here;
flagged because nothing in the retrieval spec currently claims set-stability either way.

⭐ **It surfaced only because the first run crashed writing its JSON and forced a
replicate.** A premise asserted in a docstring and never replicated is indistinguishable
from one that is true. The tool now creates its output path *before* doing the work
(a four-minute run had been discarded at its last statement — and because it was piped to
`tail`, both stdout and `$?` reported success).

### What this does and does not say

✅ **Says:** on this corpus, with this query mix, the live configuration is better than
either proposed one, and the passage index earns its place on 3-large and not on qwen3.
⛔ **Does not say:** qwen3 is a worse encoder. On the document path it is transformative at
2000+ (18.8% → 46.9%). It is the wrong upgrade *for a system that already chunks*.
⛔ **Does not evaluate** anything outside title-recall and the 30-item mid-document fact set
— no multi-turn, no negation, no cross-lingual, no recency-weighted retrieval.

## R-15 — v1 measured at last, and the encoder is doing almost none of the work

2026-08-03. First comparison that includes **v1**, the service the fleet actually
uses. `bench/results/qa-2026-08-03T153656Z.json`, n=60/band, seed 7, 300 queries
per cell, **0 query failures in any cell**.

⛔ **R-14's "incumbent" was a v2 build.** v1 had never been benchmarked — the
harness called `/search-documents`, which v1 does not expose, so its documented
example 404'd every query and printed rank-1 = 0 for every band. That reads as
"v1 retrieves nothing" and means "the bench never asked it anything." Fixed in
`4a02c30`. **Every conclusion R-14 drew about "the cutover" was drawn against the
wrong baseline.**

### own_title — searching a memo's exact title

| band | v1 3-small | v2 3-large docs | v2 3-large passages |
|---|---|---|---|
| 0-200 | 75.0 | 76.7 | 76.7 |
| 200-500 | 68.3 | 78.3 | 76.7 |
| 500-1000 | 65.0 | 71.7 | 65.0 |
| 1000-2000 | 43.3 | 56.7 | 68.3 |
| **2000+** | **15.0** | **15.0** | **43.3** |
| **TOTAL** | **53.3** | **59.7** | **66.0** |

### content_query — a mid-body excerpt as the query, which is what callers actually do

| band | v1 3-small | v2 3-large docs | v2 3-large passages |
|---|---|---|---|
| 0-200 | 88.3 | 86.7 | 85.0 |
| 200-500 | 66.7 | 68.3 | 61.7 |
| 500-1000 | 41.7 | 46.7 | 58.3 |
| 1000-2000 | 28.3 | 23.3 | 65.0 |
| 2000+ | 20.0 | 18.3 | 50.0 |
| **TOTAL** | **49.0** | **48.7** | **64.0** |

### ⭐ THE FINDING: the passage index is the whole story; the encoder is ~a no-op

**On realistic content queries the encoder upgrade buys NOTHING — 49.0 → 48.7,
and it is NEGATIVE in both long bands** (1000-2000: 28.3→23.3; 2000+: 20.0→18.3).
**On own_title the 2000+ band does not move at all: 15.0 → 15.0.**

⇒ ***Every long-document gain in this project comes from CHUNKING, not from the
encoder.*** 3-small + passages was never measured, and on this evidence it is the
configuration most likely to be both cheapest and near-best. **We are currently
paying 3-large prices for a component that is not visibly earning them.**

📌 **The cheap decisive experiment, now one command:** run v2 on **3-small@1536 +
passages**. If it lands near 64%, the encoder is unnecessary. `memo-qa-suite`
plus `memo-qa-diff` exist precisely so this costs a re-embed and a diff rather
than an argument.

⚠️ **Why own_title alone could never have shown this.** It is unrealistically
easy — nobody searches by pasting a title — and it is the only task this project
had until today. On own_title the encoder looks worth +6.4pt. On the realistic
task it is worth −0.3. **A benchmark everything passes cannot discriminate, and
we had been treating the easy one as the whole picture.**

### Caveats, stated rather than argued away

⛔ **The totals are UNWEIGHTED means of five band rates with n fixed per band, so
corpus composition cannot affect them BY CONSTRUCTION.** Do not defend or attack
them with a corpus-mix argument — the design already excluded it. (`embeddings`,
2026-08-03, correcting exactly that error made here.)
⚠️ **The live confound is WITHIN-band difficulty**: v1 holds 7,921 memos and v2
7,336, so "band 2000+" is not the same documents on both sides. n and seed do
nothing about it. **Corpus parity is the next thing to fix, and it is the reason
the v1-vs-v2 columns deserve less confidence than the v2-vs-v2 ones**, which
share a corpus exactly.
⚠️ n=60/band: a 25pt band swing is ~15 documents. The per-band figures will be
quoted alone and deserve a wider interval than the totals.

### Two secondary results

⚠️ **SET-STABILITY REGRESSED.** v1 varied its returned id list 0/25; v2 varied
2/25 (rank-1 stable on both, 3 replicates). T287 is still a requirements question
and is still undecided — but v2 is now measurably the less reproducible of the
two, which was not previously known.
✅ **API surface: 26 routes added, NONE removed.** No endpoint a v1 caller depends
on has disappeared, so the cutover carries no route-level breakage.

## R-16 — supersession is unimplemented in practice, and the replacement usually isn't returned

2026-08-03, first experiment under Ben's multi-day mandate (concern (b): *"newer
information supersedes older information it now contradicts"*).

### The census — not a sample

| | count | of corpus |
|---|---|---|
| memos declaring supersession/correction **in prose** | **655** | 8.9% |
| …of which name a specific memo id | 112 | 1.5% |
| marked in `supersede_edges` | **0** | 0% |
| documents with `valid_until` set | **0** | 0% |

⇒ **The mechanism is fully built and has never been used once.** `supersede_edges`
carries `old_id, new_id, superseded_at, actor, reason, operator_directive_ref`;
`/supersede` is a live route; `documents.valid_until` exists. All empty.
⇒ ***655 memos know they are stale. Search cannot read any of it.***

### The cost, measured — and the number I would NOT quote

28 old→new pairs where a memo declares supersession AND names a **strictly newer**
memo (the newer-than check is what makes it a pair rather than two co-mentioned
memos). Queried each OLD memo's own title, top-10:

| outcome | n | |
|---|---|---|
| stale ranked above its replacement | 21 | 75% |
| **replacement NOT RETURNED AT ALL** | **17** | **61%** |
| replacement ranked above stale | 3 | 11% |
| neither returned | 4 | 14% |

⛔ **DO NOT QUOTE THE 75%.** The query is the old memo's own title, which biases
toward the old memo *by construction* — an own-title query returning its own
document is retrieval working correctly, not a supersession failure. **That figure
mostly measures the thing it is not about.**

⭐ **The defensible number is 17/28 (61%): the replacement is absent from the top
10 entirely.** Own-title bias does not explain an absence — a system that
understood the relationship would surface the replacement alongside, and the
query text is by construction highly relevant to it (the replacement is *about the
same subject*). ⇒ **An agent recalling one of these topics usually receives the
superseded version and, more often than not, never sees the correction at all.**

### What this justifies, and what it does not

✅ **Justified:** building edges for the **112** memos that already name their
replacement. That is mechanical, reversible, and needs no judgement — the memo
states the relationship itself.
⛔ **NOT justified from this data:** inferring supersession from *content
similarity*. Nothing here measures how often two similar memos actually
contradict, and the 2026-08-02 `.42:32000` repoint work established the failure
mode directly — **a keyword classifier split 41 claims into asserted/denied and
was wrong in BOTH directions on the first eight hand-checked**, because proximity
cannot recover whether a string is *used* or *mentioned*. The memos that must not
be touched are exactly the ones that were already right.

⇒ **Next step is the 112, one class at a time, not a corpus-wide inference pass.**

⚠️ n=28 on the cost measurement. The census (655 / 112 / 0 / 0) is a full count and
is solid; the retrieval-cost figure is a small sample and deserves re-running at
larger n before it carries a decision.

## R-17 — v1 is an accidental control, and it puts the noise floor at ±1.3pt (n=60)

2026-08-03. Re-ran the suite after corpus parity (7,336 → 8,014 docs) and FR-115.
`bench/results/qa-2026-08-03T164406Z.json` vs `…T153656Z.json`.

| task | build | pre | post | Δ |
|---|---|---|---|---|
| own_title | v1 3-small | 53.3 | 52.0 | **−1.3** |
| | v2 documents | 59.7 | 57.7 | −2.0 |
| | v2 passages | 66.0 | 65.0 | −1.0 |
| content_query | v1 3-small | 49.0 | 47.7 | **−1.3** |
| | v2 documents | 48.7 | 45.7 | −3.0 |
| | v2 passages | 64.0 | 62.7 | −1.3 |

### ⭐ THE FINDING IS IN THE v1 ROW

**v1 received no code change and gained 6 documents (7,921 → 7,927), and it moved
−1.3pt on BOTH tasks.** It is the one column where the true delta is known to be
≈0. ⇒ ***±1.3pt is the resolution floor of this harness at n=60, and any smaller
difference is unreadable.***

**Mechanism, and it is not corpus size:** `random.sample` over a *changed pool*
draws different documents even under a fixed seed. Adding six memos to v1 was
enough to reshuffle which 60 land in each band. **The seed makes a run
reproducible against an unchanged corpus and does NOT make two runs comparable
across a corpus edit.**

⇒ ⛔ **THIS IS A LIMITATION OF THE HARNESS FOR EXACTLY THE QUESTION IT EXISTS TO
ANSWER** — *did this change help?* — because the interesting changes are often
accompanied by corpus edits, which is precisely when the seed stops holding the
sample fixed.

### What that implies for everything measured today

✅ **Unaffected — the gaps are far outside the floor.** passages 65.0 vs documents
57.7 (+7.3) and vs v1 52.0 (+13.0); content_query passages 62.7 vs v1 47.7
(+15.0). R-15's headline — *the encoder buys nothing on realistic queries, all the
long-document gain is chunking* — rests on a −0.3 that is INSIDE the floor, but it
is supported independently by the 2000+ band moving 15.0 → 15.0 on own_title and
by both long bands going negative. **It should be re-derived at larger n before it
is treated as settled.**
⛔ **Affected — do not read the 1-3pt drops above as a regression from FR-115 or
the sync.** They are indistinguishable from resampling.

### The fix, not yet built

Pin the sample to a **fixed document-id list** per band, stored alongside the
results, rather than re-drawing by seed. Then a corpus edit changes the corpus and
not the question. ⚠️ Cost: ids that leave the corpus have to be handled explicitly
rather than silently replaced — which is the same "a null is not a zero" discipline
the rest of this suite already enforces.

📌 Also this run: set-stability v1 **0/25**, v2 **3/25** (was 2/25). v2 remains the
less reproducible build; still undecided as a REQUIREMENT (T287).

---

## R-18 — the query log is 97% my own benchmark, and the mirror test cannot run yet (2026-08-03) [002/FR-114]

**Ben, 11:23:** *"do we have query logging in v1? if so you could compare the
results from our real live usage."* **11:27:** *"yes logging for writing /
reading from v1, then we'll run some mirror tests."*

Logging shipped in v1 `fc3d190` (0.3.8) and works. After ~4 hours it holds
**2,791 rows** — which reads like an ample corpus to replay against v2, and is
not one.

| source | agent | rows |
|---|---|---|
| 192.168.0.109 | `Python-urllib/3.10` | **2,714** |
| — (MCP) | `mcp:memo_update` | 44 |
| — (MCP) | `mcp:memo_store` | 16 |
| 192.168.1.199 | `curl/8.12.1` | 8 |
| — (MCP) | `mcp:memo_search` | **7** |
| — (MCP) | `mcp:memo_context` | **1** (my control probe, below) |
| 192.168.48.1 | `curl/7.81.0` | 1 |

**192.168.0.109 is server4** — the first address in its own `hostname -I`, and
where this benchmark harness runs. ⇒ **The instrument is drowning the log it
exists to mine.** Replaying `query_log` against v2 today would replay my own
`own_title` and `content_query` questions and report the result as *real live
usage* — the same shape as R-14's "incumbent" that turned out to be a v2 build.

⛔ **And the attribution was an INFERENCE, not a measurement.** The fleet's own
hooks (`memo-judge.py`, `memo-reconciler.py`, the periodic hooks) are Python on
these same hosts, so `Python-urllib/3.10` from a LAN address is exactly what
they would look like too. I could not separate them, and the row count would
never have said so. ⇒ Fixed by giving the harness a distinct agent string,
`memo-qa-suite/2 (benchmark; EXCLUDE-FROM-MIRROR)`, so the split is a property
of the data rather than of my reasoning about it.

### The zero that needed a control

`op='context'` was **0** across all 2,791 rows. Zero because nobody called it and
zero because the log line is unreachable look identical, and only one of them is
a fact about the fleet. One `POST /context` against v1 → the counter moved to 1.
⇒ **`/context` is instrumented and reachable; the fleet genuinely made no
`memo_context` calls in four hours.** (This is the `usage_triggers_without_data`
lesson from `/remind`, in a different system: an unfed probe is indistinguishable
from a quiet one *from the inside*.)

### What this actually means for the mirror test

Unambiguously-not-mine reads: **16 searches and 0 contexts in ~4 hours.** That is
not a sample; it is an anecdote. ⚠️ It is also lower than a ~50-session fleet
would suggest, which is worth understanding on its own — but the immediate
consequence is scheduling, not diagnosis:

⇒ **The mirror test needs DAYS of accumulation, not hours, and it needed the
agent-string fix first — without which more waiting would only have produced a
larger pile of my own questions.** The instrument is now correct and the clock
has started. Nothing about v2 is blocked on it.


---

## R-19 — the first pinned baseline: the encoder is a wash, passages are the whole win (2026-08-03) [002/FR-114]

First run on a **fixed question set** (`s-2026-08-03T184827Z`, 300 per task),
so these deltas are not resampling. Run `qa-2026-08-03T184959Z`.

| task (identical questions) | v1 `/search` | v2 `document` | v2 `passages` |
|---|---|---|---|
| `own_title` | 53.3% | 56.9% **(+3.6)** | **69.8%** (+12.9 vs document) |
| `content_query` | 44.7% | 41.4% **(−3.3)** | **62.2%** (+20.8 vs document) |

Both deltas clear R-17's ±1.3pt floor. ⭐ **The whole-document path moves in
OPPOSITE directions on the two tasks: +3.6 on titles, −3.3 on the realistic
query.** R-15 asserted the encoder was doing almost none of the work on a −0.3
that sat *inside* the floor; on pinned questions the realistic task now says
something stronger and with a sign — the change is **not** an improvement where
it matters.

⛔ **DO NOT READ THAT ROW AS "3-large IS WORSE THAN 3-small".** `v1 → v2 document`
bundles **two** changes: the encoder (3-small@1536 → 3-large@3072) **and the
entire v2 build**. Nothing here separates them, and the obvious reading —
attributing a −3.3 to the encoder because the encoder is the salient difference —
is unsupported by this table. ⇒ **The discriminating run is v2's build on
3-small@1536**: same code, one variable. It was already queued as "cheapest and
probably near-best"; it is now the experiment that decides *what the cutover
actually is*, and it is the next thing to run.

**What IS attributable, because it is one build against itself:** `document` →
`passages` is +12.9 and **+20.8**, on the same corpus, same encoder, same
process, same questions. **Passage chunking is carrying the result.** Consistent
with the R-15 finding that chunking and encoder upgrades are substitutes rather
than complements — and if 3-small+passages holds up, the expensive half of the
cutover buys nothing.

**Where it wins:** every band on `own_title` except 2000+ (−3.1), largest at
1000-2000 (+8.3). On `content_query` the long bands are where both builds
collapse — v1 13.3%/6.7% and v2 8.5%/8.3% at 1000-2000 and 2000+. **Long memos
are badly served by whole-document retrieval in both builds**, which is exactly
the gap passages close.

### Two things the run surfaced that no score would have

**1. The `missing` guard fired on real data, on its first outing.** 2 pinned ids
are absent from v2 and present in v1 — v1 is live and took writes after the
parity sync. They were excluded from the denominator and printed beside the
score (`170/298, 2✗`) rather than counted as retrieval misses. Without it, v2
would have been charged for 2 documents it was never given.

**2. ⛔ FR-115 IS CORRECT CODE OPERATING ON AN EMPTY SET.** Checking whether
supersession could confound this comparison (v2 excludes superseded memos from
search; v1 has no such notion) turned up something else entirely:

```
v2:  documents 8014 · valid_until NOT NULL: 0 · supersede_edges: 0 rows
```

**The 112 supersession edges did not survive the 3-large rebuild.** The DB was
swapped and re-synced from v1, and v1 has no supersession columns to carry over.
So "search excludes superseded memos" currently excludes nothing, and **Ben's
concern (b) — *newer information supersedes older information it contradicts* —
is not merely unmeasured in v2, it is inert.**

⭐ **The unit tests pass, and could not have caught this.** `test_supersede_excluded_from_search.py`
constructs its own superseded document and asserts it is not returned; that is a
true statement about the code and says nothing about whether the corpus contains
a single instance for it to act on. **This is the capture-miss detector again: a
mechanism verified against data it created itself, deployed over a world that has
none.** ⇒ Edges must be regenerated before any supersession measurement, and the
measurement must read the corpus, not the test fixture.


---

## R-20 — ground truth and context fit: passages are the best RETRIEVER and the worst DELIVERER (2026-08-03) [002/FR-116, FR-117]

Ben's concerns (a) *"is what memo returns actually correct"* and (c) *"efficient
context, not more not less than agents need"*, measured. Runs
`qa-2026-08-03T201926Z` (answer_recall) and `qa-2026-08-03T204109Z` (context_fit),
pinned sample `s-2026-08-03T184827Z`, 256 questions whose answer is a literal
carried by **exactly one** memo — free supervision, exact string containment, no
LLM judge and so nothing to drift between re-runs.

### The finding

| path | ranks right doc @1 | answer in **span** | answer in **full doc** |
|---|---|---|---|
| v1 `/search` (3-small) | 72.3% | 96.7% | 96.7% |
| v2 `document` | 74.5% | 95.8% | 95.8% |
| v2 `passages` | **84.0%** | **77.3%** | **98.7%** |

⭐ **Passage retrieval finds the right memo ~10pt more often than whole-document
search, and the memo it finds contains the answer 98.7% of the time — the best
of any path. But the SPAN it matched carries the answer only 77.3% of the time,
a 21.3pt gap.** The chunk that matches the query and the chunk holding the fact
are routinely different chunks of the same document.

⇒ **So take the ranking and discard the span.** Shipped as FR-117: `/context`
now ranks by passage and packs whole documents.

⛔ **R-19 SAID "PASSAGES ARE THE ENTIRE WIN" AND THAT WAS HALF RIGHT IN A WAY
THAT MATTERS.** The rank-1 advantage is real and this run confirms it. But R-19
measured *which document ranked first* and I let it stand for *whether the
question got answered*. On the ground-truth metric the span-delivering
configuration is the **worst** of the three. A build can top the ranking table
and hand back text without the fact in it.

### ⚠️ Two confounds checked before the number was believed

1. **My grading choice would otherwise have decided the headline.** `/search-passages`
   returns BOTH `passage.text` AND the complete `document.content`, so "did the
   answer come back" has two honest answers: grade the span (what passage
   retrieval BUYS — fewer tokens for the same answer) or grade the document
   (what the caller ACTUALLY RECEIVES, since the full text is on the wire
   anyway). Reporting one and not the other is an argument disguised as a
   measurement. Both are now recorded per run and the GAP is the finding.
2. **Passage-index coverage.** A document absent from the passage index cannot be
   returned by that path at all, which would look identical to "the span didn't
   carry the answer". Checked: **8,014 of 8,014 documents have ≥1 passage.** Not
   a coverage artifact. (21,623 chunks ⇒ ~2.7 per document, so most memos are
   1–3 chunks and the span cost necessarily concentrates in the long ones.)

### Concern (c), quantified — the budget is the binding constraint

`/context` at the default 4,000-token budget, same 256 questions:

| | v1 | v2 (document path) |
|---|---|---|
| answer delivered in `/context` | 85.9% | 87.7% |
| answer *retrieved* (`answer_in_topk`) | 96.7% | 95.8% |
| ⇒ **retrieved then truncated away** | **10.8pt** | **8.1pt** |
| documents matched / packed | 9.94 → 5.96 | 10.0 → 6.22 |
| responses hitting the ceiling | **88.6%** | **86.5%** |
| budget consumed | 91.7% | 92.2% |
| exact-duplicate sections per call | 0.34 | 0.53 |

⭐ **memo retrieves the answer ~96% of the time and delivers it ~86%. Roughly
one answer in ten is found and then packed out of the response.** Nearly every
call (87–89%) is bound by the budget rather than by the corpus, and ~40% of
matched documents never reach the caller.

⛔ **The duplicate figures are LOWER BOUNDS and were wrong before they were
right.** The first implementation compared whole sections, but each block is
`## <title> (score: 0.83)\n<body>` and two copies of one memo get **different
scores** — so byte-equality missed exactly the duplicates it existed to find and
reported a near-zero rate that read as "no redundancy". Fixed to compare bodies.
Even fixed it catches only EXACT repeats, and the corpus's real problem is
near-duplicates: **203 title-groups holding 524 memos and 212k tokens**, of which
109 groups were written within an hour of each other (burst duplicates) and 80
span ≥1 day (genuine versions).
⚠️ That bimodality is invisible in the median, which is 0.01 days — reading the
distribution through its median nearly discarded a viable recency-ordering task.

### What is still unmeasured

Concern (b), supersession, remains **inert** rather than unmeasured: `supersede_edges`
is 0 rows and `valid_until` is NULL for all 8,014 documents (R-19). The edges must
be regenerated before FR-115 can do anything at all.

