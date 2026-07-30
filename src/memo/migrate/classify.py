"""v1 → v2 classification + retagging rules. [001/FR-039]

The per-class backfill table from contracts/migration-cli.md, as pure
functions. Kept deterministic and LLM-free by default: a migration that
classifies the same corpus differently on each run cannot be verified, and
7,000+ memos through an LLM is both slow and unrepeatable. `--llm-classify` is
opt-in for the ambiguous remainder only.

The fallback is always `legacy-unattributed`, never a guess. A memo
misclassified as `constitutional` would be force-injected into every session on
the fleet; a memo parked in `legacy-unattributed` merely waits for a human.
That asymmetry decides every judgement call in this module.
"""
from __future__ import annotations

import re
from typing import Any

# --- C44 canonical tag vocabulary ---
CANONICAL_TAGS = {
    "hard-rule": "behavioral-rule",
    "ben-hard-rule": "behavioral-rule",
    "behavioural-rule": "behavioral-rule",
    "operator-rule": "behavioral-rule",
    "operator-coaching": "behavioral-rule",
    "antipattern": "anti-pattern",
}

# --- tag sets per target class (contracts/migration-cli.md) ---
CONSTITUTIONAL_TAGS = {"constitution", "constitutional"}
OPERATOR_AUTHORITY_TAGS = {"hard-rule", "ben-hard-rule"}
BEHAVIORAL_TAGS = {"behavioral-rule", "operator-coaching", "anti-pattern",
                   "behavioural-rule", "operator-rule"}
GOAL_TAGS = {"goal", "mission", "done-line"}
VERBATIM_TAGS = {"verbatim-critical", "pinned"}
DECISION_TAGS = {"decision", "wip", "in-flight"}
EPISODIC_TAGS = {"session-log", "incident", "event", "session-sourced"}
TIME_SCOPED_TAGS = {"parking", "travel", "appointment", "booking"}

# Content patterns. Anchored to imperative phrasing rather than keywords —
# "don't cache the token" is a rule; "we don't cache tokens" is a fact.
_PROHIBITION = re.compile(r"\b(don'?t|do not|never|avoid|must not)\b", re.I)
_GOAL_LANG = re.compile(r"\b(we want|target|goal is|aim to|ship)\b", re.I)
_OPERATOR_AUTHORITY = re.compile(
    r"\b(ben (says|wants|requires|directed)|operator (says|requires)|"
    r"always|must|never)\b", re.I)
_FULL_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)

DECISION_RECENCY_S = 30 * 24 * 3600


def canonicalize_tags(tags: list[str]) -> list[str]:
    """Apply the C44 canonical vocabulary, preserving order and de-duping."""
    out: list[str] = []
    for t in tags or []:
        c = CANONICAL_TAGS.get((t or "").strip().lower(), t)
        if c and c not in out:
            out.append(c)
    return out


def classify(memo: dict[str, Any], *, now: float) -> tuple[str, str]:
    """Return `(class, class_source)` for a v1 memo.

    Tag heuristics first (deterministic), then narrow content patterns, then
    `legacy-unattributed`. Order matters: the earlier a rule sits, the more
    force-injected its outcome, so the most consequential classes require the
    most explicit evidence.
    """
    tags = {(t or "").strip().lower() for t in (memo.get("tags") or [])}
    content = memo.get("content") or ""
    created = memo.get("created_at") or 0.0

    # constitutional — the highest-consequence class, so it needs an explicit
    # tag, or an operator-authority tag WITH matching content.
    if tags & CONSTITUTIONAL_TAGS:
        return ("constitutional", "tag-heuristic")
    if (tags & OPERATOR_AUTHORITY_TAGS) and _OPERATOR_AUTHORITY.search(content):
        return ("constitutional", "tag-heuristic")

    if tags & VERBATIM_TAGS:
        return ("verbatim-critical", "tag-heuristic")
    # Full UUIDs plus hard-constraint language is the verbatim-critical
    # signature: content whose exact wording matters (a UUID summarized is a
    # UUID destroyed).
    if _FULL_UUID.search(content) and _PROHIBITION.search(content):
        return ("verbatim-critical", "content-heuristic")

    if tags & BEHAVIORAL_TAGS:
        return ("behavioral", "tag-heuristic")
    if tags & OPERATOR_AUTHORITY_TAGS:
        return ("behavioral", "tag-heuristic")
    if _PROHIBITION.search(content) and len(content) < 600:
        return ("behavioral", "content-heuristic")

    if tags & GOAL_TAGS:
        return ("goal", "tag-heuristic")
    if _GOAL_LANG.search(content) and len(content) < 400:
        return ("goal", "content-heuristic")

    if tags & DECISION_TAGS and (now - created) <= DECISION_RECENCY_S:
        return ("decision-in-progress", "tag-heuristic")

    if tags & TIME_SCOPED_TAGS:
        # Without an extractable window a time-scoped memo would never inject
        # and never expire — worse than calling it a fact. The contract says
        # fall back to `fact`; the LLM path may promote it later.
        return ("fact", "tag-heuristic-timescoped-fallback")

    if tags & EPISODIC_TAGS:
        return ("episodic", "tag-heuristic")

    if tags:
        return ("fact", "tag-heuristic-default")
    return ("legacy-unattributed", "legacy-unattributed")


def injection_mode_for(cls: str, scope: list[str]) -> str:
    """Per-class injection mode from the backfill table."""
    if cls in ("constitutional", "verbatim-critical"):
        return "forcible-constitutional"
    if cls == "behavioral":
        return ("forcible-constitutional" if "global" in (scope or ["global"])
                else "forcible-current-focus")
    if cls == "goal":
        return "forcible-current-focus"
    return "on-recall"


def reconstruct_provenance(memo: dict[str, Any]) -> tuple[dict | None, str | None]:
    """Best-effort provenance from v1 tags/metadata. Returns `(prov, source)`.

    Only claims provenance it can actually point at. An invented provenance
    block is worse than none: `legacy-unattributed` is honest about not
    knowing, whereas a fabricated one would make an unsourced memo look
    verified.
    """
    tags = {(t or "").strip().lower() for t in (memo.get("tags") or [])}
    meta = memo.get("metadata") or {}
    content = memo.get("content") or ""

    if "gmail-sourced" in tags:
        m = re.search(r"\b(?:msg[-_]?id|message[-_]?id)[:=\s]+([\w.-]+)", content, re.I)
        msg_id = meta.get("gmail_msg_id") or (m.group(1) if m else None)
        if msg_id:
            return ({"gmail_msg_id": msg_id}, "gmail-sourced-tag-inference")

    if "session-sourced" in tags or any(t.startswith("session-") for t in tags):
        uid = meta.get("session_uuid")
        if not uid:
            m = _FULL_UUID.search(content)
            uid = m.group(0) if m else None
        if not uid:
            for t in tags:
                if t.startswith("session-") and len(t) > 8:
                    uid = t[len("session-"):]
                    break
        if uid:
            return ({"claude_log_ref": {
                "host": meta.get("source_host") or "unknown",
                "project_dir": meta.get("project_dir") or "unknown",
                "session_uuid": uid, "line_range_start": 0, "line_range_end": 0,
            }}, "session-sourced-tag-inference")

    if meta.get("url"):
        return ({"url": meta["url"]}, "metadata-url")

    return (None, None)
