# memo v2 — what's done and what isn't

**Audited 2026-07-30, re-measured 2026-07-31** against the tree, the test suite and
`speckit-trace`, not against memory of what was intended. Where a marker and the code
disagreed, the code won and the marker was corrected. Where a number and a larger
sample disagreed, the larger sample won and the old number was annotated, not deleted.

| spec | state | tasks |
|---|---|---|
| **001 — memo renovation** | Built and green. Everything open is operator/runtime or blocked. | 84 done · 16 open · 2 withdrawn |
| **002 — passage retrieval** | Built through Phase D. **Phase E measured and correctly blocked: SC-101 and SC-103 both fail.** T253 done — and it ruled out chunk tuning as the fix. | 24 done · 13 open · 2 partial |
| **003 — agentic memory** | Design only, by intent. No tasks.md yet. | — |

**Tests**: 426 passing in docker (`docker compose run --rm test`). Host runs are not
trustworthy here — see the note in `docker-compose.yml`.

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

**The honest headline: the passage path is a large, measured improvement — larger than
this document claimed yesterday — and it still misses two of its three criteria, so
the default has not been flipped.**

Every band measured in full, both paths, one sample (research.md **R-07**, 2026-07-31).
This supersedes the 14-memo samples that R-05 and yesterday's STATUS were built on:

| band | n | document rank-1 | **passage rank-1** | document absent | passage absent |
|---|---|---|---|---|---|
| 500–1000 | 67 | 56 (83.6%) | 56 (83.6%) | 3 | 4 |
| 1000–2000 | 67 | 28 (41.8%) | **46 (68.7%)** | 18 | **3** |
| **2000+** | 66 | **10 (15.2%)** | **38 (57.6%)** | **36** | **8** |

Mid-document fact set: document **1/12 (8%)** → passages **8/12 (67%)**.

- **SC-102 holds** — no band regresses on rank-1.
- **SC-101 FAILS** — 57.6% against ≥80%.
- **SC-103 FAILS** — 67% against ≥75%, at n=12.

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
- ⚠️ **SC-103 is capped at n=12 by T202, not by indexing.** The fact set names 30
  memos; only 12 exist in the v2 corpus, because it was built against v1's 7,511
  while v2 holds a partial 1,655. T253a indexed the 4 that were present-but-unindexed
  (coverage gap now zero); the other 18 were never migrated. **T202 therefore gates
  whether a Phase-E criterion can be evaluated at its intended power** — it was
  previously understood to block only T270.

**Method note that has now cost a wrong answer three times:** passage coverage is
412/1655 memos (24.9%), so any comparison must be restricted to memos in **both**
indexes. An unrestricted run scores un-indexed memos as `absent` and reports *indexing
coverage* while looking exactly like a retrieval result. That mistake was made on the
first document-vs-passage pass (which showed the passage path doing *worse*), again in
R-05's fact-set column, and again in the fact set's 5/30. The bench now takes
`--path {document,passages}` and `--both-indexed-only`, applies the restriction to the
own-title sample **and** the fact set, and **prints the excluded count on every run** —
a silently reduced denominator is how a partial index flatters itself.

**Blocked on you — three open clarifications (T201–T203):**

1. **T201 — result shape for `memo_search` / `/recall`.** Does a hit return the whole memo,
   the matching passage, or both? **This one blocks four tasks** (T240–T243) and is the
   decision the rest of Phase C waits on.
2. **T202 — disposition of the partial v2 corpus** (1,655 memos, ~25% passage-indexed).
   Roll back and re-migrate, or backfill in place? Blocks T270 — **and, newly, caps
   SC-103 at n=12**: 18 of the fact set's 30 target memos were never migrated, so a
   Phase-E success criterion cannot be evaluated at full power until this is settled.
3. **T203 — is `text-embedding-3-large` in scope for this feature or a separate one?**

**T260 is marked PARTIAL, not done.** Both paths are live and independently callable
(`/search`, `/search-passages`), which was deliberate — a config flag invites flipping the
default before the numbers exist. But "selectable by config" is what the flip actually
needs, so the switch is still to build.

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
