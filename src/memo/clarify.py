"""Clarification round-trip for the storage mediator. [001/FR-015d]

FR-015d requires the storage mediator to ask the CALLING AGENT a question —
synchronously, mid-write — when an incoming memo is ambiguous (missing class,
missing provenance, looks compound, or contradicts something already stored).

The transport is the HTTP response itself, not a side channel: the mediator
returns 409 with a `clarification_token`, and the agent retries its `POST
/store` carrying that token plus its answer. So "synchronous" here means
"resolved within the caller's own write flow", not "the server blocks on a
socket" — a server that blocked waiting for an agent to think would tie up a
worker for as long as the agent's own turn takes.

State lives in-process with a short TTL. That is deliberate:

* A pending clarification is worthless once stale — the corpus may have moved
  under it, so resolving a 20-minute-old token could apply a decision to a
  memo that has since been superseded. Expiry is a correctness property, not
  just cleanup.
* Persisting it would mean a durable table of half-finished writes to
  reconcile on restart. A dropped token just costs the agent one retry.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from time import time
from typing import Any

logger = logging.getLogger(__name__)

# Matches `expires_in: 300` in contracts/mediator-store.md.
DEFAULT_TTL_S = 300

# Backstop so a burst of ambiguous writes can't grow the map without bound if
# nobody ever retries. Oldest-first eviction.
MAX_PENDING = 1000


@dataclass
class PendingClarification:
    """One outstanding question to a calling agent."""
    token: str
    session_id: str
    prompt: str
    conflicting_memo_id: str | None
    request_snapshot: dict[str, Any]
    created_at: float
    ttl_s: float = DEFAULT_TTL_S
    rounds: int = 1

    @property
    def expires_at(self) -> float:
        return self.created_at + self.ttl_s

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time()) >= self.expires_at


_pending: dict[str, PendingClarification] = {}


def _evict_expired(now: float | None = None) -> int:
    now = now if now is not None else time()
    dead = [t for t, p in _pending.items() if p.expired(now)]
    for t in dead:
        del _pending[t]
    return len(dead)


def open_clarification(*, session_id: str, prompt: str,
                       request_snapshot: dict[str, Any],
                       conflicting_memo_id: str | None = None,
                       ttl_s: float = DEFAULT_TTL_S,
                       rounds: int = 1) -> PendingClarification:
    """Register a question and return its token.

    `request_snapshot` is the original store request. It is kept so the retry
    can be resolved against what was ORIGINALLY asked rather than trusting the
    agent to resend it faithfully — otherwise an agent could answer the
    clarification while quietly substituting different content.
    """
    _evict_expired()
    if len(_pending) >= MAX_PENDING:
        oldest = min(_pending.values(), key=lambda p: p.created_at)
        del _pending[oldest.token]
        logger.warning(
            "clarification table full (%d) — evicted oldest token for session %s",
            MAX_PENDING, oldest.session_id,
        )

    pending = PendingClarification(
        token=f"clr-{secrets.token_urlsafe(12)}",
        session_id=session_id,
        prompt=prompt,
        conflicting_memo_id=conflicting_memo_id,
        request_snapshot=request_snapshot,
        created_at=time(),
        ttl_s=ttl_s,
        rounds=rounds,
    )
    _pending[pending.token] = pending
    return pending


def resolve(token: str, *, session_id: str) -> PendingClarification | None:
    """Consume a token. Returns None if unknown, expired, or wrong session.

    Single-use: the entry is removed on a successful lookup so one answer
    cannot drive two writes.

    The `session_id` check matters — a token is an authorization to complete a
    specific pending write, and without it any agent that learned a token could
    resolve another agent's clarification, including one gated on operator
    authority.
    """
    _evict_expired()
    pending = _pending.get(token)
    if pending is None:
        return None
    if pending.session_id != session_id:
        logger.warning(
            "clarification token %s presented by session %r but belongs to %r — refusing",
            token, session_id, pending.session_id,
        )
        return None
    del _pending[token]
    return pending


def peek(token: str) -> PendingClarification | None:
    """Look at a token without consuming it. For tests/diagnostics."""
    _evict_expired()
    return _pending.get(token)


def pending_count() -> int:
    _evict_expired()
    return len(_pending)


def clear() -> None:
    """Drop all pending clarifications. Test helper."""
    _pending.clear()
