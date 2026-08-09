"""/context must not spend its budget twice on the same text. [002/FR-119]

⭐ WHY. 87-89% of /context calls hit the token ceiling and only ~6 of ~10 matched
memos are delivered (R-20). The corpus holds 203 same-title groups covering 524
memos and ~212k tokens of duplicated text, so a budget spent twice on one fact
costs a DIFFERENT memo its place in the response.

⛔ THE NEGATIVE CASES ARE THE POINT. A dedupe is easy to write and easy to write
WRONG in a way that is silent from the caller's side — dropping something that
merely looked similar, or suppressing a copy that would have fitted. Each test
here pins a case where the wrong implementation looks fine.
"""
from __future__ import annotations

import pytest

import memo.main as main


def _doc(doc_id: str, content: str, title: str | None = None) -> dict:
    return {"id": doc_id, "title": title or f"t-{doc_id}", "content": content,
            "tags": [], "metadata": {}, "token_count": max(1, len(content) // 4),
            "created_at": 1.0, "updated_at": 1.0}


@pytest.fixture()
def stub(monkeypatch):
    """Feed memo_context a fixed result list; nothing else is under test."""
    box: dict = {"results": []}

    async def _search(*a, **kw):
        return box["results"]

    async def _embed_query(q):
        return [0.0] * 8

    monkeypatch.setattr(main.db, "search", _search)
    monkeypatch.setattr(main.embeddings, "embed_query", _embed_query)
    monkeypatch.setattr(main.settings, "memo_retrieval_path", "document")
    return box


@pytest.mark.asyncio
async def test_identical_memos_are_packed_once(stub):
    body = "the barn cluster has four nodes and lives on 192.168.1.0/24"
    stub["results"] = [
        {"document": _doc("a", body), "score": 0.9},
        {"document": _doc("b", body), "score": 0.8},   # cross-host duplicate
        {"document": _doc("c", "something else entirely about the greenhouse"),
         "score": 0.7},
    ]
    out = await main.memo_context("barn cluster", token_budget=4000)
    assert out["duplicates_dropped"] == 1
    assert out["doc_count"] == 2
    # ⭐ The freed budget must be SPENT, not merely saved: the distinct memo has
    # to be present. A dedupe that shrinks the response without admitting
    # anything new has not fixed the problem it was built for.
    assert "greenhouse" in out["content"]


@pytest.mark.asyncio
async def test_whitespace_only_differences_still_count_as_duplicates(stub):
    body = "server4 is 192.168.1.168 and ssh runs on port 4999"
    stub["results"] = [
        {"document": _doc("a", body), "score": 0.9},
        {"document": _doc("b", body + "\n\n  "), "score": 0.8},
    ]
    out = await main.memo_context("server4", token_budget=4000)
    assert out["duplicates_dropped"] == 1, "a trailing newline is not a new memo"


@pytest.mark.asyncio
async def test_similar_but_different_memos_are_both_kept(stub):
    """⛔ THE ONE THAT GUARDS AGAINST OVERREACH.

    These two differ by a single token and that token IS the fact. A similarity
    threshold would collapse them, the caller would get a confident answer with
    the distinguishing memo removed, and nothing in the response would say so.
    """
    stub["results"] = [
        {"document": _doc("a", "the gate code is 4417"), "score": 0.9},
        {"document": _doc("b", "the gate code is 8823"), "score": 0.8},
    ]
    out = await main.memo_context("gate code", token_budget=4000)
    assert out["duplicates_dropped"] == 0
    assert out["doc_count"] == 2
    assert "4417" in out["content"] and "8823" in out["content"]


@pytest.mark.asyncio
async def test_a_copy_that_did_not_fit_does_not_suppress_a_later_one(stub):
    """⛔ Marking a body 'seen' on the BUDGET-SKIP path turns a transient miss
    into a permanent omission.

    Here the first copy is too large for the remaining budget. If it were
    recorded as seen anyway, the identical smaller copy behind it would be
    dropped as a duplicate and the text would never be delivered at all —
    strictly worse than having no dedupe.
    """
    filler = "x " * 700               # consumes most of the budget
    body = "the answer is 192.168.1.168"
    stub["results"] = [
        {"document": _doc("big", filler), "score": 0.95},
        {"document": _doc("a", body + " " + "y " * 400), "score": 0.9},
        {"document": _doc("b", body), "score": 0.5},
    ]
    out = await main.memo_context("the answer", token_budget=900)
    assert "192.168.1.168" in out["content"], (
        "an oversized copy suppressed the smaller one that would have fitted")
