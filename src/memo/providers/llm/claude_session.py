"""LLM provider backed by an INTERACTIVE Claude Code session. [001/FR-045]

The concrete adapter for research.md **R-17**, and the reason the whole
`LLMProvider` abstraction exists. Inference is served by a standing interactive
session (`memo-llm`) reached over ATC, riding the Claude Max subscription memo's
operator already pays for.

**`claude -p` is PROHIBITED here and must never be reintroduced.** Shelling out
to `claude -p` would run the prompt as programmatic API usage and be billed
separately — precisely the cost this design avoids (operator directive
2026-07-29; same reasoning that moved memo-minder off its `claude -p` cron on
2026-07-15). If a future maintainer finds this adapter slow and reaches for
`-p`, that is a regression, not an optimization.

There is also deliberately **no paid-API fallback**. An unavailable session
degrades (below); it must never quietly escalate to billing.

Three properties this adapter must hold, all of them about not making an
outage worse:

1. **Never raise.** `complete()` returns None. Callers are built around the
   degrade path; an exception here would turn a recall into a 500.
2. **Bounded wait.** A soft timeout (default 10s) — a wedged session must not
   hold a memo request open indefinitely.
3. **One escalation per outage.** On failure the adapter DMs the `agents`
   supervisor to respawn `memo-llm`, but rate-limited to a single notify per
   outage. Per-call notification with fleet-wide memo traffic behind it would
   fan out hundreds of DMs and wedge the supervisor — the thundering-herd
   failure the operator's CLAUDE.md warns about.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from time import time

import httpx

from memo.config import settings
from memo.providers.llm.base import DEFAULT_TIMEOUT_S

logger = logging.getLogger(__name__)

# Once a session has been down this long without recovering, allow one more
# supervisor notify. Bounds the "notify once" rule so a permanently dead
# session is re-reported occasionally rather than exactly once ever.
RENOTIFY_AFTER_S = 30 * 60


class ClaudeSessionLLMProvider:
    """Serves inference from the interactive `memo-llm` session over ATC."""

    name = "claude_session"

    def __init__(self, *, atc_url: str | None = None,
                 target_session: str | None = None,
                 supervisor: str | None = None,
                 self_id: str | None = None) -> None:
        self.atc_url = (atc_url or os.environ.get("MEMO_ATC_URL")
                        or "http://localhost:3030").rstrip("/")
        self.target_session = (target_session
                               or os.environ.get("MEMO_LLM_SESSION") or "memo-llm")
        self.supervisor = supervisor or os.environ.get("MEMO_SUPERVISOR_SESSION") or "agents"
        self.self_id = self_id or os.environ.get("MEMO_ATC_SESSION") or "memo"

        # Outage state. `_outage_since` is None when healthy; the pair is what
        # makes escalation once-per-outage rather than once-per-failure.
        self._outage_since: float | None = None
        self._last_notified_at: float | None = None
        self._lock = asyncio.Lock()

    # --- availability bookkeeping ---

    async def _on_success(self) -> None:
        async with self._lock:
            if self._outage_since is not None:
                logger.info("memo-llm recovered after %.0fs outage",
                            time() - self._outage_since)
            self._outage_since = None
            self._last_notified_at = None

    async def _on_failure(self, reason: str) -> None:
        """Record the failure and escalate at most once per outage."""
        async with self._lock:
            now = time()
            first = self._outage_since is None
            if first:
                self._outage_since = now
            due = (self._last_notified_at is None
                   or now - self._last_notified_at >= RENOTIFY_AFTER_S)
            if not due:
                return
            self._last_notified_at = now
            outage_for = now - (self._outage_since or now)

        # Outside the lock: the notify is best-effort and must not serialize
        # every concurrent caller behind an HTTP round-trip.
        await self._notify_supervisor(reason, outage_for)

    async def _notify_supervisor(self, reason: str, outage_for: float) -> None:
        """Ask the `agents` supervisor to respawn memo-llm. Best-effort."""
        body = (
            f"memo: LLM session `{self.target_session}` is unavailable "
            f"({reason}). memo is DEGRADING — recall answers are search-only and "
            f"stores are written unreconciled — but no requests are failing. "
            f"Please respawn `{self.target_session}`.\n"
            f"Outage duration so far: {outage_for:.0f}s. "
            f"This notice is rate-limited to one per outage "
            f"(re-sent at most every {RENOTIFY_AFTER_S // 60} min)."
        )
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{self.atc_url}/messages",
                    json={"to": self.supervisor, "from": self.self_id,
                          "content": body, "priority": "warning"},
                )
            logger.warning("memo-llm unavailable (%s) — notified %s",
                           reason, self.supervisor)
        except Exception as e:
            # If ATC is down too there is nobody to tell. Log and carry on;
            # this must never propagate into a memo request.
            logger.warning("memo-llm unavailable (%s); supervisor notify "
                           "also failed: %s", reason, e)

    # --- LLMProvider interface ---

    async def complete(self, prompt: str, *, budget_tokens: int = 512,
                       timeout_s: float = DEFAULT_TIMEOUT_S) -> str | None:
        """Ask `memo-llm` to complete a prompt. None on any failure.

        Uses ATC's synchronous ask so the reply comes back on the same request
        rather than as an out-of-band DM we would have to correlate ourselves.
        """
        correlation = str(uuid.uuid4())
        payload = {
            "to": self.target_session,
            "from": self.self_id,
            "content": prompt,
            "correlation_id": correlation,
            "timeout_seconds": timeout_s,
            "max_tokens": budget_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_s + 2.0) as client:
                resp = await client.post(f"{self.atc_url}/ask", json=payload)
            if resp.status_code != 200:
                await self._on_failure(f"HTTP {resp.status_code}")
                return None
            data = resp.json()
        except (httpx.TimeoutException, asyncio.TimeoutError):
            await self._on_failure(f"timeout after {timeout_s}s")
            return None
        except Exception as e:
            await self._on_failure(f"{type(e).__name__}: {e}")
            return None

        text = (data or {}).get("reply") or (data or {}).get("content")
        if not text:
            # A 200 with no reply means the session did not answer — an outage
            # in substance even though the transport worked.
            await self._on_failure("empty reply")
            return None

        await self._on_success()
        return text

    async def available(self) -> bool:
        """Advisory liveness probe against the ATC session registry."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.atc_url}/sessions")
            if resp.status_code != 200:
                return False
            sessions = resp.json()
            names = {s.get("session_id") or s.get("name")
                     for s in (sessions if isinstance(sessions, list)
                               else sessions.get("sessions", []))}
            return self.target_session in names
        except Exception:
            return False
