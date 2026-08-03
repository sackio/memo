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
async def test_superseded_document_is_excluded_and_opt_in_reaches_it(tmp_memo_db):
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
async def test_passage_search_still_returns_results(tmp_memo_db):
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
    """
    vec = [0.1] * db.settings.embedding_dimensions
    doc_id = await db.store(db_path=None, content="barn cluster node inventory",
                            title="barn", tags=[], metadata={}, embedding=vec)
    conn = db._get_or_create_conn(db._resolve_path(None))
    conn.execute("INSERT INTO document_chunks (doc_id, chunk_index, text, "
                 "token_start, token_end, embedding_model, embedding_route, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (doc_id, 0, "barn cluster node inventory", 0, 4,
                  db.settings.embedding_model, "test", 1.0))
    conn.execute("INSERT INTO chunk_embeddings (doc_id, chunk_index, embedding) VALUES (?,?,?)",
                 (doc_id, 0, db._serialize_vector(vec)))
    conn.commit()

    hits = await db.search_passages(None, vec, 5)
    assert hits, "passage search returned NOTHING — the overfetch slot bug is back"
