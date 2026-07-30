"""Shared fixtures for the v2 unit suite.

The critical job here is DB isolation. ``db._resolve_path`` ignores its
``db_path`` argument entirely and always returns
``settings.resolved_default_db_path`` (the single-global refactor of
2026-06-29), so a unit test that merely passes a temp path would still read and
WRITE THE REAL MEMO STORE. The ``temp_db`` fixture is therefore ``autouse`` —
isolation must not depend on a test remembering to ask for it.
"""
import pytest

from memo import db
from memo.config import settings


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point the whole module at a throwaway DB, then tear down its connection.

    Migrations apply automatically: ``db._apply_migrations`` falls back to the
    working-tree ``migrations/`` dir, so the temp DB gets the full v2 schema.
    """
    path = tmp_path / "memo-test.db"
    monkeypatch.setattr(settings, "default_db_path", str(path))

    # The connection cache is keyed by path, but a stale entry from a previous
    # test would shadow the new path — clear before AND after.
    db._connections.clear()
    yield str(path)
    for conn in db._connections.values():
        conn.close()
    db._connections.clear()


@pytest.fixture
def embedding():
    """A dimensionally-valid, non-degenerate dummy vector.

    ``document_embeddings`` is a vec0 table declared
    ``FLOAT[embedding_dimensions]``, so a short vector is rejected by sqlite-vec
    rather than silently padded — length matters.

    So does the VALUE, which is less obvious: an all-zeros vector has undefined
    cosine distance (zero magnitude ⇒ divide by zero), and sqlite-vec returns
    NULL for it, which blows up `score = 1.0 - distance` with a TypeError the
    moment a test actually searches. The earlier `[0.0] * n` fixture only
    survived because nothing exercised the search path. Use a unit vector.
    """
    v = [0.0] * settings.embedding_dimensions
    v[0] = 1.0
    return v
