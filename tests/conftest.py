"""Shared fixtures for the whole v2 suite (unit + contract + integration).

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
    # test would shadow the new path — clear before AND after. Connections are
    # thread-local as of 2026-07-30, so this clears THIS thread's cache; worker
    # threads hold their own and are torn down with the pool.
    db._clear_thread_connections()
    yield str(path)
    db._clear_thread_connections(close=True)


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


@pytest.fixture(autouse=True)
def _no_network_embeddings(monkeypatch):
    """Stub embeddings for EVERY test. Autouse and global on purpose.

    The test service runs with `network_mode: none`, so a real embedding call
    fails with a DNS error — and it should: no test may depend on a live
    OpenRouter. Rather than let each suite discover that separately, every test
    gets a deterministic offline vector by default.

    Suites that need similarity to mean something (the mediator contract tests)
    override this with their own topic-aware stub; their module-level autouse
    fixture applies after this one.

    The vector is a UNIT vector, not zeros: an all-zeros embedding has undefined
    cosine, so sqlite-vec returns NULL and `1.0 - distance` raises.
    """
    import hashlib

    from memo import embeddings

    async def fake_embed(text: str) -> list[float]:
        v = [0.0] * settings.embedding_dimensions
        h = int(hashlib.sha256((text or "").encode()).hexdigest(), 16)
        v[h % settings.embedding_dimensions] = 1.0
        return v

    # BOTH sides, and deliberately the SAME stub. [002/FR-111]
    #
    # The real `embed_query` prepends qwen3's instruction and `embed_document` does
    # not, so a test double that differed between them would make every stored
    # vector and every query vector land in different places and nothing would
    # retrieve anything. The distinction under test here is the ROUTING — which
    # function each call site chose — not the prefix text.
    #
    # ⚠️ Patch both or neither. Patching one leaves the other making real network
    # calls from a suite that runs with `network_mode: none`, which surfaces as a
    # connection error far from its cause.
    async def fake_embed_batch(texts: list[str]) -> list[list[float]]:
        return [await fake_embed(t) for t in texts]

    monkeypatch.setattr(embeddings, "embed_query", fake_embed)
    monkeypatch.setattr(embeddings, "embed_document", fake_embed)
    # ⚠️ `embed_batch` MUST be stubbed here too, as of the 2026-08-19 inline
    # passage index. `db.store()`/`db.update()` now chunk and embed on every
    # write via `passages.index_document`, which calls `embed_batch` — so
    # without this, every test that stores a memo reaches for the network from a
    # suite running `network_mode: none`.
    #
    # ⛔ AND IT WOULD NOT HAVE FAILED THE SUITE, WHICH IS THE POINT OF STUBBING
    # IT RATHER THAN LETTING IT BLOW UP: the inline hook swallows its own
    # exceptions by design (a memo must never be lost because the embedder is
    # down), so an unstubbed batch call surfaces as slow tests and silently
    # unindexed fixtures — never as a red test. The stub keeps the suite honest
    # about what a write actually produces.
    monkeypatch.setattr(embeddings, "embed_batch", fake_embed_batch)
