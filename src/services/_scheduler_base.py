"""Base class for fixed-interval background schedulers.

All three interval schedulers (MediaBuyStatusScheduler, DeliveryWebhookScheduler,
TMPHealthScheduler) share an identical scaffold:
  - __init__ / start / stop / _run_scheduler  → :class:`IntervalScheduler`
  - singleton accessor + module-level start_*/stop_*  → :func:`make_singleton`

This base extracts *both* halves, so each concrete scheduler only overrides
``tick()`` and binds the three module-level functions from the factory.

``make_singleton`` also *registers* the start/stop pair here, so the app entry
point iterates real callables it imported rather than resolving names off a
table of strings — see :func:`registered_scheduler` (#1197 review).

Usage::

    class MyScheduler(IntervalScheduler):
        async def tick(self) -> None:
            await do_work()

    get_my_scheduler, start_my_scheduler, stop_my_scheduler = make_singleton(
        MyScheduler, name="my thing"
    )
"""

from __future__ import annotations

import abc
import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from types import ModuleType
from typing import NamedTuple

logger = logging.getLogger(__name__)


def parse_interval_env(env_var: str, default: int) -> int:
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


class RegisteredScheduler(NamedTuple):
    """One scheduler's lifecycle pair, as registered by :func:`make_singleton`.

    Holds the bound callables themselves, not their names: the app entry point
    imports the scheduler module and iterates these, so a renamed or moved
    lifecycle function is an import-time failure instead of a ``getattr`` miss
    swallowed by a startup ``except Exception`` (#1197 review).
    """

    name: str
    start: Callable[[], Awaitable[None]]
    stop: Callable[[], Awaitable[None]]


#: Registered schedulers, keyed by the ``__module__`` of the scheduler class.
#: Populated at import time by :func:`make_singleton`; read via
#: :func:`registered_scheduler`, which keys off the imported module object so
#: the caller controls start/stop ORDER explicitly rather than inheriting
#: whatever import happened to run first.
_REGISTRY: dict[str, RegisteredScheduler] = {}


def registered_scheduler(module: ModuleType) -> RegisteredScheduler:
    """Return the scheduler *module* registered when it was imported.

    Raises ``KeyError`` if the module defines no scheduler — a module that
    stopped registering one is then a loud startup failure, which is the
    property the string-keyed table could not offer.
    """
    return _REGISTRY[module.__name__]


def make_singleton[SchedulerT: IntervalScheduler](
    cls: Callable[[], SchedulerT],
    *,
    name: str,
) -> tuple[Callable[[], SchedulerT], Callable[[], Awaitable[None]], Callable[[], Awaitable[None]]]:
    """Build the ``(get_x, start_x, stop_x)`` singleton trio for *cls* and register it.

    *name* is the operator-facing display name used in startup/shutdown log
    lines ("TMP health"). It lives here, beside the scheduler it names, rather
    than in a table in the app entry point.

    *cls* is typed ``Callable[[], SchedulerT]`` rather than ``type[SchedulerT]``
    because the requirement is a **zero-argument** constructor: each concrete
    scheduler's ``__init__`` supplies its own interval and name to the base.
    ``type[SchedulerT]`` would advertise the base's two-argument ``__init__``
    and make the ``cls()`` call below a type error.

    Every scheduler module used to hand-roll a byte-identical ``_scheduler``
    global plus ``get_``/``start_``/``stop_`` wrappers.  Bind them from here
    instead::

        get_my_scheduler, start_my_scheduler, stop_my_scheduler = make_singleton(
            MyScheduler, name="my thing"
        )

    The instance is created lazily on first ``get_`` call and cached in this
    factory's closure (one cell per call, so each scheduler module keeps its own
    independent singleton).  Tests can still construct ``MyScheduler()`` directly
    without touching the cached instance.
    """
    instance: SchedulerT | None = None

    def get_scheduler() -> SchedulerT:
        """Get or create the global scheduler instance."""
        nonlocal instance
        if instance is None:
            instance = cls()
        return instance

    async def start_scheduler() -> None:
        """Start the global scheduler (called at application startup)."""
        await get_scheduler().start()

    async def stop_scheduler() -> None:
        """Stop the global scheduler (called at application shutdown)."""
        await get_scheduler().stop()

    _REGISTRY[cls.__module__] = RegisteredScheduler(name, start_scheduler, stop_scheduler)
    return get_scheduler, start_scheduler, stop_scheduler
