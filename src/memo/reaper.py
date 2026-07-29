"""TTL reaper — background sweep of expired memos. [001/FR-007]

FR-007 gives memos an ``expires_at`` and requires auto-reap for the classes
that need it (primarily ``ephemeral-flush``). This module is the sweeper: every
``memo_reaper_interval_seconds`` (default 300 = the 5-minute cadence the spec
asks for) it deletes every row whose ``expires_at`` has passed, embeddings
included.

Started from the FastAPI lifespan; see ``main.lifespan``. The whole module is
FR-007, hence the module-level marker.
"""
from __future__ import annotations

import asyncio
import logging

from memo import db
from memo.config import settings

logger = logging.getLogger(__name__)

# Set by start(); module-level so tests can assert the task is gone after stop().
_task: asyncio.Task | None = None


async def sweep_once(now: float | None = None) -> list[str]:
    """Run a single reap pass and return the ids removed.

    Separated from the loop so tests can drive one deterministic sweep with an
    injected ``now`` instead of waiting out a real interval.
    """
    return await db.reap_expired(None, now)


async def _run() -> None:
    """Sweep forever on the configured interval.

    Sleeps FIRST so process start-up isn't competing with a DB write, and
    swallows per-iteration exceptions: a transient sqlite error must not kill
    the task, because nothing would restart it and TTLs would silently stop
    being honored for the life of the process.
    """
    interval = settings.memo_reaper_interval_seconds
    logger.info("TTL reaper started — sweeping every %ss", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            reaped = await sweep_once()
            if reaped:
                logger.info("TTL reaper removed %d memo(s): %s", len(reaped), reaped)
        except asyncio.CancelledError:
            logger.info("TTL reaper stopping")
            raise
        except Exception:
            logger.exception("TTL reaper sweep failed — continuing")


def start() -> asyncio.Task | None:
    """Launch the sweep task. Idempotent; returns the running task.

    Returns None when disabled via ``memo_reaper_enabled``.
    """
    global _task
    if not settings.memo_reaper_enabled:
        logger.info("TTL reaper disabled by config — expires_at will NOT be enforced")
        return None
    if _task is not None and not _task.done():
        return _task
    _task = asyncio.create_task(_run(), name="memo-ttl-reaper")
    return _task


async def stop() -> None:
    """Cancel the sweep task and await its exit. Safe to call if never started."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    finally:
        _task = None
