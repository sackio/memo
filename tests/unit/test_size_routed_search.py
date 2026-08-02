"""Size-routed retrieval: each memo served by the index that suits its size. [002/FR-113]

T273, and it exists because of SC-102 rather than SC-101. The R-09 full census
(7,221 queries per path, whole bands, no sampling) found neither path dominates:

    band          document   passages
    0-200            78.5%      77.1%
    200-500          76.9%      72.0%
    500-1000         76.6%      74.5%
    1000-2000        51.8%      68.5%
    2000+            18.8%      47.6%

So replacing the document path with the passage path buys a large win above 1000
tokens and pays a real 1.5-4.9 point loss below it — measured at n=1216-2293 per
band, which is signal, not noise. SC-102 forbids exactly that regression, and
both indexes are already live under FR-113, so the router has everything it needs.

**The load-bearing idea, and the thing these tests protect:** the routing choice
is made PER RESULT, not per query. At query time the target's size is unknown —
that is what is being searched for. But every candidate arrives carrying its own
`token_count`, so the decision is made where the information actually exists.
"""
from __future__ import annotations

import pytest

from memo import main
from memo.config import settings
from memo.models import SearchRequest


def _doc(doc_id: str, tokens: int) -> dict:
    return {"id": doc_id, "content": f"body {doc_id}", "title": f"t-{doc_id}",
            "tags": [], "metadata": {}, "token_count": tokens,
            "created_at": 1.0, "updated_at": 1.0}


@pytest.fixture
def routed(monkeypatch):
    """Wire both indexes with controllable results and no network."""
    state: dict = {"documents": [], "passages": []}

    async def _embed(_text):
        return [0.0] * 8

    async def _search(**kwargs):
        return state["documents"]

    async def _search_passages(*_a, **_k):
        return state["passages"]

    monkeypatch.setattr(main.embeddings, "embed", _embed)
    monkeypatch.setattr(main.db, "search", _search)
    monkeypatch.setattr(main.db, "search_passages", _search_passages)
    monkeypatch.setattr(settings, "memo_size_route_threshold", 1000)
    return state


def _req(limit: int = 10) -> SearchRequest:
    return SearchRequest(query="q", limit=limit)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# --- The routing rule ---

def test_short_memo_is_served_by_the_document_path(routed):
    """Below the threshold the document index measures better, so its score wins
    even though the passage index also found the memo."""
    routed["documents"] = [{"document": _doc("short", 300), "score": 0.60}]
    routed["passages"] = [{"document": _doc("short", 300), "score": 0.90}]

    out = _run(main._size_routed_search(_req()))

    assert len(out) == 1
    assert out[0].score == 0.60, (
        "the preferred path's score must be kept even when the other path scores "
        "HIGHER — this is routing, not max()")


def test_long_memo_is_served_by_the_passage_path(routed):
    routed["documents"] = [{"document": _doc("long", 5000), "score": 0.90}]
    routed["passages"] = [{"document": _doc("long", 5000), "score": 0.55}]

    out = _run(main._size_routed_search(_req()))

    assert out[0].score == 0.55


def test_threshold_boundary_is_inclusive_toward_passages(routed):
    """At exactly the threshold, passages win — 1000 is the first band boundary
    where they are unambiguously ahead."""
    routed["documents"] = [{"document": _doc("edge", 1000), "score": 0.9}]
    routed["passages"] = [{"document": _doc("edge", 1000), "score": 0.4}]

    assert _run(main._size_routed_search(_req()))[0].score == 0.4


# --- Fallback: neither index is allowed to lose a result ---

def test_a_memo_only_the_nonpreferred_path_found_is_still_returned(routed):
    """A long memo the passage index missed but the document index found must not
    vanish. Routing chooses between paths; it never discards a candidate."""
    routed["documents"] = [{"document": _doc("only-doc", 4000), "score": 0.7}]
    routed["passages"] = []

    out = _run(main._size_routed_search(_req()))

    assert [r.document.id for r in out] == ["only-doc"]
    assert out[0].score == 0.7


def test_a_short_memo_only_the_passage_path_found_is_still_returned(routed):
    routed["documents"] = []
    routed["passages"] = [{"document": _doc("only-pass", 200), "score": 0.8}]

    out = _run(main._size_routed_search(_req()))
    assert [r.document.id for r in out] == ["only-pass"]


def test_results_are_deduplicated_by_document(routed):
    """Both indexes returning the same memo yields ONE result, not two."""
    routed["documents"] = [{"document": _doc("dup", 300), "score": 0.6}]
    routed["passages"] = [{"document": _doc("dup", 300), "score": 0.7}]

    out = _run(main._size_routed_search(_req()))
    assert len(out) == 1


def test_ordering_is_by_score_and_limit_is_respected(routed):
    routed["documents"] = [{"document": _doc(f"d{i}", 300), "score": 0.1 * i}
                           for i in range(1, 6)]
    routed["passages"] = []

    out = _run(main._size_routed_search(_req(limit=3)))

    assert len(out) == 3
    assert [r.document.id for r in out] == ["d5", "d4", "d3"]
    assert out[0].score > out[1].score > out[2].score


def test_mixed_sizes_each_take_their_own_path(routed):
    """The point of the feature: one query, two size classes, each served by the
    index that measures better for it."""
    routed["documents"] = [{"document": _doc("short", 200), "score": 0.61},
                           {"document": _doc("long", 3000), "score": 0.99}]
    routed["passages"] = [{"document": _doc("short", 200), "score": 0.95},
                          {"document": _doc("long", 3000), "score": 0.62}]

    by_id = {r.document.id: r.score for r in _run(main._size_routed_search(_req()))}

    assert by_id["short"] == 0.61, "short memo must keep the DOCUMENT score"
    assert by_id["long"] == 0.62, "long memo must keep the PASSAGE score"


def test_zero_token_count_routes_to_document(routed):
    """A memo of unknown/zero size is treated as short — the document path is the
    conservative default, being the current production one.

    Originally written with `token_count = None`, which is UNREACHABLE: the
    `Document` model types it as a required int and rejects None outright, so the
    test was asserting a state the system forbids. The `or 0` guard in the router
    stays as cheap insurance for pre-validation dict input, but the reachable case
    is zero, and that is what is tested.
    """
    routed["documents"] = [{"document": _doc("no-count", 0), "score": 0.5}]
    routed["passages"] = [{"document": _doc("no-count", 0), "score": 0.9}]

    assert _run(main._size_routed_search(_req()))[0].score == 0.5
