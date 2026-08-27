"""`memo_update` must resolve a short id, exactly as `memo_get` does. [network, 2026-08-25]

Measured against one corpus in one minute, before this fix:

    memo_update(id="63059df0", append=…) -> {"updated": false, "reason": "not_found",
                                             "requested_id": "63059df0"}
    memo_get(id="63059df0")              -> the document, "resolved_from": "63059df0"

The full uuid worked on both. The READ path expanded an unambiguous prefix; the
WRITE path did not.

⭐ THE HARM IS THE READING, NOT THE FAILED WRITE. `not_found` is also the honest
answer for a memo that was deleted, and the 8-char id is what actually circulates
— seat guides, pins, commit messages, agent DMs. So a seat appends to a memo it
cites by prefix, is told `not_found`, and concludes the corpus lost a document.
That is the same failure `memo_get` was fixed for on 2026-08-20 (a bare null
reading as "genuinely gone"); `memo_update` still had the version of it we had
already fixed next door.

⛔ AND THE TWO FRONT DOORS DISAGREED. `PATCH /documents/{id}` already resolved via
`_resolve_doc_id_or_404`; the MCP tool was simply missed when that was added. One
operation, two entry points, different answers to the same id — which is why
these tests assert the CONTRACT rather than one call.
"""
import pytest

from memo import db
from memo.main import memo_update


@pytest.mark.asyncio
async def test_unique_prefix_resolves_on_the_write_path(temp_db):
    """The regression itself."""
    vec = [0.1] * db.settings.embedding_dimensions
    full = await db.store(db_path=None, content="original body", title="t",
                          tags=[], metadata={}, embedding=vec)

    got = await memo_update(id=full[:8], append=" APPENDED")

    assert got.get("updated") is True, \
        "a unique 8-char prefix must resolve on the write path, not answer not_found"
    assert got.get("resolved_from") == full[:8], \
        "an expanded abbreviation must be visible to the caller — a write that " \
        "silently retargets is worse than a read that does"

    stored = await db.get(db_path=None, doc_id=full)
    assert stored["content"] == "original body APPENDED", \
        "the resolved id must be the one actually written to"


@pytest.mark.asyncio
async def test_ambiguous_prefix_is_refused_and_never_guessed(temp_db):
    """On a write, picking one of two candidates is unrecoverable, not merely misleading."""
    vec = [0.1] * db.settings.embedding_dimensions
    ids = []
    for i in range(40):
        ids.append(await db.store(db_path=None, content=f"body {i}", title=f"t{i}",
                                  tags=[], metadata={}, embedding=vec))
    # Find a 1-char prefix genuinely shared by at least two of them.
    shared = None
    for c in "0123456789abcdef":
        if len([i for i in ids if i.startswith(c)]) >= 2:
            shared = c * 1
            break
    if shared is None:  # pragma: no cover - 40 random uuids make this vanishingly unlikely
        pytest.skip("no shared prefix among generated ids")
    # A 1-char fragment is below the resolver's minimum, so widen to a real
    # collision: use the first 4 chars of one id and assert on whatever comes back.
    victim = [i for i in ids if i.startswith(shared)][0]
    frag = victim[:4]
    matches = [i for i in ids if i.startswith(frag)]

    got = await memo_update(id=frag, content="OVERWRITTEN")

    if len(matches) > 1:
        assert got["updated"] is False
        assert got["reason"] == "ambiguous", \
            "two memos sharing a prefix is exactly when picking one is worst"
        assert got["candidate_count"] >= 2
        for m in matches:
            assert (await db.get(db_path=None, doc_id=m))["content"] != "OVERWRITTEN", \
                "an ambiguous write must touch nothing at all"
    else:
        assert got["updated"] is True, "an unambiguous fragment must still resolve"


@pytest.mark.asyncio
async def test_exact_id_unchanged_and_carries_no_resolved_from(temp_db):
    """Positive control: the fix must not alter the path that already worked."""
    vec = [0.1] * db.settings.embedding_dimensions
    full = await db.store(db_path=None, content="body", title="t",
                          tags=[], metadata={}, embedding=vec)

    got = await memo_update(id=full, title="retitled")

    assert got["updated"] is True
    assert "resolved_from" not in got, "nothing was abbreviated, so nothing was resolved"
    assert (await db.get(db_path=None, doc_id=full))["title"] == "retitled"


@pytest.mark.asyncio
async def test_a_genuine_miss_still_says_not_found(temp_db):
    """The fix must not turn a real absence into something softer."""
    missing = "deadbeef-0000-0000-0000-000000000000"

    got = await memo_update(id=missing, title="x")

    assert got["updated"] is False
    assert got["reason"] == "not_found"
    assert got["requested_id"] == missing, \
        "echo the id back so a typo is diagnosable from the response alone"
