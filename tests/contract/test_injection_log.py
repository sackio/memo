"""The compaction ledger. [001/FR-044]

An independent observation of an event whose other participant cannot report
its own absence. Session state across compaction is ATC's, and ATC's delivery
is best-effort — hook fires, agent CHOOSES to dispatch, subagent writes a good
beacon, beacon gets acked. Any link can silently not happen, and the mechanism
that would report it is the one that failed.

memo's post-compact hook fires unconditionally, so memo can count. These tests
pin the properties that make the count trustworthy.
"""
import pytest

from memo import db, main


@pytest.mark.asyncio
async def test_post_compact_records_the_compaction(embedding):
    before = len(await db.injection_log())
    await main.hook_post_compact({"session_id": "sess-alpha"})
    rows = await db.injection_log()
    assert len(rows) == before + 1
    assert rows[0]["session_id"] == "sess-alpha"
    assert rows[0]["fire_point"] == "post-compact"


@pytest.mark.asyncio
async def test_a_failed_injection_is_still_recorded(monkeypatch):
    """memo cannot report its own ABSENCE, but it must report its own
    presence-and-failure — that is the half it can actually observe."""
    from memo.injection import set as injection_set

    async def boom(**kwargs):
        raise RuntimeError("injection exploded")

    monkeypatch.setattr(injection_set, "build", boom)
    r = await main.hook_post_compact({"session_id": "sess-broken"})
    assert r["degraded"] is True

    rows = await db.injection_log()
    mine = [x for x in rows if x["session_id"] == "sess-broken"]
    assert mine, "a failed compaction must still appear in the ledger"
    assert mine[0]["injected_ok"] == 0


@pytest.mark.asyncio
async def test_a_ledger_failure_never_breaks_the_hook(monkeypatch):
    """The observer must not be able to break the thing it observes.

    This rides the session-start critical path. A ledger write failing must not
    stop a session from receiving its standing rules — an observer that can
    take down its subject is worse than no observer.
    """
    async def boom(*a, **k):
        raise RuntimeError("ledger table on fire")

    monkeypatch.setattr(db, "record_injection", boom)
    r = await main.hook_post_compact({"session_id": "sess-resilient"})
    assert "additionalContext" in r, "the hook must still answer"


@pytest.mark.asyncio
async def test_session_start_is_not_counted_as_a_compaction():
    """Only post-compact counts. Counting ordinary session starts would inflate
    memo's side of the reconciliation and manufacture phantom ATC failures."""
    before = len(await db.injection_log())
    await main.hook_session_start({"session_id": "sess-fresh"})
    assert len(await db.injection_log()) == before


@pytest.mark.asyncio
async def test_ledger_is_readable_for_reconciliation():
    await main.hook_post_compact({"session_id": "sess-a"})
    await main.hook_post_compact({"session_id": "sess-b"})
    out = await main.get_injection_log(since=None, limit=500)
    assert out["count"] >= 2
    ids = {r["session_id"] for r in out["injections"]}
    assert {"sess-a", "sess-b"} <= ids
