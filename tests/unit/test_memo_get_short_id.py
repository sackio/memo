"""`memo_get` must never answer a short id with a bare null. [atc, 2026-08-20]

`memo_get("823181f1")` returned `{"result": null}` while the memo existed —
it needed the full uuid. Short ids are what circulate, in pins, commit messages
and agent DMs, so this was the common call.

⭐ The reported harm is not the failed lookup, it is the READING: a bare null
cannot distinguish "mistyped/abbreviated id" from "memo genuinely gone", and the
alarming interpretation is the plausible one. `atc` was one step from reporting a
canonical memo deleted.

`memo_update`'s docstring already made exactly this argument and implemented the
structured `not_found`. The two tools faced the same decision and answered it
differently, which is why this test asserts the CONTRACT rather than one call.
"""
import pytest

from memo import db
from memo.main import memo_get


@pytest.mark.asyncio
async def test_unique_prefix_resolves_and_says_it_did(temp_db):
    vec = [0.1] * db.settings.embedding_dimensions
    full = await db.store(db_path=None, content="gate code is 1111", title="gate",
                          tags=[], metadata={}, embedding=vec)

    got = await memo_get(full[:8])

    assert got.get("id") == full, "a unique 8-char prefix must resolve to the memo"
    assert got.get("resolved_from") == full[:8], \
        "an expanded abbreviation must be visible to the caller, not silent"


@pytest.mark.asyncio
async def test_a_miss_is_structured_and_never_a_bare_null(temp_db):
    """The regression itself: the shape of a miss."""
    missing = "deadbeef-0000-0000-0000-000000000000"

    got = await memo_get(missing)

    assert got is not None, "a bare null is the bug — it reads as 'genuinely gone'"
    assert got["found"] is False
    assert got["reason"] == "not_found"
    assert got["requested_id"] == missing, \
        "echo the id back so a typo is diagnosable from the response alone"


@pytest.mark.asyncio
async def test_exact_id_is_unchanged_and_carries_no_resolved_from(temp_db):
    """Positive control: the fix must not alter the path that already worked."""
    vec = [0.1] * db.settings.embedding_dimensions
    full = await db.store(db_path=None, content="barn node inventory", title="barn",
                          tags=[], metadata={}, embedding=vec)

    got = await memo_get(full)

    assert got["id"] == full
    assert got["content"] == "barn node inventory"
    assert "resolved_from" not in got, "nothing was abbreviated, so nothing was resolved"


@pytest.mark.asyncio
async def test_ambiguous_prefix_reports_candidates_instead_of_guessing(temp_db):
    """⛔ Two memos sharing a prefix is exactly when returning one is worst."""
    vec = [0.1] * db.settings.embedding_dimensions
    # ⛔ ALL stores complete BEFORE the raw connection opens a write transaction.
    # Interleaving them deadlocks: the uncommitted UPDATE holds the write lock
    # and the next `store()` blocks on its own connection — `database is locked`,
    # a failure in the scaffolding rather than in the behaviour under test.
    stored = [await db.store(db_path=None, content=body, title=f"t{n}",
                             tags=[], metadata={}, embedding=vec)
              for n, body in ((1, "first"), (2, "second"))]

    # Force a shared prefix — the collision IS the condition under test, and
    # waiting for uuid4 to produce one is not a test.
    conn = db._get_or_create_conn(db._resolve_path(None))
    ids = [f"abcd1234-0000-0000-0000-00000000000{n}" for n in (1, 2)]
    for doc_id, forced in zip(stored, ids):
        conn.execute("UPDATE documents SET id = ? WHERE id = ?", (forced, doc_id))
    conn.commit()

    got = await memo_get("abcd1234")

    assert got["found"] is False
    assert got["reason"] == "ambiguous"
    assert got["candidate_count"] == 2
    assert {c["id"] for c in got["candidates"]} == set(ids), \
        "name the candidates so the caller can disambiguate without a second guess"


@pytest.mark.asyncio
async def test_a_short_non_id_string_does_not_scan_for_prefixes(temp_db):
    """`memo_get("the barn")` is a mistake, not an abbreviation — no LIKE scan."""
    got = await memo_get("the barn")

    assert got["found"] is False
    assert got["reason"] == "not_found"
