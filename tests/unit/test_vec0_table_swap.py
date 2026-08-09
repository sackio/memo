"""Swapping a vec0 table: RENAME destroys it. [002/FR-105 002/FR-108]

Found by `groton` on a scratch DB before spending GPU time, relayed by the
`embeddings` seat, and reproduced here independently 2026-08-01 rather than taken
on report.

A `vec0` virtual table is not one table. It is a main table plus six shadow
tables (`_rowids`, `_chunks`, `_info`, `_vector_chunks00`, `_metadatachunks00`,
`_metadatatext00`). `ALTER TABLE ... RENAME` moves ONLY the main table and
orphans the rest, so the next query dies with `no such table: main.<new>_chunks`.

Why that is worse than it sounds: the natural swap sequence drops the OLD table
first, so a RENAME-based swap destroys the working index and leaves an unusable
replacement — discovered at the very end, with every vector already paid for. For
this corpus that is ~65 minutes of GPU time and a dead index.

These tests exist because the RENAME form is the obvious way to write this and
looks correct right up until query time.
"""
from __future__ import annotations

import sqlite3
import struct

import pytest

sqlite_vec = pytest.importorskip("sqlite_vec")


def _conn():
    c = sqlite3.connect(":memory:")
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    return c


def _vec(n: int, s: float = 1.0) -> bytes:
    return struct.pack(f"{n}f", *[0.1 * s] * n)


def _create(c, name: str, dims: int, with_chunk_index: bool = False):
    cols = "doc_id TEXT, chunk_index INTEGER, " if with_chunk_index else "doc_id TEXT, "
    c.execute(f"CREATE VIRTUAL TABLE {name} USING vec0({cols}"
              f"embedding FLOAT[{dims}] distance_metric=cosine)")


def test_rename_orphans_the_shadow_tables():
    """The hazard itself. If this ever starts passing a query, sqlite-vec changed
    and the swap procedure can be simplified — until then, do not."""
    c = _conn()
    _create(c, "t_new", 4)
    c.execute("INSERT INTO t_new(doc_id, embedding) VALUES (?,?)", ("a", _vec(4)))
    c.execute("ALTER TABLE t_new RENAME TO t")

    shadows = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 't_new%'")]
    assert shadows, "shadow tables kept the OLD prefix — that is the whole bug"

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        list(c.execute("SELECT doc_id FROM t WHERE embedding MATCH ? AND k=1", (_vec(4),)))


def test_drop_create_insert_swap_survives_a_knn_query():
    """The prescribed path, verified end to end rather than assumed."""
    c = _conn()
    _create(c, "emb", 1536)
    c.execute("INSERT INTO emb(doc_id, embedding) VALUES (?,?)", ("old", _vec(1536)))
    _create(c, "emb_new", 2560)
    for i in range(3):
        c.execute("INSERT INTO emb_new(doc_id, embedding) VALUES (?,?)",
                  (f"d{i}", _vec(2560, i + 1)))

    c.execute("DROP TABLE emb")
    _create(c, "emb", 2560)
    c.execute("INSERT INTO emb(doc_id, embedding) "
              "SELECT doc_id, embedding FROM emb_new")
    c.execute("DROP TABLE emb_new")

    rows = list(c.execute(
        "SELECT doc_id FROM emb WHERE embedding MATCH ? AND k=3", (_vec(2560, 1),)))
    assert len(rows) == 3, "KNN through the swapped table must work"
    assert c.execute("SELECT COUNT(*) FROM emb").fetchone()[0] == 3


def test_chunk_embeddings_keeps_its_chunk_index_column_through_the_swap():
    """`chunk_embeddings` carries an extra column. Recreating it as
    doc_id+embedding would silently drop the passage ordinal, which is what makes
    a passage addressable at all."""
    c = _conn()
    _create(c, "ce_new", 2560, with_chunk_index=True)
    c.execute("INSERT INTO ce_new(doc_id, chunk_index, embedding) VALUES (?,?,?)",
              ("d1", 7, _vec(2560)))

    _create(c, "ce", 2560, with_chunk_index=True)
    c.execute("INSERT INTO ce(doc_id, chunk_index, embedding) "
              "SELECT doc_id, chunk_index, embedding FROM ce_new")

    got = c.execute("SELECT doc_id, chunk_index FROM ce").fetchone()
    assert got == ("d1", 7), "the passage ordinal must survive the swap"


def test_a_mismatched_width_is_rejected_loudly():
    """The safety property the 2560 choice rests on: a half-finished re-embed
    must CRASH, not score nonsense. 1536 and 3072 have both been live in this
    corpus, so 2560 was picked to collide with neither."""
    c = _conn()
    _create(c, "emb", 2560)
    with pytest.raises(sqlite3.OperationalError, match="[Dd]imension mismatch"):
        c.execute("INSERT INTO emb(doc_id, embedding) VALUES (?,?)", ("bad", _vec(1536)))
