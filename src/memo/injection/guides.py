"""SESSION_GUIDE resolver — maps a session id to its agent-family + guide. [001/FR-016 001/FR-017]

`agent_family` decides which scoped memos a session receives, so resolving it is
on the critical path of every injection. The roster lives outside memo (the
`agents` supervisor owns it), which means this module's real job is to keep an
external dependency from becoming a hard one.

Resolution order, cheapest and most reliable first:

1. `SESSION_GUIDE_cache` table (migration 008) — a local snapshot.
2. Parse the `SESSION_GUIDE` array out of `~/scripts/agents` — the roster's
   source of truth on this host, readable without the supervisor being up.
3. Fall back to treating the session id AS the family.

Step 3 is why nothing here raises. An unreachable roster must degrade to a
smaller, correct-by-default injection set (`session_id` as family, global memos
still included), never to a failed session start. Per contracts/injection-set.md
a SESSION_GUIDE failure is a WARN + empty forcible set, not a 500.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from memo import db

logger = logging.getLogger(__name__)

AGENTS_SCRIPT = Path.home() / "scripts" / "agents"
CACHE_TTL_S = 24 * 3600  # roster is refreshed daily per data-model.md

VALID_CONVENTIONS = ("standard", "agent-guide-md", "session-handoff-doc", "skill-based")

# Matches `session-name:path/to/guide.md` style entries inside a bash array.
_ENTRY = re.compile(r"""["']?([A-Za-z0-9][\w.-]*)["']?\s*[:=]\s*["']?([^"'\s]+)["']?""")


def _sync_cache_get(db_path: str, session_name: str) -> dict | None:
    conn = db._get_or_create_conn(db_path)
    row = conn.execute(
        "SELECT * FROM SESSION_GUIDE_cache WHERE session_name = ?", (session_name,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if time.time() - (d.get("fetched_at") or 0) > CACHE_TTL_S:
        return None          # stale; let a fresher source answer
    return d


def _sync_cache_put(db_path: str, session_name: str, guide_path: str,
                    convention: str) -> None:
    conn = db._get_or_create_conn(db_path)
    conn.execute(
        "INSERT INTO SESSION_GUIDE_cache (session_name, guide_path, guide_convention, fetched_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(session_name) DO UPDATE SET "
        "guide_path=excluded.guide_path, guide_convention=excluded.guide_convention, "
        "fetched_at=excluded.fetched_at",
        (session_name, guide_path, convention, time.time()),
    )
    conn.commit()


def parse_agents_script(text: str) -> dict[str, str]:
    """Extract `session -> guide path` pairs from the SESSION_GUIDE array.

    Deliberately loose. This parses someone else's bash script, which memo does
    not own and which will change shape without warning; a strict parser would
    turn a harmless upstream edit into a fleet-wide injection outage. Anything
    unrecognised is skipped.
    """
    out: dict[str, str] = {}
    in_array = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if "SESSION_GUIDE" in stripped and "(" in stripped:
            in_array = True
            continue
        if in_array:
            if stripped.startswith(")"):
                break
            if not stripped or stripped.startswith("#"):
                continue
            m = _ENTRY.search(stripped)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def _classify(guide_path: str) -> str:
    """Infer the guide convention from its path (Agent H's four shapes)."""
    p = (guide_path or "").lower()
    if "agent-guide" in p or p.endswith("agent-guide.md"):
        return "agent-guide-md"
    if "handoff" in p:
        return "session-handoff-doc"
    if "/skills/" in p or p.endswith(".skill.md"):
        return "skill-based"
    return "standard"


async def resolve_agent_family(session_id: str) -> tuple[str, str | None, str]:
    """Return `(agent_family, guide_path, convention)` for a session.

    Never raises. Worst case returns `(session_id, None, "standard")`, which
    yields a smaller but valid injection set rather than a failed session start.
    """
    import asyncio

    if not session_id:
        return ("unknown", None, "standard")

    try:
        cached = await asyncio.to_thread(_sync_cache_get, db.global_path(), session_id)
        if cached:
            return (session_id, cached.get("guide_path"),
                    cached.get("guide_convention") or "standard")
    except Exception:
        logger.exception("guides: cache read failed — continuing to roster")

    try:
        if AGENTS_SCRIPT.is_file():
            mapping = parse_agents_script(AGENTS_SCRIPT.read_text(errors="replace"))
            guide = mapping.get(session_id)
            if guide:
                convention = _classify(guide)
                try:
                    await asyncio.to_thread(_sync_cache_put, db.global_path(),
                                            session_id, guide, convention)
                except Exception:
                    logger.exception("guides: cache write failed — non-fatal")
                return (session_id, guide, convention)
    except Exception:
        logger.exception("guides: roster parse failed — falling back")

    logger.warning(
        "guides: no SESSION_GUIDE entry for %r — treating the session id as its "
        "agent-family; scoped memos for a differently-named family will not inject",
        session_id,
    )
    return (session_id, None, "standard")
