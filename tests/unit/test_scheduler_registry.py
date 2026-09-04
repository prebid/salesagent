"""The app entry point's contract with the scheduler modules.

``lifespan_context`` is the only thing that starts the interval schedulers, and
it had no test: a renamed lifecycle function used to be neither an import error
nor a type error, and the app ran with that scheduler silently absent behind
``except Exception`` (#1197 review).

These tests grade the contract that replaced the string table:
  - every module in ``_SCHEDULER_MODULES`` actually registered a lifecycle pair,
  - the pair is callable and carries the operator-facing display name,
  - lifespan starts them forward and stops them in reverse (LIFO),
  - a failing scheduler does not abort the startup of its siblings,
  - the test-only disable knob starts nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.main import (
    _SCHEDULER_MODULES,
    _SCHEDULER_VERB_FORMS,
    _registered_schedulers,
    lifespan_context,
)
from src.services._scheduler_base import RegisteredScheduler, registered_scheduler

EXPECTED_NAMES = ["delivery webhook", "media buy status", "TMP health"]


class TestRegistryResolves:
    """Every declared scheduler module registered a usable lifecycle pair."""

    def test_every_module_registered_a_scheduler(self):
        # KeyError here means a module stopped registering — the loud failure
        # the getattr-off-a-string form could not give us.
        registered = [registered_scheduler(module) for module in _SCHEDULER_MODULES]
        assert len(registered) == len(_SCHEDULER_MODULES)

    def test_registered_names_are_the_operator_facing_ones_in_startup_order(self):
        assert [s.name for s in _registered_schedulers()] == EXPECTED_NAMES

    def test_registered_lifecycle_pairs_are_callable(self):
        for scheduler in _registered_schedulers():
            assert callable(scheduler.start), scheduler.name
            assert callable(scheduler.stop), scheduler.name

    def test_verb_forms_are_the_spelled_out_words(self):
        # The "Stoping"/"stoped" derivation is gone, not retained as a default.
        assert _SCHEDULER_VERB_FORMS == {
            "start": ("Starting", "started"),
            "stop": ("Stopping", "stopped"),
        }


def _fake_schedulers(count: int = 3) -> tuple[list[RegisteredScheduler], list[str]]:
    """Build *count* fake schedulers that append to a shared call log."""
    calls: list[str] = []

    def make(name: str) -> RegisteredScheduler:
        async def start() -> None:
            calls.append(f"start:{name}")

        async def stop() -> None:
            calls.append(f"stop:{name}")

        return RegisteredScheduler(name, start, stop)

    return [make(f"s{i}") for i in range(count)], calls


class TestLifespanOrder:
    """Startup iterates forward; shutdown iterates in reverse."""

    @pytest.mark.asyncio
    async def test_starts_forward_and_stops_in_reverse(self, monkeypatch):
        schedulers, calls = _fake_schedulers()
        monkeypatch.setattr("src.core.main._registered_schedulers", lambda: schedulers)
        monkeypatch.setenv("ADCP_RUN_BACKGROUND_SCHEDULERS", "true")

        async with lifespan_context(object()):
            assert calls == ["start:s0", "start:s1", "start:s2"]

        assert calls == [
            "start:s0",
            "start:s1",
            "start:s2",
            "stop:s2",
            "stop:s1",
            "stop:s0",
        ]

    @pytest.mark.asyncio
    async def test_disable_knob_starts_nothing(self, monkeypatch):
        schedulers, calls = _fake_schedulers()
        monkeypatch.setattr("src.core.main._registered_schedulers", lambda: schedulers)
        monkeypatch.setenv("ADCP_RUN_BACKGROUND_SCHEDULERS", "false")

        async with lifespan_context(object()):
            pass

        assert calls == []

    @pytest.mark.asyncio
    async def test_one_failing_scheduler_does_not_stop_the_others(self, monkeypatch):
        schedulers, calls = _fake_schedulers()
        boom = AsyncMock(side_effect=RuntimeError("no db"))
        schedulers[1] = RegisteredScheduler("s1", boom, schedulers[1].stop)
        monkeypatch.setattr("src.core.main._registered_schedulers", lambda: schedulers)
        monkeypatch.setenv("ADCP_RUN_BACKGROUND_SCHEDULERS", "true")

        async with lifespan_context(object()):
            pass

        boom.assert_awaited_once_with()
        assert "start:s0" in calls and "start:s2" in calls
        assert calls.count("stop:s1") == 1
