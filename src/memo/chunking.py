"""Splitting a memo into passages for indexing. [002/FR-101 002/FR-102 002/FR-103 002/FR-104]

Pure: no database, no network, no embedding provider. That is deliberate — the
edge cases here (a memo with no headings, one enormous paragraph, a code fence
larger than the target, a heading with no body) are where a chunker silently
loses text, and they are only cheap to pin down while nothing else is in the
way.

**Chunking is an INDEX-TIME concern.** Stored memo content is never altered and
never truncated; these passages exist so that search can match a paragraph
instead of averaging a whole document into one vector. The memo remains the unit
of identity, versioning, provenance and return.

Splitting prefers structure over arithmetic, in order:

1. markdown headings — in this corpus they are already topic boundaries, which
   is exactly what a passage wants to be, and they beat any blind window;
2. paragraph boundaries, within a section that is still over target;
3. a bounded hard-wrap, for a span that has no internal boundary at all.

There is deliberately **no `if token_count > N` branch**: text that fits in one
passage produces exactly one passage because the loop never finds a reason to
split it. The single-passage threshold is emergent, not configured — a
configured cutoff would need its own tuning, create a discontinuity at the
boundary, and leave two code paths to keep in agreement. [002/FR-103]
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

_tokenizer = tiktoken.get_encoding("cl100k_base")

# Comfortably under every provider cap we might use (8,192 for
# text-embedding-3-*), so no single embed call can ever be refused. [002/FR-104]
MAX_PASSAGE_TOKENS = 1000
DEFAULT_TARGET = 384
DEFAULT_OVERLAP = 0.15

_HEADING = re.compile(r"^#{1,6} \S", re.M)
_FENCE = re.compile(r"^```", re.M)


@dataclass(frozen=True)
class Passage:
    text: str
    index: int
    token_start: int
    token_end: int


def count_tokens(text: str) -> int:
    return len(_tokenizer.encode(text or ""))


def _fence_spans(text: str) -> list[tuple[int, int]]:
    """Character spans covered by fenced code blocks.

    Splitting inside a fence turns one table or command into two unusable
    halves, so boundaries falling inside these spans are skipped when any legal
    boundary exists outside them. [002/FR-102]
    """
    marks = [m.start() for m in _FENCE.finditer(text)]
    return [(marks[i], marks[i + 1]) for i in range(0, len(marks) - 1, 2)]


def _in_fence(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos <= b for a, b in spans)


def _candidate_boundaries(text: str) -> tuple[list[int], list[int]]:
    """Character offsets where a split is structurally acceptable, best first.

    Headings outrank paragraph breaks because a heading is a topic boundary and
    a blank line is only a typographic one.
    """
    spans = _fence_spans(text)
    heads = [m.start() for m in _HEADING.finditer(text) if not _in_fence(m.start(), spans)]
    paras = [m.end() for m in re.finditer(r"\n[ \t]*\n", text) if not _in_fence(m.start(), spans)]
    return sorted(set(h for h in heads if h > 0)), sorted(set(paras))


def _hard_wrap(text: str, start_tok: int, target: int, index: int) -> list[Passage]:
    """Last resort for a span with no internal boundary — a single vast
    paragraph, or one oversized fence. Splits on token counts so the cap in
    FR-104 holds unconditionally."""
    toks = _tokenizer.encode(text)
    out: list[Passage] = []
    step = max(1, min(target, MAX_PASSAGE_TOKENS))
    for i in range(0, len(toks), step):
        piece = toks[i:i + step]
        out.append(Passage(text=_tokenizer.decode(piece), index=index + len(out),
                           token_start=start_tok + i, token_end=start_tok + i + len(piece)))
    return out


def chunk(text: str, *, target: int = DEFAULT_TARGET,
          overlap: float = DEFAULT_OVERLAP) -> list[Passage]:
    """Split `text` into passages of roughly `target` tokens.

    `overlap` is the fraction of the previous passage's tail repeated at the
    head of the next, so a fact sitting on a boundary is not cut in half and
    made unmatchable by both neighbours.

    Returns `[]` only for empty input. Text that fits in one passage returns
    exactly one passage whose `.text` is the input unchanged. [002/FR-103]
    """
    if not text or not text.strip():
        return []

    total = count_tokens(text)
    if total <= target:
        return [Passage(text=text, index=0, token_start=0, token_end=total)]

    heads, paras = _candidate_boundaries(text)

    # Cut at the best available boundary at or before the point where the
    # running span reaches `target`; fall back through paragraph breaks, then
    # hard-wrap.
    passages: list[Passage] = []
    pos = 0
    tok_pos = 0
    while pos < len(text):
        remaining = text[pos:]
        if count_tokens(remaining) <= target:
            passages.append(Passage(text=remaining, index=len(passages),
                                    token_start=tok_pos,
                                    token_end=tok_pos + count_tokens(remaining)))
            break

        limit = _char_for_tokens(remaining, target)
        abs_limit = pos + limit
        floor = pos + _char_for_tokens(remaining, max(1, int(target * MIN_FILL)))
        cut = _best_boundary(heads, paras, lower=pos + 1, upper=abs_limit,
                             floor=floor)

        if cut is None:
            # No structural boundary in range: hard-wrap just this span, then
            # continue from where it ends.
            span_end = abs_limit
            piece = text[pos:span_end]
            passages.extend(_hard_wrap(piece, tok_pos, target, len(passages)))
            tok_pos += count_tokens(piece)
            pos = span_end
            continue

        piece = text[pos:cut]
        passages.append(Passage(text=piece, index=len(passages), token_start=tok_pos,
                                token_end=tok_pos + count_tokens(piece)))
        tok_pos += count_tokens(piece)
        pos = cut

    return _apply_overlap(text, passages, overlap)


def _char_for_tokens(text: str, n_tokens: int) -> int:
    """Character offset at which `text` reaches `n_tokens`."""
    toks = _tokenizer.encode(text)
    if len(toks) <= n_tokens:
        return len(text)
    return len(_tokenizer.decode(toks[:n_tokens]))


# A cut is only worth taking if it fills a reasonable share of the target.
# Without this, a section whose only paragraph break sits immediately after its
# heading yields a 3-token passage containing nothing but "# Alpha" — which can
# never match a query, and pushes the section's real content into an unbounded
# hard-wrap. Found by test 2026-07-30.
MIN_FILL = 0.5


def _best_boundary(heads: list[int], paras: list[int], *, lower: int,
                   upper: int, floor: int | None = None) -> int | None:
    """Latest heading in (lower, upper]; else latest paragraph break; else None.

    `floor` rejects boundaries that would produce a passage too small to be
    worth indexing. Headings are still preferred over paragraph breaks — a
    heading is a topic boundary, a blank line is only a typographic one — but
    neither is worth a stub passage.
    """
    lo = max(lower, floor) if floor is not None else lower
    in_range = [h for h in heads if lo <= h <= upper]
    if in_range:
        return in_range[-1]
    in_range = [p for p in paras if lo <= p <= upper]
    if in_range:
        return in_range[-1]
    return None


def _apply_overlap(text: str, passages: list[Passage], overlap: float) -> list[Passage]:
    """Prepend a tail of the previous passage to each subsequent passage.

    Overlap is additive context only: `token_start`/`token_end` continue to
    describe the passage's OWN span in the document, so offsets stay usable for
    highlighting and the coverage property stays checkable.
    """
    if overlap <= 0 or len(passages) < 2:
        return passages
    out = [passages[0]]
    for prev, cur in zip(passages, passages[1:]):
        n = int(count_tokens(prev.text) * overlap)
        if n <= 0:
            out.append(cur)
            continue
        tail_toks = _tokenizer.encode(prev.text)[-n:]
        tail = _tokenizer.decode(tail_toks)
        out.append(Passage(text=tail + cur.text, index=cur.index,
                           token_start=cur.token_start, token_end=cur.token_end))
    return out


def reconstruct(passages: list[Passage], text: str) -> str:
    """Rebuild the original from passage spans, ignoring overlap.

    Exists so the coverage property is checkable rather than assumed — a
    chunker that silently drops a span is the failure mode that matters here,
    and it is invisible without this. [002/FR-101]
    """
    if not passages:
        return ""
    toks = _tokenizer.encode(text)
    pieces = [toks[p.token_start:p.token_end] for p in passages]
    return _tokenizer.decode([t for piece in pieces for t in piece])
