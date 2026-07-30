"""Shadow auditor — per-session observer. [001/FR-021 001/FR-022]

One shadow per running session (FR-021), watching transcript growth, memo query
patterns, and operator signals. FR-022 gives it real autonomy: write memos,
modify NON-constitutional ones, trigger `/compact` on its observed session, and
re-inject `verbatim-critical` memos.

The boundaries are what make that autonomy safe, and each is enforced here
rather than left to good behavior:

* **Constitutional memos are read-only to it** (FR-023 / Principle V). It files
  a proposal instead. `modify_memo()` refuses outright.
* **Compaction is idle-gated** (FR-037). Compacting a session mid-turn destroys
  the work in flight, so a bloat threshold alone is never sufficient.
* **Every action is logged with a rationale** (FR-025) before it takes effect,
  so a bad decision is reviewable rather than merely observable in its effects.

This class is the decision logic and is deliberately transport-free — it is
handed observations and returns/performs decisions. That keeps it testable
without spawning a session or standing up ATC.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import time
from typing import Any

from memo.auditor import actions, proposals
from memo.auditor.liveness import LivenessMonitor
from memo.repositories.documents import documents as documents_repo

logger = logging.getLogger(__name__)

# C-10 composite bloat thresholds. ALL must trip — any one alone produces
# false positives (a long healthy session, a big one-off paste).
BLOAT_TRANSCRIPT_BYTES = 2_500_000
BLOAT_CACHE_READ_PER_DAY = 20_000_000
BLOAT_TURNS = 120

# Transcript size at which the Bun --resume segfault becomes likely.
RESPAWN_TRANSCRIPT_BYTES = 6_000_000

# Operator phrasings that read as frustration with the agent's behavior. Used
# as a TRIAGE signal for whether a pattern is worth proposing a rule about —
# never as an action trigger on its own.
FRUSTRATION_MARKERS = (
    "i already told you", "again", "stop doing", "why did you",
    "you should have", "don't do that", "i asked you to", "as i said",
)


@dataclass
class Observation:
    """One sample of an observed session's state."""
    session_id: str
    transcript_bytes: int = 0
    turns: int = 0
    cache_read_tokens_today: int = 0
    idle_seconds: float = 0.0
    recent_operator_messages: list[str] = field(default_factory=list)
    transcript_tail: str = ""
    at: float = field(default_factory=time)


class ShadowAuditor:
    """Observes one session and decides what, if anything, to do about it."""

    def __init__(self, *, session_id: str, agent_family: str | None = None,
                 auditor_id: str | None = None,
                 agent_controller=None, idle_threshold_s: float = 120.0) -> None:
        self.session_id = session_id
        self.agent_family = agent_family or session_id
        self.auditor_id = auditor_id or f"auditor-shadow:{session_id}"
        self.idle_threshold_s = idle_threshold_s
        self._controller = agent_controller
        self.liveness = LivenessMonitor()
        self.frustration_hits: list[dict[str, Any]] = []

    # --- controller is resolved lazily so tests can omit it ---
    @property
    def controller(self):
        if self._controller is None:
            from memo.providers.registry import get_agent_controller
            self._controller = get_agent_controller()
        return self._controller

    # --- FR-022 assessments ---

    def is_bloated(self, obs: Observation) -> bool:
        """C-10 composite. ALL three, deliberately."""
        return (obs.transcript_bytes >= BLOAT_TRANSCRIPT_BYTES
                and obs.cache_read_tokens_today >= BLOAT_CACHE_READ_PER_DAY
                and obs.turns >= BLOAT_TURNS)

    def is_idle(self, obs: Observation) -> bool:
        return obs.idle_seconds >= self.idle_threshold_s

    def needs_respawn(self, obs: Observation) -> bool:
        """Past the transcript size where `--resume` segfaults on Bun."""
        return obs.transcript_bytes >= RESPAWN_TRANSCRIPT_BYTES

    def detect_frustration(self, obs: Observation) -> list[str]:
        """Operator phrasings suggesting a repeated mistake.

        Triage only. Being told "again" is evidence a rule might be missing,
        never on its own grounds to change one.
        """
        hits = []
        for msg in obs.recent_operator_messages:
            low = (msg or "").lower()
            for marker in FRUSTRATION_MARKERS:
                if marker in low:
                    hits.append(msg)
                    break
        return hits

    # --- FR-022 actions ---

    async def maybe_compact(self, obs: Observation) -> dict[str, Any]:
        """Trigger /compact only when bloated AND idle. [001/FR-022 001/FR-037]

        The idle gate is not politeness — compacting mid-turn discards the work
        in flight. A bloated busy session is left alone until it goes quiet.
        """
        if not self.is_bloated(obs):
            return {"compacted": False, "reason": "not bloated"}
        if not self.is_idle(obs):
            return {"compacted": False, "reason": "bloated but not idle — "
                                                  "compacting mid-turn would "
                                                  "discard in-flight work"}
        rationale = (f"composite bloat: transcript={obs.transcript_bytes}B, "
                     f"turns={obs.turns}, cache_read={obs.cache_read_tokens_today}; "
                     f"idle {obs.idle_seconds:.0f}s")
        await actions.record(action="compact", auditor_id=self.auditor_id,
                             target=self.session_id, rationale=rationale,
                             observed_session=self.session_id)
        result = await self.controller.compact(session_name=self.session_id,
                                               reason=rationale)
        return {"compacted": bool(result.get("ok")), "reason": rationale,
                "controller": result}

    async def maybe_respawn(self, obs: Observation) -> dict[str, Any]:
        if not self.needs_respawn(obs):
            return {"respawned": False, "reason": "under the segfault threshold"}
        if not self.is_idle(obs):
            return {"respawned": False, "reason": "oversized but not idle"}
        rationale = (f"transcript {obs.transcript_bytes}B exceeds the "
                     f"{RESPAWN_TRANSCRIPT_BYTES}B Bun --resume segfault threshold")
        await actions.record(action="respawn", auditor_id=self.auditor_id,
                             target=self.session_id, rationale=rationale,
                             observed_session=self.session_id)
        result = await self.controller.respawn(session_name=self.session_id,
                                               preserve_transcript=False,
                                               reason=rationale)
        return {"respawned": bool(result.get("ok")), "reason": rationale,
                "controller": result}

    async def modify_memo(self, memo_id: str, *, content: str,
                          rationale: str) -> dict[str, Any]:
        """Modify a NON-constitutional memo. [001/FR-022 001/FR-023]

        Refusing on constitutional is the enforcement point for Principle V: an
        auditor that could edit constitutional memos could silently rewrite the
        standing rules force-injected into every session on the fleet.
        """
        memo = await documents_repo.get_current(memo_id)
        if memo is None:
            return {"ok": False, "error": f"no current memo {memo_id}"}
        if memo.get("class") == "constitutional":
            await actions.record(
                action="modify", auditor_id=self.auditor_id, target=memo_id,
                rationale=f"REFUSED: {rationale}",
                observed_session=self.session_id,
                details={"refused": "constitutional memos are operator-owned"},
            )
            return {"ok": False, "error": "constitutional memos are operator-owned "
                                          "(Principle V) — file a proposal instead",
                    "use": "POST /constitution/propose"}

        from memo import db, embeddings
        embedding = await embeddings.embed(content)
        await db.update(db_path=None, doc_id=memo_id, content=content, title=None,
                        tags=None, metadata=None, embedding=embedding)
        await actions.record(action="modify", auditor_id=self.auditor_id,
                             target=memo_id, rationale=rationale,
                             observed_session=self.session_id)
        return {"ok": True, "memo_id": memo_id}

    async def propose_rule(self, *, content: str, evidence: dict[str, Any],
                           layer: str = "constitutional",
                           urgency: str = "medium") -> dict[str, Any]:
        """File a constitutional proposal. [001/FR-023]"""
        result = await proposals.propose(
            proposed_by=self.auditor_id, layer=layer, proposed_content=content,
            scope=[f"agent-family:{self.agent_family}"],
            proposed_tags=["auditor-proposed"], evidence=evidence, urgency=urgency,
        )
        await actions.record(action="propose", auditor_id=self.auditor_id,
                             target=str(result["proposal_id"]),
                             rationale=content[:200],
                             observed_session=self.session_id, details=evidence)
        return result

    # --- the observe loop's single step ---

    async def step(self, obs: Observation) -> dict[str, Any]:
        """Process one observation and act. Returns what it decided and why."""
        self.liveness.observe(f"session:{obs.session_id}", obs.transcript_tail,
                              now=obs.at)
        frustration = self.detect_frustration(obs)
        for msg in frustration:
            self.frustration_hits.append({"at": obs.at, "message": msg})

        outcome: dict[str, Any] = {
            "session_id": obs.session_id,
            "bloated": self.is_bloated(obs),
            "idle": self.is_idle(obs),
            "frustration_signals": len(frustration),
            "stalled": self.liveness.stalled(f"session:{obs.session_id}", now=obs.at),
        }
        if self.needs_respawn(obs):
            outcome["respawn"] = await self.maybe_respawn(obs)
        elif self.is_bloated(obs):
            outcome["compact"] = await self.maybe_compact(obs)
        return outcome
