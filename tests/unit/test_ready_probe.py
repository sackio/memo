"""/ready must FAIL when the read path is wedged. [002/FR-116]

⛔ THE POINT OF THESE TESTS IS THE NEGATIVE CASE. On 2026-08-03 `/health`
returned 200 in 0.27s while `POST /search` hung past 90 seconds; every monitor
was green through a fleet-wide outage. A test that only asserts `/ready` returns
200 on a healthy store would have passed against the old `/health` too — it would
prove nothing about the thing that failed.

⇒ So each test here has a partner that makes the store unusable and asserts the
probe NOTICES. A probe that has never been observed to fail is not a probe.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from memo.main import app


@pytest.fixture()
def client():
    # ⚠️ NOT `with TestClient(app)`. The context-manager form runs the app
    # lifespan, which starts the MCP StreamableHTTPSessionManager — and that
    # refuses to `.run()` twice in one process, so the first test passes and
    # every later one ERRORS at setup. Matches test_retrieval_path_config.py.
    # /health and /ready need no lifespan; they touch the store directly.
    return TestClient(app)


def test_health_stays_up_and_says_nothing_about_the_read_path(client):
    """Liveness is deliberately constant. Documenting it so nobody 'fixes' it.

    Keeping the two separate is what lets a monitor tell a wedged disk from a
    dead process; collapsing them loses that.
    """
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_reports_ready_on_a_working_store(client):
    """POSITIVE CONTROL — required, but on its own it proves nothing."""
    r = client.get("/ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert isinstance(body["documents"], int)
    # ⭐ The vector index specifically, not just the table beside it: the
    # 2026-08-03 wedge was IO against the vec0 index, and a COUNT(*) on
    # `documents` alone would have sailed through it.
    assert "vector_index_readable" in body
    assert isinstance(body["elapsed_ms"], (int, float))


def test_ready_returns_503_when_the_store_hangs(client, monkeypatch):
    """⭐ THE TEST THAT MATTERS: a store that never answers must read as 503.

    This is the 2026-08-03 shape reproduced — the process is fine, the endpoint
    is reachable, and the storage layer never comes back.
    """
    import memo.main as main

    def _hang(*a, **kw):
        # Blocks the worker thread the probe dispatches to, exactly as a wedged
        # disk does. asyncio.wait_for must give up on it.
        import time
        time.sleep(30)

    monkeypatch.setattr(main.db, "_get_or_create_conn", _hang)
    r = client.get("/ready?timeout_s=0.5")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "not_ready"
    assert "did not respond" in body["reason"]
    # ⛔ The elapsed time must reflect the BOUND, not the hang. An unbounded
    # probe becomes a load source under exactly the conditions it detects.
    assert body["elapsed_ms"] < 5000


def test_ready_returns_503_when_the_store_raises(client, monkeypatch):
    """A broken store is `not_ready` with the reason attached — not a 500.

    A 500 would be indistinguishable from a bug in the probe itself, and the
    caller is a monitor that will only ever read the status code.
    """
    import memo.main as main

    def _boom(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(main.db, "_get_or_create_conn", _boom)
    r = client.get("/ready")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "not_ready"
    assert "database is locked" in body["reason"]


def test_ready_does_not_call_the_embedding_provider(client, monkeypatch):
    """⛔ Probe your own dependencies, not your vendors'.

    Measured during the same incident: OpenRouter answered in 1.3s while memo
    was unusable. A probe that embeds would have been green for the opposite
    reason — and would page us for vendor latency that says nothing about
    this host.
    """
    import memo.main as main

    called = []

    async def _tripwire(*a, **kw):
        called.append(a)
        raise AssertionError("/ready must not embed")

    for name in ("embed_query", "embed_batch", "embed_documents"):
        if hasattr(main.embeddings, name):
            monkeypatch.setattr(main.embeddings, name, _tripwire)

    r = client.get("/ready")
    assert r.status_code in (200, 503), r.text
    assert called == [], "/ready reached the embedding provider"
