# Feature Specification: Passage-Level Retrieval

**Feature Branch**: `002-passage-retrieval`

**Created**: 2026-07-30

**Status**: Draft (operator-requested, awaiting review)

**Input**: Operator directive 2026-07-30 ("I think we should fix that V2 embed
per document… one of the big aspects of RAG which memo is all about is the
ability to search and find precise things, so I'm surprised we're not
chunking"). Grounded in a measurement taken the same morning during a
memo-minder cycle (memo `fa777d2f`) and the design proposal in memo
`7aecbb5f`.

## The Defect This Fixes

memo computes **one embedding per document**. Retrieval quality therefore
degrades as a memo gets longer, and past roughly 2,000 tokens it collapses
entirely.

Measured by searching each memo's **own exact title** — the easiest possible
retrieval task, where rank 1 is the only correct answer. Reproducible via
`scripts/memo-retrieval-bench` (14 memos/band, seed 7, limit 10). Baseline run
against v1 at 7,511 memos, 2026-07-30:

| token_count | rank-1 | in top-5 | ABSENT from top-10 | median score |
|---|---|---|---|---|
| 0–200 | 14/14 | 14/14 | 0 | 0.732 |
| 200–500 | 9/14 | 12/14 | 2 | 0.716 |
| 500–1000 | 10/14 | 13/14 | 0 | 0.668 |
| 1000–2000 | 5/14 | 8/14 | 5 | 0.650 |
| **2000+** | **2/14** | 4/14 | **9/14** | 0.596 |

An earlier ad-hoc run on a 7,496-memo snapshot gave 10/14 · 9/14 · 10/14 · 3/14
· **0/14** with 10/14 absent in the top band. The exact counts move with the
sample — the corpus is written to continuously — but the shape does not: short
memos are found, memos past ~1000 tokens degrade, and the top band mostly
cannot be retrieved by its own name. Treat the committed bench numbers as
canonical and re-baseline before comparing.

**56.1% of corpus content sits in memos ≥1000 tokens; 24.6% in memos ≥2000.**
So roughly a quarter of what memo knows cannot be recalled — and it is
disproportionately the considered material (postmortems, contracts,
architecture notes, incident records), because those are the long ones.

**The mechanism is dilution, not a stale vector.** A competing hypothesis —
that `PATCH` fails to re-embed — was tested with a nonce probe and **rejected**:
create a memo containing NONCE_A, PATCH the body to NONCE_B, and afterwards
NONCE_B retrieves it while NONCE_A does not. Re-embedding on write works
correctly. The cause is that a single vector over a topically heterogeneous
document lands at the centroid of its topics and is close to none of them.

**A bigger embedding model does not fix it.** `text-embedding-3-small` (1536
dims) and `-3-large` (3072 dims) share the **same 8,192-token input cap**, and
dilution is a property of what is being averaged rather than the precision of
the average. A 3072-dim vector of a five-topic document is still that
document's centroid. (Chunking and a dimension upgrade compose, in that order —
see FR-108.)

**Second edge of the same root cause:** `embeddings.py` performs no chunking or
truncation, so a memo above the 8,192-token model cap cannot be embedded at
all. The corpus currently holds 7 memos over 7,500 tokens (largest 8,160) — the
biggest memos are the closest to silently failing.

## User Scenarios & Testing

### User Story 1 — A fact inside a long memo is findable (Priority: P1)

An agent asks for a specific fact — a port number, a decision, a contractor's
rate — that is recorded in the middle of a 3,000-token memo. Today that memo
does not appear in the top 10 even for its own title, so the agent concludes
memo does not know, and either asks the operator or re-derives it. After this
feature, the passage containing the fact is matched directly and the memo is
returned.

**Why this priority**: it is the whole product. memo exists to find precise
things; a quarter of its content is currently unreachable.

**Independent Test**: build a query set of facts that live mid-document in
memos ≥2000 tokens, with known correct memos. Assert rank-1 retrieval. This
set must fail against the pre-change implementation.

### User Story 2 — The injection budget buys relevant tokens (Priority: P2)

`InjectionSet` has a 5,000-token budget. Today a relevant 3,000-token memo
consumes 60% of it to deliver perhaps one relevant paragraph. After this
feature the matching passage can be injected instead of the whole memo, so the
same budget carries substantially more relevant material, with the existing
drop order (transclusions → current-focus → never constitutional) unchanged.

**Independent Test**: build an InjectionSet for a fixed scope before and after;
assert relevant-token count rises materially at equal or lower total cost.

### User Story 3 — Oversized memos embed at all (Priority: P2)

A memo above the model's input cap is stored and retrievable rather than
failing to embed.

**Independent Test**: store a 9,000-token memo; assert it is retrievable by a
phrase appearing only in its final third.

## Requirements

### Functional Requirements — chunking

- **FR-101**: memo MUST split each memo into passages for indexing, and MUST
  NOT alter stored memo content. Chunking is an index-time concern; the memo
  remains the unit of identity, versioning, provenance and return.
- **FR-102**: splitting MUST prefer existing structure — markdown headings
  first, then paragraph boundaries — and fall back to a bounded hard-wrap only
  for spans that remain oversized. memo content is heavily section-structured
  and those headings are already topic boundaries.
- **FR-103**: a memo short enough to fit one passage MUST produce exactly one
  passage, so that short memos behave exactly as they do today. **The size
  threshold is emergent, not configured** — there MUST NOT be an explicit
  "chunk only if larger than N" branch, which would require its own tuning,
  introduce a discontinuity at the boundary, and create two code paths to keep
  in agreement.
- **FR-104**: no single embedding call may receive more than the provider's
  input cap; passages MUST be bounded well below it.

### Functional Requirements — retrieval

- **FR-105**: vector search MUST operate over passages, and results MUST be
  grouped back to memos before they are returned. Callers continue to receive
  memos.
- **FR-106**: a memo's score MUST be its **best** matching passage, not the mean
  of its passages. A mean re-introduces exactly the dilution this feature
  removes.
- **FR-107**: a result MUST return the **whole memo** by default, and MUST carry
  the matching passage (with its offsets) alongside it as a highlight. Matching
  narrowly and returning broadly is the point: the passage is evidence that the
  memo is relevant, not a claim that the rest of it is not. A memo is written as
  one thing by one author, so the paragraphs around the hit are the most likely
  place for the caller's *next* question to be answered, and they may simply have
  phrased that part in words the query did not resemble. Truncating to the hit
  would trade a retrieval defect for a comprehension one.
  (Operator directive 2026-07-30: *"when we find a good target passage… we then
  also retrieve the entire original memo, because part of that memo was relevant
  and maybe other parts will also be relevant, they just didn't trigger well on
  the embedding match."*)
- **FR-107a**: a **budget-constrained** consumer MUST be able to opt into
  passage-only, and the whole-memo default MUST NOT be silently applied where it
  would blow a budget. `InjectionSet` is the motivating case: at a 5,000-token
  ceiling, always spending whole memos rebuilds the very problem this feature
  fixes — one 3,000-token memo consuming 60% of the budget to deliver one
  relevant paragraph. The rule is therefore: **whole memo by default; passage on
  request; never a truncated memo presented as if whole.** When a consumer takes
  the passage, the result MUST still identify the parent memo so the caller can
  fetch the rest.
- **FR-108**: passage vectors MUST NOT silently change dimensionality or model.
  Any move to a higher-dimension model (e.g. `text-embedding-3-large` at 3072)
  is a **separate, separately-measured change** made after this one, and the
  stored vectors MUST record which model and route produced them.

### Functional Requirements — class safety

- **FR-109**: `verbatim-critical` memos MUST be returned whole. Chunking governs
  the index only; a memo of this class is never delivered as an excerpt. "A UUID
  summarized is a UUID destroyed" holds unchanged, because content is never
  rewritten.
- **FR-110**: passages MUST belong to a document **version**. Supersession
  replaces the superseded version's passage set; as-of queries resolve documents
  first and consult only that version's passages.

### Functional Requirements — measurement (the deliverable that prevents recurrence)

- **FR-111**: the feature MUST ship a retrieval regression harness that scores
  the corpus on rank-1 and absent-from-top-10, broken down by memo size band.
  Every memo's own title is a labelled example with an unambiguous correct
  answer, so this set is free and already exists.
- **FR-112**: chunk size and overlap MUST be **selected by measurement**, not
  chosen a priori. Sweep at least {256, 384, 512} tokens × {0%, 15%, 25%}
  overlap against both the title set and a mid-document fact set, and record the
  results in `research.md`.
- **FR-113**: the passage path MUST be introduced alongside the document path,
  with both queryable, and MUST become the default only after it wins on
  FR-111's numbers. The document path MUST remain available behind a flag for
  one release, so a regression is a config change rather than a migration.
- **FR-114**: duplicate-detection thresholds MUST be re-measured against passage
  vectors before reuse. `DUP_COSINE = 0.90` + title-4gram ≥ 0.60 (migration) and
  `>= 0.80` (read-path reconcile) were calibrated against document vectors. A
  related 2026-07-30 measurement found a memo's own 800-character prefix
  retrieves it at only 0.70–0.89, i.e. the 0.80 "duplicate" bar already sits
  above some documents' self-similarity.

## Success Criteria

- **SC-101**: memos ≥2000 tokens achieve **≥80% rank-1** on the own-title set,
  up from 2/14 (14%) at the 2026-07-30 baseline.
- **SC-102**: **no** size band regresses against its pre-change rank-1 rate.
- **SC-103**: mid-document facts in memos ≥2000 tokens are retrieved at rank 1
  in ≥75% of cases.
- **SC-104**: a memo larger than the provider input cap is stored and retrieved
  by a phrase from its final third.
- **SC-105**: at equal injection budget, relevant-token share rises measurably
  (baseline recorded before the change; no target asserted in advance, because a
  number invented before the measurement is not a criterion).

## Out of Scope

- **Truncating long memos.** Discards content to fix a symptom.
- **Splitting long memos into separate memo records.** Breaks identity,
  supersession, provenance, and the operator's model of "one memo, one thing".
  The split belongs in the index, not in the corpus.
- **Reranking.** A cross-encoder would improve precision but cannot recover a
  document that vector search never returned. Recall first.
- **Migrating to `text-embedding-3-large`.** Related and probably desirable, but
  a separate change with its own measurement (FR-108).

## Cost

7,510 memos → roughly 21,000 passages at 384 tokens with 15% overlap (29.6k at
256; 16.7k at 512). Corpus is 5.6M tokens, so a **full re-embed costs about
$0.13** at `text-embedding-3-small` pricing, and the vector store grows to tens
of thousands of rows — trivial for sqlite-vec. Cost is not a constraint on this
design and MUST NOT be used to justify compromising it.

## Dependencies and Sequencing

Additive and reversible: the passage table and query path land beside the
existing document vectors, so this does **not** block or invalidate the v1→v2
backfill, and no re-migration is required.
