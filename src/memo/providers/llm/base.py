"""LLM provider interface for the mediators. [001/FR-015]

Per research.md **R-17**, the mediators' generative calls are served by an
**interactive Claude Code session** (`memo-llm`) riding the existing Claude Max
subscription — never a per-token inference API, and never `claude -p`, which is
billed as API usage and is precisely the cost this design avoids.

The single most important thing about this interface:

    complete() RETURNS None ON UNAVAILABILITY. IT DOES NOT RAISE.

That is not defensive style, it is the contract. An LLM behind an interactive
session is *expected* to be unavailable sometimes — it compacts, it gets busy,
it can be wedged or dead. Both mediators must degrade rather than fail the
caller:

  * recall  -> return the search-only answer plus an `anomalies` entry
  * store   -> write-new and flag the auditor

Losing an agent's memo is strictly worse than deferring a merge decision, so a
missing LLM must never become a 4xx/5xx. Making None the normal return keeps
every call site on the degrade path by construction instead of relying on each
one remembering a try/except.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


# Default soft timeout for a session round-trip (R-17). Milliseconds are the
# wrong unit here: the transport is an ATC round-trip to an interactive
# session, so seconds is the honest scale.
DEFAULT_TIMEOUT_S = 10.0


@runtime_checkable
class LLMProvider(Protocol):
    """What the mediators require of an inference backend.

    Implementations live in this package: `null.NullLLMProvider` (always
    unavailable, the Phase 3 default) and — from Phase 5 —
    `claude_session.ClaudeSessionLLMProvider`.
    """

    name: str

    async def complete(
        self,
        prompt: str,
        *,
        budget_tokens: int = 512,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> str | None:
        """Return the completion text, or None if inference was unavailable.

        MUST NOT raise for unavailability, timeout, a down session, or a
        transport error — return None. Raising is reserved for genuine
        programming errors (bad argument types), which are bugs, not
        conditions.
        """
        ...

    async def available(self) -> bool:
        """Cheap liveness probe. Advisory only.

        Callers must still handle a None from `complete()`: the session can die
        between the probe and the call, so a True here is never a guarantee.
        """
        ...
