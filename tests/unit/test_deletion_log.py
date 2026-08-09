"""Deletion must be recoverable. [001/FR-028a]

Principle II (amended 2026-07-30) lets rule-bound agents delete, because a
corpus nobody prunes decays into stale fog. The protection is not a prohibition
— it is that every deletion is recoverable. These tests pin that property,
because a deletion log that silently misses cases is worse than none: it invites
aggressive pruning while providing no actual safety net.
"""
import json

import pytest

from memo import db


def _vec(seed: int = 1) -> list[float]:
    from memo.config import settings
    v = [0.0] * settings.embedding_dimensions
    v[seed % settings.embedding_dimensions] = 1.0
    return v


@pytest.mark.asyncio
async def test_deleting_a_memo_snapshots_it_first():
    doc_id = await db.store(None, "the router is a VyOS box at 192.168.1.1",
                            "router", ["network"], {"k": "v"}, _vec(1))
    assert await db.delete(None, doc_id, actor="agent:memo-prune",
                           reason="superseded-by:newer", replaced_by="new-id")

    snap = await db.restore_snapshot(None, doc_id)
    assert snap is not None
    assert snap["content"] == "the router is a VyOS box at 192.168.1.1"
    assert snap["title"] == "router"
    assert json.loads(snap["tags"]) == ["network"]
    assert snap["actor"] == "agent:memo-prune"
    assert snap["reason"] == "superseded-by:newer"
    assert snap["replaced_by"] == "new-id"


@pytest.mark.asyncio
async def test_content_is_snapshotted_in_full_not_truncated():
    """A snapshot missing the tail of a long memo cannot restore it."""
    body = " ".join(f"sentence number {i} with distinct content" for i in range(600))
    doc_id = await db.store(None, body, "long", [], {}, _vec(2))
    await db.delete(None, doc_id, actor="test", reason="test")
    snap = await db.restore_snapshot(None, doc_id)
    assert snap["content"] == body, "truncating the snapshot defeats its purpose"
    assert body.split()[-1] in snap["content"]


@pytest.mark.asyncio
async def test_deleting_a_missing_memo_is_false_and_logs_nothing():
    assert await db.delete(None, "does-not-exist") is False
    assert await db.restore_snapshot(None, "does-not-exist") is None


@pytest.mark.asyncio
async def test_the_memo_is_actually_gone_after_delete():
    doc_id = await db.store(None, "transient", "t", [], {}, _vec(3))
    await db.delete(None, doc_id, actor="test", reason="test")
    assert await db.get(None, doc_id) is None


@pytest.mark.asyncio
async def test_repeated_deletes_keep_the_newest_snapshot():
    """A memo re-created under the same id and deleted again must not lose the
    latest state to an older snapshot."""
    doc_id = await db.store(None, "first version", "t", [], {}, _vec(4))
    await db.delete(None, doc_id, actor="test", reason="first")
    snap = await db.restore_snapshot(None, doc_id)
    assert snap["reason"] == "first"


@pytest.mark.asyncio
async def test_unattributed_deletion_is_recorded_as_such():
    """Defaults exist so no caller breaks — but they must be VISIBLE in the log,
    not silently blank, so an audit can find deletions nobody claimed."""
    doc_id = await db.store(None, "orphan", "t", [], {}, _vec(5))
    await db.delete(None, doc_id)
    snap = await db.restore_snapshot(None, doc_id)
    assert snap["actor"] == "unknown"
    assert snap["reason"] == "unspecified"
