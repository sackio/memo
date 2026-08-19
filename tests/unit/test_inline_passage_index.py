"""The write path indexes passages inline. [002/FR-110]

Background, because these tests are shaped by a specific failure. Until
2026-08-19 `memo_retrieval_path` was `passages` — `/search` ranked by passage —
while `db.store()` wrote `document_embeddings` and nothing else. `passages.py`
was reached from nothing but `GET /admin/passage-indexed-ids` and a backfill
script. 686 memos were stored, embedded, and unfindable.

Two properties make that class of bug invisible, and both are asserted here:

* **An unindexed memo looks stored.** It has a row, a token count and a document
  embedding; only a passage-path search can tell.
* **An EDIT is worse than a create.** The backfill selects documents with ZERO
  passages, so a patched memo keeps its old chunks and goes on ranking on
  pre-correction text — confidently, and with no absence anywhere to notice.
"""
import pytest

from memo import db, passages
from memo.config import settings


async def _store(content: str, embedding, **kw) -> str:
    return await db.store(db_path=None, content=content,
                          title=kw.pop("title", "t"), tags=kw.pop("tags", []),
                          metadata=kw.pop("metadata", {}), embedding=embedding, **kw)


@pytest.mark.asyncio
async def test_store_indexes_passages_without_a_backfill(embedding):
    """A stored memo is passage-findable by the time `store` returns."""
    doc_id = await _store("The barn switch uplink runs on SFP+ port 25.", embedding)

    rows = await passages.get_passages(doc_id)
    assert rows, "store() left the memo with no passages"
    assert all(r["doc_id"] == doc_id for r in rows)
    assert not await passages.find_unindexed(), "a fresh store left a coverage gap"


@pytest.mark.asyncio
async def test_update_replaces_stale_passages(embedding):
    """A corrected memo must stop ranking on the text it was corrected FROM.

    This is the case `memo-index-corpus` structurally cannot repair: it selects
    only documents with zero passages, and a patched document has some.
    """
    doc_id = await _store("server5 eno2 holds the CGNAT address 100.68.35.97.", embedding)
    assert any("100.68.35.97" in r["text"] for r in await passages.get_passages(doc_id))

    await db.update(db_path=None, doc_id=doc_id,
                    content="server5 eno2 holds 192.168.1.140 in Starlink router mode.",
                    title=None, tags=None, metadata=None, embedding=embedding)

    texts = " ".join(r["text"] for r in await passages.get_passages(doc_id))
    assert "192.168.1.140" in texts, "the correction was not indexed"
    assert "100.68.35.97" not in texts, "pre-correction text survived in the passage index"


@pytest.mark.asyncio
async def test_metadata_only_update_does_not_re_embed(embedding, monkeypatch):
    """A tag edit must not spend a provider round-trip rewriting identical rows.

    `content=None` means "leave content alone", so the existing passages are
    still correct by construction.
    """
    doc_id = await _store("A memo whose tags are about to change.", embedding)
    before = await passages.get_passages(doc_id)

    calls = []

    async def counting_batch(texts):
        calls.append(texts)
        return [embedding for _ in texts]

    from memo import embeddings
    monkeypatch.setattr(embeddings, "embed_batch", counting_batch)

    await db.update(db_path=None, doc_id=doc_id, content=None, title=None,
                    tags=["new-tag"], metadata=None, embedding=None)

    assert calls == [], "a content-less update re-embedded the document"
    assert await passages.get_passages(doc_id) == before


@pytest.mark.asyncio
async def test_conflicted_update_does_not_index(embedding, monkeypatch):
    """A REFUSED write must not install passages for text the document lacks.

    `expect_content` mismatch returns `{"conflict": True}` and changes nothing.
    Indexing anyway would rank the memo on words no version of it ever held —
    strictly worse than the stale chunks this hook exists to replace.
    """
    doc_id = await _store("original content", embedding)

    result = await db.update(db_path=None, doc_id=doc_id,
                             content="content that will never land", title=None,
                             tags=None, metadata=None, embedding=embedding,
                             expect_content="something the row does not contain")

    assert result.get("conflict") is True
    texts = " ".join(r["text"] for r in await passages.get_passages(doc_id))
    assert "never land" not in texts
    assert "original content" in texts


@pytest.mark.asyncio
async def test_store_survives_an_unreachable_embedder(embedding, monkeypatch):
    """The memo is stored even when passage indexing fails, and the gap shows.

    ⛔ The ordering matters: a memo stored-but-unindexed is recoverable by a
    backfill; a memo whose store RAISED because the embedder was down is gone,
    and there is no retry queue in front of the seats writing here.
    """
    async def exploding_batch(texts):
        raise ConnectionError("embedding endpoint unreachable")

    from memo import embeddings
    monkeypatch.setattr(embeddings, "embed_batch", exploding_batch)

    doc_id = await _store("A memo written while the embedder was down.", embedding)

    assert doc_id, "the write did not survive an indexing failure"
    stored = await db.get(db_path=None, doc_id=doc_id)
    assert stored is not None and "embedder was down" in stored["content"]

    # And the hole is VISIBLE rather than silent — this is what makes the
    # swallowed exception acceptable.
    gap = await passages.find_unindexed()
    assert [d["id"] for d in gap] == [doc_id]

    # Restoring the embedder and running the backfill's own selector closes it.
    monkeypatch.setattr(embeddings, "embed_batch",
                        lambda texts: _fixed_vectors(texts, embedding))
    await passages.index_document(doc_id, stored["content"])
    assert not await passages.find_unindexed()


async def _fixed_vectors(texts, embedding):
    return [embedding for _ in texts]


@pytest.mark.asyncio
async def test_disabling_the_flag_stops_indexing_but_not_storing(embedding, monkeypatch):
    """`memo_inline_passage_index=False` is an escape hatch, not a crash.

    Asserted because turning it off does NOT fail loudly — it returns the corpus
    to silently accumulating unfindable memos, and the only thing that would
    show it is the coverage gap.
    """
    monkeypatch.setattr(settings, "memo_inline_passage_index", False)

    doc_id = await _store("Stored with inline indexing disabled.", embedding)

    assert doc_id
    assert await passages.get_passages(doc_id) == []
    assert [d["id"] for d in await passages.find_unindexed()] == [doc_id]
