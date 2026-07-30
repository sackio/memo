"""Post-migration verification. [001/FR-038 001/FR-039 001/FR-040]

The checks from contracts/migration-cli.md §"Post-migration verification".
Exits non-zero on any failure, because a migration that half-worked and
reported success is the worst outcome available here — worse than one that
failed loudly, since the flip to v2 would then happen on a corpus nobody
re-examined.

Each check returns a structured result rather than just a bool: "5.2% legacy"
tells you what to do next, "FAIL" does not.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from memo import db

logger = logging.getLogger(__name__)

# SC-009: at most 5% may land in legacy-unattributed.
MAX_LEGACY_PCT = 5.0


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def _sync_counts(db_path: str) -> dict[str, Any]:
    conn = db._get_or_create_conn(db_path)
    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    by_class = {r["class"]: r["n"] for r in conn.execute(
        "SELECT class, COUNT(*) AS n FROM documents GROUP BY class")}
    no_class = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE class IS NULL OR class = ''"
    ).fetchone()[0]
    bad_valid_from = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE valid_from IS NULL OR valid_from = 0"
    ).fetchone()[0]
    return {"total": total, "by_class": by_class, "no_class": no_class,
            "bad_valid_from": bad_valid_from}


def _sync_redirects(db_path: str) -> dict[str, str]:
    conn = db._get_or_create_conn(db_path)
    try:
        return {r["v1_id"]: r["canonical_v2_id"] for r in conn.execute(
            "SELECT v1_id, canonical_v2_id FROM migration_redirects")}
    except Exception:
        return {}


def _sync_ids(db_path: str) -> set[str]:
    conn = db._get_or_create_conn(db_path)
    return {r["id"] for r in conn.execute("SELECT id FROM documents")}


async def verify(v1_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """Run every post-migration check. [001/FR-039]"""
    import asyncio

    counts = await asyncio.to_thread(_sync_counts, db.global_path())
    redirects = await asyncio.to_thread(_sync_redirects, db.global_path())
    v2_ids = await asyncio.to_thread(_sync_ids, db.global_path())

    checks: list[Check] = []

    # 1. Every v1 id resolves — directly or via a redirect.
    if v1_ids is not None:
        v1 = list(v1_ids)
        unresolved = [i for i in v1 if i not in v2_ids and i not in redirects]
        checks.append(Check(
            name="every_v1_id_resolves",
            passed=not unresolved,
            detail=(f"{len(v1) - len(unresolved)}/{len(v1)} v1 ids resolve"
                    + (f"; {len(unresolved)} unresolved" if unresolved else "")),
            data={"unresolved_sample": unresolved[:10],
                  "unresolved_count": len(unresolved)},
        ))

    # 2. No memo without a class.
    checks.append(Check(
        name="no_unclassified_memos",
        passed=counts["no_class"] == 0,
        detail=f"{counts['no_class']} memo(s) without a class",
        data={"no_class": counts["no_class"]},
    ))

    # 3. SC-009 — legacy-unattributed share.
    legacy = counts["by_class"].get("legacy-unattributed", 0)
    pct = (100.0 * legacy / counts["total"]) if counts["total"] else 0.0
    checks.append(Check(
        name="legacy_unattributed_within_budget",
        passed=pct <= MAX_LEGACY_PCT,
        detail=f"{pct:.1f}% legacy-unattributed (budget {MAX_LEGACY_PCT}%)",
        data={"legacy": legacy, "total": counts["total"], "pct": pct},
    ))

    # 4. SC-005 — no duplicate clusters left. Checked by content equality,
    #    which is the cheap conservative version: anything the migration
    #    SHOULD have merged is at minimum byte-identical here.
    dupes = await asyncio.to_thread(_sync_exact_dupes, db.global_path())
    checks.append(Check(
        name="no_duplicate_clusters",
        passed=not dupes,
        detail=f"{len(dupes)} exact-duplicate content group(s) remain",
        data={"sample": dupes[:5]},
    ))

    # 5. FR-002 — bi-temporal fields preserved.
    checks.append(Check(
        name="bi_temporal_preserved",
        passed=counts["bad_valid_from"] == 0,
        detail=f"{counts['bad_valid_from']} memo(s) with missing/zero valid_from",
        data={"bad_valid_from": counts["bad_valid_from"]},
    ))

    passed = all(c.passed for c in checks)
    return {
        "passed": passed,
        "checks": [c.__dict__ for c in checks],
        "counts": counts,
        "redirects": len(redirects),
        "exit_code": 0 if passed else 1,
    }


def _sync_exact_dupes(db_path: str) -> list[dict]:
    conn = db._get_or_create_conn(db_path)
    rows = conn.execute(
        "SELECT content, COUNT(*) AS n, GROUP_CONCAT(id) AS ids "
        "FROM documents WHERE valid_until IS NULL "
        "GROUP BY content HAVING n > 1"
    ).fetchall()
    return [{"count": r["n"], "ids": (r["ids"] or "").split(",")[:5],
             "excerpt": (r["content"] or "")[:80]} for r in rows]
