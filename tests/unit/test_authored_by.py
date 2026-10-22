"""`authored_by` — which SEAT wrote a memo.

Ben, Slack #memo 2026-10-22 11:47 EDT, answering ask a0r (open since 09-20):
"I think it should record who wrote each memo that's good idea".
"""
import pytest
from fastapi.testclient import TestClient

from memo.main import app


@pytest.fixture()
def client():
    # `temp_db` is autouse, so the app this wraps is already pointed at a
    # throwaway DB. Defined locally for the same reason the tombstone suite
    # does: there is no shared `client` fixture in tests/conftest.py.
    return TestClient(app)


@pytest.mark.asyncio
async def test_a_store_records_the_writing_seat_and_every_read_returns_it(client, embedding):
    """⛔ A STORED FIELD MUST COME BACK FROM THE BULK READ, NOT JUST THE SINGLE GET.

    This repo has paid for that shape twice: the short-id fix landed on MCP and
    not REST, and `superseded_by` was computed by `GET /documents/<id>` while
    `/documents` answered null for all 11,599 live memos. A field that is
    present and always empty is worse than an absent one — absent sends the
    caller to go and ask, present-and-null gives them a false answer.
    """
    r = client.post("/documents", json={
        "content": "the embedder cap is data loss behind a cron",
        "title": "cap", "actor": "memo"})
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]

    assert client.get(f"/documents/{doc_id}").json()["authored_by"] == "memo"

    listed = client.get("/documents", params={"limit": 20000}).json()
    row = next(d for d in listed if d["id"] == doc_id)
    assert row["authored_by"] == "memo", \
        "the list must not drop a field the single GET returns"


@pytest.mark.asyncio
async def test_a_store_without_an_actor_is_accepted_and_honestly_null(client):
    """⛔ OPTIONAL ON PURPOSE — REQUIRING IT WOULD LOSE MEMOS.

    ~9,900 existing memos have no recoverable author and every current client
    predates this field. A 422 for a missing label discards the memo, which is
    strictly worse than storing it unlabelled: an unlabelled memo is still true.
    """
    r = client.post("/documents", json={"content": "no actor given", "title": "anon"})
    assert r.status_code == 200, r.text
    assert client.get(f"/documents/{r.json()['id']}").json()["authored_by"] is None


@pytest.mark.asyncio
async def test_an_edit_does_not_reassign_authorship(client):
    """⛔⛔ AN EDITOR IS NOT THE AUTHOR, AND CONFLATING THEM REWRITES HISTORY.

    The audit PATCHes memos it did not write — banners, corrections, appends.
    If `actor` on an update overwrote `authored_by`, the first audit pass would
    silently reassign every memo it touched to `memo`, and the corpus would then
    assert that this seat wrote work it only corrected. That is unrecoverable:
    nothing in the record would show the original author had been replaced.
    """
    created = client.post("/documents", json={
        "content": "original", "title": "t", "actor": "cluster"}).json()["id"]

    patched = client.patch(f"/documents/{created}",
                           json={"append": "\nan audit correction", "actor": "memo"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["authored_by"] == "cluster", \
        "the editing seat must not take over authorship"
    assert client.get(f"/documents/{created}").json()["authored_by"] == "cluster"
