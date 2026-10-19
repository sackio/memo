"""`/search` is selectable by config, and says which path answered. [002/FR-113]

T260. Both paths were previously reachable only by endpoint, which was deliberate
while the numbers did not exist — a flag invites flipping the default before
anything justifies it. The numbers now exist (research.md R-07) and they say do
not flip: SC-101 is 57.6% against 80%, SC-103 is 67% against 75%. So the switch
is built, the default stays `document`, and these tests pin both halves of that.

The property worth protecting is the LAST one: the explicit endpoints must be
immune to the config. A bench that reached the document path through `/search`
would silently start measuring passages the moment someone edited a setting, and
every label in its output would still read "document". That is the same shape as
the coverage confound this feature has already produced three times.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from memo import db, embeddings, main
from memo.config import settings


@pytest.fixture
def client(monkeypatch):
    async def _embed(_text):
        return [0.1] * 8

    async def _doc_search(**_kwargs):
        return [{"document": _memo("doc-hit"), "score": 0.9}]

    async def _passage_search(*_args, **_kwargs):
        return [{"document": _memo("passage-hit"), "score": 0.8,
                 "passage": {"text": "p", "chunk_index": 0,
                             "token_start": 0, "token_end": 5}}]

    monkeypatch.setattr(embeddings, "embed_query", _embed)
    monkeypatch.setattr(embeddings, "embed_document", _embed)
    monkeypatch.setattr(db, "search", _doc_search)
    monkeypatch.setattr(db, "search_passages", _passage_search)
    return TestClient(main.app)


def _memo(doc_id: str) -> dict:
    return {"id": doc_id, "title": "t", "content": "c", "tags": [],
            "metadata": {}, "created_at": 0.0, "updated_at": 0.0,
            "token_count": 1}


def _ids(resp):
    return [r["document"]["id"] for r in resp.json()]


def test_default_is_the_document_path():
    assert settings.memo_retrieval_path == "document", (
        "the flip is an operator decision gated on SC-101/SC-103, and neither "
        "passes — the default must not drift by accident")


def test_search_serves_documents_by_default(client):
    r = client.post("/search", json={"query": "q"})

    assert r.status_code == 200
    assert _ids(r) == ["doc-hit"]
    assert r.headers["X-Memo-Retrieval-Path"] == "document"


def test_search_serves_passages_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "memo_retrieval_path", "passages")

    r = client.post("/search", json={"query": "q"})

    assert _ids(r) == ["passage-hit"]
    assert r.headers["X-Memo-Retrieval-Path"] == "passages"


def test_configured_passage_results_keep_the_search_contract(client, monkeypatch):
    """The passage highlight is NOT smuggled in — that is T201's open decision."""
    monkeypatch.setattr(settings, "memo_retrieval_path", "passages")

    r = client.post("/search", json={"query": "q"})

    assert set(r.json()[0]) == {"document", "score"}, (
        "carrying `passage` through is FR-107a / T240–T241, still blocked on the "
        "operator; answering it here would pre-empt the decision")


def test_the_served_path_is_observable_from_outside(client, monkeypatch):
    monkeypatch.setattr(settings, "memo_retrieval_path", "passages")
    passages = client.post("/search", json={"query": "q"})
    monkeypatch.setattr(settings, "memo_retrieval_path", "document")
    documents = client.post("/search", json={"query": "q"})

    assert passages.headers["X-Memo-Retrieval-Path"] == "passages"
    assert documents.headers["X-Memo-Retrieval-Path"] == "document"
    assert _ids(passages) != _ids(documents), (
        "the header must track what actually answered, not merely echo config")


@pytest.mark.parametrize("configured", ["document", "passages"])
def test_explicit_endpoints_ignore_the_config(client, monkeypatch, configured):
    """The load-bearing one: a config edit must not move a measurement."""
    monkeypatch.setattr(settings, "memo_retrieval_path", configured)

    assert _ids(client.post("/search-documents", json={"query": "q"})) == ["doc-hit"]
    assert [r["document"]["id"]
            for r in client.post("/search-passages", json={"query": "q"}).json()] == [
        "passage-hit"]


# ──────────────────────────────────────────────────────────────────────────────
# ⛔⛔ EVERY CONFIGURED PATH MUST WRITE A query_log ROW. [2026-10-19]
#
# Everything above this line asserts the RESULTS and the HEADER, and all of it
# passed throughout the window in which `/search` logged nothing at all. Only
# `_document_search` carried a `log_query_async` call, so flipping the setting to
# `passages` -- which is what the live service runs -- silently ended REST search
# logging. MEASURED: 2,392 rows 2026-08-10 to 2026-09-30T16:59Z, then none.
#
# ⇒ A dispatcher test that never looks at the side effect cannot see a branch
# that forgot it. Parametrized over every path so a FOURTH branch added later
# fails here rather than going dark for a month.
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def logged(monkeypatch):
    rows: list[dict] = []

    async def _log(*args, **kwargs):
        rows.append(kwargs)

    monkeypatch.setattr(db, "log_query_async", _log)
    return rows


@pytest.mark.parametrize("configured", ["document", "passages", "size-routed"])
def test_every_retrieval_path_logs_the_query(client, monkeypatch, logged, configured):
    monkeypatch.setattr(settings, "memo_retrieval_path", configured)

    r = client.post("/search", json={"query": "q"})

    assert r.status_code == 200
    assert len(logged) == 1, (
        f"path {configured!r} wrote {len(logged)} query_log rows, expected "
        "exactly 1 -- one row per query, from whichever branch answered")
    row = logged[0]
    assert row["query"] == "q"
    assert row["latency_ms"] is not None and row["latency_ms"] >= 0
    assert row["result_ids"], "a row with no result_ids cannot be joined to anything"


def test_an_unrecognized_path_still_serves_documents(client, monkeypatch, logged):
    """`memo_retrieval_path` is an unvalidated `str` (config.py:87).

    A typo has always fallen through to the document path, and it must keep
    doing that rather than landing on whichever branch is written last -- the
    fallthrough is the only thing standing between a misspelled setting and a
    silently different retrieval model.
    """
    monkeypatch.setattr(settings, "memo_retrieval_path", "passsages")

    r = client.post("/search", json={"query": "q"})

    assert _ids(r) == ["doc-hit"]
    assert len(logged) == 1
