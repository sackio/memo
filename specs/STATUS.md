# memo v2 — what's done and what isn't

**Audited 2026-07-30, re-measured 2026-07-31** against the tree, the test suite and
`speckit-trace`, not against memory of what was intended. Where a marker and the code
disagreed, the code won and the marker was corrected. Where a number and a larger
sample disagreed, the larger sample won and the old number was annotated, not deleted.

| spec | state | tasks |
|---|---|---|
| **001 — memo renovation** | Built and green. Everything open is operator/runtime or blocked. | 84 done · 16 open · 2 withdrawn |
| **002 — passage retrieval** | **Corpus re-migrated onto 3-large and fully passage-indexed (7,336/7,336, 0 errors).** Measured by full census, no sampling: **SC-101, SC-102 and SC-103 all FAIL** — and top-5 at 87% vs rank-1 at 47% says the remainder is a **ranking** problem, so T240–T243 is now the measured bottleneck rather than a design preference. See R-09. | 29 done · 9 open · 1 partial |
| **003 — agentic memory** | Design only, by intent. No tasks.md yet. | — |

**Tests**: 466 passing in docker (`docker compose --profile test run --rm test`). Host runs
are not trustworthy here — see the note in `docker-compose.yml`. (This line read 438 for two
days after the count had moved; a stale number does not look stale, it looks authoritative.)

The +15 on 2026-08-01 cover **the corpus indexer's accounting** and **write-lock
contention**. On the latter, said plainly because it would otherwise be assumed: reverting
the fix leaves the three concurrency tests GREEN — the test container is a handful of rows on
a throwaway file and cannot reproduce real contention. What catches the regression is a
source scan asserting no write path uses a bare `BEGIN`, plus a test counting concurrent
writers inside the write body and requiring the answer to be 1.

---

## 001 — memo renovation

**Phases 1–7 are complete**: schema + CRUD, both mediators, Layer-2 injection + hooks,
provider abstractions, the auditor, and the migration script. All phase gates passed.

**What is open, and why — none of it is unfinished development:**

| task | what it is | who |
|---|---|---|
| T131 | Run the real v1→v2 backfill (7,339 memos) | **operator** |
| T132 | Run `memo-migrate-verify` | **operator** |
| T133 | Wire Claude Code hooks on server4 only | **operator** |
| T134 | Flip one test session to v2 MCP, round-trip, flip back | **operator** |
| T135–T136 | Soak test + report to Ben | **operator** |
| T140–T144 | Phase 9 cutover waves | **blocked** behind your approval of T136 |
| T150–T154 | Polish: trace-driven-tasks skill, constitution footer, final strict gate, CLAUDE.md + memo-minder retirement | dev, after 002 settles |

**Two spec corrections made during this audit** — both cases where the spec described a
world the code had left:

- **FR-034 (`POST /flush`) is now marked WITHDRAWN.** You ruled session working state is
  ATC's; the code went in `707e714`, but the requirement was still sitting in spec.md as
  a live obligation. T061/T069 withdrawn with it.
- **FR-028a (deletion log) had no task** — it was the trace gate's only L1 miss. The
  requirement arrived with your deletion ruling *after* Phase 2 was written and the code
  shipped unnamed. Added as **T039a**, marked done with its existing code and test
  anchors. Same gap T086a closed earlier for FR-044.

- **`FR-002`'s withdrawal was never executed** — see the trace-gate section below. Added as
  **T033a**, open, pending your decision.

**That is three times in this feature** that an operator ruling mid-build never became a
task. tasks.md now carries the habit `speckit` recommended: after any ruling that adds,
amends or withdraws a requirement, write the task *and* re-run
`speckit-trace --require-full` for that phase. An addition surfaces as an L1 miss; a
withdrawal surfaces as nothing at all, which is why it needs the deliberate step.

---

## 002 — passage retrieval

**The honest headline: the corpus was moved to a self-hosted encoder and retrieval got
much worse on both paths — but it was measured with the client configured wrong, so the
numbers are a floor for that model rather than a verdict on it. All three criteria fail.
Nothing has been flipped and nothing should be until the configuration is fixed.**

Two full censuses now supersede everything above: **R-09** (2026-08-01, on
`text-embedding-3-large`) and **R-10** (2026-08-02, on `qwen3-embedding-4b` @2560). Both
are 7,221 own-title queries per path plus a 30-case fact set, over the fully re-migrated
7,336-memo corpus. The n=66/n=14 samples that earlier versions of this document were
built on are gone, and two of them had produced wrong headline figures.

**R-10 — 3-large → qwen3, rank-1, full census:**

| band | n | document | passages |
|---|---|---|---|
| 0–200 | 1216 | 78.5% → 61.8% | 77.1% → 60.3% |
| 200–500 | 2293 | 76.9% → 43.4% | 72.0% → 41.8% |
| 500–1000 | 2007 | 76.6% → 50.8% | 74.5% → 44.3% |
| 1000–2000 | 1306 | 51.8% → 48.6% | 68.5% → 39.8% |
| **2000+** | 399 | **18.8% → 42.4%** | **47.6% → 25.3%** |
| **overall** | 7221 | 69.4% → 49.4% | 71.6% → 44.3% |

- ⛔ **SC-101 fails and worsened** — passages 2000+ is 25.3% against ≥80% (was 47.6%).
- ⛔ **SC-102 fails outright** — it forbids *any* band regressing; nine of ten cells do.
- ⛔ **SC-103 fails, unchanged** — fact set 14/30 (46.7%) against ≥75%.

**The cause is identified and it is ours, not the model's (research.md R-11).** qwen3 is
trained for asymmetric retrieval — an instruction prefix on the *query* side only — and
memo sends bare queries. Adding it recovers **+21.3 and +20.0 rank-1 points** across two
independent draws (n=75 each, sign test p≈2e-4 and 3e-5). ⚠️ **This document already named
"query formulation" as one of three candidate gaps. It was on the list and it was the
answer; nobody had pulled that branch.**

⛔ **It is not a free fix.** In the 2000+ band at n=120 it is a *null* on the pre-registered
primary (14 improved / 9 worsened, p=0.202) with nine documents demoted out of rank 1 and
two made unretrievable. That band is 5.5% of the corpus against 76% in the bands where the
prefix clearly works — **a trade to be decided deliberately, with the nine titles visible,
not a switch to throw.** And applying it at all requires `embed_query()`/`embed_document()`
with the ambiguous `embed()` removed: 25 call sites share one function today and the
query/document distinction lives only in local variable names.

⚠️ **Separately (R-12): two cross-text cosine cutoffs were chosen in the 3-small era and
never moved through two encoder changes** — `DUP_COSINE = 0.90` and
`auto_store_similarity_threshold = 0.82`. Their current adequacy is **unmeasured in both
directions**; an attempt to show they are now too strict used an assumed reference
population and was withdrawn. Also `memo_recall_min_score = 0.5` is dead configuration:
defined, read nowhere, and shaped exactly like the relevance floor.

**What changed since yesterday, and why it matters to your review:**

- **Yesterday's numbers understated the feature.** The 2000+ band was recorded as
  0/14 → 5/14; measured in full it is 10/66 → 38/66, and the 1000–2000 band that
  read as a wash is really 41.8% → 68.7%. Small samples produced two wrong headline
  figures here; R-05 is annotated in place rather than rewritten so the error stays
  visible.
- **T253 is done and its answer was no.** All nine `{256,384,512} × {0,15,25}%`
  configs scored within one document of each other, and on the full band the two most
  different configs score *identically*. **Chunk geometry does not move SC-101**, so
  the work STATUS previously called "the next piece of real work here" is closed off
  rather than pending. The remaining gap is query formulation, re-ranking, or the
  embedding model (**T203**, with you).
- **Top-5 on the gating band is 56/66 (85%).** The right memo is nearly always
  retrieved and merely mis-ordered — a re-ranking problem, which points at **T201**
  (result shape).
- ✅ **SC-103 is no longer capped.** It was limited to n=12 while the v2 corpus held a
  partial 1,655 memos and 18 of the fact set's 30 targets did not exist. The full
  re-migration (R-09) fixed that: all 30 are present and the criterion now evaluates at
  its intended power. It still fails — 14/30 — but it fails on retrieval rather than on
  a missing denominator, which is a different fact.

**Method note that has now cost a wrong answer three times:** any comparison must be
restricted to memos present in **both** indexes. An unrestricted run scores un-indexed
memos as `absent` and reports *indexing coverage* while looking exactly like a retrieval
result. (The coverage gap that made this acute — 412 of 1,655 memos — is closed by the
full re-migration, but the restriction stays: it is what makes the two paths comparable
at all, not a workaround for a partial index.) That mistake was made on the
first document-vs-passage pass (which showed the passage path doing *worse*), again in
R-05's fact-set column, and again in the fact set's 5/30. The bench now takes
`--path {document,passages}` and `--both-indexed-only`, applies the restriction to the
own-title sample **and** the fact set, and **prints the excluded count on every run** —
a silently reduced denominator is how a partial index flatters itself.

**Blocked on you — one open clarification, and one decision that is new:**

1. **T201 — result shape for `memo_search` / `/recall`.** Does a hit return the whole memo,
   the matching passage, or both? **This one blocks four tasks** (T240–T243) and is the
   decision the rest of Phase C waits on. Still open.
2. **NEW — the query prefix (R-11).** Apply it uniformly, or not at all? There is no basis
   for applying it by document length: individual bands cannot be resolved at the sample
   sizes available, and a sibling service independently failed to resolve theirs. Large
   gain over 76% of the corpus, a null with nine named casualties over 5.5%, and a
   ~21-call-site refactor before anything can be switched on.

- ✅ **T202 is resolved.** The partial 1,655-memo corpus was rolled back and re-migrated in
  full: 7,336 written, 0 skipped, 0 errored (R-09). SC-103 is no longer capped at n=12 —
  the fact set now evaluates at its full n=30.
- ✅ **T203 is resolved and then some.** `text-embedding-3-large` was ruled in and migrated
  to on 2026-08-01; the corpus then moved again to self-hosted `qwen3-embedding-4b` @2560,
  which is what R-10 measures.

**T260 is now DONE (2026-07-31), and the flip is still yours to make.**
`settings.memo_retrieval_path` selects what `/search` serves. **The default remains
`document`** — the earlier objection to a flag was that it "invites flipping the default
before the numbers exist," and the numbers now exist and say don't. Building the switch
and throwing it are separate acts; only the first is done.

Two properties worth knowing before you review it:

- **A config edit cannot move a measurement.** `/search-documents` and
  `/search-passages` are both immune to the setting, and the bench targets those.
  Without this, one config change would have silently altered what `--path document`
  measured while the output still said "document" — the same confound that has already
  produced three wrong answers here.
- **Passage results keep the existing `{document, score}` contract.** The matching
  passage and its offsets are deliberately not carried through, because that is
  **T201**'s result-shape decision; implementing an answer would pre-empt you.

That makes the rollback path for **T262** real: reverting is a config change, not a
migration.

---

## 003 — agentic memory

Design artifacts only, and deliberately so: `PROPOSAL.md`, `SEAMS.md`, `SPLIT.md`,
`PROMOTION-CONTRACT.md`, `DEFERRED-shadow.md`, plus draft `agents/` and `skills/`. Nothing
is installed. No tasks.md, so `speckit-trace` does not rate it.

Settled since the last review: the build is split by coordinator dependency; the session
shadow is parked until the agent coordinator lands; `authored_by` was renamed off `origin`
to avoid colliding with ATC's transport trust class; and the ATC→memo promotion contract
now closes ATC's Q-B (reason codes, the deletion collision, the attribution ceiling,
memo-paced batches).

---

## Trace gate

`speckit-trace --strict` currently **FAILS**, and the reasons are legible rather than rot:

- **L1 misses: 0** (was 1 — FR-028a, fixed above). **Dangling markers: 0.**
- **001 is FULL on every requirement except FR-034**, which is withdrawn. The tool has no
  concept of a withdrawn requirement, so a withdrawn FR with no code reads PARTIAL
  permanently. **Confirmed by the `speckit` session**: retirement exists only at *spec*
  level (`declared_status()`), never per requirement. The convention — an FR declaring its
  own withdrawal, still listed and rated, gaps no longer counted as debt — is queued but
  **cannot ship tonight**: speckit-trace is version-frozen at 0.10.6 under the operator
  halt and quantum-feed pins the exact version, so a release would move their ratchet
  mid-measurement. They also tested that the `**WITHDRAWN**` wording used here does **not**
  accidentally retire the whole spec, and that a `speckit-trace: ignore` workaround would
  make it strictly worse (INVISIBLE + an L1 miss, with a directive implying it was handled).

- ⚠️ **The inverse case, and the sharper one: `FR-002` is withdrawn and rates FULL.**
  Bi-temporal versioning was withdrawn by operator directive on 2026-07-30 — the spec says
  `get_as_of`, `GET /documents/{id}/as-of`, `valid_from`/`valid_until` and supersede-chain
  resolution "all go". **None of it went.** 23 files still reference the surface, the
  endpoint is live and tested, and the retained `001/FR-002` anchors make a withdrawn
  requirement read as fully implemented and gated. **A trace gate can check that code
  matches a marker; it cannot check that the code should exist.** Now tracked as **T033a**,
  and it needs an operator decision before removal — it deletes a working, tested read path
  and touches the migration/verify path.
- **002 has 5 zero-anchor FRs** — FR-107a, FR-109, FR-112, FR-114 and the gate halves of
  FR-111/FR-113. Every one corresponds to an open task above. This is mid-flight work
  showing up honestly, which is what the gate is for.

`--write-baseline` was **not** run; freezing this debt would defeat the measurement.

---

## Also landed today, outside the task lists

Four production fixes to **v1** (the live service), each ported to v2 — they were
maintenance, not spec work, so they appear in neither tasks.md:

- **0.3.4 — a shared SQLite connection racing across threads.** The one that mattered:
  concurrent DB operations shared one connection, which corrupts results rather than
  failing. Fleet-visible as multi-angle `memo_context` crashing three different ways.
- **0.3.3 — `memo_context` returned an empty result** whenever the top-ranked memo exceeded
  the token budget, which reads as "the corpus has nothing on this topic". Added
  `matched_count`.
- **0.3.5 / 0.3.6** — tool descriptions promising databases removed in June; and
  `memo_update` / `memo_copy` / `memo_move` returning answers a caller could misread as
  success. `memo_copy` had been a silent no-op since June while returning an id.

Full record in memo `76ae0993`.
