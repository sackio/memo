"""Null LLM provider — always unavailable, by design.

Two jobs:

1. **Phase 3 default.** The mediators are built and fully tested against this
   before the `claude_session` transport exists (R-17 sequencing). With this
   adapter installed, every LLM-fallback path in the contract tests exercises
   the DEGRADED branch — which is specified behavior, not a stub.
2. **Standalone mode.** memo running without the fleet around it (FR-045) has
   no `memo-llm` session to talk to and must still serve CRUD + mediated
   reads/writes.

It reports unavailable rather than returning canned text on purpose: a fake
completion would let a mediator believe it had reasoned about a conflict when
it had not, silently corrupting merge/supersede decisions. Unavailable is
honest, and the degrade path is well-defined.
"""
from __future__ import annotations

import logging

from memo.providers.llm.base import DEFAULT_TIMEOUT_S

logger = logging.getLogger(__name__)


class NullLLMProvider:
    """Reports unavailable for every request."""

    name = "null"

    def __init__(self) -> None:
        # Sampled, not per-call: with fleet-wide traffic a line per call would
        # bury the log for a condition that is expected and already visible in
        # each response's `anomalies`.
        self._warned = False

    async def complete(
        self,
        prompt: str,
        *,
        budget_tokens: int = 512,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> str | None:
        if not self._warned:
            self._warned = True
            logger.info(
                "MEMO_LLM_PROVIDER=null — LLM inference disabled; mediators "
                "will take their degrade path (search-only recall, "
                "write-new+auditor-flag on store). This is expected in Phase 3 "
                "and in standalone mode."
            )
        return None

    async def available(self) -> bool:
        return False
