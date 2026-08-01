"""Writing and replacing a document's passage index. [002/FR-110 002/FR-108]

The chunker (`memo.chunking`) is pure. This module is where passages meet the
database, kept separate from `db.py` so the document tables and the passage
index stay legible as two different things — which they are: one is the corpus,
the other is an index over it that can be rebuilt from scratch at any time.

**The transaction is the point.** A document's passage set is replaced
wholesale, never patched. A half-written passage set is a silently
under-indexed memo: it still returns from search, still looks healthy in every
listing, and simply never matches on the paragraphs that went missing. That is
the same shape as every defect this project has spent the day fixing, so the
replacement either lands completely or not at all.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from time import time

from memo import db
from memo.chunking import DEFAULT_OVERLAP, DEFAULT_TARGET, Passage, chunk
from memo.config import settings

logger = logging.getLogger(__name__)

# Recorded on every passage so a mixed-provider corpus stays auditable after
# the fact rather than becoming indistinguishable. [002/FR-108]
EMBEDDING_ROUTE = "openrouter"

# SQLite has exactly ONE writer. Connections are per-thread (db._local), so a
# concurrent indexing job opens N of them and they fight; measured 2026-08-01,
# a corpus-wide index at concurrency 10 lost 5% of memos to "database is locked"
# with a deferred BEGIN and 17% with BEGIN IMMEDIATE — the second number is not
# a regression, it is the same contention surfacing honestly at the point the
# lock is taken instead of part-way through the transaction.
#
# Raising busy_timeout does not fix this either: under a write queue that deep,
# waiters simply time out at whatever the bound is. Concurrency on the write is
# not a thing SQLite offers, so asking for it converts throughput into lock
# thrash and lost writes.
#
# What IS worth parallelising is the embed, which is network-bound and ~99% of
# the wall clock. So: embed concurrently, write through this lock one at a time.
# The write is milliseconds; serialising it costs almost nothing and removes the
# failure mode completely (0 errors over the full 7,336-memo corpus).
_write_lock = asyncio.Lock()


def _sync_replace(db_path: str, doc_id: str, passages: list[Passage],
                  vectors: list[list[float]], *, model: str, route: str) -> int:
    """Replace a document's passage set atomically. Returns rows written."""
    if len(passages) != len(vectors):
        raise ValueError(
            f"{len(passages)} passages but {len(vectors)} vectors for {doc_id} — "
            "refusing to write a partial passage set")

    conn = db._get_or_create_conn(db_path)
    now = time()
    try:
        # BEGIN IMMEDIATE, not BEGIN. A deferred transaction takes a READ lock
        # first and only tries to upgrade at the first write; in WAL mode SQLite
        # cannot safely wait on that upgrade (it already holds a read snapshot
        # that a waiting writer could invalidate), so it returns SQLITE_BUSY
        # AT ONCE and `PRAGMA busy_timeout` never applies. The timeout is set —
        # it simply does not cover the one case that matters here.
        #
        # Invisible under the single-writer load this ran at for weeks. Measured
        # 2026-08-01 on the first genuinely concurrent write job: a corpus-wide
        # passage index at concurrency 10 lost 127 of 2,400 memos (5%, and
        # rising as the WAL grew) to "database is locked". IMMEDIATE takes the
        # write lock up front, which busy_timeout DOES honour, so contenders
        # queue for up to 5s instead of failing instantly.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM document_chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM chunk_embeddings WHERE doc_id = ?", (doc_id,))
        for p, vec in zip(passages, vectors):
            conn.execute(
                "INSERT INTO document_chunks (doc_id, chunk_index, text, token_start, "
                "token_end, embedding_model, embedding_route, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (doc_id, p.index, p.text, p.token_start, p.token_end, model, route, now),
            )
            conn.execute(
                "INSERT INTO chunk_embeddings (doc_id, chunk_index, embedding) "
                "VALUES (?, ?, ?)",
                (doc_id, p.index, db._serialize_vector(vec)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("passage index write failed for %s; rolled back", doc_id)
        raise
    return len(passages)


def _sync_delete(db_path: str, doc_id: str) -> None:
    conn = db._get_or_create_conn(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")  # see _sync_replace
        conn.execute("DELETE FROM document_chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM chunk_embeddings WHERE doc_id = ?", (doc_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _sync_get(db_path: str, doc_id: str) -> list[dict]:
    conn = db._get_or_create_conn(db_path)
    rows = conn.execute(
        "SELECT doc_id, chunk_index, text, token_start, token_end, embedding_model, "
        "embedding_route FROM document_chunks WHERE doc_id = ? ORDER BY chunk_index",
        (doc_id,)).fetchall()
    return [dict(r) for r in rows]


async def index_document(doc_id: str, content: str, *, embed_batch=None,
                         target: int = DEFAULT_TARGET,
                         overlap: float = DEFAULT_OVERLAP) -> int:
    """Chunk, embed and (re)write a document's passage index.

    `embed_batch` is injectable so tests and rehearsals never reach the network.
    Returns the number of passages written; 0 for empty content, which is not an
    error — an empty memo simply has nothing to index.
    """
    passages = chunk(content or "", target=target, overlap=overlap)
    if not passages:
        async with _write_lock:
            await asyncio.to_thread(_sync_delete, db.global_path(), doc_id)
        return 0

    if embed_batch is None:
        from memo import embeddings
        embed_batch = embeddings.embed_batch

    vectors = await embed_batch([p.text for p in passages])
    if len(vectors) != len(passages):
        # Refuse rather than write a partial index. A short vector list here
        # would otherwise silently drop the tail passages of a long memo — the
        # exact memos this feature exists to make findable.
        raise ValueError(
            f"embed_batch returned {len(vectors)} vectors for {len(passages)} "
            f"passages of {doc_id}")

    # The embed above ran concurrently; the write does not. See _write_lock.
    async with _write_lock:
        return await asyncio.to_thread(
            _sync_replace, db.global_path(), doc_id, passages, vectors,
            model=settings.embedding_model, route=EMBEDDING_ROUTE)


async def delete_document(doc_id: str) -> None:
    async with _write_lock:
        await asyncio.to_thread(_sync_delete, db.global_path(), doc_id)


async def get_passages(doc_id: str) -> list[dict]:
    return await asyncio.to_thread(_sync_get, db.global_path(), doc_id)


def _sync_unindexed(db_path: str, limit: int) -> list[dict]:
    conn = db._get_or_create_conn(db_path)
    rows = conn.execute(
        "SELECT d.id, d.token_count, substr(coalesce(d.title,''),1,80) AS title "
        "FROM documents d "
        "LEFT JOIN (SELECT DISTINCT doc_id FROM document_chunks) c ON c.doc_id = d.id "
        "WHERE c.doc_id IS NULL AND d.valid_until IS NULL AND length(coalesce(d.content,'')) > 0 "
        "ORDER BY d.token_count DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def _sync_indexed_ids(db_path: str) -> list[str]:
    conn = db._get_or_create_conn(db_path)
    rows = conn.execute("SELECT DISTINCT doc_id FROM document_chunks").fetchall()
    return [r[0] for r in rows]


async def indexed_ids() -> list[str]:
    """Doc ids with at least one passage — the inverse of find_unindexed.

    Exists for the retrieval bench: while passage coverage is partial, a
    document-vs-passage comparison MUST be restricted to memos present in both
    indexes, or the passage run scores every un-indexed memo as `absent` and
    reports indexing coverage while looking exactly like a retrieval result.
    That error was made and caught on 2026-07-30. [002/FR-111]
    """
    return await asyncio.to_thread(_sync_indexed_ids, db.global_path())


async def find_unindexed(limit: int = 500) -> list[dict]:
    """Live memos with no passages. [002/FR-110]

    The guarantee "every memo is indexed" cannot rest on ten write call sites
    all remembering to call `index_document` — someone adds an eleventh and the
    corpus quietly develops holes that no listing reveals. So the invariant is
    made **checkable** instead of trusted: this returns the violations, ordered
    biggest-first because the largest memos are both the most expensive to lose
    and the ones this whole feature exists to rescue.

    Intended callers: a post-write reconcile sweep, the migration verifier, and
    a test asserting the list is empty after a normal write.
    """
    return await asyncio.to_thread(_sync_unindexed, db.global_path(), limit)
