"""AgentController backed by the `agents` supervisor. [001/FR-043]"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TIMEOUT_S = 15.0


class AgentsSupervisorController:
    name = "agents_supervisor"

    def __init__(self, url: str | None = None) -> None:
        self.url = (url or os.environ.get("MEMO_AGENT_CONTROLLER_URL")
                    or "http://localhost:3030/agents").rstrip("/")

    async def _post(self, op: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST one operation. Never raises — returns {ok: False, error} instead."""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.post(f"{self.url}/{op}", json=body)
            if r.status_code >= 300:
                logger.warning("agent-controller %s -> HTTP %s", op, r.status_code)
                return {"ok": False, "error": f"HTTP {r.status_code}", "op": op}
            return {"ok": True, "op": op, **(r.json() if r.content else {})}
        except Exception as e:
            logger.warning("agent-controller %s failed: %s", op, e)
            return {"ok": False, "error": str(e), "op": op}

    async def spawn(self, *, session_name: str, agent_family: str | None = None,
                    guide_path: str | None = None, atc_zones: list[str] | None = None,
                    no_memory: bool = False,
                    additional_args: list[str] | None = None) -> dict[str, Any]:
        return await self._post("spawn", {
            "session_name": session_name, "agent_family": agent_family,
            "guide_path": guide_path, "atc_zones": atc_zones or [],
            "no_memory": no_memory, "additional_args": additional_args or [],
        })

    async def respawn(self, *, session_name: str, preserve_transcript: bool = False,
                      reason: str = "") -> dict[str, Any]:
        return await self._post("respawn", {
            "session_name": session_name,
            "preserve_transcript": preserve_transcript, "reason": reason,
        })

    async def compact(self, *, session_name: str, reason: str = "") -> dict[str, Any]:
        return await self._post("compact", {"session_name": session_name,
                                            "reason": reason})

    async def inject(self, *, session_name: str, content: str) -> dict[str, Any]:
        return await self._post("inject", {"session_name": session_name,
                                           "content": content})
