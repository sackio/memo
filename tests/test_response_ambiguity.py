"""Responses must distinguish states a caller would act on differently.

2026-07-30, third instance of one shape in a day:
  - memo_context   doc_count:0     — nothing matched, or matched but didn't fit?
  - search_multi   []              — empty corpus, or every search crashed?
  - memo_update    null            — bad id, memo vanished, or applied-and-silent?

`gigs` fat-fingered a UUID and got back a bare null; taking it at face value
would have meant reporting either a lost memo or a successful correction, both
wrong. Session-ids and memo-ids are both 36-char UUIDs, so passing the wrong
KIND of id lands in the same undifferentiated null.

memo_copy/memo_move are the sharper case: they have been no-ops since the
2026-06-29 single-global refactor while returning {id: ...}, which reads as a
successful duplicate. The response has to carry that truth because MCP tool
descriptions are cached per session at startup — a corrected docstring never
reaches an already-running session, but a corrected response does.
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
    monkeypatch.setattr(main.embeddings, "embed", _no_embed)


@pytest.fixture
def existing_memo(monkeypatch):
    async def _doc(**_kwargs):
        return {"id": "abc", "content": "hello", "title": "t", "tags": []}

    async def _no_embed(_text):
        return [0.0] * 8

    monkeypatch.setattr(main.db, "update", _doc)
    monkeypatch.setattr(main.embeddings, "embed", _no_embed)


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


def test_copy_does_not_pretend_to_have_duplicated_anything(monkeypatch):
    async def _same_id(**_kwargs):
        return "abc"

    monkeypatch.setattr(main.db, "copy", _same_id)

    result = _run(main.memo_copy(id="abc"))

    assert result["copied"] is False, "copy has been a no-op since 2026-06-29"
    assert result["reason"] == "single_global_db"
    assert result["id"] == "abc", "the id returned is the ORIGINAL, not a new copy"


def test_move_does_not_pretend_to_have_moved_anything(monkeypatch):
    async def _same_id(**_kwargs):
        return "abc"

    monkeypatch.setattr(main.db, "move", _same_id)

    result = _run(main.memo_move(id="abc"))

    assert result["moved"] is False
    assert result["reason"] == "single_global_db"


@pytest.mark.parametrize("tool,verb", [("memo_copy", "copied"), ("memo_move", "moved")])
def test_copy_and_move_distinguish_missing_from_no_op(monkeypatch, tool, verb):
    async def _missing(**_kwargs):
        return None

    monkeypatch.setattr(main.db, tool.replace("memo_", ""), _missing)

    result = _run(getattr(main, tool)(id="nope"))

    assert result[verb] is False
    assert result["reason"] == "not_found", "a missing memo is not the same as a no-op"
    assert "id" not in result, "no id should be returned for a memo that isn't there"
