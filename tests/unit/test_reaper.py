"""TTL reaper — expires_at sweep behavior. [001/FR-007]

Covers what gets swept, what must survive, that embeddings go with the row, and
the background task's lifecycle/resilience. `now` is injected everywhere rather
than slept for, so the suite stays fast and deterministic.
"""
import asyncio

import pytest
import pytest_asyncio

from memo import db, reaper
from memo.config import settings

NOW = 1_700_000_000.0


# MUST be pytest_asyncio.fixture, not pytest.fixture: pytest-asyncio runs in
# strict mode here, and a plain @pytest.fixture async generator is never
# awaited — it silently becomes a no-op, so the cleanup below would not run and
# reaper tasks would leak across tests. (Caught 2026-07-29 via
# PytestRemovedIn9Warning; pytest 9 will make it a hard error.)
@pytest_asyncio.fixture(autouse=True)
async def _reset_reaper_task():
    """reaper._task is module-global — a leaked task would poison later tests."""
    yield
    await reaper.stop()


async def store_with_expiry(embedding, expires_at, content="body"):
    """Store a memo then stamp expires_at (db.store has no TTL parameter)."""
    doc_id = await db.store(None, content, None, [], {}, embedding)
    conn = db._get_or_create_conn(db.global_path())
    conn.execute("UPDATE documents SET expires_at = ? WHERE id = ?", (expires_at, doc_id))
    conn.commit()
    return doc_id


def doc_exists(doc_id):
    conn = db._get_or_create_conn(db.global_path())
    row = conn.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return row is not None


def embedding_exists(doc_id):
    conn = db._get_or_create_conn(db.global_path())
    row = conn.execute(
        "SELECT 1 FROM document_embeddings WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    return row is not None


# --- What gets swept ---

@pytest.mark.asyncio
async def test_expired_memo_is_reaped(embedding):
    doc_id = await store_with_expiry(embedding, NOW - 1)
    reaped = await reaper.sweep_once(now=NOW)
    assert reaped == [doc_id]
    assert not doc_exists(doc_id)


@pytest.mark.asyncio
async def test_unexpired_memo_survives(embedding):
    doc_id = await store_with_expiry(embedding, NOW + 600)
    assert await reaper.sweep_once(now=NOW) == []
    assert doc_exists(doc_id)


@pytest.mark.asyncio
async def test_memo_without_expires_at_survives(embedding):
    """expires_at IS NULL is the common case — it must never be swept."""
    doc_id = await db.store(None, "permanent", None, [], {}, embedding)
    assert await reaper.sweep_once(now=NOW) == []
    assert doc_exists(doc_id)


@pytest.mark.asyncio
async def test_expiry_boundary_is_inclusive(embedding):
    """expires_at == now counts as expired (`expires_at <= cutoff`)."""
    doc_id = await store_with_expiry(embedding, NOW)
    assert await reaper.sweep_once(now=NOW) == [doc_id]


@pytest.mark.asyncio
async def test_sweep_reaps_only_the_expired_subset(embedding):
    expired = [await store_with_expiry(embedding, NOW - 10, f"old-{i}") for i in range(3)]
    alive = [await store_with_expiry(embedding, NOW + 10, f"new-{i}") for i in range(2)]
    permanent = await db.store(None, "permanent", None, [], {}, embedding)

    reaped = await reaper.sweep_once(now=NOW)

    assert sorted(reaped) == sorted(expired)
    assert all(not doc_exists(d) for d in expired)
    assert all(doc_exists(d) for d in alive)
    assert doc_exists(permanent)


@pytest.mark.asyncio
async def test_embedding_is_deleted_with_the_row(embedding):
    """Leaving the vector behind would keep reaped content semantically searchable."""
    doc_id = await store_with_expiry(embedding, NOW - 1)
    assert embedding_exists(doc_id)
    await reaper.sweep_once(now=NOW)
    assert not embedding_exists(doc_id)


@pytest.mark.asyncio
async def test_empty_sweep_is_a_noop(embedding):
    assert await reaper.sweep_once(now=NOW) == []


@pytest.mark.asyncio
async def test_sweep_is_idempotent(embedding):
    doc_id = await store_with_expiry(embedding, NOW - 1)
    assert await reaper.sweep_once(now=NOW) == [doc_id]
    assert await reaper.sweep_once(now=NOW) == []


@pytest.mark.asyncio
async def test_default_now_uses_wall_clock(embedding):
    """Omitting `now` must sweep against the real clock, not a zero default."""
    doc_id = await store_with_expiry(embedding, 1.0)  # long past
    assert await reaper.sweep_once() == [doc_id]


# --- Task lifecycle ---

@pytest.mark.asyncio
async def test_start_returns_task_and_stop_cancels_it():
    task = reaper.start()
    assert task is not None
    assert not task.done()
    await reaper.stop()
    assert task.cancelled() or task.done()
    assert reaper._task is None


@pytest.mark.asyncio
async def test_start_is_idempotent():
    first = reaper.start()
    second = reaper.start()
    assert first is second


@pytest.mark.asyncio
async def test_start_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "memo_reaper_enabled", False)
    assert reaper.start() is None


@pytest.mark.asyncio
async def test_stop_without_start_is_safe():
    await reaper.stop()  # must not raise


@pytest.mark.asyncio
async def test_loop_sweeps_on_interval(monkeypatch, embedding):
    """Drive the real loop briefly with a tiny interval."""
    monkeypatch.setattr(settings, "memo_reaper_interval_seconds", 0.01)
    doc_id = await store_with_expiry(embedding, 1.0)  # already expired
    reaper.start()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if not doc_exists(doc_id):
            break
    assert not doc_exists(doc_id)


@pytest.mark.asyncio
async def test_loop_survives_a_failing_sweep(monkeypatch, embedding):
    """A transient DB error must not kill the task — nothing would restart it,
    and TTLs would silently stop being honored for the process's whole life.
    """
    monkeypatch.setattr(settings, "memo_reaper_interval_seconds", 0.01)
    calls = {"n": 0}
    real = db.reap_expired

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient sqlite failure")
        return await real(*args, **kwargs)

    monkeypatch.setattr(db, "reap_expired", flaky)

    task = reaper.start()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if calls["n"] >= 3:
            break

    assert calls["n"] >= 3, "loop stopped after the failing sweep"
    assert not task.done()
