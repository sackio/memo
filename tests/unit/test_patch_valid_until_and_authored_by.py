"""`valid_until` (clear-only) and `authored_by` on PATCH /documents/{id}.

AUTHORISED by Ben, Slack #memo 2026-10-23 11:05 ("yes"), scoped to clearing the
expiry on 52 continuations that were marked expired although their successor
CONTINUES them rather than replacing them.

⛔⛔ THE TEST THAT MATTERS MOST IN THIS FILE IS `test_c_...`, THE ONE THAT WRITES
   NEITHER FIELD. Both columns are legitimately NULL, so the codebase-wide
   convention of `None` == "caller did not send this" cannot express clearing
   them — and the obvious one-line implementation (`if req.valid_until is None:
   leave alone` / `else write`) inverts into its opposite: an ordinary
   title-only PATCH would write NULL over a live expiry. That is a silent
   un-supersession of every memo anyone edits, from a change that reviews as
   harmless. The presence test is `model_fields_set`; this asserts it.
"""
import pytest
from fastapi.testclient import TestClient

from memo.main import app


@pytest.fixture()
def client():
    # Local for the same reason test_authored_by.py defines one: there is no
    # shared `client` fixture in tests/conftest.py. `temp_db` is autouse.
    return TestClient(app)


def _make(client, **kw):
    body = {"content": "x" * 50, "title": "t"}
    body.update(kw)
    r = client.post("/documents", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_a_explicit_null_clears_an_expiry_and_the_read_back_agrees(client, embedding):
    """⛔ READ BACK, do not trust the 200. This API has returned success for a
    field it discarded three times (`add_tags`, `expect_content` over HTTP, and
    `valid_until` itself, which is why this exists)."""
    old_id, new_id = _make(client), _make(client)
    r = client.post("/supersede-link", json={
        "old_id": old_id, "new_id": new_id, "actor": "test", "reason": "continues"})
    assert r.status_code == 200, r.text
    assert client.get(f"/documents/{old_id}").json()["valid_until"] is not None

    assert client.patch(f"/documents/{old_id}", json={"valid_until": None}).status_code == 200
    back = client.get(f"/documents/{old_id}").json()
    assert back["valid_until"] is None, f"200 but still expired: {back['valid_until']}"
    # ⭐ The lineage is TRUE even though the expiry was wrong — Ben's scope was
    #   the expiry alone, and dropping the edge would lose real information.
    assert back["superseded_by"] == new_id, "clearing the expiry must not drop the edge"


@pytest.mark.asyncio
async def test_b_setting_an_expiry_is_refused_and_names_the_route_that_does_it(client, embedding):
    doc_id = _make(client)
    r = client.patch(f"/documents/{doc_id}", json={"valid_until": 1_700_000_000.0})
    assert r.status_code == 422, r.text
    assert "supersede-link" in r.json()["detail"], \
        "a refusal that does not name the working route just blocks the caller"
    assert client.get(f"/documents/{doc_id}").json()["valid_until"] is None


@pytest.mark.asyncio
async def test_c_an_ordinary_patch_touches_neither_column(client, embedding):
    """THE REGRESSION THIS FILE EXISTS FOR — see the module docstring."""
    old_id, new_id = _make(client, actor="slack-miner"), _make(client)
    client.post("/supersede-link", json={
        "old_id": old_id, "new_id": new_id, "actor": "test", "reason": "r"})
    expiry = client.get(f"/documents/{old_id}").json()["valid_until"]
    assert expiry is not None

    assert client.patch(f"/documents/{old_id}", json={"title": "renamed"}).status_code == 200
    back = client.get(f"/documents/{old_id}").json()
    assert back["title"] == "renamed"
    assert back["valid_until"] == expiry, "a title-only PATCH un-superseded the memo"
    assert back["authored_by"] == "slack-miner", "a title-only PATCH erased the author"


@pytest.mark.asyncio
async def test_d_authored_by_is_correctable_and_is_not_the_editor(client, embedding):
    """29 rows carried five spellings of one writer because the column was
    write-once and the writer is a `claude -p` worker. ⚠️ `actor` on an UPDATE
    is whoever is editing; it must not silently reassign authorship."""
    doc_id = _make(client, actor="slack-mining")
    assert client.patch(f"/documents/{doc_id}",
                        json={"authored_by": "slack-miner"}).status_code == 200
    assert client.get(f"/documents/{doc_id}").json()["authored_by"] == "slack-miner"

    client.patch(f"/documents/{doc_id}", json={"actor": "memo", "title": "edited"})
    assert client.get(f"/documents/{doc_id}").json()["authored_by"] == "slack-miner", \
        "`actor` on an edit must not overwrite the original author"


@pytest.mark.asyncio
async def test_e_the_mcp_door_can_clear_it_too(client, embedding):
    """⛔ BOTH FRONT DOORS. The last time `memo_update` and PATCH diverged, a
    seat was told `not_found` for a memo that existed."""
    from memo.main import memo_update

    old_id, new_id = _make(client), _make(client)
    client.post("/supersede-link", json={
        "old_id": old_id, "new_id": new_id, "actor": "test", "reason": "r"})
    assert client.get(f"/documents/{old_id}").json()["valid_until"] is not None

    await memo_update(id=old_id, clear_valid_until=True)
    assert client.get(f"/documents/{old_id}").json()["valid_until"] is None

    # And the default must be inert, for the same reason as test_c.
    other = _make(client)
    client.post("/supersede-link", json={
        "old_id": other, "new_id": new_id, "actor": "test", "reason": "r"})
    expiry = client.get(f"/documents/{other}").json()["valid_until"]
    await memo_update(id=other, title="renamed")
    assert client.get(f"/documents/{other}").json()["valid_until"] == expiry
