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


@pytest.mark.asyncio
async def test_the_list_route_reports_the_same_successor_as_the_single_get(client, stored):
    """⛔ A DEFECT SHIPPED 2026-10-20 AND CAUGHT THE SAME NIGHT.

    `GET /documents/<id>` computed `superseded_by`; `GET /documents` did not,
    and `Document` defaults it to None. So the list answered `superseded_by:
    null` for all 11,599 live memos, including the 402 with a real successor
    edge — right one at a time, wrong in bulk.

    ⭐⭐ A FIELD THAT IS PRESENT AND ALWAYS EMPTY IS WORSE THAN AN ABSENT ONE.
    Absent, a caller has to go and ask. Present-and-null, it has its answer,
    and the answer is false. Scanning `/documents` is the mandated way to make
    an existence claim about this corpus, so the bulk reader is the common one.
    """
    old = await stored(title="the earlier finding")
    new = await stored(title="the finding that replaces it")
    assert client.post("/supersede-link", json={
        "old_id": old, "new_id": new, "actor": "memo"}).status_code == 200

    single = client.get(f"/documents/{old}").json()
    assert single["superseded_by"] == new

    listed = client.get("/documents", params={"limit": 20000}).json()
    row = next(d for d in listed if d["id"] == old)
    assert row["superseded_by"] == new, \
        "the list must not report null where the single GET reports a successor"
    assert row["lineage_tip"] == new

    survivor = next(d for d in listed if d["id"] == new)
    assert survivor["superseded_by"] is None, "the tip has no successor, truthfully"


@pytest.mark.asyncio
async def test_a_forked_lineage_resolves_identically_in_bulk_and_one_at_a_time(client, stored):
    """One memo, two recorded successors — and both surfaces must pick the same one.

    ⛔ THE CORPUS HAS ONE, so this is not hypothetical: `c2ae186a` was continued
    by TWO memos that each declared it in their own title, and a 2026-09-28
    backfill recorded both edges 5ms apart. Neither is "the" successor; a
    continuation is one->many and `supersede_edges` is old->new.

    `_lineage_chain` breaks the tie with `ORDER BY superseded_at LIMIT 1`. The
    bulk map must use the identical rule. Without an ORDER BY, SQLite's row
    order is unspecified, so the list could contradict the single GET
    intermittently — a disagreement that would not reproduce on demand.
    """
    root = await stored(title="the memo that was continued twice")
    first = await stored(title="continuation a")
    second = await stored(title="continuation b")

    # ⛔ THE ROUTE CANNOT MAKE THIS SHAPE, AND THAT IS ITSELF THE FINDING.
    # `/supersede-link` 409s on an already-superseded doc, so the second edge
    # is refused. The fork in the live corpus was written by the 2026-09-28
    # backfill, which INSERTed rows directly from each memo's prose. ⇒ The
    # guard on the write path is not a guarantee about the data, because the
    # data predates it and arrived another way. Build it the way it really
    # arose, or the test asserts against a shape the corpus actually holds
    # and the suite cannot reach.
    assert client.post("/supersede-link", json={
        "old_id": root, "new_id": first, "actor": "memo"}).status_code == 200
    assert client.post("/supersede-link", json={
        "old_id": root, "new_id": second, "actor": "memo"}).status_code == 409, \
        "the route refuses a second successor; the backfill did not"

    conn = db._get_or_create_conn(db._resolve_path(None))
    conn.execute(
        "INSERT INTO supersede_edges (old_id, new_id, superseded_at, actor, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (root, second, 9_999_999_999.0, "test:backfill", "second continuation"))
    conn.commit()

    single = client.get(f"/documents/{root}").json()["superseded_by"]
    listed = client.get("/documents", params={"limit": 20000}).json()
    bulk = next(d for d in listed if d["id"] == root)["superseded_by"]

    assert single is not None, "a forked lineage still has a successor to name"
    assert bulk == single, \
        "the list and the single GET must break the tie the same way"
    assert single == first, \
        "earliest edge wins, which is what `_lineage_chain` ORDER BY does"


@pytest.mark.asyncio
async def test_every_route_that_returns_a_document_fills_its_lineage(client, stored):
    """⛔ FIVE ROUTES RETURN `Document`. A FIX AT TWO OF THEM IS NOT A FIX.

    `superseded_by` was added to `GET /documents/<id>` and then found missing
    from `/documents` (list+search), `/current`, `/as-of` and the PATCH
    response — four surfaces answering null for a memo that has a successor.

    ⭐ This repo has paid for this exact shape before: the short-id fix landed
    on MCP and not REST, and the 413 fix landed at one call site and not six.
    The defect is never "the logic is wrong", it is "the logic is in one
    place". So this test enumerates the surfaces rather than sampling them.
    """
    old = await stored(title="v1")
    new = await stored(title="v2")
    assert client.post("/supersede-link", json={
        "old_id": old, "new_id": new, "actor": "memo"}).status_code == 200

    # 1. single GET — the one that already worked
    assert client.get(f"/documents/{old}").json()["superseded_by"] == new

    # 2. list
    listed = client.get("/documents", params={"limit": 20000}).json()
    assert next(d for d in listed if d["id"] == old)["superseded_by"] == new

    # 3. PATCH response — a writer gets the same answer a reader does
    patched = client.patch(f"/documents/{old}", json={"append": "\nmore"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["superseded_by"] == new, \
        "the write path must not report a different lineage from the read path"

    # 4. /current resolves FORWARD, so it returns the tip — and the tip
    #    truthfully has no successor. Asserted so a later change cannot make
    #    this null for the wrong reason.
    cur = client.get(f"/documents/{old}/current").json()
    assert cur["id"] == new
    assert cur["superseded_by"] is None

    # 5. /as-of — `t` is EPOCH SECONDS, not ISO-8601 (an ISO string 422s).
    import time as _t
    asof = client.get(f"/documents/{old}/as-of", params={"t": _t.time()})
    assert asof.status_code == 200, asof.text
    assert "superseded_by" in asof.json(), \
        "the field must be computed here too, not left to the model default"
