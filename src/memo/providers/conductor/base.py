"""Conductor interface — memo emits events outward. [001/FR-041 001/FR-042 001/FR-042a 001/FR-046]

memo tells the outside world when something notable happens (a memo stored, a
supersession, a mediator anomaly). ATC is today's transport; the interface
exists so it is not the only possible one (FR-046).

**Emission MUST NOT block the hot path.** `emit()` enqueues and returns; a
background worker delivers with retry. A mediator response must never wait on a
message bus, and an unreachable Conductor must never turn a successful store
into a failed request. That is the whole reason this is a queue and not a call.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from time import time
from typing import Any, Protocol

# FR-041 trigger -> event kinds, per contracts/conductor-push.md.
EVENT_KINDS = (
    "memo.stored", "memo.superseded", "mediator.anomaly",
    "auditor.recommendation", "injection.updated",
    "time_scope.enter", "time_scope.exit",
)


@dataclass
class Event:
    """The generic wrapper every Conductor event uses."""
    event_kind: str
    payload: dict[str, Any]
    target: str = "auto"
    priority: str = "info"                 # info | warning | critical
    delivery_mode: str = "message"         # message | beacon | board-post
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_time: float = field(default_factory=time)
    source: str = "memo"

    def to_wire(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_kind": self.event_kind,
            "event_time": self.event_time,
            "source": self.source,
            "payload": self.payload,
            "delivery_hints": {
                "target": self.target,
                "priority": self.priority,
                "delivery_mode": self.delivery_mode,
            },
        }


class Conductor(Protocol):
    """What memo needs of an event transport."""

    name: str

    async def emit(self, event: Event) -> bool:
        """Enqueue an event. Returns False if it could not even be queued.

        MUST NOT raise and MUST NOT block on network I/O — the caller is
        usually finishing a mediator request.
        """
        ...

    async def start(self) -> None:
        """Begin background delivery."""
        ...

    async def stop(self) -> None:
        """Drain/stop background delivery."""
        ...
