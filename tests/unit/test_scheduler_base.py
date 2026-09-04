"""Unit tests for the shared interval-scheduler scaffold.

Covers ``src/services/_scheduler_base.py``'s ``make_singleton()`` factory — the
second half of the scaffold the base advertises.  Before it existed, each of the
three scheduler modules hand-rolled a byte-identical ``_scheduler`` global plus
``get_`` / ``start_`` / ``stop_`` wrappers while the base's docstring claimed to
extract them (#1197 review).

Covers:
- The trio caches one lazily-built instance per factory call
- Separate factory calls own independent instances (no shared global)
- ``start_``/``stop_`` delegate to the cached instance's ``start()``/``stop()``
- All three production scheduler modules bind their trio from this factory
"""

from __future__ import annotations

import inspect

import pytest

from src.services._scheduler_base import IntervalScheduler, make_singleton


class _RecordingScheduler(IntervalScheduler):
    """Minimal concrete scheduler that records start/stop instead of running a loop."""

    def __init__(self) -> None:
        super().__init__(interval_seconds=3600, name="recording")
        self.started = 0
        self.stopped = 0

    async def tick(self) -> None:  # pragma: no cover - never scheduled in these tests
        raise AssertionError("tick() must not run in these tests")

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


class TestMakeSingleton:
    """make_singleton(cls, name="test scheduler") returns a (get, start, stop) trio over one cached instance."""

    def test_get_returns_same_instance_on_repeated_calls(self):
        get_scheduler, _, _ = make_singleton(_RecordingScheduler, name="test scheduler")

        first = get_scheduler()
        second = get_scheduler()

        assert first is second
        assert isinstance(first, _RecordingScheduler)

    def test_separate_factory_calls_own_independent_instances(self):
        """Each module's trio caches in its own closure cell, not a shared global.

        Mutation this pins: caching on the class (or in a module-level dict keyed
        by nothing) would make two scheduler modules share one instance, so
        stopping one would stop the other.
        """
        get_a, _, _ = make_singleton(_RecordingScheduler, name="test scheduler")
        get_b, _, _ = make_singleton(_RecordingScheduler, name="test scheduler")

        assert get_a() is not get_b()

    @pytest.mark.asyncio
    async def test_start_and_stop_delegate_to_the_cached_instance(self):
        get_scheduler, start_scheduler, stop_scheduler = make_singleton(_RecordingScheduler, name="test scheduler")

        await start_scheduler()
        await stop_scheduler()

        instance = get_scheduler()
        assert (instance.started, instance.stopped) == (1, 1)

    @pytest.mark.asyncio
    async def test_start_creates_the_instance_when_get_was_never_called(self):
        """The instance is built lazily on first use, whichever function is called first."""
        get_scheduler, start_scheduler, _ = make_singleton(_RecordingScheduler, name="test scheduler")

        await start_scheduler()

        assert get_scheduler().started == 1


class TestProductionSchedulersUseTheFactory:
    """All three scheduler modules bind their singleton trio from make_singleton().

    This is the guard against a fourth hand-rolled copy: a module that reverts to
    its own ``_scheduler`` global + ``def get_x()`` fails here, because functions
    produced by the factory are the factory's own closures (identifiable by their
    ``__qualname__``) rather than module-level defs.
    """

    _MODULES = [
        ("src.services.tmp_health_scheduler", "tmp_health_scheduler"),
        ("src.services.media_buy_status_scheduler", "media_buy_status_scheduler"),
        ("src.services.delivery_webhook_scheduler", "delivery_webhook_scheduler"),
    ]

    @pytest.mark.parametrize(("module_path", "stem"), _MODULES, ids=lambda v: v.split(".")[-1])
    def test_trio_is_bound_from_make_singleton(self, module_path: str, stem: str):
        import importlib

        module = importlib.import_module(module_path)

        for prefix, expected_qualname in (
            ("get_", "make_singleton.<locals>.get_scheduler"),
            ("start_", "make_singleton.<locals>.start_scheduler"),
            ("stop_", "make_singleton.<locals>.stop_scheduler"),
        ):
            fn = getattr(module, f"{prefix}{stem}")
            assert fn.__qualname__ == expected_qualname, (
                f"{module_path}.{prefix}{stem} is hand-rolled — bind it from make_singleton()"
            )

        # No module keeps a leftover `_scheduler` global alongside the factory.
        assert not hasattr(module, "_scheduler"), f"{module_path} still holds a hand-rolled _scheduler global"

    @pytest.mark.parametrize(("module_path", "stem"), _MODULES, ids=lambda v: v.split(".")[-1])
    def test_get_returns_the_module_specific_scheduler_subclass(self, module_path: str, stem: str):
        """Each module's trio is bound over its own class, not another module's."""
        import importlib

        module = importlib.import_module(module_path)
        instance = getattr(module, f"get_{stem}")()

        assert isinstance(instance, IntervalScheduler)
        # The instance's class is defined in the module that exposes the accessor.
        assert inspect.getmodule(type(instance)).__name__ == module_path
