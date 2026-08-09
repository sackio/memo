"""The corpus indexer's accounting, with no database and no network. [002/FR-110]

T271. The thing under test is not "does indexing work" — that needs a corpus and
a live provider, and is measured deliberately against :8091 and recorded in
research.md. What is tested here is the part that has actually failed twice:

  - 2026-07-31 — the migration backfill folded every exception into `skipped`,
    which also counts deliberate no-ops, and exited 0 having failed 3,489 of
    7,657 memos. Two states a caller acts on completely differently, rendered
    identically, and nothing could catch it because the logic lived inline in
    `main` where no test could reach it.
  - 2026-08-01 — this script's first cut returned 2 from a `--limit` rehearsal,
    where a remainder exists by construction. Exit 2 is the one signal that has
    to keep meaning "the corpus is not fully indexed"; a version that cries wolf
    on a rehearsal teaches the reader to ignore it.

So `index_all` and `exit_code` are extracted, and these tests hold the line on
the distinction between an error, an empty memo, and a shortfall.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


def _load():
    """Import the indexer, which has no `.py` extension by design (it is a CLI)."""
    for candidate in (Path("/app/scripts/memo-index-corpus"),
                      Path(__file__).resolve().parents[2] / "scripts" / "memo-index-corpus"):
        if candidate.exists():
            spec = importlib.util.spec_from_loader(
                "memo_index_corpus",
                importlib.machinery.SourceFileLoader("memo_index_corpus", str(candidate)))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    pytest.skip("memo-index-corpus not present in this image")


idx = _load()


def _todo(n: int, prefix: str = "doc"):
    return [(f"{prefix}-{i}", f"content {i}") for i in range(n)]


# --- An error is not a skip, and not an empty ---

@pytest.mark.asyncio
async def test_an_exception_is_counted_as_errored_not_indexed():
    """The 2026-07-31 failure, in miniature."""
    async def flaky(doc_id, content):
        if doc_id == "doc-2":
            raise RuntimeError("402 insufficient credits")
        return 3

    stats = await idx.index_all(_todo(5), flaky, progress_every=0)

    assert stats["errored"] == 1
    assert stats["indexed"] == 4
    assert stats["empty"] == 0, "an exception must never be filed as an empty memo"
    assert stats["passages_written"] == 12


@pytest.mark.asyncio
async def test_error_pct_is_reported():
    async def always_fails(doc_id, content):
        raise RuntimeError("provider down")

    stats = await idx.index_all(_todo(4), always_fails, progress_every=0)
    assert stats["errored"] == 4
    assert stats["error_pct"] == 100.0


@pytest.mark.asyncio
async def test_a_memo_with_no_passages_is_empty_not_errored():
    """A memo that chunks to nothing is a legitimate no-op, not a failure —
    counting it as either an error or a success misstates coverage."""
    async def yields_nothing(doc_id, content):
        return 0

    stats = await idx.index_all(_todo(3), yields_nothing, progress_every=0)
    assert stats["empty"] == 3
    assert stats["indexed"] == 0
    assert stats["errored"] == 0


@pytest.mark.asyncio
async def test_every_memo_lands_in_exactly_one_bucket():
    """The invariant that makes the counts trustworthy: no double-count, no
    memo silently dropped."""
    async def mixed(doc_id, content):
        n = int(doc_id.rsplit("-", 1)[1])
        if n % 3 == 0:
            raise RuntimeError("boom")
        return 0 if n % 3 == 1 else 2

    stats = await idx.index_all(_todo(30), mixed, progress_every=0)
    assert stats["indexed"] + stats["empty"] + stats["errored"] == stats["attempted"] == 30


@pytest.mark.asyncio
async def test_failures_carry_the_doc_id_and_the_error():
    async def flaky(doc_id, content):
        if doc_id == "doc-1":
            raise ValueError("embed_batch returned 2 vectors for 5 passages")
        return 1

    stats = await idx.index_all(_todo(3), flaky, progress_every=0)
    assert len(stats["failures"]) == 1
    doc_id, err = stats["failures"][0]
    assert doc_id == "doc-1"
    assert "ValueError" in err and "2 vectors for 5 passages" in err


# --- Exit codes: the signal a cron or a later phase acts on ---

def _stats(errored=0, attempted=10):
    return {"attempted": attempted, "indexed": attempted - errored, "empty": 0,
            "errored": errored, "passages_written": 0, "error_pct": 0.0,
            "elapsed_min": 0.0}


def test_clean_full_run_with_nothing_remaining_exits_zero():
    assert idx.exit_code(_stats(), remaining=0, limit=None) == 0


def test_any_error_exits_two():
    assert idx.exit_code(_stats(errored=1), remaining=0, limit=None) == 2


def test_a_silent_shortfall_exits_two():
    """No exception, but memos remain unindexed. This is the shape that shipped
    a 46%-complete corpus reporting success."""
    assert idx.exit_code(_stats(), remaining=17, limit=None) == 2


def test_a_limited_rehearsal_does_not_fail_on_its_own_remainder():
    assert idx.exit_code(_stats(), remaining=7316, limit=20) == 0


def test_a_limited_rehearsal_still_fails_on_a_real_error():
    """`--limit` excuses the remainder, never an exception."""
    assert idx.exit_code(_stats(errored=1), remaining=7316, limit=20) == 2
