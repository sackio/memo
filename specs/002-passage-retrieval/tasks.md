# Tasks: Passage-Level Retrieval

**Spec**: `specs/002-passage-retrieval/spec.md` · **Plan**: `plan.md`
**Created**: 2026-07-30 · **Last audited**: 2026-07-31 07:30 EDT
**Status**: **Phases A–D built (D now complete); Phase E measured and CORRECTLY BLOCKED on SC-101 and SC-103.**
T201–T203 remain OPEN CLARIFICATIONS for the operator, and T201 blocks four
Phase C tasks (T240–T243). 26/39 done. **Phases D and E-build are complete; the FLIP is the operator decision that remains.**

The passage path is live and a large measured improvement on the band this
feature exists for — **10/66 → 38/66 rank-1** at ≥2000 tokens, and absent-from-
top-10 **36/66 → 8/66** (R-07, whole bands). But **SC-101 requires 80% and we are
at 57.6%**, and **SC-103 requires 75% and we are at 67%** (n=12), so the default
has NOT been flipped.

**T253 is done, and it closed off the route the plan expected to take.** The
{256,384,512} × {0,15,25}% sweep found no configuration better than any other —
chunk geometry does not move SC-101 at all (R-06). The remaining gap is
elsewhere: query formulation, re-ranking, or the embedding model (T203, with the
operator). Two things that shifted with it: R-05's 36% was a small-sample
artifact and is annotated in place, and **top-5 on the gating band is 56/66
(85%)** — the right memo is nearly always retrieved and merely mis-ordered, which
makes this a re-ranking problem and points at T201's open result-shape decision.

Marker discipline: every implementation file carries `[002/FR-1XX]` in a
comment; every test that proves an FR carries the same marker. Never write a
literal `002/FR-` marker in prose that is not a real anchor — the scanner counts
it and fails the run as dangling.

Gate command per phase is stated at the end of each phase. **Never** run
`--write-baseline`. **Never** attach `--strict` to a per-phase gate — it is
repo-wide and will fail on later-phase FRs. Run inside the v2 worktree.

---

## Phase 0 — Clarifications (BLOCKING, operator)

- [x] **T201** — *Result shape for `memo_search` / `/recall`.* FR-107 says whole
      memo; the open question is what happens when 10 whole memos is 30k tokens.
      Options put to the operator: (A) always whole, (B) whole for top hit +
      passages after, (C) passages + parent id only, (D) whole until a total
      token cap, then degrade to passages.
      **RESOLVED 2026-07-31: option (D).** Whole memos until a total token
      budget is reached, then degrade to passages for the remainder. **T240–T243
      are unblocked.**
      Note for whoever builds it: the budget-packing bug fixed in v1 0.3.3 is
      the exact failure mode to avoid here — when the top hit exceeded the
      budget the loop `break`s and returned an empty result, which reads to a
      caller as "the corpus has nothing on this topic". Degrading to a passage
      is precisely the fallback that was missing. Carry `matched_count` so a
      caller can tell "nothing matched" from "matched but did not fit".
- [ ] **T202** — *Disposition of the partial v2 corpus* (1,655 memos from the
      stopped 2026-07-30 backfill). Keep as a development fixture (real content,
      real size distribution, useful for the sweep) or roll back now for
      cleanliness. **Recommendation: keep**, and roll back immediately before the
      final clean migration.
      ⚠️ **"Blocks nothing" was wrong — corrected 2026-07-31.** This corpus is now
      known to cap two measurements:
      (a) **SC-103 at n=12** — 18 of the fact set's 30 target memos were never
      migrated, so a Phase-E success criterion cannot be evaluated at its
      intended power (R-07);
      (b) **the `DUP_COSINE` calibration** — 7.5% of this corpus is
      machine-generated checkpoint logs, which dominate the near-duplicate
      distribution, so a must-collapse set cannot be drawn from it (R-08).
      Neither is fatal, but both mean the decision has measurement consequences
      it was not credited with.
- [x] **T203** — *Does the operator want `text-embedding-3-large` in scope here?*
      Spec sequences it after (FR-108). Recommendation was "after".
      **RESOLVED 2026-07-31: IN SCOPE for this feature — operator overrode the
      recommendation, and was probably right to.** T253 ruled out chunk geometry
      as a lever on SC-101 the same morning, which leaves the embedding model as
      one of the few remaining candidates for the 57.6% → 80% gap.
      Measured facts behind the change (verified live, not assumed):
      - OpenRouter serves `openai/text-embedding-3-large` at native **3072** and
        at a truncated **1536**. Both tested.
      - Neither memo v1 nor v2 is on the large model today; both run
        `3-small`/1536. This is a real change, not an alignment.
      - Full re-embed of the whole corpus, both indexes: **~$1.61** (vs $0.25 on
        small). Vector storage 177 MB → 354 MB. Neither is a constraint.
      - **Go native 3072, not truncated 1536 — for safety, not quality.** The
        vec0 tables bake the dimension in and *reject* a wrong-sized vector
        (tested). At 1536 an old and a new vector are indistinguishable in shape,
        so a half-finished migration would silently score nonsense; at 3072 it
        fails loudly. Given this project's failure history, take the option that
        cannot fail quietly.
      **Sequencing is the load-bearing part: switch BEFORE the real backfill**
      (T131/T271), or the corpus is embedded on the small model and immediately
      re-embedded — the same "embedded twice" waste T271 already calls out for
      chunking.
      ⚠️ **Every measurement in R-01…R-08 was taken on `3-small`.** SC-101 and
      SC-103 must be re-measured after the switch; the current 57.6% / 67% do not
      carry over.

---

## Phase A — The chunker (pure, no I/O) — FR-101, FR-102, FR-103, FR-104

- [x] **T210** — `src/memo/chunking.py`: `Passage` dataclass (`text`, `index`,
      `token_start`, `token_end`) and `chunk(text, *, target, overlap)`. No
      imports from `db` or `embeddings` — this module must be testable with no
      database, no network, no provider. `[002/FR-101]`
- [x] **T211** — Structure-first splitting: markdown headings (`^#{1,6} `), then
      paragraph boundaries within an over-target section, then bounded hard-wrap.
      `[002/FR-102]`
- [x] **T212** — Fenced-code awareness: do not split inside a ``` fence when a
      legal boundary exists outside it. Half a table or half a command is worse
      than a slightly oversized passage. `[002/FR-102]`
- [x] **T213** — Bound every passage below the provider input cap, including the
      hard-wrap fallback. `[002/FR-104]`
- [x] **T214** — **Coverage test: the union of passages, minus overlap, must
      reconstruct the input exactly.** A chunker that silently drops a span is
      the failure mode that matters and is invisible without this assertion.
      `[002/FR-101]`
- [x] **T215** — Edge cases, one test each: no headings; a single 8k-token
      paragraph; heading with no body; fence larger than target; empty content;
      content of exactly `target` tokens; content one token over. `[002/FR-102]`
- [x] **T216** — **Single-passage invariant**: text under `target` yields exactly
      one passage whose text is the input, unchanged. Assert there is no
      `if token_count > N` branch anywhere in the module — the threshold is
      emergent, not configured. `[002/FR-103]`

**Gate A**: `speckit-trace --require-full 002/FR-101,002/FR-102,002/FR-103,002/FR-104`

---

## Phase B — Storage — FR-110, FR-108

- [x] **T220** — Migration `migrations/0XX_document_chunks.sql`:
      `document_chunks(doc_id, chunk_index, text, token_start, token_end,
      embedding_model, embedding_route, PRIMARY KEY (doc_id, chunk_index))`
      plus the sqlite-vec passage index. `[002/FR-110]`
- [x] **T221** — Record `embedding_model` **and** `embedding_route` on every
      passage. A corpus that mixes providers must stay auditable afterwards:
      quantum-data measured the same text via OpenRouter vs OpenAI-direct as 4/5
      bit-identical and one at cosine 0.999580, so one model *label* can cover
      non-identical outputs. `[002/FR-108]`
- [~] **T222** — Write path: chunk → `embed_batch` → replace that document
      version's rows **in one transaction**. *Mechanism done and tested
      (`passages.index_document`); CALL-SITE WIRING DEFERRED.* There are 10
      `db.store`/`db.update` call sites across 5 modules, and a guarantee that
      rests on all of them remembering is the same shape as every silent defect
      found on 2026-07-30. `db.store` cannot own it either — it receives a
      precomputed embedding and must not start making network calls. So the
      invariant is made checkable instead (`find_unindexed`, T226) and the
      wiring lands with Phase C, when the read path settles where indexing
      belongs. `[002/FR-110]`
- [x] **T226** — `passages.find_unindexed()` + tests: live memos with no
      passages, biggest first. Makes "every memo is indexed" verifiable rather
      than trusted. `[002/FR-110]`
- [x] **T223** — Supersede path: the new version gets its own passage set; the
      superseded version's rows are retained so as-of queries stay answerable.
      `[002/FR-110]`
- [x] **T224** — Test: a memo stored, then updated, then superseded has exactly
      one live passage set per version and no orphans. `[002/FR-110]`
- [x] **T225** — Test: a 9,000-token memo (over the provider cap) stores
      successfully and every passage is under the cap. This is User Story 3 and
      it must fail against the pre-change code. `[002/FR-104]`

**Gate B**: `speckit-trace --require-full 002/FR-108,002/FR-110`

---

## Phase C — Retrieval — FR-105, FR-106, FR-107, FR-107a, FR-109

- [x] **T230** — Passage search returning `(doc_id, chunk_index, score)`, then
      group by `doc_id`. `[002/FR-105]`
- [x] **T231** — Score each memo by its **best** passage. Test explicitly that a
      mean is not used: a memo with one strong and four weak passages must
      outrank a memo with five mediocre ones. `[002/FR-106]`
- [x] **T232** — Overlap must not let one memo occupy several result slots —
      group before ranking. `[002/FR-105]`
- [ ] **T240** — *(blocked by T201)* Result assembly per the chosen option;
      whole memo carries the matching passage + offsets as a highlight.
      `[002/FR-107]`
- [ ] **T241** — *(blocked by T201)* `expand: "memo" | "passage"` on the
      retrieval API, defaulting to `"memo"`. The passage form MUST carry its
      parent id. `[002/FR-107a]`
- [ ] **T242** — *(blocked by T201)* `InjectionSet` opts into passage form under
      the 5k budget; drop order unchanged. Measure relevant-token share before
      and after — that is User Story 2's actual payoff. `[002/FR-107a]`
- [ ] **T243** — *(blocked by T201)* Test: **no path ever returns a truncated
      memo presented as whole.** Either it is the whole memo, or it is a passage
      that says so and names its parent. `[002/FR-107]`
- [ ] **T244** — `verbatim-critical` returns whole, enforced in the result
      assembler rather than left to callers. Note this now coincides with the
      default; the test must still pin it, because the default may change and
      this must not. `[002/FR-109]`

**Gate C**: `speckit-trace --require-full 002/FR-105,002/FR-106,002/FR-107,002/FR-107a,002/FR-109`

---

## Phase D — Measurement — FR-111, FR-112, FR-114

- [x] **T250** — `scripts/memo-retrieval-bench`: own-title set, reported by size
      band. **DONE 2026-07-30** (`31ec402`). `[002/FR-111]`
      **Extended 2026-07-30**: `--path {document,passages}` drives either
      retrieval path, and `--both-indexed-only` restricts the sample to memos
      present in BOTH indexes (backed by `GET /admin/passage-indexed-ids`).
      The restriction is load-bearing while passage coverage is partial — an
      unrestricted passage run scores un-indexed memos as `absent` and reports
      **indexing coverage while looking exactly like a retrieval result**.
- [x] **T251** — Baseline recorded before any change:
      `specs/002-passage-retrieval/baseline-2026-07-30-v1.json` — v1 @ 7,511
      memos, top band 2/14 rank-1, 9/14 absent. **DONE 2026-07-30.** `[002/FR-111]`
- [x] **T252** — Mid-document fact set: ~30 facts living in the middle of memos
      ≥2000 tokens, with known correct answers. **This set must FAIL against the
      current implementation** — if it passes today it is not testing the defect.
      `[002/FR-111]`
- [x] **T253** — Sweep {256, 384, 512} × {0, 15, 25}% overlap against both sets;
      write the table to `research.md` with the winner and *why*. `[002/FR-112]`
      **DONE 2026-07-31 — research.md R-06. There is no winner, and that is the
      result.** All nine configs scored 5 or 6 of 14 on the gating band; the
      whole spread is one document. Re-measured over the **entire** 63-memo
      2000+ band instead of sampling it, the two configs furthest apart
      (512/25% at 3.37 passages/doc, 384/15% at 4.31) score **identically:
      37/63 = 58.7%**. Chunk geometry does not move SC-101.
      Driver checked in as `scripts/memo-chunk-sweep`; it re-asserts the control
      set between rounds and aborts if it changed, so a coverage shift can never
      be reported as a retrieval difference. Index left at the default 384/15%.
      **Two corrections fell out of this and are recorded in R-06:**
      R-05's headline 36% was a 14-of-63 sampling artifact (true value 58.7%,
      annotated in place, not rewritten); and top-5 on that band is **88.9%**,
      so the answer is almost always retrieved and it is *rank-1 ordering* that
      misses — a re-ranking problem, not a chunking one.
- [x] **T253a** — Passage-index the fact-set target memos that have no passages,
      so SC-103 can be judged at full size. `[002/FR-111]`
      **DONE 2026-07-31 — and the task as originally written described the wrong
      problem.** It assumed 22 targets needed indexing. In fact **18 of the fact
      set's 30 memos are not in the v2 corpus at all**: the set was built against
      v1 (7,511 memos) and v2 currently holds a partially-migrated 1,655. Only 4
      were present-but-unindexed; those are now indexed (32 passages, coverage
      gap zero) via `scripts/memo-index-factset-targets`.
      Result: SC-103 is measurable at **n=12, not n=30**, and 8/12 (67%) against
      a ≥75% bar — **FAILS**. See R-07.
      ⚠️ **Raising n is blocked by T202**, not by indexing: the missing 18 cannot
      be indexed because they were never migrated. T202 was understood to block
      only T270; it also caps the power at which a Phase-E success criterion can
      be evaluated, and that belongs in the decision.
      Origin of the task: `--both-indexed-only` restricted the own-title sample
      but never the fact set, so unreachable cases scored `absent` and SC-103
      read 5/30 (17%). The bench now restricts both sets and prints the excluded
      count on every run.
- [x] **T254** — Re-measure duplicate thresholds against passage vectors:
      `DUP_COSINE = 0.90` + title-4gram ≥ 0.60 (migration) and the `>= 0.80`
      read-path bar were calibrated on document vectors. Use known
      must-collapse and must-not-collapse pairs. Record both numbers even if
      unchanged, so the next reader knows they were checked. `[002/FR-114]`
      **DONE 2026-07-31 — research.md R-08. Both unchanged, and the task's own
      premise needed correcting first.**
      The `>= 0.80` read-path bar is **not a cosine and not vector-based** — it is
      Jaccard over content words, and `DedupFilter`'s docstring already explains
      that the read path never holds pairwise embeddings. Passage vectors cannot
      affect it. The migration rule compares whole-document embeddings, which
      passage retrieval adds to rather than replaces. So both numbers stand, for
      structural reasons rather than measured ones.
      Measured anyway (`scripts/memo-dup-threshold-check`, 400-memo sample, 384
      nearest-neighbour pairs): **0 pairs would collapse**; 87 (22.7%) clear the
      cosine bar and are stopped by the 4-gram gate. The gate is the entire
      decision and **the cosine bar is therefore untested here**.
      15 of the top 18 pairs are memo-minder's own backfill checkpoints (124 of
      1,655 live memos), up to cosine 0.9998 — genuinely redundant, since all
      three hosts proxy to one DB, and kept apart only because their titles
      differ in the hostname. Upstream fix landed the same day (one checkpoint
      per cycle, not three); the historical 124 are left alone.
      ⚠️ Calibrating the cosine bar needs a must-collapse set drawn from content
      memos, not logs, and **this corpus is not the one to draw it from** —
      partially migrated and 7.5% machine-generated. Another dependency on T202.
- [x] **T255** — Wire the bench into CI against a fixed corpus slice with the
      numbers checked in. An uninstrumented bench rots; that is how the original
      defect survived.
      **DONE 2026-07-31 — but NOT as written, and the difference matters.**
      *Checking the numbers into CI is not possible and would be dishonest if
      faked.* The test container is `network_mode: none` with a throwaway DB —
      no corpus, no embedding provider. Pinning expected rank-1 counts would mean
      either committing megabytes of vectors or quietly testing a fixture instead
      of the corpus, and a green CI would then say nothing about retrieval. The
      quality numbers stay a deliberate run against :8091, recorded in
      research.md with their date and sample size.
      **What IS now covered is the instrument — which is where all three of this
      feature's defects actually were.** `tests/unit/test_retrieval_bench.py`
      (12 tests, no network) pins the shape of each wrong answer that once looked
      plausible: an unrestricted run scoring un-indexed memos as `absent`; the
      fact set not receiving the same restriction; a failed query counting as
      anything but absent; rank-1 requiring position 0 rather than presence; and
      one seed drawing one sample, without which a two-path comparison is not a
      comparison.
      Two changes were required rather than optional: `scripts/` was **not in the
      test image at all**, so the measurement code was wholly uncovered; and the
      fact-set logic sat inline in `main()` where **nothing could call it**,
      which is exactly why that defect shipped. Extracted as `eligible_factset()`
      / `score_factset()`.
      Verified by falsification: reverting the filter makes
      `test_factset_restriction_drops_cases_the_path_cannot_reach` fail and the
      other 11 pass. A test that has never failed is not known to detect anything.

**Gate D**: `speckit-trace --require-full 002/FR-111,002/FR-112,002/FR-114`

---

## Phase E — Flip the default — FR-113

- [x] **T260** — Both query paths live simultaneously, selectable by config.
      `[002/FR-113]`
      *(was PARTIAL 2026-07-30: selection was by endpoint, not by config. The
      reason given was that "a flag invites flipping the default before the
      numbers exist" — that objection expired when R-07 produced the numbers.)*
      **DONE 2026-07-31.** `settings.memo_retrieval_path` selects what `/search`
      serves; **the default stays `document`** and flipping it remains an
      operator decision gated on SC-101/SC-103, neither of which passes.
      Building the switch and throwing it are separate acts.
      **The switch created a new way for this feature's recurring confound to
      return, so the design forecloses it.** The bench reached the document path
      through `/search`; once `/search` became configurable, one config edit
      would have silently changed *what the bench measured* while every label in
      its output still read "document". So `/search-documents` was added
      alongside `/search-passages`, both immune to the setting, and the bench
      now targets those. `/search` is the product surface; the explicit
      endpoints are the measurement surface.
      `/search` also returns `X-Memo-Retrieval-Path`, so a caller can tell which
      index answered instead of inferring it from config it cannot see.
      Passage results are narrowed to the existing `{document, score}` contract:
      the matching passage and offsets are deliberately NOT carried through,
      because that is T201/T240–T241 and answering it by implementation would
      pre-empt the operator. A test asserts the highlight is absent.
      Verified by falsification: routing `/search-documents` through the config
      makes `test_explicit_endpoints_ignore_the_config[passages]` fail.
- [x] **T261** — Re-run the bench on the passage path; compare against T251's
      committed baseline. Flip only if SC-101 (≥80% rank-1 for ≥2000 tokens),
      SC-102 (no band regresses) and SC-103 (mid-document ≥75%) all clear.
      **DONE 2026-07-30 — comparison run, and the flip is CORRECTLY BLOCKED.**
      SC-102 holds (no band regresses). **SC-101 fails: 36% vs the required
      80%** for ≥2000 tokens, though that band moves 0/14 → 5/14 rank-1 and
      3/14 → 11/14 top-5. SC-103 not yet re-run against the passage path. See
      research.md R-05. The bench gained `--path` and `--both-indexed-only` so
      this is reproducible rather than a one-off claim.
      **Superseded 2026-07-31 by R-07**, which re-runs this comparison over whole
      bands instead of 14-memo samples. The verdict is unchanged — the flip stays
      blocked — but every magnitude moved, and the feature looks *better*, not
      worse: 2000+ rank-1 is **10/66 → 38/66** (15.2% → 57.6%) and absent-from-
      top-10 **36/66 → 8/66**; the 1000–2000 band, read here as a wash, is
      actually 41.8% → 68.7%. SC-101 fails at 57.6%; SC-102 holds; **SC-103 now
      measured properly at 8/12 (67%) vs the document path's 1/12 (8%)** — fails
      a 75% bar, and capped at n=12 by T202. Prefer R-07's numbers to this task's.
- [ ] **T262** — Document path stays behind a flag for one release, so a
      regression is a config change and not a migration. `[002/FR-113]`
      **UNBLOCKED 2026-07-31** — T260's flag exists, so the rollback path is
      real: `memo_retrieval_path=document` restores today's behaviour without
      touching data, and `/search-documents` keeps the path addressable
      regardless. Nothing further to build until the flip itself happens; this
      task is the *commitment* to keep the flag for one release after it does.
- [x] **T263** — Record the post-change numbers in `research.md` beside the
      baseline. If a criterion is missed, say so and stop — a criterion quietly
      relaxed to fit the result is worse than a failed gate.
      **DONE 2026-07-30 — research.md R-05.** SC-101 recorded as FAILED at 36%.
      Bar not moved; T253's chunk-size sweep is the designed way to close it.

**Gate E**: `speckit-trace --require-full 002/FR-113`

---

## Phase F — Re-migrate

- [ ] **T270** — *(blocked by T202)* Roll back the partial v2 corpus.
- [ ] **T271** — Re-run the v1→v2 backfill on the passage-enabled build, so the
      corpus is chunked once rather than embedded twice. ~$0.13, single pass.
- [ ] **T272** — Post-migration verify + the bench against the migrated v2, so
      the corpus the operator kicks the tires on has a recorded retrieval score
      rather than an assumed one.

**Final gate**: `speckit-trace --strict` (repo-wide, all of 001 + 002)
