"""Unit tests for fire-and-forget asyncio task pinning in activity_feed.

asyncio's task tracker keeps only weak references; without pinning, a task
whose only strong reference is a local variable can be GC'd mid-execution.
Symptoms: silently lost WebSocket broadcasts + slow leak growth from
retained await frames. Covers the two call sites missed by gh-#1264's
original fix at ``context_manager.py:25-30``.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core import async_utils
from src.services import activity_feed as af


@pytest.fixture(autouse=True)
def clean_pending_tasks():
    """Reset the module-level pending-task set before each test."""
    async_utils._pinned_tasks.clear()
    yield
    async_utils._pinned_tasks.clear()


@pytest.mark.asyncio
async def test_add_connection_pins_replay_tasks():
    """``add_connection`` schedules replay sends; each task is pinned."""
    feed = af.ActivityFeed()
    tenant_id = "t1"

    feed.recent_activities[tenant_id] = af.deque([{"timestamp": "2026-05-18T12:00:00+00:00", "type": "test", "n": 1}])
    feed.connections[tenant_id] = set()

    websocket = MagicMock()
    websocket.send = AsyncMock()

    feed.add_connection(tenant_id, websocket)

    # Task created and pinned synchronously
    assert len(async_utils._pinned_tasks) == 1

    # Let the task and its done-callback run
    pending_task = next(iter(async_utils._pinned_tasks))
    await pending_task
    await asyncio.sleep(0)

    assert len(async_utils._pinned_tasks) == 0
    websocket.send.assert_awaited_once_with(json.dumps(feed.recent_activities[tenant_id][0]))


@pytest.mark.asyncio
async def test_try_broadcast_pins_task():
    """``_try_broadcast`` schedules a broadcast; the task is pinned."""
    feed = af.ActivityFeed()
    feed.broadcast_activity = AsyncMock()

    feed._try_broadcast("t1", {"type": "test", "n": 1})

    assert len(async_utils._pinned_tasks) == 1

    pending_task = next(iter(async_utils._pinned_tasks))
    await pending_task
    await asyncio.sleep(0)

    assert len(async_utils._pinned_tasks) == 0
    feed.broadcast_activity.assert_awaited_once_with("t1", {"type": "test", "n": 1})


@pytest.mark.asyncio
async def test_pin_survives_task_exception():
    """If the pinned task raises, the done-callback still discards from the set."""
    feed = af.ActivityFeed()

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated broadcast failure")

    feed.broadcast_activity = boom

    feed._try_broadcast("t1", {"n": 1})
    assert len(async_utils._pinned_tasks) == 1

    pending_task = next(iter(async_utils._pinned_tasks))
    with pytest.raises(RuntimeError, match="simulated broadcast failure"):
        await pending_task
    await asyncio.sleep(0)

    # Discard ran despite the exception
    assert len(async_utils._pinned_tasks) == 0


@pytest.mark.asyncio
async def test_multiple_concurrent_tasks_coexist():
    """N in-flight broadcasts = N entries in the pending set."""
    feed = af.ActivityFeed()
    release = asyncio.Event()

    async def wait_then_done(*args, **kwargs):
        await release.wait()

    feed.broadcast_activity = wait_then_done

    for i in range(5):
        feed._try_broadcast("t1", {"n": i})

    assert len(async_utils._pinned_tasks) == 5

    release.set()
    # Let all tasks finish
    pending_snapshot = list(async_utils._pinned_tasks)
    await asyncio.gather(*pending_snapshot)
    await asyncio.sleep(0)

    assert len(async_utils._pinned_tasks) == 0


@pytest.mark.asyncio
async def test_try_broadcast_no_loop_does_not_pin():
    """When called without a running loop, no task is created and nothing is pinned.

    ``_try_broadcast`` short-circuits via ``asyncio.get_running_loop()`` —
    behavior unchanged by the pinning fix.
    """

    feed = af.ActivityFeed()
    feed.broadcast_activity = AsyncMock()

    # Synchronous context: no event loop running
    def call_from_sync():
        feed._try_broadcast("t1", {"n": 1})

    # Wrap in a thread to ensure no running loop
    import threading

    t = threading.Thread(target=call_from_sync)
    t.start()
    t.join(timeout=1)

    assert len(async_utils._pinned_tasks) == 0
    feed.broadcast_activity.assert_not_called()
