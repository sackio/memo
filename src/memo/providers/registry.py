"""Provider selection. [001/FR-045]

One construction point per family, chosen by env var:

    MEMO_CONDUCTOR_PROVIDER          atc | null
    MEMO_AGENT_CONTROLLER_PROVIDER   agents_supervisor | null
    MEMO_LLM_PROVIDER                claude_session | null   (see providers.llm)

Unknown names fall back to `null` with a warning rather than raising. A typo in
an env var must not crash-loop the container: degraded integrations are
recoverable, a service that will not boot is not.
"""
from __future__ import annotations

import logging

from memo.config import settings
from memo.providers.null import NullAgentController, NullConductor

logger = logging.getLogger(__name__)

_conductor = None
_agent_controller = None


def get_conductor():
    global _conductor
    if _conductor is not None:
        return _conductor
    name = (settings.memo_conductor_provider or "null").strip().lower()
    if name == "atc":
        from memo.providers.conductor.atc import ATCConductor
        _conductor = ATCConductor()
    else:
        if name != "null":
            logger.warning("unknown MEMO_CONDUCTOR_PROVIDER=%r — using null", name)
        _conductor = NullConductor()
    logger.info("conductor provider: %s", _conductor.name)
    return _conductor


def get_agent_controller():
    global _agent_controller
    if _agent_controller is not None:
        return _agent_controller
    name = (settings.memo_agent_controller_provider or "null").strip().lower()
    if name == "agents_supervisor":
        from memo.providers.agent_controller.agents_supervisor import (
            AgentsSupervisorController,
        )
        _agent_controller = AgentsSupervisorController()
    else:
        if name != "null":
            logger.warning("unknown MEMO_AGENT_CONTROLLER_PROVIDER=%r — using null", name)
        _agent_controller = NullAgentController()
    logger.info("agent controller provider: %s", _agent_controller.name)
    return _agent_controller


def reset() -> None:
    """Drop memoized providers. For tests."""
    global _conductor, _agent_controller
    _conductor = None
    _agent_controller = None
