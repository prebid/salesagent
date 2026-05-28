"""Base class for fixed-interval background schedulers.

All three interval schedulers (MediaBuyStatusScheduler, DeliveryWebhookScheduler,
TMPHealthScheduler) share an identical scaffold:
  - __init__ / start / stop / _run_scheduler / singleton accessor / module-level start_*/stop_*

This base extracts that scaffold so each concrete scheduler only overrides ``tick()``.

Usage::

    class MyScheduler(IntervalScheduler):
        async def tick(self) -> None:
            await do_work()

    _scheduler: MyScheduler | None = None

    def get_my_scheduler() -> MyScheduler:
        global _scheduler
        if _scheduler is None:
            _scheduler = MyScheduler(interval_seconds=60, name="my")
        return _scheduler
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class IntervalScheduler:
    """Background scheduler that calls ``tick()`` on a fixed cadence.

    Subclasses must implement :meth:`tick`.  The scaffold handles:
    - Singleton-safe ``start`` / ``stop`` with an asyncio lock.
    - ``CancelledError`` propagation so shutdown is clean.
    - Exception isolation: an unhandled error in ``tick`` is logged but does
      not kill the loop.
    - The sleep always runs in ``finally`` so the cadence is maintained even
      when ``tick`` raises.

    Args:
        interval_seconds: Seconds to sleep between ticks.
        name: Human-readable name used in log messages.
    """

    def __init__(self, interval_seconds: int, name: str) -> None:
        self._interval_seconds = interval_seconds
        self._name = name
        self.is_running = False
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the scheduler background task."""
        async with self._lock:
            if self.is_running:
                logger.warning("%s scheduler is already running", self._name)
                return
            self.is_running = True
            self._task = asyncio.create_task(self._run_scheduler())
            logger.info(
                "%s scheduler started (interval=%ds)",
                self._name,
                self._interval_seconds,
            )

    async def stop(self) -> None:
        """Stop the scheduler background task."""
        async with self._lock:
            if not self.is_running:
                return
            self.is_running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            logger.info("%s scheduler stopped", self._name)

    async def _run_scheduler(self) -> None:
        """Main scheduler loop — runs on a fixed cadence."""
        while self.is_running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "Error in %s scheduler: %s",
                    self._name,
                    exc,
                    exc_info=True,
                )
            finally:
                await asyncio.sleep(self._interval_seconds)

    async def tick(self) -> None:
        """Override in subclasses to perform one unit of work."""
        raise NotImplementedError
