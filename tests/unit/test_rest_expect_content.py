"""`expect_content` must guard the REST write path, not only the MCP tool.

[memo, 2026-09-06] Reported by `quantum-library` and routed by Ben — verbatim,
*"tell memo to fix"*. Measured that day with a deliberately WRONG
`expect_content` and a no-op body: `PATCH /documents/{id}` returned **200 and
bumped `updated_at`** instead of conflicting. The compare-and-set guard existed
only on `memo_update`.

⛔ WHY THIS IS WORSE THAN A MISSING FEATURE, AND THE REASON THE FIX IS A 409 AND
NOT A SHRUG. `UpdateRequest` subclasses `PermissiveRequest`, which tolerates
unknown fields by design (Ben, 2026-08-05) — so the field was ACCEPTED, logged
as a phantom, and DISCARDED. `memo_update`'s own documentation tells callers
that `expect_content` is the guard for a destructive `content=` replace, the one
operation with no undo and no version table. A caller who read that and then
wrote over HTTP got back a 200 with a fresh timestamp: byte-identical to a
successful guarded write, and they had stopped writing their own check because
the API said it had one. ⭐ Silently accepting a guard you do not apply converts
an explicit safety request into a false one.

⛔ BOTH FRONT DOORS. `memo_update` and `PATCH /documents/{id}` are two entrances
to one operation, and this file exists because the sibling suite
(`test_memo_update_prepend.py`) asserts in its own docstring that both doors are
tested while in fact exercising only the MCP tool. That blind spot is precisely
how a guard can be real at one entrance and phantom at the other for weeks.
"""
import pytest
from fastapi.testclient import TestClient

from memo import db
from memo.main import app


@pytest.fixture()
def client():
    # Not the context-manager form — it starts the MCP session manager, which
    # refuses to run twice per process. See test_ready_probe.py.
    return TestClient(app)


@pytest.fixture()
def stored(temp_db):
    async def _mk(content="ORIGINAL BODY", title="t"):
        vec = [0.1] * db.settings.embedding_dimensions
        return await db.store(db_path=None, content=content, title=title,
                              tags=[], metadata={}, embedding=vec)
    return _mk


@pytest.mark.asyncio
async def test_wrong_expect_content_conflicts_instead_of_writing(client, stored):
    """The exact probe `quantum-library` ran. It used to return 200."""
    full = await stored("ORIGINAL BODY")
    before = (await db.get(db_path=None, doc_id=full))["updated_at"]

    r = client.patch(f"/documents/{full}",
                     json={"title": "renamed", "expect_content": "SOMETHING ELSE"})

    assert r.status_code == 409, \
        "a guard the caller explicitly asked for must refuse, not report success"
    after = await db.get(db_path=None, doc_id=full)
    assert after["title"] == "t", "a refused write must touch nothing at all"
    assert after["content"] == "ORIGINAL BODY"
    assert after["updated_at"] == before, \
        "a bumped timestamp on a refused write is the silent-success shape itself"


@pytest.mark.asyncio
async def test_matching_expect_content_permits_the_write(client, stored):
    """The guard must not become a refusal machine — the honest case still lands."""
    full = await stored("ORIGINAL BODY")

    r = client.patch(f"/documents/{full}",
                     json={"title": "renamed", "expect_content": "ORIGINAL BODY"})

    assert r.status_code == 200
    assert (await db.get(db_path=None, doc_id=full))["title"] == "renamed"


@pytest.mark.asyncio
async def test_it_guards_a_title_only_patch_not_merely_a_content_write(client, stored):
    """`expect_content` asks *did the body move under me*, whatever I am writing.

    The comparison in `_sync_update` is unconditional, and this asserts the REST
    layer does not narrow it to content writes — retitling a memo whose body
    changed underneath is the same lost-update class.
    """
    full = await stored("ORIGINAL BODY")
    # someone else writes between the caller's read and their PATCH
    await db.update(db_path=None, doc_id=full, content="MOVED UNDER YOU",
                    title=None, tags=None, metadata=None, embedding=None)

    r = client.patch(f"/documents/{full}",
                     json={"title": "renamed", "expect_content": "ORIGINAL BODY"})

    assert r.status_code == 409
    assert (await db.get(db_path=None, doc_id=full))["title"] == "t"


@pytest.mark.asyncio
async def test_a_caller_assertion_beats_the_value_derived_for_prepend(client, stored):
    """append/prepend derive `expect_content` from what they just read, so the
    derived value can never fail. A caller's own assertion is about what they
    read EARLIER — the only one that catches a concurrent write, so it wins."""
    full = await stored("ORIGINAL BODY")
    await db.update(db_path=None, doc_id=full, content="MOVED UNDER YOU",
                    title=None, tags=None, metadata=None, embedding=None)

    r = client.patch(f"/documents/{full}",
                     json={"prepend": "BANNER\n", "expect_content": "ORIGINAL BODY"})

    assert r.status_code == 409, \
        "the derived value would have matched and overwritten a concurrent write"
    assert (await db.get(db_path=None, doc_id=full))["content"] == "MOVED UNDER YOU"


@pytest.mark.asyncio
async def test_prepend_without_an_assertion_keeps_its_own_guard(client, stored):
    """Falling back to the derived value preserves the v0.4.0/v0.5.0 contract."""
    full = await stored("TAIL")

    r = client.patch(f"/documents/{full}", json={"prepend": "HEAD "})

    assert r.status_code == 200
    assert (await db.get(db_path=None, doc_id=full))["content"] == "HEAD TAIL"


@pytest.mark.asyncio
async def test_omitting_the_field_leaves_the_write_unguarded_as_before(client, stored):
    """No implicit guard. Adding one would break callers who never asked."""
    full = await stored("ORIGINAL BODY")

    r = client.patch(f"/documents/{full}", json={"title": "renamed"})

    assert r.status_code == 200
    assert (await db.get(db_path=None, doc_id=full))["title"] == "renamed"
