"""Unit tests for src.core.async_utils.pin_task.

asyncio keeps only weak references to tasks (asyncio.tasks._all_tasks); a
fire-and-forget task whose only strong reference is a local variable can be
garbage-collected mid-execution. ``pin_task`` adds a module-level strong
reference and installs a done-callback that discards it on completion —
running the discard BEFORE any user-supplied ``on_done`` callback so a
log-and-swallow ``on_done`` cannot keep the task alive past completion.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core import async_utils


@pytest.fixture(autouse=True)
def clean_pinned_tasks():
    """Reset the module-level pinned-task set before and after each test."""
    async_utils._pinned_tasks.clear()
    yield
    async_utils._pinned_tasks.clear()


@pytest.mark.asyncio
async def test_pin_task_adds_to_set_before_returning():
    """pin_task registers the task in _pinned_tasks synchronously, before return."""
    release = asyncio.Event()

    async def waiter():
        await release.wait()

    task = asyncio.create_task(waiter())
    returned = async_utils.pin_task(task)

    # Pinned synchronously — before the task has a chance to complete.
    assert returned is task
    assert task in async_utils._pinned_tasks
    assert len(async_utils._pinned_tasks) == 1

    release.set()
    await task
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_on_success_discards_and_calls_on_done():
    """On task success, _pinned_tasks discards the task and on_done is called."""
    calls: list[asyncio.Task] = []

    async def ok():
        return "done"

    task = asyncio.create_task(ok())
    async_utils.pin_task(task, on_done=calls.append)

    await task
    await asyncio.sleep(0)

    assert task not in async_utils._pinned_tasks
    assert len(async_utils._pinned_tasks) == 0
    assert calls == [task]


@pytest.mark.asyncio
async def test_on_exception_still_discards_before_on_done():
    """On task exception the discard still runs, and runs BEFORE on_done.

    on_done observes that the task is already gone from _pinned_tasks — proving
    discard runs first so a log-and-swallow on_done cannot pin the task.
    """
    observed_membership: list[bool] = []

    def on_done(t: asyncio.Task) -> None:
        observed_membership.append(t in async_utils._pinned_tasks)

    async def boom():
        raise RuntimeError("simulated failure")

    task = asyncio.create_task(boom())
    async_utils.pin_task(task, on_done=on_done)

    with pytest.raises(RuntimeError, match="simulated failure"):
        await task
    await asyncio.sleep(0)

    assert task not in async_utils._pinned_tasks
    # on_done saw the task already discarded (discard ran first).
    assert observed_membership == [False]


@pytest.mark.asyncio
async def test_on_done_none_is_valid_cleanup_still_runs():
    """on_done=None is valid; the discard cleanup still runs on completion."""

    async def ok():
        return 1

    task = asyncio.create_task(ok())
    async_utils.pin_task(task)  # on_done defaults to None

    assert len(async_utils._pinned_tasks) == 1
    await task
    await asyncio.sleep(0)

    assert len(async_utils._pinned_tasks) == 0


@pytest.mark.asyncio
async def test_multiple_pinned_tasks_coexist_without_interference():
    """N in-flight pinned tasks = N entries; each discards independently."""
    release = asyncio.Event()

    async def waiter():
        await release.wait()

    tasks = [asyncio.create_task(waiter()) for _ in range(5)]
    for t in tasks:
        async_utils.pin_task(t)

    assert len(async_utils._pinned_tasks) == 5

    release.set()
    await asyncio.gather(*tasks)
    await asyncio.sleep(0)

    assert len(async_utils._pinned_tasks) == 0
