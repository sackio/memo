# Tasks: Passage-Level Retrieval

**Spec**: `specs/002-passage-retrieval/spec.md` · **Plan**: `plan.md`
**Created**: 2026-07-30 · **Status**: Draft — T201–T203 are OPEN CLARIFICATIONS

Marker discipline: every implementation file carries `[002/FR-1XX]` in a
comment; every test that proves an FR carries the same marker. Never write a
literal `002/FR-` marker in prose that is not a real anchor — the scanner counts
it and fails the run as dangling.

Gate command per phase is stated at the end of each phase. **Never** run
`--write-baseline`. **Never** attach `--strict` to a per-phase gate — it is
repo-wide and will fail on later-phase FRs. Run inside the v2 worktree.

---

## Phase 0 — Clarifications (BLOCKING, operator)

- [ ] **T201** — *Result shape for `memo_search` / `/recall`.* FR-107 says whole
      memo; the open question is what happens when 10 whole memos is 30k tokens.
      Options put to the operator: (A) always whole, (B) whole for top hit +
      passages after, (C) passages + parent id only, (D) whole until a total
      token cap, then degrade to passages. **Recommendation: D.** Blocks
      T240–T243.
- [ ] **T202** — *Disposition of the partial v2 corpus* (1,655 memos from the
      stopped 2026-07-30 backfill). Keep as a development fixture (real content,
      real size distribution, useful for the sweep) or roll back now for
      cleanliness. **Recommendation: keep**, and roll back immediately before the
      final clean migration. Blocks nothing; decide before the re-run.
- [ ] **T203** — *Does the operator want `text-embedding-3-large` in scope here?*
      Spec sequences it after (FR-108). **Recommendation: after**, measured on
      passage vectors. Blocks nothing unless the answer changes.

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

- [ ] **T220** — Migration `migrations/0XX_document_chunks.sql`:
      `document_chunks(doc_id, chunk_index, text, token_start, token_end,
      embedding_model, embedding_route, PRIMARY KEY (doc_id, chunk_index))`
      plus the sqlite-vec passage index. `[002/FR-110]`
- [ ] **T221** — Record `embedding_model` **and** `embedding_route` on every
      passage. A corpus that mixes providers must stay auditable afterwards:
      quantum-data measured the same text via OpenRouter vs OpenAI-direct as 4/5
      bit-identical and one at cosine 0.999580, so one model *label* can cover
      non-identical outputs. `[002/FR-108]`
- [ ] **T222** — Write path: on store/update, chunk → `embed_batch` the passages
      → replace that document version's rows **in one transaction**. A partial
      passage set is a silently under-indexed memo. `[002/FR-110]`
- [ ] **T223** — Supersede path: the new version gets its own passage set; the
      superseded version's rows are retained so as-of queries stay answerable.
      `[002/FR-110]`
- [ ] **T224** — Test: a memo stored, then updated, then superseded has exactly
      one live passage set per version and no orphans. `[002/FR-110]`
- [ ] **T225** — Test: a 9,000-token memo (over the provider cap) stores
      successfully and every passage is under the cap. This is User Story 3 and
      it must fail against the pre-change code. `[002/FR-104]`

**Gate B**: `speckit-trace --require-full 002/FR-108,002/FR-110`

---

## Phase C — Retrieval — FR-105, FR-106, FR-107, FR-107a, FR-109

- [ ] **T230** — Passage search returning `(doc_id, chunk_index, score)`, then
      group by `doc_id`. `[002/FR-105]`
- [ ] **T231** — Score each memo by its **best** passage. Test explicitly that a
      mean is not used: a memo with one strong and four weak passages must
      outrank a memo with five mediocre ones. `[002/FR-106]`
- [ ] **T232** — Overlap must not let one memo occupy several result slots —
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
- [x] **T251** — Baseline recorded before any change:
      `specs/002-passage-retrieval/baseline-2026-07-30-v1.json` — v1 @ 7,511
      memos, top band 2/14 rank-1, 9/14 absent. **DONE 2026-07-30.** `[002/FR-111]`
- [ ] **T252** — Mid-document fact set: ~30 facts living in the middle of memos
      ≥2000 tokens, with known correct answers. **This set must FAIL against the
      current implementation** — if it passes today it is not testing the defect.
      `[002/FR-111]`
- [ ] **T253** — Sweep {256, 384, 512} × {0, 15, 25}% overlap against both sets;
      write the table to `research.md` with the winner and *why*. `[002/FR-112]`
- [ ] **T254** — Re-measure duplicate thresholds against passage vectors:
      `DUP_COSINE = 0.90` + title-4gram ≥ 0.60 (migration) and the `>= 0.80`
      read-path bar were calibrated on document vectors. Use known
      must-collapse and must-not-collapse pairs. Record both numbers even if
      unchanged, so the next reader knows they were checked. `[002/FR-114]`
- [ ] **T255** — Wire the bench into CI against a fixed corpus slice with the
      numbers checked in. An uninstrumented bench rots; that is how the original
      defect survived.

**Gate D**: `speckit-trace --require-full 002/FR-111,002/FR-112,002/FR-114`

---

## Phase E — Flip the default — FR-113

- [ ] **T260** — Both query paths live simultaneously, selectable by config.
      `[002/FR-113]`
- [ ] **T261** — Re-run the bench on the passage path; compare against T251's
      committed baseline. Flip only if SC-101 (≥80% rank-1 for ≥2000 tokens),
      SC-102 (no band regresses) and SC-103 (mid-document ≥75%) all clear.
- [ ] **T262** — Document path stays behind a flag for one release, so a
      regression is a config change and not a migration. `[002/FR-113]`
- [ ] **T263** — Record the post-change numbers in `research.md` beside the
      baseline. If a criterion is missed, say so and stop — a criterion quietly
      relaxed to fit the result is worse than a failed gate.

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
