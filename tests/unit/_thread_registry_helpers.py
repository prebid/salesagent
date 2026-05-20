"""Shared thread-lifecycle helpers for reaper / ThreadRegistry tests.

Single source of truth for the create-a-dead-thread / create-a-live-thread /
release-a-live-thread trio. Before this module each reaper test file
duplicated these verbatim — a CLAUDE.md DRY invariant violation that the
``check_code_duplication.py`` ratchet caught.

The leading underscore keeps pytest from collecting this as a test module.
"""

from __future__ import annotations

import threading


def dead_thread() -> threading.Thread:
    """Create and immediately drain a thread (returns a dead one)."""
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    assert not t.is_alive()
    return t


def live_thread() -> tuple[threading.Thread, threading.Event]:
    """Create a thread blocking on an Event. Returns ``(thread, release_event)``."""
    event = threading.Event()
    t = threading.Thread(target=event.wait, daemon=True)
    t.start()
    return t, event


def release(t: threading.Thread, event: threading.Event) -> None:
    """Release a live thread created by :func:`live_thread` and join it."""
    event.set()
    t.join(timeout=2)
    assert not t.is_alive()
