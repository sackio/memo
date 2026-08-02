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

Every number in this section was measured on a **single index in isolation** — a direct KNN
against `document_embeddings`, and the harness's `/search-documents` and `/search-passages`.
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
