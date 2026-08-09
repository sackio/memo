"""Concurrent writers must queue, not fail. [002/FR-110]

Measured 2026-08-01. The corpus-wide passage index was the first job to write
from more than one task at a time, and it lost 127 of its first 2,400 memos
(5%, rising as the WAL grew) to `sqlite3.OperationalError: database is locked`.

`PRAGMA busy_timeout=5000` was already set, which is why this looked impossible.
The gap: every write opened a **deferred** `BEGIN`, which takes a READ lock and
only attempts the upgrade to a write lock at the first mutating statement. In WAL
mode SQLite cannot safely block on that upgrade — the transaction already holds a
read snapshot that the competing writer would invalidate — so it returns
SQLITE_BUSY *immediately* and busy_timeout is never consulted. The timeout was
set and simply did not cover the one case that needed it.

`BEGIN IMMEDIATE` takes the write lock up front, where busy_timeout DOES apply,
so a second writer waits its turn instead of failing.

**Which of these tests actually catches the regression, stated honestly.** Only
the last one. Reverting to a deferred `BEGIN` and re-running leaves the three
concurrency tests GREEN — the test container's database is a handful of rows on
a throwaway /tmp file, and the write window is too short for two tasks to
collide. Reproducing the real contention would mean reproducing the real corpus,
which is the thing CI deliberately does not have.

So the concurrency tests are here as necessary-but-not-sufficient cover: they
assert the write path works under concurrent use and would catch a deadlock or a
lost write. The invariant that fails loudly on the actual defect is the source
scan, `test_no_write_path_uses_a_deferred_begin`, which checks the transaction
mode as text. That is a weaker kind of test and it is the one that works — worth
saying plainly rather than leaving a reader to assume the concurrency tests are
guarding something they are not.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from memo import db, passages


def _vec(n: int = 8):
    from memo.config import settings
    return [0.0] * settings.embedding_dimensions


def _passages(n: int):
    return [passages.Passage(index=i, text=f"chunk {i}", token_start=i * 10,
                             token_end=(i + 1) * 10) for i in range(n)]


@pytest.mark.asyncio
async def test_concurrent_passage_writes_all_succeed(embedding):
    """Twenty writers against one database, no losses.

    Against a deferred BEGIN this raises `database is locked` for some subset;
    the failure is load-dependent, so the assertion is on the count of survivors
    rather than on any particular writer.
    """
    doc_ids = []
    for i in range(20):
        doc_ids.append(await db.store(None, f"body {i}", None, [], {}, embedding))

    async def write(doc_id: str):
        ps = _passages(6)
        return await asyncio.to_thread(
            passages._sync_replace, db.global_path(), doc_id, ps,
            [_vec() for _ in ps], model="test-model", route="test")

    results = await asyncio.gather(*(write(d) for d in doc_ids),
                                   return_exceptions=True)

    locked = [r for r in results if isinstance(r, sqlite3.OperationalError)]
    other = [r for r in results if isinstance(r, Exception)
             and not isinstance(r, sqlite3.OperationalError)]
    assert not other, f"unexpected failures: {other[:3]}"
    assert not locked, f"{len(locked)}/20 writers hit 'database is locked'"
    assert all(r == 6 for r in results)


@pytest.mark.asyncio
async def test_every_concurrently_written_document_is_readable(embedding):
    """A write that reported success must actually be there — the count passing
    is not enough if a rolled-back writer still returned a number."""
    doc_ids = [await db.store(None, f"body {i}", None, [], {}, embedding)
               for i in range(12)]

    async def write(doc_id: str):
        ps = _passages(4)
        return await asyncio.to_thread(
            passages._sync_replace, db.global_path(), doc_id, ps,
            [_vec() for _ in ps], model="test-model", route="test")

    await asyncio.gather(*(write(d) for d in doc_ids))

    for doc_id in doc_ids:
        got = await passages.get_passages(doc_id)
        assert len(got) == 4, f"{doc_id[:8]} has {len(got)} passages, expected 4"


@pytest.mark.asyncio
async def test_concurrent_delete_and_write_do_not_deadlock(embedding):
    """The reaper deletes while an indexer writes. Both take the write lock, so
    both must queue rather than one dying."""
    doc_ids = [await db.store(None, f"body {i}", None, [], {}, embedding)
               for i in range(10)]
    for doc_id in doc_ids:
        ps = _passages(3)
        await asyncio.to_thread(passages._sync_replace, db.global_path(), doc_id,
                                ps, [_vec() for _ in ps], model="m", route="r")

    async def rewrite(doc_id):
        ps = _passages(5)
        return await asyncio.to_thread(passages._sync_replace, db.global_path(),
                                       doc_id, ps, [_vec() for _ in ps],
                                       model="m", route="r")

    async def wipe(doc_id):
        return await asyncio.to_thread(passages._sync_delete, db.global_path(), doc_id)

    tasks = []
    for i, doc_id in enumerate(doc_ids):
        tasks.append(wipe(doc_id) if i % 2 else rewrite(doc_id))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    failed = [r for r in results if isinstance(r, Exception)]
    assert not failed, f"{len(failed)} operation(s) failed: {failed[:3]}"


@pytest.mark.asyncio
async def test_index_document_serialises_its_writes(embedding):
    """The actual fix, and unlike the tests above this one FAILS without it.

    `BEGIN IMMEDIATE` alone did not help — it moved the failure to the point the
    lock is taken (5% → 17% on the real corpus) without reducing it, because
    SQLite has one writer and ten tasks were asking for it. The embed is what
    gets to be concurrent; the write is serialised through `_write_lock`.

    Asserted by observation rather than by timing: the write body records how
    many writers are inside it at once, and the answer has to be 1.
    """
    doc_ids = [await db.store(None, f"body {i}", None, [], {}, embedding)
               for i in range(15)]

    inside = {"now": 0, "max": 0}
    real = passages._sync_replace

    def counting(*args, **kwargs):
        inside["now"] += 1
        inside["max"] = max(inside["max"], inside["now"])
        try:
            return real(*args, **kwargs)
        finally:
            inside["now"] -= 1

    passages._sync_replace = counting
    try:
        async def embed_batch(texts):
            await asyncio.sleep(0)  # yield, so tasks genuinely interleave
            return [_vec() for _ in texts]

        await asyncio.gather(*(
            passages.index_document(d, "para one.\n\npara two.\n\npara three.",
                                    embed_batch=embed_batch)
            for d in doc_ids))
    finally:
        passages._sync_replace = real

    assert inside["max"] == 1, (
        f"{inside['max']} concurrent writers reached the database — the write "
        "must be serialised; SQLite has exactly one writer")


def test_no_write_path_uses_a_deferred_begin():
    """The rule, asserted directly on the source.

    The three call sites were found and fixed by hand; nothing stops a fourth
    from being added with a plain `BEGIN`, and the resulting loss is silent,
    load-dependent, and only shows up under real concurrency — which is to say
    in production and not here. So the invariant is checked as text.
    """
    import re
    from pathlib import Path

    root = Path(passages.__file__).resolve().parent
    offenders = []
    for path in sorted(root.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r'''execute\(\s*["']BEGIN["']\s*\)''', line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "deferred BEGIN in a write path — use BEGIN IMMEDIATE so busy_timeout "
        f"applies: {offenders}")
