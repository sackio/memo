"""Content-based liveness monitor. [001/FR-025]

Mirrors the stale-guide-detector pattern (C70): decide whether a watched thing
is alive by looking at whether its CONTENT is changing, not by whether a
process exists. A wedged Claude Code session is still a running process — pid
liveness would call it healthy while it sits on a permission prompt forever.

Used to police the shadow auditors (FR-024a) and to spot observed sessions that
have stopped producing.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from time import time

logger = logging.getLogger(__name__)

# Unchanged content for this long ⇒ treat as stalled.
DEFAULT_STALL_S = 30 * 60


@dataclass
class Watch:
    """One watched target's content fingerprint over time."""
    key: str
    digest: str | None = None
    last_change_at: float = field(default_factory=time)
    last_check_at: float = field(default_factory=time)
    checks: int = 0
    changes: int = 0


class LivenessMonitor:
    """Tracks content digests and reports stalls."""

    def __init__(self, stall_after_s: float = DEFAULT_STALL_S) -> None:
        self.stall_after_s = stall_after_s
        self._watches: dict[str, Watch] = {}

    def observe(self, key: str, content: str, *, now: float | None = None) -> Watch:
        """Record a sample. Digest, not raw content — a watch on a 40 MB
        transcript must cost bytes, not megabytes."""
        now = now if now is not None else time()
        digest = hashlib.sha256((content or "").encode("utf-8", "replace")).hexdigest()
        w = self._watches.get(key)
        if w is None:
            w = Watch(key=key, digest=digest, last_change_at=now, last_check_at=now)
            self._watches[key] = w
        else:
            w.last_check_at = now
            if digest != w.digest:
                w.digest = digest
                w.last_change_at = now
                w.changes += 1
        w.checks += 1
        return w

    def stalled(self, key: str, *, now: float | None = None) -> bool:
        now = now if now is not None else time()
        w = self._watches.get(key)
        if w is None:
            return False          # never observed is not the same as stalled
        return (now - w.last_change_at) >= self.stall_after_s

    def stalled_keys(self, *, now: float | None = None) -> list[str]:
        return [k for k in self._watches if self.stalled(k, now=now)]

    def report(self, key: str, *, now: float | None = None) -> dict | None:
        w = self._watches.get(key)
        if w is None:
            return None
        now = now if now is not None else time()
        return {"key": w.key, "stalled": self.stalled(key, now=now),
                "seconds_since_change": now - w.last_change_at,
                "checks": w.checks, "changes": w.changes}

    def forget(self, key: str) -> None:
        self._watches.pop(key, None)
