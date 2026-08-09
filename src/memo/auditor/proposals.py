"""Constitution proposals — the auditor's only route to constitutional memos. [001/FR-023]

Principle V: the OPERATOR owns the constitution. An auditor that noticed a
recurring anti-pattern cannot simply write a `class=constitutional` memo, because
constitutional memos are force-injected into every session — an auditor with
write access to them could silently rewrite the standing rules of the entire
fleet. So it files a proposal, and nothing enters `documents` until the operator
ratifies it.

Lifecycle (contracts/constitution-proposals.md):

    auditor: POST /constitution/propose   -> status=pending
    operator: POST /constitution/resolve  -> accepted  -> memo created
                                          -> rejected  -> archived with a note
"""
from __future__ import annotations

import asyncio
import json
import logging
from time import time
from typing import Any

from memo import db, embeddings

logger = logging.getLogger(__name__)

VALID_LAYERS = ("constitutional", "behavioral", "goal", "verbatim-critical")
VALID_STATUS = ("pending", "accepted", "rejected")


def _sync_insert(db_path: str, *, proposed_by: str, layer: str, scope: list[str],
                 content: str, tags: list[str], evidence: dict,
                 proposed_class: str, urgency: str) -> int:
    conn = db._get_or_create_conn(db_path)
    cur = conn.execute(
        "INSERT INTO constitution_proposals ("
        "  proposed_at, proposed_by, layer, scope, proposed_content,"
        "  proposed_tags, proposed_class, evidence, urgency, status"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
        (time(), proposed_by, layer, json.dumps(scope),
         content, json.dumps(tags), proposed_class,
         json.dumps(evidence), urgency),
    )
    conn.commit()
    return cur.lastrowid


def _row(r) -> dict:
    d = dict(r)
    for col in ("scope", "proposed_tags", "evidence"):
        if isinstance(d.get(col), str):
            try:
                d[col] = json.loads(d[col])
            except ValueError:
                pass
    return d


def _sync_list(db_path: str, status: str | None, limit: int) -> list[dict]:
    conn = db._get_or_create_conn(db_path)
    if status:
        rows = conn.execute(
            "SELECT * FROM constitution_proposals WHERE status = ? "
            "ORDER BY proposed_at DESC LIMIT ?", (status, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM constitution_proposals ORDER BY proposed_at DESC LIMIT ?",
            (limit,)).fetchall()
    return [_row(r) for r in rows]


def _sync_get(db_path: str, proposal_id: int) -> dict | None:
    conn = db._get_or_create_conn(db_path)
    r = conn.execute("SELECT * FROM constitution_proposals WHERE id = ?",
                     (proposal_id,)).fetchone()
    return _row(r) if r else None


def _sync_resolve(db_path: str, proposal_id: int, status: str, note: str | None,
                  resulting_memo_id: str | None) -> bool:
    conn = db._get_or_create_conn(db_path)
    cur = conn.execute(
        "UPDATE constitution_proposals SET status = ?, resolved_at = ?, "
        "resolution_note = ?, resulting_memo_id = ? "
        "WHERE id = ? AND status = 'pending'",
        (status, time(), note, resulting_memo_id, proposal_id),
    )
    conn.commit()
    return cur.rowcount > 0


async def propose(*, proposed_by: str, layer: str, proposed_content: str,
                  scope: list[str] | None = None, proposed_tags: list[str] | None = None,
                  evidence: dict[str, Any] | None = None,
                  urgency: str = "medium",
                  proposed_class: str | None = None) -> dict[str, Any]:
    """File a proposal. Nothing enters `documents` here. [001/FR-023]"""
    if layer not in VALID_LAYERS:
        raise ValueError(f"layer must be one of {VALID_LAYERS}, got {layer!r}")
    if not (proposed_content or "").strip():
        raise ValueError("proposed_content is required")
    if not proposed_by:
        raise ValueError("proposed_by is required")

    pid = await asyncio.to_thread(
        _sync_insert, db.global_path(), proposed_by=proposed_by, layer=layer,
        scope=scope or ["global"], content=proposed_content,
        tags=proposed_tags or [], evidence=evidence or {},
        # `layer` and `proposed_class` are distinct columns in migration 007:
        # layer is WHICH layer the proposal targets, proposed_class is the memo
        # class to create on acceptance. They coincide for every layer we
        # currently support, so the class defaults to the layer.
        proposed_class=proposed_class or layer, urgency=urgency,
    )
    logger.info("constitution proposal %s filed by %s (layer=%s, urgency=%s)",
                pid, proposed_by, layer, urgency)
    return {"proposal_id": pid, "status": "pending", "urgency": urgency}


async def list_proposals(status: str | None = "pending", limit: int = 50) -> list[dict]:
    return await asyncio.to_thread(_sync_list, db.global_path(), status, limit)


async def get_proposal(proposal_id: int) -> dict | None:
    return await asyncio.to_thread(_sync_get, db.global_path(), proposal_id)


async def resolve(*, proposal_id: int, accept: bool, resolved_by: str,
                  note: str | None = None) -> dict[str, Any]:
    """Operator accepts or rejects. On accept, the memo is created here. [001/FR-023]

    Only a PENDING proposal can be resolved — the guard is what stops a
    double-accept from creating the same constitutional memo twice.
    """
    proposal = await get_proposal(proposal_id)
    if proposal is None:
        return {"ok": False, "error": f"no proposal {proposal_id}"}
    if proposal.get("status") != "pending":
        return {"ok": False, "error": f"proposal {proposal_id} is already "
                                      f"{proposal['status']}"}

    memo_id = None
    if accept:
        content = proposal["proposed_content"]
        layer = proposal.get("proposed_class") or proposal["layer"]
        tags = list(proposal.get("proposed_tags") or [])
        embedding = await embeddings.embed_document(content)
        memo_id = await db.store(db_path=None, content=content,
                                 title=f"[{layer}] {content[:60]}",
                                 tags=tags, metadata={}, embedding=embedding)
        # Ratification metadata (C45) — a constitutional memo without it would
        # fail the Memo model's own validation.
        now = time()
        meta = {"version": "1.0.0", "ratified_at": now, "amended_at": now,
                "incident_ref": f"constitution-proposal:{proposal_id}"}
        await asyncio.to_thread(
            _stamp_accepted, db.global_path(), memo_id, layer,
            json.dumps(proposal.get("scope") or ["global"]),
            json.dumps(meta) if layer == "constitutional" else None,
        )

    ok = await asyncio.to_thread(_sync_resolve, db.global_path(), proposal_id,
                                 "accepted" if accept else "rejected", note, memo_id)
    if not ok:
        return {"ok": False, "error": "proposal was resolved concurrently"}

    logger.info("constitution proposal %s %s by %s (memo=%s)", proposal_id,
                "accepted" if accept else "rejected", resolved_by, memo_id)
    return {"ok": True, "proposal_id": proposal_id,
            "status": "accepted" if accept else "rejected",
            "resulting_memo_id": memo_id}


def _stamp_accepted(db_path: str, memo_id: str, layer: str, scope_json: str,
                    constitution_meta_json: str | None) -> None:
    conn = db._get_or_create_conn(db_path)
    conn.execute(
        "UPDATE documents SET class=?, injection_mode=?, scope=?, constitution_meta=? "
        "WHERE id=?",
        (layer,
         "forcible-constitutional" if layer == "constitutional" else "on-recall",
         scope_json, constitution_meta_json, memo_id),
    )
    conn.commit()
