"""Auditor action log — every write, modify, injection, compaction. [001/FR-025]

FR-025 requires an after-the-fact operator review trail for everything the
auditor does autonomously. That is the price of giving it autonomy at all: an
auditor that can modify memos and trigger compactions without a reviewable
record is indistinguishable from corruption.

Reuses `mediator_audit_log` with `mediator_kind='auditor'` rather than adding a
table — same retention, same queries, and the auditor's actions belong in the
same timeline as the mediator calls that prompted them.
"""
from __future__ import annotations

import logging
from typing import Any

from memo.mediators import audit

logger = logging.getLogger(__name__)

ACTIONS = ("write", "modify", "inject", "compact", "respawn", "propose",
           "override-recorded", "reap", "coalesce")


async def record(*, action: str, auditor_id: str, target: str | None,
                 rationale: str, observed_session: str | None = None,
                 details: dict[str, Any] | None = None) -> int | None:
    """Log one auditor action. Never raises. [001/FR-025]

    `rationale` is mandatory and free-text: the review question is always "why
    did it do that", and an action row without a reason cannot answer it.
    """
    if action not in ACTIONS:
        logger.warning("auditor: unknown action %r — logging anyway", action)
    return await audit.log(
        mediator_kind="auditor",
        calling_session_id=auditor_id,
        calling_role=observed_session,
        query={"action": action, "target": target, "rationale": rationale},
        filters=[],
        results=details or {},
        chosen_action=action,
        clarification_rounds=0,
        latency_ms=0,
        anomaly_flags=[],
    )


def _sync_list(db_path: str, limit: int, auditor_id: str | None) -> list[dict]:
    import json

    from memo import db
    conn = db._get_or_create_conn(db_path)
    if auditor_id:
        rows = conn.execute(
            "SELECT * FROM mediator_audit_log WHERE mediator_kind='auditor' "
            "AND calling_session_id = ? ORDER BY at DESC LIMIT ?",
            (auditor_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM mediator_audit_log WHERE mediator_kind='auditor' "
            "ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for col in ("query", "results"):
            if isinstance(d.get(col), str):
                try:
                    d[col] = json.loads(d[col])
                except ValueError:
                    pass
        out.append(d)
    return out


async def recent(limit: int = 50, auditor_id: str | None = None) -> list[dict]:
    """Recent auditor actions, newest first. The operator's review surface."""
    import asyncio

    from memo import db
    return await asyncio.to_thread(_sync_list, db.global_path(), limit, auditor_id)
