"""Refuse to search with a model that did not write the vectors. [002/FR-108]

Raised by the `embeddings` seat 2026-08-02 during the fleet embedding inventory,
and it found a real gap that my own model/dimension table would have hidden:

    memo   "2560, self-hosted qwen3"   ← looks identical to mind's row
    mind   "2560, self-hosted qwen3"   ← but resolves from the ACTIVE COLLECTION

Same values, opposite safety properties. `mind` cannot have a query/corpus
disagreement because the model is resolved from the collection being read. memo
names the model in config, independently of what wrote the vectors, so it can.

**The defence I had was real but keyed on the wrong thing.** `vec0` bakes the
width into the table and rejects a mismatched query vector — verified, not
assumed. But width is a PROXY for model, faithful only because 2560 happens to be
unusual. That is a property of the model landscape, not of memo.

⇒ A same-width model swap is the case the width guard misses entirely: no
exception, no width error, vectors compared across models, plausible and
confidently wrong results. This asserts on the READ path, because writes already
fail loudly (a store has a shape) and reads are the silent side.

**A real instance existed when this was written.** `document_chunks` recorded
`openai/text-embedding-3-large` for 19,554 chunks whose vectors had been
re-embedded with qwen3 — the re-embed wrote the vector tables directly and never
updated the provenance column. The field was present and LYING, which is worse
than absent because it would be trusted. Corrected, and this check would have
surfaced it on the first search.
"""
from __future__ import annotations

import pytest

from memo import db
from memo.config import settings


@pytest.fixture(autouse=True)
def _clear_cache():
    db._write_model_cache.clear()
    yield
    db._write_model_cache.clear()


def _record_write_model(model: str) -> None:
    conn = db._get_or_create_conn(db.global_path())
    conn.execute(
        "INSERT INTO document_chunks (doc_id, chunk_index, text, token_start, "
        "token_end, embedding_model, embedding_route, created_at) "
        "VALUES ('d1', 0, 'text', 0, 10, ?, 'test', 1.0)", (model,))
    conn.commit()


def test_matching_model_passes(embedding):
    _record_write_model(settings.embedding_model)
    db.assert_read_model_matches()          # must not raise


def test_a_different_model_is_refused(embedding):
    """The headline: the case the width guard cannot see."""
    _record_write_model("some-other-2560-dim-model")

    with pytest.raises(db.EmbeddingModelMismatch) as e:
        db.assert_read_model_matches()

    msg = str(e.value)
    assert "some-other-2560-dim-model" in msg, "say what wrote the vectors"
    assert settings.embedding_model in msg, "and what is configured now"


def test_an_empty_passage_index_does_not_block_reads(embedding):
    """No chunks yet means nothing to disagree with. A fresh corpus must not be
    unsearchable — the check exists to catch divergence, not to require an index."""
    db.assert_read_model_matches()          # must not raise


@pytest.mark.asyncio
async def test_search_refuses_on_mismatch(embedding):
    """Asserted on the READ PATH, not merely available as a helper.

    A check nobody calls is documentation. This drives `db.search` itself.
    """
    _record_write_model("mismatched-model")

    with pytest.raises(db.EmbeddingModelMismatch):
        await db.search(db_path=None, embedding=embedding, limit=5,
                        min_score=None, tags=[], after=None, before=None,
                        min_tokens=None, max_tokens=None)


@pytest.mark.asyncio
async def test_search_passages_refuses_on_mismatch(embedding):
    """Both read paths, independently — one guarded and one not is the same
    silent hole in a different place."""
    _record_write_model("mismatched-model")

    with pytest.raises(db.EmbeddingModelMismatch):
        await db.search_passages(None, embedding, 5)


@pytest.mark.asyncio
async def test_search_works_when_the_model_agrees(embedding):
    _record_write_model(settings.embedding_model)
    out = await db.search(db_path=None, embedding=embedding, limit=5,
                          min_score=None, tags=[], after=None, before=None,
                          min_tokens=None, max_tokens=None)
    assert isinstance(out, list)


def test_the_result_is_cached_not_re_read_per_query(embedding):
    """A per-query round-trip for a value that cannot change without a restart
    would be a real cost. Cached after the first call."""
    _record_write_model(settings.embedding_model)
    db.assert_read_model_matches()

    calls = {"n": 0}
    real = db._sync_stored_write_model

    def counting(path):
        calls["n"] += 1
        return real(path)

    db._sync_stored_write_model = counting
    try:
        for _ in range(5):
            db.assert_read_model_matches()
    finally:
        db._sync_stored_write_model = real

    assert calls["n"] == 0, "the write-model lookup must not repeat per query"
