"""ATC Conductor adapter. [001/FR-041 001/FR-042]

Delivers events over ATC's HTTP surface. Two design points, both about
isolation:

* **Bounded queue.** If the Conductor is down, events accumulate. An unbounded
  queue turns a message-bus outage into a memory leak in the memo process, so
  the queue has a ceiling and drops oldest-first with a WARN when full.
  Losing an advisory event beats OOM-ing the service the whole fleet reads from.
* **Retry with backoff, then drop.** Events are advisory notifications, not
  durable state — the memo itself is already committed to sqlite before any
  event is emitted. Retrying forever would be a queue that never drains.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from memo.providers.conductor.base import Event

logger = logging.getLogger(__name__)

MAX_QUEUE = 1000
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2.0


class ATCConductor:
    name = "atc"

    def __init__(self, url: str | None = None, session_id: str | None = None) -> None:
        self.url = (url or os.environ.get("MEMO_ATC_URL")
                    or "http://localhost:3030").rstrip("/")
        self.session_id = session_id or os.environ.get("MEMO_ATC_SESSION") or "memo"
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._task: asyncio.Task | None = None
        self.dropped = 0
        self.delivered = 0

    async def emit(self, event: Event) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            # Drop the OLDEST: a backed-up queue's head is the most stale, and
            # the newest event is the one most likely to still matter.
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(event)
            except Exception:
                pass
            self.dropped += 1
            if self.dropped % 50 == 1:
                logger.warning("conductor queue full — dropped %d event(s) so far",
                               self.dropped)
            return False

    def _endpoint(self, event: Event) -> str:
        return {"beacon": "/beacons", "board-post": "/statuses"}.get(
            event.delivery_mode, "/messages")

    async def _deliver(self, event: Event) -> bool:
        body = event.to_wire()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.post(f"{self.url}{self._endpoint(event)}",
                                          json={"from": self.session_id, **body})
                if r.status_code < 300:
                    return True
                logger.debug("conductor: %s -> HTTP %s (attempt %d)",
                             event.event_kind, r.status_code, attempt)
            except Exception as e:
                logger.debug("conductor: %s failed (attempt %d): %s",
                             event.event_kind, attempt, e)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(BACKOFF_BASE_S * attempt)
        return False

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                if await self._deliver(event):
                    self.delivered += 1
                else:
                    self.dropped += 1
                    logger.warning("conductor: dropping %s after %d attempts",
                                   event.event_kind, MAX_ATTEMPTS)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("conductor worker error — continuing")
            finally:
                self._queue.task_done()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="memo-conductor")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
