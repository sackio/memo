---
name: memo-storage-method
description: How to write to memo correctly — reconcile before insert, never fabricate provenance, preserve exact strings. Loaded by the memo-memorize agent.
---

# Storing something in memo

The procedure below is the whole job. It is versioned here, in one place, rather
than duplicated into every agent that writes — so a fix lands once.

## 1. Understand what you were handed

The caller may give you free text, a file path, a URL, or a mix.

- **File path** → `Read` it. Store the content, not the path alone: a path is a
  pointer that rots. If the file is large, store what matters plus the path.
- **URL** → `WebFetch` it. Same reasoning. Record the URL as provenance — that is a
  real locator and rare enough to be valuable.
- **Free text** → use it, but do not assume it is already memo-shaped.

## 2. Reconcile BEFORE you write

**Always search first.** At minimum two searches with different phrasings —
semantic search misses obvious duplicates when you only try the obvious phrase.

Then choose one of six actions:

| action | when |
|---|---|
| **write-new** | nothing related exists |
| **merge** | an existing memo covers this; add the new detail to it |
| **supersede** | an existing memo is now WRONG; write the new one and supersede the old |
| **split** | the material is two unrelated facts; store them separately |
| **clarify** | you cannot tell whether it duplicates or contradicts — ask the caller |
| **reject** | it does not belong in memo at all |

**Bias toward merge and supersede over write-new.** A corpus with 44 duplicate
groups is a corpus where every read risks a stale answer. That number is measured,
not hypothetical.

**Supersede, never overwrite,** when a fact changes. The old version stays
answerable for as-of queries; the new one becomes current.

## 3. Rules you may not reason your way around

These exist to constrain judgment, so an agent talking itself past them is the
failure, not an edge case.

- **Never fabricate provenance.** Unknown source → provenance stays null and the
  memo is tagged `provenance-pending`. Do not infer a plausible source. An invented
  citation makes an unverified memo look verified.
- **Never rewrite `verbatim-critical` content.** Add around it; quote it exactly.
- **Never enact a constitutional memo.** Propose it; the operator ratifies.
- **Delete only what is provably redundant or expired** — byte-identical
  duplicates (keep one), superseded past retention, TTL-expired, empty stubs.
  Anything with unique content gets superseded instead. The test is "would this
  lose the only copy of something", not "is this false".
- **Log every deletion with a content snapshot.** That is what makes pruning
  safe enough to do aggressively.
- **Preserve exact strings verbatim** — UUIDs, IPs, ports, amounts, commands, error
  text — even when summarising everything around them.
- **Never store a secret** you were not explicitly asked to store.

## 4. Write it well

- **Title**: what someone would search for, not what the conversation called it.
- **Content**: self-contained. It will be read by someone with no memory of today.
  State the *why*, not only the *what* — the reasoning is the part that cannot be
  re-derived.
- **Tags**: 2–5, from the canonical vocabulary. Tags are the primary retrieval
  mechanism after search.
- **Cross-reference**: link related memos by id. A memo that names its neighbours is
  findable by more routes than one that stands alone.
- **Length**: prefer several focused memos over one sprawling one. Long memos are
  measurably harder to retrieve (see the retrieval-dilution finding), so a
  comprehensive memo nobody can find is worse than three that surface.

## 5. Report what you did

Action, memo ids, what you reconciled against, what you could not establish. The
caller needs to be able to cite and correct it.
