"""Superseded memos must not be returned by search. [002/FR-115]

Before 2026-08-03 `/supersede` wrote a `supersede_edges` row and set
`valid_until`, and **search returned the superseded memo anyway** — measured
live, at rank 3, immediately after a successful supersede. The bitemporal model
was complete on the write side and absent from the read path: `get_current` and
`as-of` honoured `valid_until`; `_sync_search`, which every `/recall` goes
through, never referenced it.

⇒ The corpus's 655 self-declared-stale memos could all have been correctly
superseded and every one would still have been served.
"""
import pytest

from memo import db


@pytest.mark.asyncio
async def test_superseded_document_is_excluded_and_opt_in_reaches_it(temp_db):
    """The exclusion, and the escape hatch, in one test."""
    vec = [0.1] * db.settings.embedding_dimensions
    old_id = await db.store(db_path=None, content="gate code is 1111",
                            title="gate code", tags=[], metadata={}, embedding=vec)
    conn = db._get_or_create_conn(db._resolve_path(None))

    # POSITIVE CONTROL — it must be findable BEFORE, or the assertion after
    # proves nothing. A test that cannot distinguish "excluded" from "was never
    # there" is not a test.
    before = await db.search(None, vec, 10, None, [], None, None, None, None)
    assert old_id in [r["document"]["id"] for r in before], "control failed: not findable before"

    conn.execute("UPDATE documents SET valid_until = ? WHERE id = ?", (1.0, old_id))
    conn.commit()

    after = await db.search(None, vec, 10, None, [], None, None, None, None)
    assert old_id not in [r["document"]["id"] for r in after], "superseded doc still returned"

    opted = await db.search(None, vec, 10, None, [], None, None, None, None,
                            include_superseded=True)
    assert old_id in [r["document"]["id"] for r in opted], "escape hatch does not reach it"


@pytest.mark.asyncio
async def test_passage_search_still_returns_results(temp_db):
    """⛔ REGRESSION GUARD for a silent zero.

    `include_superseded` was first appended POSITIONALLY to the `to_thread` call
    for `_sync_search_passages`, which takes `overfetch: int = 8` BEFORE it. So
    `False` landed in the overfetch slot, `k = limit * False = 0`, and passage
    search returned **0 hits with HTTP 200 for every query** — a plausible "no
    results", not an error.

    ⇒ **Appending a positional argument is unsafe whenever the callee has an
    intervening default, and `to_thread` forwards positionally and cannot warn.**
    The supersede test above PASSED throughout; only an unrelated positive
    control caught it.

    ⚠️ THE FIXTURE CHANGED 2026-08-19 AND THE GUARD DID NOT. This used to
    hand-insert a `document_chunks` / `chunk_embeddings` row because `db.store()`
    left the memo unindexed. `store()` now indexes inline, so the hand-insert
    collides with the real row (`UNIQUE constraint failed: document_chunks.doc_id,
    document_chunks.chunk_index`) — the test failed on its own scaffolding, not on
    the behaviour it guards.

    ⭐ Removing the scaffold makes it a STRONGER guard, not a weaker one: the
    passage it searches is now produced by the real chunk-and-embed path rather
    than by a row this test wrote to look like one. A fixture that fakes the thing
    under test can only ever prove the query runs.
    """
    vec = [0.1] * db.settings.embedding_dimensions
    doc_id = await db.store(db_path=None, content="barn cluster node inventory",
                            title="barn", tags=[], metadata={}, embedding=vec)

    # Precondition, stated rather than assumed: the silent-zero this guards
    # against is indistinguishable from "there was nothing to find".
    from memo import passages
    assert await passages.get_passages(doc_id), \
        "precondition: store() should have indexed the memo inline"

    hits = await db.search_passages(None, vec, 5)
    assert hits, "passage search returned NOTHING — the overfetch slot bug is back"
