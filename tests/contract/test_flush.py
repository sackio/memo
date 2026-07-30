"""POST /flush contract. [001/FR-034 001/FR-036]"""
import pytest

from memo import db, flush as flush_mod

SLOTS = {
    "active-threads": "T094 seam authorship; blocked on dedup",
    "open-tasks": "1. finish seam 2. respawn if transcript >5MB",
}


@pytest.mark.asyncio
async def test_flush_creates_one_memo_per_slot(embedding):
    r = await flush_mod.flush(session_id="dojo", flush_generation=1, slots=SLOTS)
    assert set(r["memo_ids"]) == set(SLOTS)
    assert r["flush_generation"] == 1


@pytest.mark.asyncio
async def test_slots_are_stored_as_ephemeral_flush_with_ttl(embedding):
    r = await flush_mod.flush(session_id="dojo", flush_generation=1, slots=SLOTS)
    doc = await db.get_current(None, r["memo_ids"]["open-tasks"])
    assert doc["class"] == "ephemeral-flush"
    assert doc["expires_at"] is not None and doc["expires_at"] > 0


@pytest.mark.asyncio
async def test_reflushing_a_generation_upserts_rather_than_appends(embedding):
    """Appending would bury the corpus in near-duplicate state dumps.

    An active session flushes the same six slots on every compaction; without
    upsert that is a new memo per slot per compaction, all day.
    """
    first = await flush_mod.flush(session_id="dojo", flush_generation=1,
                                  slots={"open-tasks": "v1"})
    second = await flush_mod.flush(session_id="dojo", flush_generation=1,
                                   slots={"open-tasks": "v2"})
    assert first["memo_ids"]["open-tasks"] == second["memo_ids"]["open-tasks"]
    doc = await db.get_current(None, second["memo_ids"]["open-tasks"])
    assert doc["content"] == "v2"

    conn = db._get_or_create_conn(db.global_path())
    n = conn.execute("SELECT COUNT(*) FROM documents WHERE class='ephemeral-flush'").fetchone()[0]
    assert n == 1


@pytest.mark.asyncio
async def test_distinct_generations_are_distinct_memos(embedding):
    a = await flush_mod.flush(session_id="dojo", flush_generation=1,
                              slots={"open-tasks": "gen1"})
    b = await flush_mod.flush(session_id="dojo", flush_generation=2,
                              slots={"open-tasks": "gen2"})
    assert a["memo_ids"]["open-tasks"] != b["memo_ids"]["open-tasks"]


@pytest.mark.asyncio
async def test_distinct_sessions_do_not_collide(embedding):
    a = await flush_mod.flush(session_id="dojo", flush_generation=1,
                              slots={"open-tasks": "dojo work"})
    b = await flush_mod.flush(session_id="memo", flush_generation=1,
                              slots={"open-tasks": "memo work"})
    assert a["memo_ids"]["open-tasks"] != b["memo_ids"]["open-tasks"]


@pytest.mark.asyncio
async def test_empty_slot_values_are_skipped(embedding):
    r = await flush_mod.flush(session_id="dojo", flush_generation=1,
                              slots={"open-tasks": "real", "pending-dms": "   "})
    assert "open-tasks" in r["memo_ids"]
    assert "pending-dms" not in r["memo_ids"]


@pytest.mark.asyncio
async def test_custom_slot_names_allowed(embedding):
    r = await flush_mod.flush(session_id="dojo", flush_generation=1,
                              slots={"my-custom-slot": "anything"})
    assert "my-custom-slot" in r["memo_ids"]


@pytest.mark.asyncio
async def test_missing_session_id_rejected(embedding):
    with pytest.raises(ValueError):
        await flush_mod.flush(session_id="", flush_generation=1, slots=SLOTS)


@pytest.mark.asyncio
async def test_empty_slots_rejected(embedding):
    with pytest.raises(ValueError):
        await flush_mod.flush(session_id="dojo", flush_generation=1, slots={})


@pytest.mark.asyncio
async def test_reaper_sweeps_expired_flush_memos(embedding):
    """FR-007 + R-14: flush content is stale in hours and must self-clean."""
    from memo import reaper
    r = await flush_mod.flush(session_id="dojo", flush_generation=1,
                              slots={"open-tasks": "x"}, expires_at=1.0)
    reaped = await reaper.sweep_once()
    assert r["memo_ids"]["open-tasks"] in reaped
