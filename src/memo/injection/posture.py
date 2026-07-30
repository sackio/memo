"""Per-session memory posture + injection opt-out detection. [001/FR-017 001/FR-018]

Two env vars on the CALLING session decide how much memo injects:

* ``CLAUDE_CODE_DISABLE_AUTO_MEMORY`` — the session's native MEMORY.md auto-load
  is off. memo does not turn itself off in response; per C71 it means the
  OPPOSITE — Layer 2 *is* the memory layer for that session, so the auditor
  treats it as a role-expanded case.
* ``MEMO_DISABLE_INJECTION`` — an explicit opt out of Layer 2 entirely. memo
  returns an empty set and the hook produces no additionalContext.

Read from ``/proc/<pid>/environ``, because there is no other way to see another
process's environment on Linux and the hook knows its own pid. Everything here
fails OPEN (posture "on", not opted out): a session whose environ we cannot read
should get memory, not silently lose it. The one thing that must never happen is
inferring "opted out" from a read failure.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DISABLE_AUTO_MEMORY = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"
DISABLE_INJECTION = "MEMO_DISABLE_INJECTION"

# Values that count as "set". Anything else (including "0", "false", "") is not.
_TRUTHY = {"1", "true", "yes", "on"}


def read_environ(pid: int | None) -> dict[str, str]:
    """Read a process's environment. Returns {} when unreadable.

    /proc/<pid>/environ is NUL-separated. Unreadable covers the ordinary cases —
    process already exited, different user, not Linux — none of which are errors
    worth raising into a hook that is trying to start a session.
    """
    if not pid:
        return {}
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except (OSError, ValueError) as e:
        logger.debug("posture: cannot read environ for pid %s: %s", pid, e)
        return {}
    env: dict[str, str] = {}
    for chunk in raw.split(b"\0"):
        if not chunk or b"=" not in chunk:
            continue
        k, _, v = chunk.partition(b"=")
        env[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return env


def _is_set(env: dict[str, str], key: str) -> bool:
    return env.get(key, "").strip().lower() in _TRUTHY


def memory_posture(pid: int | None = None,
                   env: dict[str, str] | None = None) -> str:
    """"on" | "off" — whether the session's NATIVE memory auto-load is active.

    "off" does NOT mean inject less. Per C71 it means memo's Layer 2 is now the
    only memory the session has, which if anything raises the stakes on getting
    the injection set right.
    """
    env = read_environ(pid) if env is None else env
    return "off" if _is_set(env, DISABLE_AUTO_MEMORY) else "on"


def injection_opted_out(pid: int | None = None,
                        env: dict[str, str] | None = None) -> bool:
    """True only on an explicit, readable MEMO_DISABLE_INJECTION.

    Fails OPEN: an unreadable environ yields False, so a session keeps its
    memory rather than losing it to a failed /proc read.
    """
    env = read_environ(pid) if env is None else env
    return _is_set(env, DISABLE_INJECTION)
