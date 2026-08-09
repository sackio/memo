"""Mediator audit log — every mediated call recorded. [001/FR-014 001/FR-015f]

FR-014 (retrieval) and FR-015f (storage) both require a durable record of every
mediated call, retained >=30 days, as the auditor's input. Rows land in
`mediator_audit_log` (migration 005).

**Logging must never break the call it is recording.** Every writer here
swallows its exceptions: an audit-log failure that turned a successful recall
into a 500 would trade an observability gap for an outage. Same posture as
`db._bump_access` and the leak-incident logger.
"""
from __future__ import annotations

import asyncio
import json
import logging
from time import time
from typing import Any

from memo import db

logger = logging.getLogger(__name__)


def _sync_log(db_path: str, *, mediator_kind: str, calling_session_id: str | None,
              calling_role: str | None, query: Any, filters: Any, results: Any,
              chosen_action: str | None, clarification_rounds: int,
              latency_ms: int, anomaly_flags: Any) -> int | None:
    conn = db._get_or_create_conn(db_path)
    cur = conn.execute(
        "INSERT INTO mediator_audit_log ("
        "  mediator_kind, at, calling_session_id, calling_role, query, filters,"
        "  results, chosen_action, clarification_rounds, latency_ms, anomaly_flags"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            mediator_kind, time(), calling_session_id, calling_role,
            json.dumps(query, default=str), json.dumps(filters, default=str),
            json.dumps(results, default=str), chosen_action,
            clarification_rounds, latency_ms,
            json.dumps(anomaly_flags, default=str),
        ),
    )
    conn.commit()
    return cur.lastrowid


async def log(**kwargs) -> int | None:
    """Write one audit row. Returns its id, or None if logging failed."""
    try:
        return await asyncio.to_thread(_sync_log, db.global_path(), **kwargs)
    except Exception:
        logger.exception("mediator audit log write failed — continuing")
        return None


async def log_recall(req, resp, ctx) -> int | None:
    """Record a retrieval-mediator call. [001/FR-014]"""
    return await log(
        mediator_kind="retrieval",
        calling_session_id=getattr(req, "session_id", None),
        calling_role=getattr(req, "agent_family", None),
        query={
            "query": getattr(req, "query", None),
            "as_of": getattr(req, "as_of", None),
            "scope_hint": list(getattr(req, "scope_hint", []) or []),
            "max_results": getattr(req, "max_results", None),
            "project": getattr(req, "project", None),
            "trigger_context": getattr(req, "trigger_context", None),
        },
        filters=list(getattr(ctx, "trace", []) or []),
        results={
            "citations": list(resp.citations or []),
            "llm_fallback_used": bool(resp.llm_fallback_used),
        },
        chosen_action=None,
        clarification_rounds=0,
        latency_ms=int(resp.latency_ms or 0),
        anomaly_flags=list(resp.anomalies or []),
    )


async def log_store(req, resp, *, clarification_rounds: int = 0,
                    filters: Any = None, anomalies: Any = None) -> int | None:
    """Record a storage-mediator call. [001/FR-015f]"""
    return await log(
        mediator_kind="storage",
        calling_session_id=getattr(req, "session_id", None),
        calling_role=None,
        query={
            "content_head": (getattr(req, "content", "") or "")[:200],
            "title": getattr(req, "title", None),
            "tags": list(getattr(req, "tags", []) or []),
            "class": getattr(req, "class_", None),
            "scope": list(getattr(req, "scope", []) or []),
            "bypass_mediator": bool(getattr(req, "bypass_mediator", False)),
            "has_operator_directive": getattr(req, "operator_directive_ref", None) is not None,
        },
        filters=filters or [],
        results={
            "memo_id": getattr(resp, "memo_id", None),
            "memo_ids": getattr(resp, "memo_ids", None),
            "superseded": getattr(resp, "superseded", None),
            "merged_into": getattr(resp, "merged_into", None),
        },
        chosen_action=getattr(resp, "action", None),
        clarification_rounds=clarification_rounds,
        latency_ms=int(getattr(resp, "latency_ms", 0) or 0),
        anomaly_flags=anomalies or [],
    )


def _sync_purge(db_path: str, older_than_s: float) -> int:
    conn = db._get_or_create_conn(db_path)
    cutoff = time() - older_than_s
    cur = conn.execute("DELETE FROM mediator_audit_log WHERE at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


async def purge_older_than(days: int = 30) -> int:
    """Drop rows past the retention window.

    FR-014/FR-015f set a >=30 day FLOOR, so this is opt-in maintenance rather
    than something the service runs on its own — purging below the floor by
    accident would destroy the auditor's evidence.
    """
    return await asyncio.to_thread(_sync_purge, db.global_path(), days * 86400.0)
