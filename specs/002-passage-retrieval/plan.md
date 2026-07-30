# Implementation Plan: Passage-Level Retrieval

**Spec**: `specs/002-passage-retrieval/spec.md`
**Created**: 2026-07-30
**Status**: Draft

## Shape of the change

Four seams, in dependency order. Each is independently testable, and the whole
thing is additive — nothing existing is removed until Phase E, and even then
only behind a flag.

```
  A. chunker           pure function, no I/O, no DB          FR-101..104
  B. storage           document_chunks table + write path    FR-110
  C. retrieval         passage search -> group -> best       FR-105..107, 109
  D. measurement       the harness + the sweep               FR-111, 112, 114
  E. default flip      only if D says so                     FR-113
```

**A is built first because it is pure.** It needs no database, no embedding
provider and no network, so its edge cases (a memo with no headings, a single
8k-token paragraph, a heading with no body, a code fence spanning a boundary)
are cheap to pin down before anything depends on them. Same reasoning that put
the causality gate first in dojo's 006.

## Phase A — the chunker (pure)

`src/memo/chunking.py`, no imports from `db` or `embeddings`.

`chunk(text: str, *, target: int, overlap: float) -> list[Passage]` where
`Passage` carries `text`, `index`, `token_start`, `token_end`.

Splitting strategy, in order:
1. Markdown headings (`^#{1,6} `) — these are already topic boundaries in this
   corpus, which is why they are preferred over a blind window.
2. Paragraph boundaries within an over-target section.
3. Bounded hard-wrap for any span still over target (a single enormous
   paragraph, a long table, a code fence).

**Never split mid-line inside a fenced code block** if it can be avoided — a
half table or half command is worse than a slightly oversized passage.

FR-103 falls out of this rather than being coded: text under `target` produces
exactly one passage, so short memos are unaffected with no threshold branch.

**Tests are the point of this phase.** A memo with no headings; a memo that is
one 8k paragraph; heading-with-no-body; a fenced block larger than target;
empty content; content of exactly `target` tokens. Assert coverage — the union
of passages, minus overlap, must reconstruct the original text exactly. A
chunker that silently drops a span is the failure mode that matters, and it is
invisible without that assertion.

## Phase B — storage

Migration `00X_document_chunks.sql`:

```
document_chunks(doc_id, chunk_index, text, token_start, token_end,
                embedding_model, embedding_route, PRIMARY KEY (doc_id, chunk_index))
```

`embedding_model` + `embedding_route` are FR-108: a corpus that mixes providers
must stay auditable after the fact. quantum-data measured the same text embedded
via OpenRouter vs OpenAI-direct returning 4/5 bit-identical and one at cosine
0.999580 — immaterial for retrieval, but it means one model *label* can cover
non-identical outputs, and without the route recorded a mixed corpus cannot be
told apart later.

Write path: on store/update, chunk → embed each passage (batched; `embed_batch`
finally earns its keep) → replace that document version's passage rows in one
transaction. On supersede, the new version gets its own passage set and the old
one's rows stay put (FR-110).

## Phase C — retrieval

Passage search returns `(doc_id, chunk_index, score)`. Group by `doc_id`, take
**max** (FR-106), then return the **whole memo** with the winning passage
attached as a highlight (FR-107) — match narrow, return broad.

`verbatim-critical` returns whole (FR-109) — enforced in the result assembler,
not left to callers to remember. Note this now coincides with the default rather
than being a special case, which is a good sign for the design.

The one consumer that must be able to opt out is `InjectionSet` (FR-107a). At a
5,000-token ceiling, always spending whole memos rebuilds the problem this
feature exists to fix. So the assembler exposes `expand: "memo" | "passage"`,
defaulting to `"memo"`, and the passage form still carries its parent id so the
caller can fetch the rest. **There is no third mode that returns a truncated
memo as though it were whole** — that would be the comprehension defect this
design is explicitly avoiding.

Then teach `InjectionSet` to spend the passage instead of the document where the
class allows it. That is where User Story 2's budget win actually lands.

## Phase D — measurement (the deliverable that prevents recurrence)

`scripts/memo-retrieval-bench`:
- **Own-title set**: every memo is a free labelled example with an unambiguous
  correct answer. Report rank-1 and absent-from-top-10 **by size band** — the
  banding is what made the defect legible in the first place.
- **Mid-document fact set**: hand-built, ~30 facts that live in the middle of
  memos ≥2000 tokens. This is the set that must fail against the current
  implementation; if it passes today, it is not testing the defect.
- **Sweep**: {256, 384, 512} × {0, 15, 25}% → `research.md`.
- **Threshold recalibration** (FR-114): re-measure `DUP_COSINE` and the 0.80
  read-path bar against passage vectors, using known must-collapse and
  must-not-collapse pairs. Do not carry the document-era numbers over.

**Baseline first.** Run the harness against the CURRENT implementation and
commit those numbers before writing the chunker, so the comparison is real
rather than remembered.

## Phase E — flip the default

Only if Phase D's numbers clear SC-101/102/103. Document path stays behind a
flag for one release.

## Risks

- **Passage count inflation on a corpus of small memos.** Mitigated by FR-103 —
  most memos yield one passage. Verify against the real distribution before the
  full re-embed.
- **Overlap double-counting a fact** so one memo occupies several result slots.
  Grouping by `doc_id` before ranking handles it; assert it in a test.
- **Re-embed cost/duration.** ~21k passages, ≈$0.13, single pass. Runs against
  v2 only; v1 is not touched.
- **The bench becoming decorative.** If it is not run in CI on a fixed corpus
  slice, it will rot. Wire it as a gate with the numbers checked in.

## Sequencing against other work

Independent of the v1→v2 backfill (additive, no re-migration). Should land
before any `text-embedding-3-large` evaluation, since that upgrade should be
measured on passage vectors and not on document vectors (FR-108).
