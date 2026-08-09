"""Passage-level search. [002/FR-105 002/FR-106 002/FR-107]

The point of the whole feature: match narrowly, return broadly. These pin the
three properties that make it work, and the one that proves it beats the
document path on the failure actually measured (17% rank-1 for mid-document
facts, 2026-07-30).
"""
import pytest

from memo import db, passages


def _vec(seed: int) -> list[float]:
    from memo.config import settings
    v = [0.0] * settings.embedding_dimensions
    v[seed % settings.embedding_dimensions] = 1.0
    return v


async def _index(doc_id: str, content: str, vectors: list[list[float]]):
    """Index with caller-chosen passage vectors, so a test can aim a query."""
    async def fake_batch(texts):
        assert len(texts) == len(vectors), f"{len(texts)} passages, {len(vectors)} vectors"
        return vectors
    return await passages.index_document(doc_id, content, embed_batch=fake_batch,
                                         target=200, overlap=0.0)


@pytest.mark.asyncio
async def test_a_buried_passage_is_findable_when_the_document_vector_is_not():
    """THE feature, in one test.

    A long memo whose DOCUMENT vector points at topic A, but which contains a
    passage about topic B. Querying for B must find the memo — that is exactly
    what fails today at document level, and why quoting a memo's own middle back
    at it retrieves it only 17% of the time.
    """
    body = ("# Alpha\n\n" + " ".join(["alpha"] * 250)
            + "\n\n# Beta\n\n" + " ".join(["beta"] * 250)
            + "\n\n# Gamma\n\n" + " ".join(["gamma"] * 250))
    doc_id = await db.store(None, body, "mixed", [], {}, _vec(1))   # doc vector = topic 1

    from memo.chunking import chunk
    npass = len(chunk(body, target=200, overlap=0.0))
    assert npass >= 3, "the fixture needs several passages to be meaningful"
    # Passage 0 shares the document's vector; one middle passage gets a distinct
    # one. That middle passage is the "buried fact".
    vectors = [_vec(1)] + [_vec(2)] + [_vec(900 + i) for i in range(npass - 2)]
    await _index(doc_id, body, vectors)

    # Query aimed at the BURIED passage's vector, not the document's.
    hits = await db.search_passages(None, _vec(2), limit=5)
    assert any(h["document"]["id"] == doc_id for h in hits), \
        "a passage-level match must surface the memo its passage belongs to"

    # The real claim is about SCORE, not presence. In a near-empty corpus the
    # document path returns the memo too, simply because nothing else exists —
    # asserting absence would pass or fail on corpus size rather than on the
    # behaviour under test.
    passage_score = next(h["score"] for h in hits if h["document"]["id"] == doc_id)
    doc_hits = await db.search(None, _vec(2), 5, None, [], None, None, None, None)
    doc_score = next((h["score"] for h in doc_hits if h["document"]["id"] == doc_id), 0.0)

    assert passage_score > doc_score + 0.5, (
        f"passage-level scored {passage_score:.3f} vs document-level "
        f"{doc_score:.3f} — the buried fact must be MUCH better matched when "
        "the passage carrying it is what gets compared")


@pytest.mark.asyncio
async def test_the_whole_memo_comes_back_with_the_passage_attached():
    """FR-107: match narrow, return broad."""
    body = "# One\n\n" + " ".join(["one"] * 250) + "\n\n# Two\n\n" + " ".join(["two"] * 250)
    doc_id = await db.store(None, body, "t", [], {}, _vec(9))
    from memo.chunking import chunk
    n = len(chunk(body, target=200, overlap=0.0))
    await _index(doc_id, body, [_vec(10 + i) for i in range(n)])

    hits = await db.search_passages(None, _vec(11), limit=3)
    hit = next(h for h in hits if h["document"]["id"] == doc_id)
    assert hit["document"]["content"] == body, "the WHOLE memo, not the passage"
    assert hit["passage"] is not None, "the matching passage must ride along"
    assert hit["passage"]["text"] in body
    assert "token_start" in hit["passage"] and "token_end" in hit["passage"]


@pytest.mark.asyncio
async def test_a_memo_appears_once_however_many_passages_match():
    """FR-105: group before ranking, or overlap lets one memo fill the page."""
    body = "# A\n\n" + " ".join(["same"] * 250) + "\n\n# B\n\n" + " ".join(["same"] * 250)
    doc_id = await db.store(None, body, "t", [], {}, _vec(20))
    from memo.chunking import chunk
    n = len(chunk(body, target=200, overlap=0.0))
    await _index(doc_id, body, [_vec(21)] * n)          # every passage identical

    hits = await db.search_passages(None, _vec(21), limit=10)
    ids = [h["document"]["id"] for h in hits]
    assert ids.count(doc_id) == 1, f"memo appeared {ids.count(doc_id)} times"


@pytest.mark.asyncio
async def test_score_is_the_best_passage_not_the_mean():
    """FR-106. A mean would rebuild the dilution this feature exists to remove.

    One memo has a single excellent passage among weak ones; another is
    uniformly mediocre. Best-passage ranks the first higher; a mean would not.
    """
    from memo.chunking import chunk
    spiky_body = "# A\n\n" + " ".join(["x"] * 250) + "\n\n# B\n\n" + " ".join(["y"] * 250) \
                 + "\n\n# C\n\n" + " ".join(["z"] * 250)
    flat_body = "# A\n\n" + " ".join(["p"] * 250) + "\n\n# B\n\n" + " ".join(["q"] * 250)

    spiky = await db.store(None, spiky_body, "spiky", [], {}, _vec(40))
    flat = await db.store(None, flat_body, "flat", [], {}, _vec(41))

    ns = len(chunk(spiky_body, target=200, overlap=0.0))
    nf = len(chunk(flat_body, target=200, overlap=0.0))
    # spiky: one exact hit (_vec(50)) + the rest far away
    await _index(spiky, spiky_body, [_vec(50)] + [_vec(900 + i) for i in range(ns - 1)])
    # flat: every passage a near-miss
    await _index(flat, flat_body, [_vec(51)] * nf)

    hits = await db.search_passages(None, _vec(50), limit=5)
    order = [h["document"]["id"] for h in hits]
    assert order.index(spiky) < order.index(flat), \
        "one excellent passage must beat uniform mediocrity — that is FR-106"


@pytest.mark.asyncio
async def test_tag_scope_is_applied_db_side_not_as_a_post_filter():
    """The 2026-07-21 false negative is just as reachable here.

    Post-filtering a top-K window drops correctly-tagged memos the query did not
    rank into the window. A correctly-tagged memo whose passage is a POOR match
    must still be returned when the tag scopes the search.
    """
    body = "# A\n\n" + " ".join(["obscure"] * 250)
    doc_id = await db.store(None, body, "tagged", ["rare-tag"], {}, _vec(60))
    from memo.chunking import chunk
    n = len(chunk(body, target=200, overlap=0.0))
    await _index(doc_id, body, [_vec(61) for _ in range(n)])

    # Fill the corpus with better matches for the query.
    for i in range(12):
        other = " ".join(["common"] * 300)
        oid = await db.store(None, other, f"noise {i}", [], {}, _vec(70 + i))
        await _index(oid, other, [_vec(70) for _ in range(
            len(chunk(other, target=200, overlap=0.0)))])

    hits = await db.search_passages(None, _vec(70), limit=5, tags=["rare-tag"])
    assert [h["document"]["id"] for h in hits] == [doc_id], \
        "tag scope must select the candidate set BEFORE ranking"
