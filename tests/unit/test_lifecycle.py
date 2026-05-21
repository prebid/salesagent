"""Unit tests for src.core.lifecycle — shutdown-callback registry.

Services own their own lifecycle: they self-register an async close callback
via ``register_shutdown`` at construction. ``app_lifespan`` only calls
``run_all_shutdown_callbacks()`` — it never references a specific service.

Pinned decisions (AC requires choose-and-pin):
  - Callbacks MUST be async (Awaitable). Sync callbacks are not supported.
  - ``run_all_shutdown_callbacks`` is NOT idempotent — calling it again
    re-runs every registered callback (the registry is not cleared). The
    process lifespan only fires once, so this is acceptable and simpler.
"""

from __future__ import annotations

import logging

import pytest

from src.core import lifecycle


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the module-global callback list per test."""
    lifecycle._shutdown_callbacks.clear()
    yield
    lifecycle._shutdown_callbacks.clear()


@pytest.mark.asyncio
async def test_callbacks_run_in_registration_order():
    order: list[int] = []

    async def cb1() -> None:
        order.append(1)

    async def cb2() -> None:
        order.append(2)

    async def cb3() -> None:
        order.append(3)

    lifecycle.register_shutdown(cb1)
    lifecycle.register_shutdown(cb2)
    lifecycle.register_shutdown(cb3)

    await lifecycle.run_all_shutdown_callbacks()

    assert order == [1, 2, 3]


@pytest.mark.asyncio
async def test_exception_in_one_callback_does_not_block_others():
    ran: list[str] = []

    async def good_first() -> None:
        ran.append("first")

    async def boom() -> None:
        ran.append("boom")
        raise RuntimeError("simulated shutdown failure")

    async def good_last() -> None:
        ran.append("last")

    lifecycle.register_shutdown(good_first)
    lifecycle.register_shutdown(boom)
    lifecycle.register_shutdown(good_last)

    # Must NOT raise — failures are swallowed.
    await lifecycle.run_all_shutdown_callbacks()

    assert ran == ["first", "boom", "last"]


@pytest.mark.asyncio
async def test_callback_exception_is_logged(caplog):
    async def boom() -> None:
        raise ValueError("kaboom")

    lifecycle.register_shutdown(boom)

    with caplog.at_level(logging.ERROR, logger="src.core.lifecycle"):
        await lifecycle.run_all_shutdown_callbacks()

    assert any("kaboom" in r.message or (r.exc_info and "kaboom" in str(r.exc_info[1])) for r in caplog.records), (
        "shutdown callback failure must be logged via logger.exception"
    )


@pytest.mark.asyncio
async def test_run_all_is_not_idempotent_reruns_callbacks():
    """Pinned: the registry is not cleared — a second run re-invokes callbacks."""
    calls: list[int] = []

    async def cb() -> None:
        calls.append(1)

    lifecycle.register_shutdown(cb)

    await lifecycle.run_all_shutdown_callbacks()
    await lifecycle.run_all_shutdown_callbacks()

    assert calls == [1, 1]


@pytest.mark.asyncio
async def test_empty_registry_is_a_noop():
    # No callbacks registered — must complete cleanly.
    await lifecycle.run_all_shutdown_callbacks()


@pytest.mark.asyncio
async def test_register_shutdown_returns_none_and_appends():
    async def cb() -> None:
        pass

    result = lifecycle.register_shutdown(cb)
    assert result is None
    assert lifecycle._shutdown_callbacks == [cb]
