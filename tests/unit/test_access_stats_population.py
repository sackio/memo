"""`/admin/access-stats` must count one population, and must say where it capped.

[memo, 2026-10-20] `coverage_pct` read **141.7%** on the live corpus: 16,442
over 11,599. Every one of the 4,843 excess rows was a `doc_access` row whose
`doc_id` is in `deletion_log` — read history for a doc that no longer exists,
retained deliberately. The numerator counted the table, the denominator counted
live documents, and nothing joined them.

⭐⭐ A RATIO IS A CLAIM ABOUT ONE POPULATION, AND NOTHING CHECKS THAT BOTH SIDES
CAME FROM IT. This one survived because it broke 100% and so read as an obvious
glitch nobody had to act on. Had the deleted fraction been 5% instead of 42%,
the same defect would have produced a believable 95% and been cited.

The second defect is the reverse shape: `hot_last_30d` carried a hardcoded
`LIMIT 20` while the route advertised `limit`, so a caller ranking docs by read
count got 20 rows and no indication there were more. The cap is fine; its
invisibility was not. It is now `limit`, and the applied value comes back as
`hot_limit`.
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
    async def _mk(content="a durable fact", title="t"):
        vec = [0.1] * db.settings.embedding_dimensions
        return await db.store(db_path=None, content=content, title=title,
                              tags=[], metadata={}, embedding=vec)
    return _mk


@pytest.mark.asyncio
async def test_coverage_cannot_exceed_100_when_read_docs_are_deleted(client, stored):
    """The exact live shape: read a doc, delete it, and the ratio must not blow past 100."""
    keep = await stored(title="stays")
    doomed = [await stored(title=f"goes {i}") for i in range(3)]

    for doc_id in [keep] + doomed:
        assert client.get(f"/documents/{doc_id}").status_code == 200
    for doc_id in doomed:
        assert client.delete(f"/documents/{doc_id}").status_code in (200, 204)

    s = client.get("/admin/access-stats").json()
    assert s["total_docs"] == 1
    assert s["total_with_any_get"] == 1, "only the surviving doc may count"
    assert s["coverage_pct"] <= 100.0
    assert s["reads_on_deleted_docs"] == 3, "the deleted reads are reported, not discarded"


@pytest.mark.asyncio
async def test_hot_list_honours_limit_and_reports_the_cap_it_applied(client, stored):
    docs = [await stored(title=f"doc {i}") for i in range(5)]
    for doc_id in docs:
        client.get(f"/documents/{doc_id}")

    s = client.get("/admin/access-stats", params={"limit": 2}).json()
    assert len(s["hot_last_30d"]) == 2
    assert s["hot_limit"] == 2, "a truncation the caller cannot see is the defect"

    s = client.get("/admin/access-stats", params={"limit": 50}).json()
    assert len(s["hot_last_30d"]) == 5, "no hidden 20-row ceiling below the asked-for limit"
    assert s["hot_limit"] == 50


@pytest.mark.asyncio
async def test_deleted_docs_never_appear_in_the_hot_list(client, stored):
    """The hot list already joined correctly; assert it, so the coverage fix cannot
    be 'fixed' later by dropping the join on this side instead."""
    doc_id = await stored(title="read then deleted")
    client.get(f"/documents/{doc_id}")
    client.delete(f"/documents/{doc_id}")

    s = client.get("/admin/access-stats").json()
    assert all(r["id"] != doc_id for r in s["hot_last_30d"])
