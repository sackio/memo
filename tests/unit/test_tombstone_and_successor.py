"""A dead id must say WHERE TO GO, and a replaced memo must name its successor.

[memo, 2026-10-20] Ben directed both, Slack #memo 19:36 and 19:42 EDT: a memo
that was replaced points at its replacement; one that was dead or wrong says it
was deleted and NOT replaced. Two answers, not one.

⛔ THE DEFECT THIS CLOSES. FastAPI answers an unknown path and a deleted
document with the same bare 404, and a superseded memo answered 200 with
`valid_until` set and nothing naming its successor. Measured 2026-10-20: two
independent hosts, ten minutes apart, invented `GET /documents/<id>/lineage` —
a route that does not exist — rather than discover `/current` (memo f93eba87).
A 200 that is truthful about the past and silent about the present is the
expensive kind of correct: no error fires, so the caller fabricates a path.

⛔ THREE OUTCOMES MUST STAY DISTINGUISHABLE, which is the whole point:
  410 + replaced_by   — gone, and here is the successor
  410 + replaced:False — gone, and there is deliberately no successor
  404                  — never existed here, as far as this service knows
Collapsing any two of these is the bug, so each is asserted separately.

⚠️ Measured on the live corpus the day this shipped: 4,846 deletion-log rows,
21 with a successor and 4,825 without — the no-successor branch is the COMMON
case, so it gets the same weight in tests as the replaced one.
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
    async def _mk(content="the gate code lives in the lockbox", title="t"):
        vec = [0.1] * db.settings.embedding_dimensions
        return await db.store(db_path=None, content=content, title=title,
                              tags=[], metadata={}, embedding=vec)
    return _mk


@pytest.mark.asyncio
async def test_deleted_and_replaced_returns_410_naming_the_successor(client, stored):
    dead = await stored(title="old")
    live = await stored(title="new")
    assert client.delete(f"/documents/{dead}",
                         params={"actor": "memo", "reason": "merged",
                                 "replaced_by": live}).status_code == 200

    r = client.get(f"/documents/{dead}")
    assert r.status_code == 410, "a deleted memo must not share 404 with an unknown id"
    d = r.json()["detail"]
    assert d["reason"] == "deleted"
    assert d["replaced"] is True
    assert d["replaced_by"] == live
    assert live in d["message"], "the successor id must be in the prose, not only a field"


@pytest.mark.asyncio
async def test_deleted_without_replacement_says_so_rather_than_going_quiet(client, stored):
    dead = await stored(title="wrong thing")
    assert client.delete(f"/documents/{dead}",
                         params={"actor": "memo", "reason": "dead"}).status_code == 200

    r = client.get(f"/documents/{dead}")
    assert r.status_code == 410
    d = r.json()["detail"]
    assert d["replaced"] is False
    assert d["replaced_by"] is None
    # ⛔ "Deleted and not replaced" is an ANSWER. If this ever renders as an
    # absent pointer, a reader goes looking for a successor that never existed.
    assert "NOT replaced" in d["message"]


@pytest.mark.asyncio
async def test_unknown_id_still_404s_and_is_not_given_an_invented_tombstone(client, temp_db):
    r = client.get("/documents/ffffffff-ffff-ffff-ffff-ffffffffffff")
    assert r.status_code == 404, "an id we never saw must not acquire a deletion record"


@pytest.mark.asyncio
async def test_tombstone_resolves_a_short_id_because_that_is_what_circulates(client, stored):
    dead = await stored(title="old")
    live = await stored(title="new")
    client.delete(f"/documents/{dead}",
                  params={"actor": "memo", "reason": "merged", "replaced_by": live})

    r = client.get(f"/documents/{dead[:8]}")
    assert r.status_code == 410
    assert r.json()["detail"]["replaced_by"] == live


@pytest.mark.asyncio
async def test_live_superseded_document_carries_its_successor_on_the_envelope(client, stored):
    old = await stored(title="v1")
    new = await stored(title="v2")
    assert client.post("/supersede-link", json={
        "old_id": old, "new_id": new, "actor": "memo"}).status_code == 200

    body = client.get(f"/documents/{old}").json()
    # ⛔ Must be a MODEL FIELD. `Document` is strict and `response_model`
    # silently strips unknown keys, so a dict-only addition would vanish here
    # with no error — which is exactly why this assertion exists.
    assert body["superseded_by"] == new
    assert body["lineage_tip"] == new


@pytest.mark.asyncio
async def test_tip_does_not_point_at_itself(client, stored):
    old = await stored(title="v1")
    new = await stored(title="v2")
    client.post("/supersede-link", json={"old_id": old, "new_id": new, "actor": "memo"})

    body = client.get(f"/documents/{new}").json()
    assert body["superseded_by"] is None
    # None, never the document's own id — a tip that names itself reads as a
    # redirect and sends a reader in a circle.
    assert body["lineage_tip"] is None


@pytest.mark.asyncio
async def test_multi_hop_chain_reports_next_hop_and_tip_separately(client, stored):
    a = await stored(title="v1")
    b = await stored(title="v2")
    c = await stored(title="v3")
    client.post("/supersede-link", json={"old_id": a, "new_id": b, "actor": "memo"})
    client.post("/supersede-link", json={"old_id": b, "new_id": c, "actor": "memo"})

    body = client.get(f"/documents/{a}").json()
    # ⭐ The distinction that matters: following one hop at a time through a
    # three-link chain is how a caller ends up quoting the MIDDLE version.
    assert body["superseded_by"] == b
    assert body["lineage_tip"] == c
