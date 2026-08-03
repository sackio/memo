"""Windowed packing must save tokens WITHOUT dropping the answer. [002/FR-120]

⛔ THE TRADE THIS CODE IS MAKING. R-20: the matched passage carries the answer
77.3% of the time; the whole document carries it 98.7%. So packing the bare span
saves tokens and loses answers — the wrong direction. Windowing packs the matched
chunk PLUS `window` tokens either side, betting that the fact usually sits near
the match.

⇒ Every test here checks BOTH halves. A test that only asserted "fewer tokens"
would pass the exact implementation this feature exists to avoid.
"""
from __future__ import annotations

import pytest

import memo.main as main
from memo import db


def _doc(doc_id: str, content: str) -> dict:
    return {"id": doc_id, "title": f"t-{doc_id}", "content": content,
            "tags": [], "metadata": {}, "token_count": len(content) // 4,
            "created_at": 1.0, "updated_at": 1.0}


def _hit(doc, start, end, score=0.9):
    return {"document": doc, "score": score,
            "passage": {"text": "", "chunk_index": 0,
                        "token_start": start, "token_end": end}}


def test_window_keeps_the_matched_region_and_drops_the_rest():
    filler_a = "alpha " * 400
    fact = "the gate code is 4417 "
    filler_b = "omega " * 400
    doc = _doc("a", filler_a + fact + filler_b)
    toks = db._tokenizer.encode(filler_a)
    body, windowed = main._pack_body(doc, _hit(doc, len(toks), len(toks) + 8), 60)
    assert windowed is True
    # ⭐ BOTH HALVES. Saving tokens is only good if the fact survives.
    assert "4417" in body, "the window dropped the answer it was centred on"
    assert len(body) < len(doc["content"]) / 2, "no meaningful saving"
    # ⚠️ The window is SUPPOSED to carry filler either side — that is the whole
    # mechanism for keeping a fact that sits near, but not inside, the matched
    # chunk. An earlier assertion here demanded no trailing filler at all, which
    # would only have passed a bare-span implementation: the exact behaviour
    # R-20 says loses 21pt of answers. Bound the window instead of forbidding it.
    span_len = 8
    assert len(db._tokenizer.encode(body)) <= 2 * 60 + span_len + 4, (
        "window is wider than requested")
    assert "…" in body, "no elision marker; the caller cannot tell it is a window"


def test_small_documents_are_packed_whole():
    """⛔ Windowing something that already fits can only lose information."""
    doc = _doc("small", "the gate code is 4417, and nothing else here")
    body, windowed = main._pack_body(doc, _hit(doc, 0, 5), 60)
    assert windowed is False
    assert body == doc["content"]


def test_a_window_that_saves_little_falls_back_to_the_whole_document():
    """⛔ Below the saving threshold the trade is bad: a real risk of dropping
    the answer for a few tokens."""
    doc = _doc("mid", "word " * 200)
    toks = len(db._tokenizer.encode(doc["content"]))
    # A window covering nearly everything.
    body, windowed = main._pack_body(doc, _hit(doc, 10, toks - 10), 60)
    assert windowed is False
    assert body == doc["content"]


def test_a_hit_with_no_passage_offsets_packs_whole_and_says_so():
    """⚠️ The `document` retrieval path carries no offsets. The fallback is
    silent by necessity, so `windowed` must report False — otherwise
    'windowing did nothing' and 'windowing never ran' look identical."""
    doc = _doc("nopass", "word " * 400)
    body, windowed = main._pack_body(doc, {"document": doc, "score": 0.5}, 60)
    assert windowed is False
    assert body == doc["content"]


def test_window_of_zero_disables_the_feature():
    doc = _doc("off", "word " * 400)
    body, windowed = main._pack_body(doc, _hit(doc, 10, 20), 0)
    assert windowed is False
    assert body == doc["content"]


@pytest.mark.asyncio
async def test_windowing_lets_more_distinct_memos_fit(monkeypatch):
    """⭐ THE POINT OF THE WHOLE FEATURE, end to end.

    Three long memos, a budget that fits only one whole. With windowing the
    freed budget must admit the OTHERS — not merely shrink the response.
    """
    docs = []
    for i, fact in enumerate(["4417", "8823", "9901"]):
        docs.append(_doc(f"d{i}", ("pad " * 300) + f"the code is {fact} "
                                  + ("pad " * 300)))
    offset = len(db._tokenizer.encode("pad " * 300))
    hits = [_hit(d, offset, offset + 8, score=0.9 - 0.1 * i)
            for i, d in enumerate(docs)]

    async def _search(*a, **kw):
        return hits

    async def _embed(q):
        return [0.0] * 8

    monkeypatch.setattr(main.db, "search_passages", _search)
    monkeypatch.setattr(main.embeddings, "embed_query", _embed)
    monkeypatch.setattr(main.settings, "memo_retrieval_path", "passages")

    monkeypatch.setattr(main.settings, "memo_context_span_window", 0)
    whole = await main.memo_context("the code", token_budget=700)

    monkeypatch.setattr(main.settings, "memo_context_span_window", 40)
    windowed = await main.memo_context("the code", token_budget=700)

    assert windowed["spans_windowed"] >= 1, "windowing never ran"
    assert windowed["doc_count"] > whole["doc_count"], (
        "windowing freed budget but admitted no additional memo")
    # ⭐ And the facts must actually be there — the whole reason for the window.
    assert "4417" in windowed["content"] and "8823" in windowed["content"]
