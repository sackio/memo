# memo v2 — what's done and what isn't

**Audited 2026-07-30** against the tree, the test suite and `speckit-trace`, not against
memory of what was intended. Where a marker and the code disagreed, the code won and the
marker was corrected.

| spec | state | tasks |
|---|---|---|
| **001 — memo renovation** | Built and green. Everything open is operator/runtime or blocked. | 84 done · 16 open · 2 withdrawn |
| **002 — passage retrieval** | Built through Phase D. **Phase E measured and correctly blocked: SC-101 fails.** | 21 done · 15 open · 2 partial |
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
  anchors. Same gap T086a closed earlier for FR-044; that's twice now that a
  mid-build requirement landed without a task, which is worth watching.

---

## 002 — passage retrieval

**The honest headline: the passage path works and is clearly better on the band this
feature exists for, but it does not meet its own bar, so the default has not been
flipped.**

Same corpus, same queries, same sample, scored twice (research.md **R-05**):

| band | document rank-1 | passage rank-1 |
|---|---|---|
| 500–1000 | 9/14 | 10/14 |
| 1000–2000 | 9/14 | 10/14 |
| **2000+** | **0/14** | **5/14** |
| absent from top-10 (all bands) | 12/59 | **4/59** |

- **SC-102 holds** — no band regresses.
- **SC-101 FAILS** — it requires ≥80% rank-1 for memos ≥2000 tokens; we are at **36%**.
  Recorded as measured. The bar was not moved.
- **SC-103 not yet re-run** against the passage path.

Chunking is still at the untuned default (384 tokens / 15% overlap). **T253** — the
{256, 384, 512} × {0, 15, 25}% sweep — is the designed way to close 36% → 80%, and is the
next piece of real work here.

**Method note that cost a wrong answer once:** passage coverage is 408/1655 memos (24.7%),
so any comparison must be restricted to memos in **both** indexes. An unrestricted run
scores un-indexed memos as `absent` and reports *indexing coverage* while looking exactly
like a retrieval result — the first pass at this measurement made that mistake and showed
the passage path doing worse. The bench now takes `--path {document,passages}` and
`--both-indexed-only`, so this is reproducible instead of a claim.

**Blocked on you — three open clarifications (T201–T203):**

1. **T201 — result shape for `memo_search` / `/recall`.** Does a hit return the whole memo,
   the matching passage, or both? **This one blocks four tasks** (T240–T243) and is the
   decision the rest of Phase C waits on.
2. **T202 — disposition of the partial v2 corpus** (1,655 memos, ~25% passage-indexed).
   Roll back and re-migrate, or backfill in place? Blocks T270.
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
  permanently. Named here rather than papered over.
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
