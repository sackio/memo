"""A CONTINUATION is not a REPLACEMENT. [migration 014, 2026-10-24]

Ben, Slack #memo 2026-10-24 ("go"), following the 2026-10-23 approval to clear
52 wrongly-expired memos. That clearing was the stopgap; this is the fix.

⛔ THE DEFECT THIS CLOSES. A memo that filled up is carried on by a second memo,
and the only relation two EXISTING documents could record was supersession —
which sets `valid_until` and removes the parent from every default read. So
recording a TRUE fact about 52 still-true memos is what made them unreachable,
and the relationship itself survived only in prose a regex had to guess at.
Found because `atc` pushed back on a pin finding and was right.
"""
import pytest
from fastapi.testclient import TestClient

from memo.main import app


@pytest.fixture()
def client():
    return TestClient(app)


def _make(client, title="t"):
    r = client.post("/documents", json={"content": "x" * 60, "title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _link(client, old, new, kind):
    return client.post("/supersede-link", json={
        "old_id": old, "new_id": new, "actor": "test", "kind": kind,
        "reason": "part 2"})


@pytest.mark.asyncio
async def test_a_a_continuation_leaves_the_parent_live(client, embedding):
    """THE WHOLE POINT. A supersede retires the parent; a continuation must not."""
    a, b = _make(client, "PART 1"), _make(client, "PART 2")
    assert _link(client, a, b, "continuation").status_code == 200

    parent = client.get(f"/documents/{a}").json()
    assert parent["valid_until"] is None, "a continuation retired its parent"
    assert parent["superseded_by"] is None, \
        "a continuation was reported as a replacement — the 52-memo bug"
    assert parent["lineage_tip"] is None
    assert parent["continued_by"] == [b]
    assert client.get(f"/documents/{b}").json()["continues"] == [a]


@pytest.mark.asyncio
async def test_b_supersede_is_unchanged_by_all_this(client, embedding):
    """⚠️ The default had to stay destructive: every caller written before 014
    meant supersession, and changing what their unchanged request does would be
    the worse failure."""
    a, b = _make(client), _make(client)
    assert client.post("/supersede-link", json={
        "old_id": a, "new_id": b, "actor": "test"}).status_code == 200   # no kind
    parent = client.get(f"/documents/{a}").json()
    assert parent["valid_until"] is not None
    assert parent["superseded_by"] == b
    assert parent["continued_by"] == []


@pytest.mark.asyncio
async def test_c_a_continued_parent_stays_in_the_default_listing(client, embedding):
    """The 52 memos were invisible to `GET /documents`, which is the mandated way
    to make an existence claim about this corpus."""
    a, b = _make(client, "PART 1 live"), _make(client, "PART 2")
    _link(client, a, b, "continuation")
    ids = {d["id"] for d in client.get("/documents", params={"limit": 20000}).json()}
    assert a in ids, "a continued parent fell out of the default listing"


@pytest.mark.asyncio
async def test_d_one_parent_many_continuations(client, embedding):
    """⭐ The case the old edge table explicitly could not express: `c2ae186a` was
    continued by TWO memos 5ms apart and neither was "the" successor. A list
    needs no winner."""
    a, b, c = _make(client, "PART 1"), _make(client, "PART 2"), _make(client, "PART 2b")
    assert _link(client, a, b, "continuation").status_code == 200
    assert _link(client, a, c, "continuation").status_code == 200
    parent = client.get(f"/documents/{a}").json()
    assert sorted(parent["continued_by"]) == sorted([b, c])
    assert parent["superseded_by"] is None


@pytest.mark.asyncio
async def test_e_the_bulk_route_fills_it_too(client, embedding):
    """⛔ A lineage field present and always empty turns every bulk reader into a
    confident wrong answer. That shipped here once, for `superseded_by`."""
    a, b = _make(client, "PART 1"), _make(client, "PART 2")
    _link(client, a, b, "continuation")
    rows = {d["id"]: d for d in client.get("/documents", params={"limit": 20000}).json()}
    assert rows[a]["continued_by"] == [b], "bulk route reported no continuation"
    assert rows[b]["continues"] == [a]


@pytest.mark.asyncio
async def test_f_an_unknown_kind_is_refused_not_defaulted(client, embedding):
    """⛔ Defaulting a typo to 'supersede' would RETIRE the parent in response to
    a caller trying to say the opposite."""
    a, b = _make(client), _make(client)
    r = _link(client, a, b, "contination")        # typo, deliberately
    assert r.status_code == 409, r.text
    assert "unknown kind" in r.json()["detail"]
    assert client.get(f"/documents/{a}").json()["valid_until"] is None


@pytest.mark.asyncio
async def test_g_one_relation_per_pair(client, embedding):
    a, b = _make(client), _make(client)
    assert _link(client, a, b, "continuation").status_code == 200
    r = client.post("/supersede-link", json={"old_id": a, "new_id": b, "actor": "t"})
    assert r.status_code == 409
    assert "kind=continuation" in r.json()["detail"], \
        "the refusal must say which relation already exists"


@pytest.mark.asyncio
async def test_h_cannot_continue_from_a_retired_memo(client, embedding):
    a, b, c = _make(client), _make(client), _make(client)
    client.post("/supersede-link", json={"old_id": a, "new_id": b, "actor": "t"})
    r = _link(client, a, c, "continuation")
    assert r.status_code == 409
    assert "already superseded" in r.json()["detail"]
