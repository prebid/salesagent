"""Shared asyncio helpers for fire-and-forget task lifecycle management.

asyncio's task tracker (``asyncio.tasks._all_tasks``) holds only *weak*
references. A fire-and-forget task whose only strong reference is a local
variable can therefore be garbage-collected mid-execution if the scheduling
function returns before the task finishes — silently dropping webhooks,
WebSocket broadcasts, and other side effects.

``pin_task`` is the single source of truth for the strong-ref pin pattern.
It was previously duplicated as ``_pending_webhook_tasks`` in
``context_manager.py`` and ``_pending_tasks`` in ``activity_feed.py``.
Consolidated here per the CLAUDE.md DRY invariant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

# Module-level strong references to in-flight fire-and-forget asyncio tasks.
# This is the ONLY pinned-task set in the codebase — do not reintroduce
# per-module copies.
_pinned_tasks: set[asyncio.Task] = set()


def pin_task(
    task: asyncio.Task,
    *,
    on_done: Callable[[asyncio.Task], None] | None = None,
) -> asyncio.Task:
    """Pin a fire-and-forget asyncio task against weak-ref GC.

    ``asyncio.tasks._all_tasks`` holds only weak references; a task whose only
    strong reference is a local variable can be GC'd mid-execution. This
    helper adds the task to a module-level strong-ref set and installs a
    done callback that removes it on completion.

    If ``on_done`` is provided, it runs AFTER the discard — so any logging or
    error-swallow in ``on_done`` can't keep the task alive past completion.

    Args:
        task: The task to pin. Returned as-is for call-site chaining.
        on_done: Optional callback invoked with the completed task AFTER it
            has been discarded from the pinned set.

    Returns:
        The same ``task`` object, so callers can write
        ``pin_task(asyncio.create_task(...))`` inline.
    """
    _pinned_tasks.add(task)

    def _cleanup(t: asyncio.Task) -> None:
        _pinned_tasks.discard(t)
        if on_done is not None:
            on_done(t)

    task.add_done_callback(_cleanup)
    return task
