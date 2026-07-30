"""Null providers — standalone mode. [001/FR-045]

memo running without the fleet around it: no ATC, no supervisor. FR-045
requires that CRUD and both mediators still work, with integration features
WARN-logging what they *would* have done.

The WARN is the point. A silent no-op would make a misconfigured deployment
look healthy while every notification vanished; the log line is what lets
someone notice that `MEMO_CONDUCTOR_PROVIDER` was never set.
"""
from __future__ import annotations

import logging
from typing import Any

from memo.providers.conductor.base import Event

logger = logging.getLogger(__name__)


class NullConductor:
    """Logs and drops. No queue, no retry, no dead-letter."""

    name = "null"

    def __init__(self) -> None:
        self.emitted: list[Event] = []      # inspectable by tests
        self.delivered = 0
        self.dropped = 0

    async def emit(self, event: Event) -> bool:
        self.emitted.append(event)
        self.dropped += 1
        logger.warning(
            "conductor=null — would have emitted %s to %r (%s): %s",
            event.event_kind, event.target, event.delivery_mode, event.payload,
        )
        return True

    async def start(self) -> None:
        logger.info("conductor=null — standalone mode; events are logged and dropped")

    async def stop(self) -> None:
        return None


class NullAgentController:
    """Refuses every operation, loudly."""

    name = "null"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def _refuse(self, op: str, **kw) -> dict[str, Any]:
        self.requests.append({"op": op, **kw})
        logger.warning("agent-controller=null — would have requested %s: %s", op, kw)
        return {"ok": False, "op": op, "error": "agent controller disabled (null provider)",
                "would_have": kw}

    async def spawn(self, **kw) -> dict[str, Any]:
        return await self._refuse("spawn", **kw)

    async def respawn(self, **kw) -> dict[str, Any]:
        return await self._refuse("respawn", **kw)

    async def compact(self, **kw) -> dict[str, Any]:
        return await self._refuse("compact", **kw)

    async def inject(self, **kw) -> dict[str, Any]:
        return await self._refuse("inject", **kw)
