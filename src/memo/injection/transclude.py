"""Resolve `memo:<uuid>` references embedded in instruction files. [001/FR-016 001/FR-017]

A CLAUDE.md / guide / rule file can cite a memo by uuid instead of duplicating
its text. On InstructionsLoaded, memo scans the file contents and returns the
resolved bodies, so the instruction stays a single source of truth rather than a
copy that silently drifts from the memo it was pasted from.
"""
from __future__ import annotations

import logging
import re

from memo.repositories.documents import documents as documents_repo

logger = logging.getLogger(__name__)

# `memo:<uuid>` — optionally wrapped in backticks/brackets by the host document.
# The uuid shape is validated in the pattern itself so prose like "memo:later"
# never reaches the DB as a lookup.
_REF = re.compile(
    r"memo:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

# Hard cap per file. A pathological instruction file should not be able to turn
# one hook call into hundreds of DB reads on the session-start path.
MAX_REFS_PER_FILE = 25


def find_refs(text: str) -> list[str]:
    """Unique memo uuids referenced in `text`, in first-appearance order."""
    seen: list[str] = []
    for m in _REF.finditer(text or ""):
        uid = m.group(1).lower()
        if uid not in seen:
            seen.append(uid)
    return seen


async def resolve(text: str, *, source_file: str,
                  max_refs: int = MAX_REFS_PER_FILE) -> list[dict]:
    """Resolve every `memo:<uuid>` in `text`.

    Returns one entry per reference that resolved. Uses `get_current`, so a
    reference to a SUPERSEDED memo transparently yields the current version of
    that lineage — the whole point of citing by uuid rather than pasting text.

    Unresolvable references are skipped with a warning rather than raising: a
    stale uuid in someone's CLAUDE.md must not break session start.
    """
    refs = find_refs(text)
    if len(refs) > max_refs:
        logger.warning("transclude: %s has %d refs, capping at %d",
                       source_file, len(refs), max_refs)
        refs = refs[:max_refs]

    out: list[dict] = []
    for uid in refs:
        memo = await documents_repo.get_current(uid)
        if memo is None:
            logger.warning("transclude: %s references unknown memo %s", source_file, uid)
            continue
        out.append({
            "source_file": source_file,
            "referenced_uuid": uid,
            "resolved_id": memo["id"],
            "resolved_content": memo.get("content") or "",
            "title": memo.get("title"),
        })
    return out
