"""Regression tests for fire-and-forget webhook task pinning in context_manager.

PR #1264 fix #6 pinned the fire-and-forget webhook task scheduled inside
``ContextManager._send_push_notifications`` against asyncio's weak-ref GC.
salesagent-x2h.2 extracted that pin to the shared ``src.core.async_utils``
helper, so the strong-ref set is now ``async_utils._pinned_tasks`` (per the
x2h.5 Dependencies note: pin_task landed first → target the shared module).

These tests drive the REAL ``_send_push_notifications`` code path (not
``pin_task`` in isolation — ``test_async_utils.py`` covers that). They prove:
  - the context_manager path actually pins the scheduled task,
  - the done-callback discards it on completion,
  - the discard runs BEFORE ``_log_task_result``'s result()-swallow even on
    the exception path (so a swallowed traceback can't keep the task alive),
  - N concurrent webhook tasks coexist as N distinct set entries.

Mutation coverage (verified during authoring):
  #1 delete pin_task(...) call            -> added-on-create FAILS
  #2 discard AFTER result() in _log_...    -> discard-before-swallow FAILS
  #3 remove add_done_callback (in pin_task)-> discarded-on-completion FAILS
  #4 set -> list for _pinned_tasks         -> concurrent-coexist FAILS
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core import async_utils, context_manager


@pytest.fixture(autouse=True)
def clean_pinned_tasks():
    async_utils._pinned_tasks.clear()
    yield
    async_utils._pinned_tasks.clear()


def _make_step():
    """A WorkflowStep-like object with the push_notification_config request data."""
    return SimpleNamespace(
        step_id="step_1",
        context_id="ctx_1",
        tool_name="create_media_buy",
        response_data={"ok": True},
        request_data={
            "protocol": "mcp",
            "push_notification_config": {"url": "https://buyer.example/webhook"},
        },
        context=SimpleNamespace(tenant_id="tenant_1", principal_id="principal_1"),
    )


def _session_for(mappings, context, webhooks):
    """A session whose scalars() returns mappings, then context, then webhooks.

    _send_push_notifications issues exactly three scalars() queries in order:
    ObjectWorkflowMapping (.all()), Context (.first()), PushNotificationConfig
    (.all()).
    """
    scalars_mapping = MagicMock()
    scalars_mapping.all.return_value = mappings
    scalars_context = MagicMock()
    scalars_context.first.return_value = context
    scalars_webhooks = MagicMock()
    scalars_webhooks.all.return_value = webhooks

    session = MagicMock()
    session.scalars.side_effect = [scalars_mapping, scalars_context, scalars_webhooks]
    return session


def _drive(send_notification_side):
    """Run _send_push_notifications with a mocked webhook service.

    ``send_notification_side`` is assigned to the AsyncMock so the test
    controls when/how the scheduled coroutine completes. Returns nothing —
    callers inspect async_utils._pinned_tasks.
    """
    mapping = SimpleNamespace(object_type="media_buy", object_id="mb_1", action="create")
    context = SimpleNamespace(tenant_id="tenant_1", principal_id="principal_1")
    webhook = SimpleNamespace(id="pnc_1")
    session = _session_for([mapping], context, [webhook])

    fake_service = MagicMock()
    fake_service.send_notification = AsyncMock(side_effect=send_notification_side)

    cm = context_manager.ContextManager()
    with patch.object(context_manager, "get_protocol_webhook_service", return_value=fake_service):
        cm._send_push_notifications(_make_step(), "completed", session)


@pytest.mark.asyncio
async def test_webhook_task_added_to_pending_set_on_create():
    """Scheduling a webhook via _send_push_notifications pins the task."""
    release = asyncio.Event()

    async def blocked(**_kwargs):
        await release.wait()

    _drive(blocked)

    assert len(async_utils._pinned_tasks) == 1

    release.set()
    for t in list(async_utils._pinned_tasks):
        await t
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_webhook_task_discarded_from_pending_set_on_completion():
    """The done-callback removes the task from the pinned set on completion."""

    async def ok(**_kwargs):
        return None

    _drive(ok)
    assert len(async_utils._pinned_tasks) == 1

    task = next(iter(async_utils._pinned_tasks))
    await task
    await asyncio.sleep(0)

    assert len(async_utils._pinned_tasks) == 0


@pytest.mark.asyncio
async def test_webhook_task_discarded_before_result_swallow():
    """On the exception path the discard MUST run before _log_task_result's swallow.

    If discard ran after t.result(), the swallowed exception's traceback frame
    chain could keep the task alive — exactly the leak this fix prevents.

    The discriminator: ``_log_task_result`` calls ``console.print``. We capture
    the pinned-set membership AT THAT MOMENT. Correct order -> task already
    discarded (absent). Mutation #2 (discard after on_done) -> task still
    present when _log_task_result runs.
    """

    async def boom(**_kwargs):
        raise RuntimeError("simulated webhook failure")

    membership_when_logged: list[bool] = []
    real_print = context_manager.console.print

    def spy_print(*args, **kwargs):
        # _log_task_result is the only caller that prints "Webhook failed".
        if args and isinstance(args[0], str) and "Webhook failed" in args[0]:
            task = next(iter(async_utils._pinned_tasks), None)
            membership_when_logged.append(task is not None)
        return real_print(*args, **kwargs)

    with patch.object(context_manager.console, "print", side_effect=spy_print):
        _drive(boom)
        assert len(async_utils._pinned_tasks) == 1

        task = next(iter(async_utils._pinned_tasks))
        with pytest.raises(RuntimeError, match="simulated webhook failure"):
            await task
        await asyncio.sleep(0)

    assert len(async_utils._pinned_tasks) == 0
    # Ordering proof: when _log_task_result ran, the task was ALREADY gone.
    assert membership_when_logged == [False], (
        "discard must run BEFORE _log_task_result's result()-swallow; the task "
        "was still pinned when the failure was logged (mutation #2 — discard "
        "moved after on_done)"
    )


@pytest.mark.asyncio
async def test_multiple_concurrent_webhook_tasks_coexist():
    """N webhooks in flight = N distinct entries in the pinned set."""
    release = asyncio.Event()

    async def blocked(**_kwargs):
        await release.wait()

    for _ in range(5):
        _drive(blocked)

    assert len(async_utils._pinned_tasks) == 5
    # The pinned collection MUST be a set — mutation #4 (set -> list) silently
    # changes dedup/discard semantics; pin the structural contract here.
    assert isinstance(async_utils._pinned_tasks, set), (
        "_pinned_tasks must be a set: discard() is membership-safe and "
        "deduplicating; a list silently breaks that (mutation #4)"
    )

    release.set()
    for t in list(async_utils._pinned_tasks):
        await t
    await asyncio.sleep(0)

    assert len(async_utils._pinned_tasks) == 0
