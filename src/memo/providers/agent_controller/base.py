"""AgentController interface. [001/FR-043 001/FR-046]

memo can REQUEST operator-level session control (spawn a shadow auditor,
respawn a bloated session, trigger a compact). It never performs these itself:
tmux-level execution belongs to the supervisor, and memo issuing kill/spawn
directly would put a knowledge store in charge of the fleet's process table.

Every method returns a result dict and never raises. These are requests about
OTHER processes — a refusal or an unreachable controller is an ordinary outcome
that the caller logs and moves past, not an exception.
"""
from __future__ import annotations

from typing import Any, Protocol


class AgentController(Protocol):
    name: str

    async def spawn(self, *, session_name: str, agent_family: str | None = ...,
                    guide_path: str | None = ..., atc_zones: list[str] | None = ...,
                    no_memory: bool = ...,
                    additional_args: list[str] | None = ...) -> dict[str, Any]: ...

    async def respawn(self, *, session_name: str, preserve_transcript: bool = ...,
                      reason: str = ...) -> dict[str, Any]: ...

    async def compact(self, *, session_name: str, reason: str = ...) -> dict[str, Any]: ...

    async def inject(self, *, session_name: str, content: str) -> dict[str, Any]: ...
