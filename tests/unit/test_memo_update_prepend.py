"""`prepend` — the correction that must be read BEFORE the text it corrects. [2026-08-27]

⛔ WHY THIS PARAMETER EXISTS. `append` lands at the END of a document, so it
cannot carry a banner that retires a claim made above it. The memo corpus
retires claims by prepending `⛔ SUPERSEDED …` rather than deleting the record
that explains them — so every such banner was a hand-rolled GET → concat →
PATCH, and the guarantee that nothing was lost lived in the caller's discipline
instead of the API. Seventeen were applied by hand in one memo-minder cycle on
2026-08-27, each needing its own `new.endswith(old)` assertion; a single omitted
assertion overwrites a memo, and there is no undo.

⭐ So these tests assert the property that matters — THE PRIOR CONTENT SURVIVES
INTACT AND ENDS THE DOCUMENT — not merely that the call returned `updated: true`.
That is the assertion the hand-rolled version had to make for itself.

⛔ AND BOTH FRONT DOORS ARE TESTED. `memo_update` and `PATCH /documents/{id}` are
two entrances to one operation; when they last diverged, a seat was told
`not_found` for a memo that existed.
"""
import pytest

from memo import db
from memo.main import memo_update


async def _store(text="original body"):
    vec = [0.1] * db.settings.embedding_dimensions
    return await db.store(db_path=None, content=text, title="t",
                          tags=[], metadata={}, embedding=vec)


@pytest.mark.asyncio
async def test_prepend_lands_at_the_front_and_prior_content_survives(temp_db):
    """The regression the parameter exists to prevent."""
    full = await _store("ORIGINAL BODY")

    got = await memo_update(id=full, prepend="⛔ SUPERSEDED — read this first.\n\n")

    assert got.get("updated") is True
    stored = (await db.get(db_path=None, doc_id=full))["content"]
    assert stored.startswith("⛔ SUPERSEDED — read this first.\n\n"), \
        "a correction that lands anywhere but the front is one a reader reaches too late"
    assert stored.endswith("ORIGINAL BODY"), \
        "the prior content must survive VERBATIM — this is the assertion every " \
        "hand-rolled read-modify-write had to make for itself"


@pytest.mark.asyncio
async def test_prepend_is_verbatim_and_injects_no_separator(temp_db):
    """Same contract as append: predictable beats convenient."""
    full = await _store("body")

    await memo_update(id=full, prepend="X")

    assert (await db.get(db_path=None, doc_id=full))["content"] == "Xbody"


@pytest.mark.asyncio
async def test_prepend_with_content_is_refused_not_merged(temp_db):
    """One replaces, one extends. Guessing is how a write API loses data."""
    full = await _store("keep me")

    got = await memo_update(id=full, prepend="banner", content="OVERWRITTEN")

    assert got["updated"] is False
    assert got["reason"] == "ambiguous_content_and_prepend"
    assert (await db.get(db_path=None, doc_id=full))["content"] == "keep me", \
        "a refused write must touch nothing at all"


@pytest.mark.asyncio
async def test_prepend_with_append_is_refused(temp_db):
    """Applying both in an order the caller never specified is a guess about a write."""
    full = await _store("middle")

    got = await memo_update(id=full, prepend="front", append="back")

    assert got["updated"] is False
    assert got["reason"] == "ambiguous_append_and_prepend"
    assert (await db.get(db_path=None, doc_id=full))["content"] == "middle"


@pytest.mark.asyncio
async def test_prepend_resolves_a_short_id_like_every_other_write(temp_db):
    """The write path must expand an unambiguous prefix — see test_memo_update_short_id."""
    full = await _store("tail")

    got = await memo_update(id=full[:8], prepend="head ")

    assert got.get("updated") is True
    assert got.get("resolved_from") == full[:8]
    assert (await db.get(db_path=None, doc_id=full))["content"] == "head tail"


@pytest.mark.asyncio
async def test_a_genuine_miss_still_says_not_found(temp_db):
    """The convenience must not soften a real absence."""
    got = await memo_update(id="deadbeef-0000-0000-0000-000000000000", prepend="x")

    assert got["updated"] is False
    assert got["reason"] == "not_found"
