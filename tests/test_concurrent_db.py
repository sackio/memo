"""Regression tests for concurrent database access.

2026-07-30: `memo_context` with a `queries` array crashed nondeterministically
across the fleet — `bad parameter or other API misuse` (SQLITE_MISUSE), `tuple
index out of range`, and `json.loads(None)`. Three crash sites, one cause: a
single cached sqlite3 connection was shared by every thread `asyncio.to_thread`
handed work to, with `check_same_thread=False` silencing the guard.

Single-angle and `queries: []` calls always worked because they never fan out.
Anything with a second angle raced.

These tests REPEAT deliberately. The failure is a race: one green call proves
nothing, which is precisely how it survived until three sessions hit it at once.
"""

import asyncio

import pytest

from memo import db
from memo.config import settings

ROUNDS = 25
CONCURRENCY = 8


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = str(tmp_path / "concurrent.db")
    monkeypatch.setattr(db, "_resolve_path", lambda _db_path: path)
    monkeypatch.setattr(db, "global_path", lambda: path)
    return path


def _embedding(seed: float) -> list[float]:
    # Never zero-magnitude: cosine distance against a zero vector is undefined
    # and vec0 returns NULL for it.
    return [seed + 0.01] * settings.embedding_dimensions


def _search_kwargs():
    return dict(
        limit=5, min_score=None, tags=[], after=None, before=None,
        min_tokens=None, max_tokens=None,
    )


async def _seed(count: int = 20) -> None:
    for i in range(count):
        await db.store(
            db_path=None, content=f"memo number {i}", title=f"title {i}",
            tags=["seeded"], metadata={}, embedding=_embedding(i / 100),
        )


def test_concurrent_searches_do_not_race(temp_db):
    """The bug: N concurrent searches shared one connection and corrupted each other."""

    async def go():
        await _seed()
        for _ in range(ROUNDS):
            results = await asyncio.gather(*[
                db.search(db_path=None, embedding=_embedding(0.05), **_search_kwargs())
                for _ in range(CONCURRENCY)
            ])
            assert all(len(r) == 5 for r in results), "a racing search returned a short result set"
            assert all(isinstance(r[0]["document"]["tags"], list) for r in results)

    asyncio.run(go())


def test_concurrent_mixed_reads_and_writes_do_not_race(temp_db):
    """Writes share the connection path too — a store racing a search is the same defect."""

    async def go():
        await _seed(5)
        for round_num in range(ROUNDS):
            await asyncio.gather(
                db.store(
                    db_path=None, content=f"written during round {round_num}",
                    title=f"w{round_num}", tags=[], metadata={},
                    embedding=_embedding(0.5),
                ),
                db.search(db_path=None, embedding=_embedding(0.05), **_search_kwargs()),
                db.list_docs(
                    db_path=None, tags=[], limit=5, after=None, before=None,
                    min_tokens=None, max_tokens=None,
                ),
                db.search(db_path=None, embedding=_embedding(0.9), **_search_kwargs()),
            )

    asyncio.run(go())


def test_multi_angle_fanout_is_the_reported_repro(temp_db):
    """The shape callers actually hit: one query plus extra angles, run in parallel."""

    async def go():
        await _seed()
        for _ in range(ROUNDS):
            angles = [_embedding(s) for s in (0.05, 0.2, 0.4, 0.6)]
            results = await asyncio.gather(*[
                db.search(db_path=None, embedding=e, **_search_kwargs()) for e in angles
            ])
            assert all(len(r) == 5 for r in results)

    asyncio.run(go())


def test_total_fanout_failure_raises_instead_of_returning_empty(temp_db, monkeypatch):
    """An all-DBs-failed fan-out must not look like an empty corpus."""

    def _boom(*_args, **_kwargs):
        raise sqlite_misuse()

    def sqlite_misuse():
        import sqlite3
        return sqlite3.ProgrammingError("bad parameter or other API misuse")

    monkeypatch.setattr(db, "_sync_search", _boom)

    async def go():
        with pytest.raises(Exception, match="bad parameter"):
            await db.search_multi(
                [temp_db], embedding=_embedding(0.1), **_search_kwargs()
            )

    asyncio.run(go())


def test_partial_fanout_failure_still_returns_what_worked(temp_db, monkeypatch):
    """One DB failing is survivable — it must not take the healthy results down."""
    calls = {"n": 0}
    real = db._sync_search

    def _flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            import sqlite3
            raise sqlite3.ProgrammingError("bad parameter or other API misuse")
        return real(*args, **kwargs)

    async def go():
        await _seed()
        monkeypatch.setattr(db, "_sync_search", _flaky)
        results = await db.search_multi(
            [temp_db, temp_db], embedding=_embedding(0.1), **_search_kwargs()
        )
        assert results, "the healthy DB's results must survive its sibling's failure"

    asyncio.run(go())
