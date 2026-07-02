"""Base class for fixed-interval background schedulers.

All three interval schedulers (MediaBuyStatusScheduler, DeliveryWebhookScheduler,
TMPHealthScheduler) share an identical scaffold:
  - __init__ / start / stop / _run_scheduler / singleton accessor / module-level start_*/stop_*

This base extracts that scaffold so each concrete scheduler only overrides ``tick()``.

Usage::

    class MyScheduler(IntervalScheduler):
        _env_var = "MY_INTERVAL_SECONDS"
        _default_interval = 60
        _scheduler_name = "my"

        async def tick(self) -> None:
            await do_work()

    _scheduler: MyScheduler | None = None

    def get_my_scheduler() -> MyScheduler:
        return MyScheduler.get_singleton()
"""

from __future__ import annotations

import abc
import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def _parse_interval_env(env_var: str, default: int) -> int:
    """Parse an integer interval from an environment variable.

    Wraps the conversion in try/except so a bad value (e.g. ``"sixty"``) does
    not crash the process at import time before lifespan startup can report the
    error.  Returns *default* and logs a warning on bad input.
    """
    try:
        return int(os.getenv(env_var) or str(default))
    except (ValueError, TypeError):
        logger.warning(
            "%s is not a valid integer — defaulting to %ds",
            env_var,
            default,
        )
        return default


class IntervalScheduler(abc.ABC):
    """Background scheduler that calls ``tick()`` on a fixed cadence.

    Subclasses must implement :meth:`tick`.  The scaffold handles:
    - Singleton-safe ``start`` / ``stop`` with an asyncio lock.
    - ``CancelledError`` propagation so shutdown is clean and fast.
    - Exception isolation: an unhandled error in ``tick`` is logged but does
      not kill the loop.
    - The sleep runs *after* the try/except block (not in ``finally``) so that
      a pending cancellation is not delayed by a full interval sleep.  When the
      task is cancelled, ``asyncio.sleep`` raises ``CancelledError`` immediately
      and the loop exits without waiting for the next tick.

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
        """Main scheduler loop — runs on a fixed cadence.

        The sleep is placed *after* the try/except block, not in ``finally``.
        This means:
        - A ``CancelledError`` raised inside ``tick()`` propagates immediately
          (re-raised after ``break``), so the task exits without sleeping.
        - A ``CancelledError`` raised inside ``asyncio.sleep`` also propagates
          immediately — the task exits without waiting for the next interval.
        - An unhandled exception in ``tick()`` is logged and the loop continues
          after the normal inter-tick sleep.

        Contrast with the ``finally: sleep`` pattern: that pattern clears the
        pending cancellation when ``CancelledError`` is caught in the ``except``
        clause, then runs the full sleep before the task can exit — causing
        shutdown lag equal to the interval (up to 3600 s for the webhook
        scheduler).
        """
        while self.is_running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Error in %s scheduler: %s",
                    self._name,
                    exc,
                    exc_info=True,
                )
            await asyncio.sleep(self._interval_seconds)

    @abc.abstractmethod
    async def tick(self) -> None:
        """Override in subclasses to perform one unit of work."""
