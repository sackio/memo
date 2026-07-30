"""Reconciliation — keeping stored facts true as the world changes. [001/FR-027 001/FR-028 001/FR-029 001/FR-030 001/FR-031 001/FR-032]

Two entry points, fast and slow:

* **Real-time** (FR-030): on every `class=fact` write the mediator has already
  reconciled against near neighbours. This module adds the ENTITY-level check —
  a memo that names the same subject with a different value.
* **Event-triggered** (FR-031): an `infra.change` event ("server5 is now .43")
  arrives and every memo asserting the old value becomes wrong at once. This is
  the L3a pattern already proven in the v1 memo session.

Nothing here rewrites a memo by itself. Reconciliation SUPERSEDES with
provenance or flags for review; a silent in-place rewrite would destroy the
prior value with no record that it was ever believed — which is exactly the
history bi-temporality exists to keep.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from memo import db, embeddings
from memo.auditor import actions

logger = logging.getLogger(__name__)

# Entity shapes worth reconciling on: IPv4, hostname:port, MAC, and bare
# dotted-quad-ish tokens. Deliberately narrow — "the same entity with a
# different value" is only decidable for structured values.
_IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_HOSTPORT = re.compile(r"\b([a-z][\w-]*):(\d{2,5})\b", re.I)
_MAC = re.compile(r"\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b", re.I)


def extract_entities(text: str) -> dict[str, set[str]]:
    """Structured values a memo asserts. The unit reconciliation compares."""
    t = text or ""
    return {
        "ipv4": set(_IPV4.findall(t)),
        "hostport": {f"{h}:{p}" for h, p in _HOSTPORT.findall(t)},
        "mac": {m.lower() for m in _MAC.findall(t)},
    }


def _sync_scan_for_value(db_path: str, value: str, limit: int = 50) -> list[dict]:
    """Current memos whose content contains `value` (substring, not semantic).

    Substring on purpose: for an IP or a MAC, exact-match is the RIGHT tool and
    a vector search would surface topically-similar memos that never mention it.
    """
    conn = db._get_or_create_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM documents WHERE valid_until IS NULL "
        "AND content LIKE ? ORDER BY created_at DESC LIMIT ?",
        (f"%{value}%", limit),
    ).fetchall()
    return [db._row_to_memo(r) for r in rows]


async def find_stale_by_value(old_value: str, limit: int = 50) -> list[dict]:
    """Current memos still asserting `old_value`. [001/FR-031]"""
    return await asyncio.to_thread(_sync_scan_for_value, db.global_path(),
                                   old_value, limit)


async def on_infra_change(*, entity: str, old_value: str, new_value: str,
                          source: str, actor: str = "reconciler",
                          apply: bool = False,
                          max_updates: int = 5) -> dict[str, Any]:
    """React to an infra-change event. [001/FR-031 001/FR-032]

    Default is DRY-RUN (`apply=False`): it reports what would change. Rewriting
    the corpus off a single broadcast — which may itself be wrong, or may be a
    staged change — is not something to do unprompted.

    `max_updates` caps the blast radius even when applying, so one malformed
    event cannot rewrite hundreds of memos (the v1 L3a budget rule).
    """
    candidates = await find_stale_by_value(old_value)
    plan = [{"memo_id": m["id"], "class": m.get("class"),
             "excerpt": (m.get("content") or "")[:160]} for m in candidates]

    result: dict[str, Any] = {
        "entity": entity, "old_value": old_value, "new_value": new_value,
        "source": source, "candidates": len(plan), "plan": plan[:max_updates],
        "applied": [], "dry_run": not apply,
    }
    if len(plan) > max_updates:
        result["capped"] = (f"{len(plan)} candidates exceed the {max_updates} "
                            f"per-event cap; remaining left for the global sweep")

    if not apply:
        return result

    for m in candidates[:max_updates]:
        new_content = (m.get("content") or "").replace(old_value, new_value)
        if new_content == m.get("content"):
            continue
        embedding = await embeddings.embed(new_content)
        payload = {"content": new_content, "title": m.get("title"),
                   "tags": m.get("tags") or [], "metadata": m.get("metadata") or {},
                   "class": m.get("class"), "scope": m.get("scope") or ["global"],
                   "provenance": m.get("provenance")}
        # SUPERSEDE, never in-place: the old value stays readable at its
        # historical timestamp instead of being erased.
        res = await db.supersede(None, m["id"], payload, embedding, actor=actor,
                                 reason=f"infra change: {entity} {old_value} -> {new_value}",
                                 operator_directive_ref={"kind": "infra.change",
                                                         "source": source})
        if res:
            result["applied"].append({"old_id": m["id"], "new_id": res["new_id"]})
            await actions.record(
                action="modify", auditor_id=actor, target=m["id"],
                rationale=f"infra change {entity}: {old_value} -> {new_value} "
                          f"(source {source}); superseded rather than rewritten",
                details={"new_id": res["new_id"]},
            )
    return result


async def check_fact_conflict(content: str, *, exclude_id: str | None = None
                              ) -> list[dict[str, Any]]:
    """Entity-level conflicts for an incoming fact. [001/FR-027 001/FR-030]

    Complements the mediator's semantic reconcile: this catches "same entity,
    different structured value", which similarity alone reads as two memos
    about one subject rather than as a contradiction.
    """
    incoming = extract_entities(content)
    conflicts: list[dict[str, Any]] = []
    for kind, values in incoming.items():
        for value in values:
            # For a hostport, the conflict is a DIFFERENT port on the same host.
            key = value.split(":")[0] if kind == "hostport" else value
            neighbours = await find_stale_by_value(key, limit=20)
            for n in neighbours:
                if exclude_id and n["id"] == exclude_id:
                    continue
                theirs = extract_entities(n.get("content") or "")[kind]
                if theirs and value not in theirs:
                    conflicts.append({
                        "memo_id": n["id"], "kind": kind,
                        "incoming_value": value, "stored_values": sorted(theirs),
                        "excerpt": (n.get("content") or "")[:160],
                    })
    return conflicts
