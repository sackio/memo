"""Responses must distinguish states a caller would act on differently.

2026-07-30, third instance of one shape in a day:
  - memo_context   doc_count:0     — nothing matched, or matched but didn't fit?
  - search_multi   []              — empty corpus, or every search crashed?
  - memo_update    null            — bad id, memo vanished, or applied-and-silent?

`gigs` fat-fingered a UUID and got back a bare null; taking it at face value
would have meant reporting either a lost memo or a successful correction, both
wrong. Session-ids and memo-ids are both 36-char UUIDs, so passing the wrong
KIND of id lands in the same undifferentiated null.

memo_copy/memo_move were the sharper case: no-ops since the 2026-06-29
single-global refactor that still returned {id: ...}, which reads as a
successful duplicate. They were made to return an honest discriminator on
2026-07-30 — and REMOVED entirely on 2026-08-10, which is the better fix.

⭐ The lesson survives the tools: making a misleading response honest is a
mitigation; deleting the thing that could only ever mislead is a cure. A tool
whose every successful call means "I did nothing" costs tool-list context in
every session on every host and invites callers to reach for it. Prefer removal
once a capability is provably inert — but note that removal only reaches a
session when it next restarts, because MCP tool lists are cached at startup.
That caching is exactly why the honest-response fix was worth making first.
"""

import asyncio

import pytest

from memo import main


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def no_such_memo(monkeypatch):
    async def _none(**_kwargs):
        return None

    async def _no_embed(_text):
        return [0.0] * 8

    monkeypatch.setattr(main.db, "update", _none)
    monkeypatch.setattr(main.embeddings, "embed_query", _no_embed)
    monkeypatch.setattr(main.embeddings, "embed_document", _no_embed)


@pytest.fixture
def existing_memo(monkeypatch):
    # ⚠️ `*_a` is load-bearing: `memo_update` calls `db.get(db_path, id)`
    # POSITIONALLY while `db.update(...)` is called by keyword. A kwargs-only
    # double raises TypeError from one and not the other.
    async def _doc(*_a, **_kwargs):
        return {"id": "abc", "content": "hello", "title": "t", "tags": []}

    # ⛔ THE MOCK WENT STALE UNDER THE CODE, and the test then failed for a
    # reason that had nothing to do with what it tests. The shrink guard
    # (2026-08-19) made the `content=` replace path read the memo first, via
    # `db.get`. This fixture mocked only `db.update`, so that read hit the real
    # empty test store, returned None, and `memo_update` answered
    # `{updated: False, reason: "not_found"}` — failing an assertion about the
    # SUCCESS branch's discriminator, which was never broken.
    # ⭐ A fixture that mocks a SUBSET of a function's collaborators silently
    # starts testing something else the moment a new collaborator appears, and
    # the failure it eventually produces points at the wrong thing. Found
    # 2026-08-21; red since 08-19, so `ebb62e8` shipped over it.
    monkeypatch.setattr(main.db, "get", _doc)

    async def _no_embed(_text):
        return [0.0] * 8

    monkeypatch.setattr(main.db, "update", _doc)
    monkeypatch.setattr(main.embeddings, "embed_query", _no_embed)
    monkeypatch.setattr(main.embeddings, "embed_document", _no_embed)


def test_update_miss_is_not_a_bare_null(no_such_memo):
    result = _run(main.memo_update(id="not-a-real-id", content="x"))

    assert result is not None, "null cannot distinguish bad id from vanished memo"
    assert result["updated"] is False
    assert result["reason"] == "not_found"
    assert result["requested_id"] == "not-a-real-id"


def test_update_success_carries_the_same_discriminator(existing_memo):
    result = _run(main.memo_update(id="abc", content="x"))

    assert result["updated"] is True, "both branches must carry `updated`"
    assert result["id"] == "abc", "the memo's own fields survive"
    assert result["content"] == "hello"

