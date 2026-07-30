"""Retrieval filter chain — strategy classes applied in order by the recall mediator.

Each filter takes the candidate list and returns a new one, appending a
human-readable line to `ctx.trace`. The trace is surfaced to the caller as
`filter_chain_trace`, so a surprising recall result can be explained without
re-running anything — that observability is the reason this is a chain of small
named steps rather than one query.

Order matters and is fixed by the contract:

    ScopeFilter -> BiTemporalFilter -> DedupFilter -> Boost

Cheap exact filters first, then dedup (which is O(n^2) over survivors), then
scoring. Boosting before filtering would waste work and let a filtered-out memo
influence nothing but the ranking pass.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from time import time
from typing import Any

# FR-013: operator-logistics tag families that get the class boost. These are
# the categories where "the most recent one" is almost always the right answer
# (where did I park, what's the door code) and a stale hit is actively harmful.
LOGISTICS_TAG_FAMILIES = frozenset({
    "logistics", "parking", "access-code", "booking",
    "appointment", "receipt", "travel",
})

# FR-013 recommended weights. Tuneable per the spec.
W_SEMANTIC = 0.5
W_RECENCY = 0.3
W_TAG_CLASS = 0.2

# Recency half-life. 30d means a month-old memo scores half a fresh one on the
# recency leg only — it cannot by itself outrank a much better semantic match.
RECENCY_HALF_LIFE_S = 30 * 24 * 3600.0

# Near-duplicate threshold for read-path collapse (FR-012), over CONTENT words.
DUPLICATE_CONTENT_THRESHOLD = 0.80

# Filler words stripped before comparison. Small and deliberate: the point is
# to ignore grammatical drift, not to stem or stopword-filter for retrieval.
_FILLER = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "he", "she", "it", "they", "him", "her", "them", "his", "its", "their",
    "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "from",
    "by", "as", "that", "this", "these", "those", "s", "t",
})


@dataclass
class Candidate:
    """A memo row plus its evolving score."""
    memo: dict[str, Any]
    semantic: float
    score: float = 0.0
    # Ids this candidate absorbed during dedup — kept so the citation can note
    # the cluster it stands for rather than silently dropping its twins.
    absorbed: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.memo["id"]


@dataclass
class FilterContext:
    """Per-request inputs + the accumulating trace."""
    query: str = ""
    as_of: float | None = None
    scope_hint: list[str] = field(default_factory=list)
    now: float = field(default_factory=time)
    trace: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)

    def note(self, line: str) -> None:
        self.trace.append(line)


def _shrink(before: int, after: int) -> str:
    return f"{before}→{after}"


class ScopeFilter:
    """Drop memos whose `scope` cannot apply to this caller. [001/FR-011]

    Scope elements are `global`, `project:<slug>`, `session:<id>`,
    `agent-family:<name>` (FR-008). `global` always matches. A non-global memo
    matches only if the caller hinted at that exact scope.

    With no `scope_hint` the filter is a NO-OP rather than a global-only
    filter: an unhinted caller asking a general question should still see a
    project memo that answers it. Scope narrows deliberately, it does not
    silently hide.
    """

    def __call__(self, cands: list[Candidate], ctx: FilterContext) -> list[Candidate]:
        if not ctx.scope_hint:
            ctx.note(f"scope: no hint, pass-through ({len(cands)})")
            return cands

        hints = set(ctx.scope_hint)
        before = len(cands)
        kept = [
            c for c in cands
            if not c.memo.get("scope")
            or any(s == "global" or s in hints for s in c.memo["scope"])
        ]
        ctx.note(f"scope: {_shrink(before, len(kept))}")
        return kept


class BiTemporalFilter:
    """Current-truth by default; point-in-time only on explicit `as_of`. [001/FR-011]

    This is the filter that stops agents reading superseded memos. Default is
    `valid_until IS NULL`; with `as_of` set it becomes the half-open window
    `valid_from <= as_of < valid_until`, matching `db.get_as_of`.
    """

    def __call__(self, cands: list[Candidate], ctx: FilterContext) -> list[Candidate]:
        before = len(cands)
        if ctx.as_of is None:
            kept = [c for c in cands if c.memo.get("valid_until") is None]
            ctx.note(f"bi_temporal(current): {_shrink(before, len(kept))}")
        else:
            t = ctx.as_of
            kept = [
                c for c in cands
                if (c.memo.get("valid_from") or 0) <= t
                and (c.memo.get("valid_until") is None or c.memo["valid_until"] > t)
            ]
            ctx.note(f"bi_temporal(as_of={t}): {_shrink(before, len(kept))}")
        return kept


def _content_words(text: str) -> set[str]:
    """Word set with grammatical filler removed, punctuation-insensitive.

    Comparing CONTENT words is what makes read-path dedup work. Measured over
    the real Matt-Sack cluster and a set of must-not-collapse controls:

        must collapse (wording drift)   0.85 - 1.00
        must NOT collapse (real diff)   0.00 - 0.60

    n-gram overlap could not separate those — the duplicates D1/D2 scored 0.77
    unigram while "CP1500 in the rack" vs "CP1500 in the closet" scored 0.75,
    and on 3-grams the ordering actually INVERTED. The reason is structural:
    migration duplicates differ only in filler ("he", "and", reach/reachable),
    whereas genuinely distinct memos differ in a CONTENT word (rack/closet,
    cyberpower/apc). Dropping filler measures exactly that distinction.
    """
    return {w for w in re.findall(r"\w+", (text or "").lower()) if w not in _FILLER}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class DedupFilter:
    """Collapse near-identical memos to one canonical result. [001/FR-012]

    Targets the migration-duplicate clusters (C-06) — the same fact stored
    three times by three sessions — which otherwise burn the caller's result
    budget on one repeated answer and make a single fact look corroborated by
    three independent sources.

    Similarity is Jaccard over CONTENT words of title+content (see
    `_content_words` for why filler is stripped and the measurements behind the
    threshold). Note this is NOT R-13's rule: R-13 (cosine >= 0.90 + title
    4-gram >= 60%) governs MIGRATION-TIME detection, where both embeddings are
    already in hand. On the read path we only have each candidate's similarity
    to the *query*, not to each other, and fetching every pair's embedding
    would cost more than the dedup saves.

    The highest-scoring member wins and records the ids it absorbed, so the
    cluster stays traceable instead of its twins vanishing.
    """

    def __init__(self, threshold: float = DUPLICATE_CONTENT_THRESHOLD) -> None:
        self.threshold = threshold

    def __call__(self, cands: list[Candidate], ctx: FilterContext) -> list[Candidate]:
        before = len(cands)
        if before < 2:
            ctx.note(f"dedup: {_shrink(before, before)}")
            return cands

        # Best-first so the survivor of each cluster is its strongest member.
        ordered = sorted(cands, key=lambda c: c.semantic, reverse=True)
        grams = {c.id: _content_words(
                     f"{c.memo.get('title') or ''} {c.memo.get('content') or ''}")
                 for c in ordered}

        survivors: list[Candidate] = []
        for cand in ordered:
            # Best-matching survivor, not merely the first over threshold:
            # cluster members vary in how close they are to each other, and
            # picking the first would make the outcome depend on rank order.
            best, best_sim = None, 0.0
            for surv in survivors:
                sim = _jaccard(grams[surv.id], grams[cand.id])
                if sim >= self.threshold and sim > best_sim:
                    best, best_sim = surv, sim
            if best is None:
                survivors.append(cand)
            else:
                best.absorbed.append(cand.id)

        collapsed = sum(len(s.absorbed) for s in survivors)
        ctx.note(f"dedup: {_shrink(before, len(survivors))}"
                 + (f" ({collapsed} duplicate(s) collapsed)" if collapsed else ""))
        return survivors


def recency_decay(created_at: float, now: float,
                  half_life_s: float = RECENCY_HALF_LIFE_S) -> float:
    """Exponential decay in [0, 1]. 1.0 = right now.

    Future timestamps clamp to 1.0 rather than exceeding it — clock skew across
    the fleet is real and must not manufacture a >1 score.
    """
    age = max(0.0, now - (created_at or 0.0))
    return math.pow(0.5, age / half_life_s)


class Boost:
    """Final scoring: semantic + recency + logistics-tag-class. [001/FR-013]

    `score = semantic*0.5 + recency_decay*0.3 + tag_class_match*0.2`, the
    formula recommended by FR-013.

    This is what fixes the 7/26 parking recall: a May memo about SF parking and
    a July memo about Logan both match "where did I park" on semantics alone,
    and only recency + the `parking` tag family separate them.
    """

    def __init__(self, w_semantic: float = W_SEMANTIC, w_recency: float = W_RECENCY,
                 w_tag_class: float = W_TAG_CLASS,
                 half_life_s: float = RECENCY_HALF_LIFE_S) -> None:
        self.w_semantic = w_semantic
        self.w_recency = w_recency
        self.w_tag_class = w_tag_class
        self.half_life_s = half_life_s

    def __call__(self, cands: list[Candidate], ctx: FilterContext) -> list[Candidate]:
        boosted = 0
        for c in cands:
            rec = recency_decay(c.memo.get("created_at") or 0.0, ctx.now, self.half_life_s)
            tags = set(c.memo.get("tags") or [])
            tag_match = 1.0 if tags & LOGISTICS_TAG_FAMILIES else 0.0
            if tag_match:
                boosted += 1
            c.score = (self.w_semantic * c.semantic
                       + self.w_recency * rec
                       + self.w_tag_class * tag_match)

        ranked = sorted(cands, key=lambda c: c.score, reverse=True)
        ctx.note(f"boost: ranked {len(ranked)}"
                 + (f", {boosted} logistics-tagged" if boosted else ""))
        return ranked


DEFAULT_CHAIN = (ScopeFilter(), BiTemporalFilter(), DedupFilter(), Boost())


def apply_chain(cands: list[Candidate], ctx: FilterContext,
                chain=DEFAULT_CHAIN) -> list[Candidate]:
    """Run the chain in order, accumulating the trace."""
    for stage in chain:
        cands = stage(cands, ctx)
    return cands
