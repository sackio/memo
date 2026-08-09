"""Answer-loop audit — query -> answer -> what happened next. [001/FR-035]

The auditor's calibration input. It needs more than what memo answered: it
needs what the caller did NEXT. A recall followed immediately by an operator
correction is the signal that the answer was wrong, and no amount of inspecting
the answer alone reveals that.

Reads `mediator_audit_log` (retention >=30 days per FR-014) and pairs each
entry with the next call from the same session, so "and then what happened" is
on the same record.

Plain function, not an endpoint body: keeping the logic here means it can be
tested without FastAPI, and — the reason this module exists — calling an
endpoint function directly leaves `Query(default=...)` sentinel objects bound
as parameter values, which sqlite then refuses.
"""
from __future__ import annotations

import asyncio
import json
import logging

from memo import db

logger = logging.getLogger(__name__)

MEDIATOR_KINDS = ("retrieval", "storage")


def _sync_entries(db_path: str, limit: int, session_id: str | None,
                  since: float | None) -> list[dict]:
    conn = db._get_or_create_conn(db_path)
    clauses = ["mediator_kind IN ('retrieval','storage')"]
    params: list = []
    if session_id:
        clauses.append("calling_session_id = ?")
        params.append(session_id)
    if since is not None:
        clauses.append("at >= ?")
        params.append(float(since))
    params.append(int(limit))
    rows = conn.execute(
        f"SELECT * FROM mediator_audit_log WHERE {' AND '.join(clauses)} "
        f"ORDER BY at DESC LIMIT ?", params,
    ).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        for col in ("query", "filters", "results", "anomaly_flags"):
            if isinstance(d.get(col), str):
                try:
                    d[col] = json.loads(d[col])
                except ValueError:
                    pass
        out.append(d)
    return out


async def entries(*, limit: int = 50, session_id: str | None = None,
                  since: float | None = None) -> dict:
    """Audit entries, each paired with the next call from the same session."""
    rows = await asyncio.to_thread(_sync_entries, db.global_path(),
                                   limit, session_id, since)

    by_session: dict[str, list[dict]] = {}
    for e in sorted(rows, key=lambda x: x["at"]):
        by_session.setdefault(e.get("calling_session_id") or "?", []).append(e)
    for seq in by_session.values():
        for i, e in enumerate(seq):
            nxt = seq[i + 1] if i + 1 < len(seq) else None
            e["next_turn"] = None if nxt is None else {
                "at": nxt["at"],
                "mediator_kind": nxt["mediator_kind"],
                "chosen_action": nxt.get("chosen_action"),
                "gap_seconds": nxt["at"] - e["at"],
            }
    return {"entries": rows, "count": len(rows)}
