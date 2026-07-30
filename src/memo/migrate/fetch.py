"""Reading the v1 corpus for migration. Read-only by construction. [001/FR-038]

Lives in the package rather than in the CLI script so the refusal behaviour is
importable and testable — a guard that can only be exercised by running the
real migration is a guard nobody checks.
"""
from __future__ import annotations

import httpx

class CorpusReadError(RuntimeError):
    """The v1 read could not be shown to be complete. Refuse rather than migrate."""


async def fetch_v1_corpus(v1_url: str, limit: int) -> list[dict]:
    """Read the v1 corpus in ONE request. Read-only.

    **Do not reintroduce limit/offset paging here.** v1 returns `/documents`
    with no stable ordering and the fleet writes to it continuously, so pages
    drift under the reader: rows shift between pages and are read twice, which
    also means rows shift PAST the window and are never read at all. Observed
    2026-07-30 on the first real backfill — 959 memos processed for 501 written,
    and all 339 "merges" were memos merging into themselves (same v1 id twice,
    zero genuine duplicates). Double-reading is survivable; the silent skip on
    the other side of the same drift is not, because the migration would report
    success over an incomplete corpus.

    A single unpaged read is consistent because v1 serves it from one SQLite
    query. We still verify rather than assume: duplicate ids and a short read
    both raise instead of migrating a corpus we cannot vouch for.
    """
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.get(f"{v1_url.rstrip('/')}/documents",
                             params={"limit": limit})
        r.raise_for_status()
        corpus = r.json()

    ids = [m.get("id") for m in corpus]
    dupes = len(ids) - len(set(ids))
    if dupes:
        raise CorpusReadError(
            f"v1 read returned {dupes} duplicate id(s) in a single response — "
            "the read is not self-consistent; refusing to migrate")

    # Independent count check: if v1 holds more than we read, we are about to
    # migrate a truncated corpus. `limit` being the binding constraint is the
    # caller's explicit choice (e.g. a bounded rehearsal) and is allowed.
    async with httpx.AsyncClient(timeout=60.0) as client:
        probe = await client.get(f"{v1_url.rstrip('/')}/documents",
                                 params={"limit": limit + 1000})
        probe.raise_for_status()
        available = len(probe.json())
    if available > len(corpus) and len(corpus) < limit:
        raise CorpusReadError(
            f"v1 reports {available} memos but the read returned "
            f"{len(corpus)} — refusing to migrate an incomplete corpus")

    return corpus
