"""memo_context must honour settings.memo_retrieval_path. [002/FR-117]

⭐ WHY THIS EXISTS. `memo_context` called `db.search` directly and never read
`settings.memo_retrieval_path`. The setting governed `/search` while the endpoint
agents actually consume ignored it — so a better index could be configured,
measured, and still be unreachable from the surface that matters, with no test
failing and no output saying so.

⭐ AND WHY PASSAGES-FOR-RANKING IS THE POINT. Measured on the pinned sample
(R-20), 256 questions whose answer is a literal held by exactly one memo:

    path        ranks right doc   answer in SPAN   answer in FULL DOC
    document          74.5%            95.8%             95.8%
    passages          84.0%            77.3%             98.7%

Passage retrieval is the best retriever and the worst deliverer. So the contract
under test is specifically **rank by passage, deliver the whole document** — a
test that only asserted "passages path was called" would pass an implementation
that returned spans and silently dropped 21pt of answers.
"""
from __future__ import annotations

import pytest

import memo.main as main


def _doc(doc_id: str, content: str = "the answer is 192.168.1.168") -> dict:
    return {"id": doc_id, "title": f"t-{doc_id}", "content": content,
            "tags": [], "metadata": {}, "token_count": 8,
            "created_at": 1.0, "updated_at": 1.0}


@pytest.fixture()
def spies(monkeypatch):
    calls = {"search": 0, "search_passages": 0, "search_multi": 0}

    async def _search(*a, **kw):
        calls["search"] += 1
        return [{"document": _doc("doc-from-document-search"), "score": 0.5}]

    # ⚠️ THE DOCUMENT'S content MUST CONTAIN ITS OWN PASSAGES. An earlier
    # version of this stub gave doc-A the first chunk's text as its whole
    # content, so the answer existed only inside `passage.text` and nowhere in
    # the document — a world the real index cannot produce, and one in which NO
    # correct implementation could pass. The test failed loudly, which is the
    # only reason it did not become a "bug" in working code.
    FULL = "intro paragraph, no answer here. later on: the answer is 192.168.1.168"

    async def _search_passages(*a, **kw):
        calls["search_passages"] += 1
        # Two passages of ONE document, as the real index returns: the chunk
        # that MATCHES the query is not the chunk that holds the fact. That
        # split is the entire finding this routing exists to exploit.
        return [
            {"document": _doc("doc-A", FULL),
             "passage": {"text": "intro paragraph, no answer here",
                         "chunk_index": 0, "token_start": 0, "token_end": 6},
             "score": 0.9},
            {"document": _doc("doc-A", FULL),
             "passage": {"text": "later on: the answer is 192.168.1.168",
                         "chunk_index": 1, "token_start": 6, "token_end": 12},
             "score": 0.7},
        ]

    async def _search_multi(*a, **kw):
        calls["search_multi"] += 1
        return [{"document": _doc("doc-from-multi"), "score": 0.5}]

    async def _embed_query(q):
        return [0.0] * 8

    monkeypatch.setattr(main.db, "search", _search)
    monkeypatch.setattr(main.db, "search_passages", _search_passages)
    monkeypatch.setattr(main.db, "search_multi", _search_multi)
    monkeypatch.setattr(main.embeddings, "embed_query", _embed_query)
    return calls


@pytest.mark.asyncio
async def test_default_document_path_is_unchanged(spies, monkeypatch):
    """⛔ CONTROL: the default must not move. Flipping the index for every
    existing caller as a side effect of making the setting work would be a
    behaviour change nobody asked for."""
    monkeypatch.setattr(main.settings, "memo_retrieval_path", "document")
    await main.memo_context("where is server4", token_budget=500)
    assert spies["search"] == 1
    assert spies["search_passages"] == 0


@pytest.mark.asyncio
async def test_passages_setting_routes_context_through_the_passage_index(
        spies, monkeypatch):
    monkeypatch.setattr(main.settings, "memo_retrieval_path", "passages")
    await main.memo_context("where is server4", token_budget=500)
    assert spies["search_passages"] == 1
    assert spies["search"] == 0, "document search ran despite passages config"


@pytest.mark.asyncio
async def test_passages_path_delivers_the_document_not_the_span(
        spies, monkeypatch):
    """⭐ THE CONTRACT THAT MATTERS — and the one a call-counting test misses.

    Both stub passages belong to ONE document; the answer lives only in the
    second. The packed content must carry the FULL document text, and the
    document must appear ONCE — not once per matching passage, which would
    spend the budget several times on the same memo.
    """
    monkeypatch.setattr(main.settings, "memo_retrieval_path", "passages")
    out = await main.memo_context("where is server4", token_budget=500)
    assert out["doc_count"] == 1, "one document matched twice was packed twice"
    assert "192.168.1.168" in out["content"], (
        "the answer was dropped — the span was delivered instead of the document")
    # And it is the whole document, not the winning chunk in isolation.
    assert "intro paragraph" in out["content"]


@pytest.mark.asyncio
async def test_scope_all_stays_on_document_search(spies, monkeypatch):
    """⚠️ There is no passage equivalent for the multi-path merge, so scope="all"
    keeps document search even when the setting says passages. Asserted so the
    exception is deliberate rather than discovered later as a bug."""
    monkeypatch.setattr(main.settings, "memo_retrieval_path", "passages")
    await main.memo_context("where is server4", token_budget=500,
                            db_path="/tmp/whatever.db", scope="all")
    assert spies["search_multi"] == 1
    assert spies["search_passages"] == 0
