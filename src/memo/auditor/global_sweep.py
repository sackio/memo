"""Global auditor — scheduled cross-session sweep. [001/FR-024]

Runs on a cadence (daily by default) and does the four things FR-024 lists:

  (a) police the shadow auditors
  (b) synthesize cross-session patterns
  (c) reap `ephemeral-flush` past TTL — belt-and-braces with the 5-min reaper
  (d) coalesce long supersession chains

(c) is deliberately redundant with `reaper.py`. The reaper is an in-process
task; if the process was down, restarted, or the reaper was disabled by config,
TTLs silently stopped being honored. A scheduled sweep that finds nothing is
cheap; a TTL that quietly stopped working is not.

(d) exists because supersession is append-only by design: a value corrected
twenty times leaves twenty rows. The chain is history worth keeping, but the
intermediate BODIES are not, so they compact into one summary while the edges
stay intact.
"""
from __future__ import annotations

import asyncio
import json
import logging
from time import time
from typing import Any

from memo import db, reaper
from memo.auditor import actions

logger = logging.getLogger(__name__)

# Chains at least this long are worth compacting.
COALESCE_MIN_CHAIN = 5
# A shadow whose watched content has not moved in this long is suspect.
SHADOW_STALL_S = 2 * 3600


def _sync_long_chains(db_path: str, min_len: int) -> list[list[str]]:
    """Find supersession chains of at least `min_len` versions."""
    conn = db._get_or_create_conn(db_path)
    edges = conn.execute(
        "SELECT old_id, new_id FROM supersede_edges ORDER BY superseded_at"
    ).fetchall()
    if not edges:
        return []
    nxt = {e["old_id"]: e["new_id"] for e in edges}
    has_parent = {e["new_id"] for e in edges}
    chains = []
    for root in (e["old_id"] for e in edges):
        if root in has_parent:
            continue                      # not a root
        chain, cur, seen = [root], root, {root}
        while cur in nxt and nxt[cur] not in seen:
            cur = nxt[cur]
            seen.add(cur)
            chain.append(cur)
        if len(chain) >= min_len:
            chains.append(chain)
    return chains


def _sync_coalesce(db_path: str, chain: list[str]) -> dict[str, Any]:
    """Compact a chain's intermediate bodies into a summary on the tip.

    Edges are NEVER touched — the audit trail of who changed what and when is
    the part worth keeping. Only the superseded BODIES collapse.
    """
    conn = db._get_or_create_conn(db_path)
    tip = chain[-1]
    intermediates = chain[:-1]
    rows = conn.execute(
        f"SELECT id, content, valid_from, valid_until FROM documents "
        f"WHERE id IN ({','.join('?' * len(intermediates))}) ORDER BY valid_from",
        intermediates,
    ).fetchall()
    if not rows:
        return {"coalesced": 0}

    lines = [f"- {r['id'][:8]} @{r['valid_from']:.0f}: "
             f"{(r['content'] or '')[:160]}" for r in rows]
    summary = "previous values:\n" + "\n".join(lines)

    tip_row = conn.execute("SELECT metadata FROM documents WHERE id = ?",
                           (tip,)).fetchone()
    try:
        meta = json.loads(tip_row["metadata"]) if tip_row else {}
    except (ValueError, TypeError):
        meta = {}
    meta["coalesced_history"] = summary
    meta["coalesced_at"] = time()
    conn.execute("UPDATE documents SET metadata = ? WHERE id = ?",
                 (json.dumps(meta), tip))

    placeholder = "[coalesced — see coalesced_history on the current version]"
    for r in rows:
        conn.execute("UPDATE documents SET content = ? WHERE id = ?",
                     (placeholder, r["id"]))
    conn.commit()
    return {"coalesced": len(rows), "tip": tip}


async def sweep(*, auditor_id: str = "auditor-global",
                shadows: dict[str, Any] | None = None,
                coalesce: bool = True) -> dict[str, Any]:
    """Run one global sweep. Never raises — a scheduled job must survive. [001/FR-024]"""
    started = time()
    result: dict[str, Any] = {"at": started, "reaped": [], "coalesced_chains": 0,
                              "stalled_shadows": [], "errors": []}

    # (c) TTL reap.
    try:
        result["reaped"] = await reaper.sweep_once()
    except Exception as e:
        logger.exception("global sweep: reap failed")
        result["errors"].append(f"reap: {e}")

    # (d) Coalesce long chains.
    if coalesce:
        try:
            chains = await asyncio.to_thread(_sync_long_chains, db.global_path(),
                                             COALESCE_MIN_CHAIN)
            for chain in chains:
                out = await asyncio.to_thread(_sync_coalesce, db.global_path(), chain)
                if out.get("coalesced"):
                    result["coalesced_chains"] += 1
                    await actions.record(
                        action="coalesce", auditor_id=auditor_id, target=chain[-1],
                        rationale=f"supersession chain of {len(chain)} versions "
                                  f"compacted to a summary; edges retained",
                        details={"chain_length": len(chain)},
                    )
        except Exception as e:
            logger.exception("global sweep: coalesce failed")
            result["errors"].append(f"coalesce: {e}")

    # (a) Police the shadows.
    if shadows:
        now = time()
        for sid, shadow in shadows.items():
            try:
                mon = getattr(shadow, "liveness", None)
                if mon is None:
                    continue
                for key in mon.stalled_keys(now=now):
                    result["stalled_shadows"].append({"shadow": sid, "key": key})
            except Exception as e:
                result["errors"].append(f"shadow {sid}: {e}")

    if result["reaped"] or result["coalesced_chains"] or result["stalled_shadows"]:
        await actions.record(
            action="reap", auditor_id=auditor_id, target=None,
            rationale=(f"global sweep: reaped {len(result['reaped'])}, "
                       f"coalesced {result['coalesced_chains']} chain(s), "
                       f"{len(result['stalled_shadows'])} stalled shadow(s)"),
            details={k: v for k, v in result.items() if k != "errors"},
        )
    result["duration_s"] = time() - started
    return result
