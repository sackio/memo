"""LLM provider family — selection by `MEMO_LLM_PROVIDER` (research.md R-17).

`get_llm_provider()` is the single construction point. Call sites take the
provider by dependency injection rather than importing a concrete adapter, so
Phase 5 can swap `null` -> `claude_session` without touching a mediator.
"""
from __future__ import annotations

import logging

from memo.config import settings
from memo.providers.llm.base import DEFAULT_TIMEOUT_S, LLMProvider
from memo.providers.llm.null import NullLLMProvider

logger = logging.getLogger(__name__)

__all__ = ["LLMProvider", "NullLLMProvider", "DEFAULT_TIMEOUT_S", "get_llm_provider"]

_instance: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Return the configured provider (memoized).

    Unknown names fall back to `null` with a warning rather than raising: a
    typo'd env var must not take the whole memo service down at import time —
    degraded inference is recoverable, a crash-looping container is not.
    """
    global _instance
    if _instance is not None:
        return _instance

    name = (settings.memo_llm_provider or "null").strip().lower()

    if name == "null":
        _instance = NullLLMProvider()
    elif name == "claude_session":
        # Lands in Phase 5 (T085a). Until then, fall back rather than crash.
        logger.warning(
            "MEMO_LLM_PROVIDER=claude_session is not implemented until Phase 5 "
            "(T085a) — falling back to the null provider; mediators will degrade."
        )
        _instance = NullLLMProvider()
    else:
        logger.warning(
            "unknown MEMO_LLM_PROVIDER=%r — falling back to null provider", name
        )
        _instance = NullLLMProvider()

    logger.info("LLM provider: %s", _instance.name)
    return _instance


def reset_llm_provider() -> None:
    """Drop the memoized instance. For tests that swap configuration."""
    global _instance
    _instance = None
