"""Session ephemeral-flush — POST /flush. [001/FR-034 001/FR-036]

Upserts a slot-set of `ephemeral-flush` memos in ONE call, keyed on
`(session_id, flush_generation)`. Called by the PreCompact hook (FR-036,
repurposing the atc-precompact.sh no-op) and by the auditor's SessionStop
handler.

Why upsert-by-slot rather than append: a session flushes the same six slots
over and over. Appending would make a memo per slot per compaction, so an
active session would bury its own corpus in near-duplicate state dumps within a
day. Re-flushing a generation overwrites its slots in place.

Every slot memo gets `expires_at` (default now+24h) so the TTL reaper sweeps
them. Flush content is a snapshot of transient state — stale in hours, and
actively misleading if it outlives the work it describes.
"""
from __future__ import annotations

import asyncio
import json
import logging
from time import time

from memo import db, embeddings

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = 24 * 3600

STANDARD_SLOTS = (
    "active-threads", "in-flight-work", "pending-dms",
    "open-tasks", "key-decisions", "follow-ups-owed",
)


def _sync_find_slot(db_path: str, session_id: str, generation: int,
                    slot: str) -> str | None:
    conn = db._get_or_create_conn(db_path)
    row = conn.execute(
        "SELECT id FROM documents WHERE class = 'ephemeral-flush' "
        "AND valid_until IS NULL "
        "AND json_extract(metadata, '$.session_id') = ? "
        "AND json_extract(metadata, '$.flush_generation') = ? "
        "AND json_extract(metadata, '$.slot') = ? LIMIT 1",
        (session_id, generation, slot),
    ).fetchone()
    return row["id"] if row else None


def _sync_stamp(db_path: str, doc_id: str, *, expires_at: float,
                provenance: dict | None, metadata: dict) -> None:
    """Apply the v2 columns db.store does not know about."""
    conn = db._get_or_create_conn(db_path)
    conn.execute(
        "UPDATE documents SET class='ephemeral-flush', injection_mode='on-recall', "
        "expires_at=?, provenance=?, metadata=? WHERE id=?",
        (expires_at, json.dumps(provenance) if provenance else None,
         json.dumps(metadata), doc_id),
    )
    conn.commit()


async def flush(*, session_id: str, flush_generation: int, slots: dict[str, str],
                expires_at: float | None = None,
                provenance: dict | None = None) -> dict:
    """Upsert one generation's slot-set. Returns `{slot: memo_id}`. [001/FR-034]"""
    if not session_id:
        raise ValueError("session_id is required")
    if not isinstance(slots, dict) or not slots:
        raise ValueError("slots must be a non-empty mapping")

    expiry = expires_at if expires_at is not None else time() + DEFAULT_TTL_S
    memo_ids: dict[str, str] = {}

    for slot, content in slots.items():
        body = (content or "").strip()
        if not body:
            continue                       # an empty slot is not worth a memo
        metadata = {"session_id": session_id, "flush_generation": flush_generation,
                    "slot": slot}
        title = f"[flush g{flush_generation}] {slot}"
        embedding = await embeddings.embed(body)

        existing = await asyncio.to_thread(
            _sync_find_slot, db.global_path(), session_id, flush_generation, slot)
        if existing:
            await db.update(db_path=None, doc_id=existing, content=body,
                            title=title, tags=["ephemeral-flush", slot],
                            metadata=metadata, embedding=embedding)
            doc_id = existing
        else:
            doc_id = await db.store(db_path=None, content=body, title=title,
                                    tags=["ephemeral-flush", slot],
                                    metadata=metadata, embedding=embedding)
        await asyncio.to_thread(_sync_stamp, db.global_path(), doc_id,
                                expires_at=expiry, provenance=provenance,
                                metadata=metadata)
        memo_ids[slot] = doc_id

    logger.info("flush: session=%s generation=%s slots=%d",
                session_id, flush_generation, len(memo_ids))
    return {"flush_generation": flush_generation, "memo_ids": memo_ids,
            "expires_at": expiry}
