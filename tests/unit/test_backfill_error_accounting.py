"""A memo that blew up is not a memo that was skipped. [001/FR-039]

2026-07-31: a backfill run exited **0** having failed 3,489 of 7,657 memos. The
embedding provider ran out of credits mid-run; every exception was folded into
`skipped`, which also counts deliberate skips, and the process reported success.
Anything reading that exit code — a cron, a later migration phase, an operator
glancing at the terminal — would have concluded the corpus was migrated. It was
46% complete.

This is the same shape as the v1 0.3.6 discriminated-response fix: two states a
caller would act on differently were rendered identically. The fix is the same —
make the response carry the distinction.
"""
from __future__ import annotations

import asyncio

from memo.migrate import backfill


def _memo(doc_id: str) -> dict:
    return {"id": doc_id, "content": f"content for {doc_id}", "title": f"t {doc_id}",
            "tags": [], "metadata": {}, "created_at": 1.0, "updated_at": 1.0}


def _run(coro):
    return asyncio.run(coro)


async def _ok_embed(_text):
    return [0.1] * 8


def test_a_failed_memo_is_counted_as_errored_not_skipped():
    calls = {"n": 0}

    async def embed_that_dies(_text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("Error code: 402 - Insufficient credits")
        return [0.1] * 8

    stats, lines = _run(backfill.migrate_corpus(
        [_memo("a"), _memo("b"), _memo("c")], dry_run=True, embed=embed_that_dies))

    assert stats.errored == 1, "the 402 must be counted as an error"
    assert stats.skipped == 0, (
        "a crash folded into `skipped` is indistinguishable from a deliberate "
        "skip — that is what let a 46%-complete migration report success")
    assert stats.total == 3
    assert [ln.action for ln in lines].count("error") == 1


def test_error_pct_is_reported():
    async def always_dies(_text):
        raise RuntimeError("boom")

    stats, _ = _run(backfill.migrate_corpus(
        [_memo("a"), _memo("b")], dry_run=True, embed=always_dies))

    d = stats.as_dict()
    assert d["errored"] == 2
    assert d["error_pct"] == 100.0


def test_a_clean_run_reports_zero_errors():
    stats, _ = _run(backfill.migrate_corpus(
        [_memo("a"), _memo("b")], dry_run=True, embed=_ok_embed))

    assert stats.errored == 0
    assert stats.as_dict()["error_pct"] == 0.0
    assert stats.written == 2, "the happy path must still be counted as written"
