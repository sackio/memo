"""The passage index write path. [002/FR-110 002/FR-108]

The property that matters is atomicity: a document's passage set is replaced
wholesale or not at all. A half-written set is a silently under-indexed memo —
it still returns from search, still looks healthy in every listing, and simply
never matches on the paragraphs that went missing.
"""
import pytest

from memo import db, passages
from memo.chunking import Passage


def _vec(seed: int, dim: int | None = None) -> list[float]:
    from memo.config import settings
    dim = dim or settings.embedding_dimensions
    v = [0.0] * dim
    v[seed % dim] = 1.0
    return v


async def fake_embed_batch(texts):
    return [_vec(i + 1) for i, _ in enumerate(texts)]


@pytest.mark.asyncio
async def test_indexes_a_long_memo_into_multiple_passages():
    content = "# One\n\n" + " ".join(["alpha"] * 500) + "\n\n# Two\n\n" + " ".join(["beta"] * 500)
    doc_id = await db.store(None, content, "long", [], {}, _vec(0))
    n = await passages.index_document(doc_id, content, embed_batch=fake_embed_batch)
    assert n > 1
    rows = await passages.get_passages(doc_id)
    assert len(rows) == n
    assert [r["chunk_index"] for r in rows] == list(range(n))


@pytest.mark.asyncio
async def test_reindexing_replaces_rather_than_appends():
    """A memo edited from long to short must not keep its old passages.

    Orphaned passages from a previous version would keep matching text the memo
    no longer contains — a stale-index defect that is invisible from the
    document tables.
    """
    long_content = "# A\n\n" + " ".join(["alpha"] * 800)
    doc_id = await db.store(None, long_content, "t", [], {}, _vec(0))
    first = await passages.index_document(doc_id, long_content, embed_batch=fake_embed_batch)
    assert first > 1

    short = "now it is short"
    second = await passages.index_document(doc_id, short, embed_batch=fake_embed_batch)
    rows = await passages.get_passages(doc_id)
    assert second == 1
    assert len(rows) == 1, "old passages must be gone, not orphaned"
    assert rows[0]["text"] == short


@pytest.mark.asyncio
async def test_empty_content_clears_the_index_and_is_not_an_error():
    doc_id = await db.store(None, "something", "t", [], {}, _vec(0))
    await passages.index_document(doc_id, "something", embed_batch=fake_embed_batch)
    n = await passages.index_document(doc_id, "", embed_batch=fake_embed_batch)
    assert n == 0
    assert await passages.get_passages(doc_id) == []


@pytest.mark.asyncio
async def test_refuses_a_short_vector_list_rather_than_writing_a_partial_index():
    """A provider returning fewer vectors than passages must NOT silently drop
    the tail — those are the long memos this feature exists to make findable."""
    content = "# A\n\n" + " ".join(["alpha"] * 900)
    doc_id = await db.store(None, content, "t", [], {}, _vec(0))

    async def short_batch(texts):
        return [_vec(1)]        # one vector regardless of passage count

    with pytest.raises(ValueError, match="vectors"):
        await passages.index_document(doc_id, content, embed_batch=short_batch)
    assert await passages.get_passages(doc_id) == [], "nothing may be written on refusal"


def test_replace_is_atomic_on_failure():
    """A mid-write failure must leave the PREVIOUS passage set intact."""
    path = db.global_path()
    good = [Passage(text="a", index=0, token_start=0, token_end=1),
            Passage(text="b", index=1, token_start=1, token_end=2)]
    passages._sync_replace(path, "doc-atomic", good, [_vec(1), _vec(2)],
                           model="m", route="r")
    assert len(passages._sync_get(path, "doc-atomic")) == 2

    bad = [Passage(text="c", index=0, token_start=0, token_end=1)]
    with pytest.raises(ValueError):
        passages._sync_replace(path, "doc-atomic", bad, [_vec(1), _vec(2)],
                               model="m", route="r")
    # The count check fires before any DELETE, so the old set survives.
    assert len(passages._sync_get(path, "doc-atomic")) == 2


@pytest.mark.asyncio
async def test_records_embedding_model_and_route_on_every_passage():
    """002/FR-108: a mixed-provider corpus must stay auditable after the fact."""
    content = "# A\n\n" + " ".join(["alpha"] * 600)
    doc_id = await db.store(None, content, "t", [], {}, _vec(0))
    await passages.index_document(doc_id, content, embed_batch=fake_embed_batch)
    rows = await passages.get_passages(doc_id)
    assert rows
    for r in rows:
        assert r["embedding_model"], "an unlabelled vector cannot be audited later"
        assert r["embedding_route"] == passages.EMBEDDING_ROUTE


@pytest.mark.asyncio
async def test_passage_offsets_describe_own_span_not_overlap():
    """Offsets must stay usable for highlighting even with overlap enabled."""
    content = "# A\n\n" + " ".join(["alpha"] * 700) + "\n\n# B\n\n" + " ".join(["beta"] * 700)
    doc_id = await db.store(None, content, "t", [], {}, _vec(0))
    await passages.index_document(doc_id, content, embed_batch=fake_embed_batch,
                                  target=200, overlap=0.15)
    rows = await passages.get_passages(doc_id)
    for prev, cur in zip(rows, rows[1:]):
        assert cur["token_start"] == prev["token_end"], "spans must stay contiguous"


@pytest.mark.asyncio
async def test_find_unindexed_reports_memos_with_no_passages():
    """The 'every memo is indexed' invariant must be CHECKABLE, not trusted.

    It cannot rest on ten write call sites all remembering to call
    index_document — someone adds an eleventh and the corpus develops holes that
    no listing reveals.
    """
    doc_id = await db.store(None, "a memo nobody indexed", "t", [], {}, _vec(0))
    unindexed = await passages.find_unindexed()
    assert any(r["id"] == doc_id for r in unindexed)

    await passages.index_document(doc_id, "a memo nobody indexed",
                                  embed_batch=fake_embed_batch)
    unindexed = await passages.find_unindexed()
    assert not any(r["id"] == doc_id for r in unindexed)


@pytest.mark.asyncio
async def test_unindexed_is_ordered_biggest_first():
    """Largest memos first: they are the most expensive to lose and the exact
    ones this feature exists to rescue."""
    await db.store(None, "small", "s", [], {}, _vec(0))
    big = " ".join(["alpha"] * 900)
    await db.store(None, big, "b", [], {}, _vec(1))
    rows = await passages.find_unindexed()
    counts = [r["token_count"] for r in rows]
    assert counts == sorted(counts, reverse=True)
