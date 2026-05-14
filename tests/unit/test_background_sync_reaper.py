"""Unit tests for the dead-thread reaper in ``background_sync_service``.

Pins the defensive cleanup that catches threads which exited without
hitting the worker's ``finally`` block — uncatchable exceptions
(``KeyboardInterrupt``, ``SystemExit``), abnormal exits, etc.

Without the reaper, dead threads keep references to their captured DB
sessions, GAM clients, and result payloads — slow growth as syncs fail
abnormally over weeks of uptime (production memory-leak triage #5).
"""

from __future__ import annotations

import threading

from src.services.background_sync_service import (
    _active_syncs,
    _reap_dead_syncs,
    get_active_syncs,
    is_sync_running,
)


def _dead_thread() -> threading.Thread:
    """Create and immediately drain a thread (returns a dead one)."""
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    assert not t.is_alive()
    return t


def _live_thread() -> threading.Thread:
    """Create a thread that blocks on an event (so it stays alive)."""
    event = threading.Event()
    t = threading.Thread(target=event.wait, daemon=True)
    t.start()
    # Stash the event so the test can release the thread later
    t._test_release = event  # type: ignore[attr-defined]
    return t


def _release(t: threading.Thread) -> None:
    t._test_release.set()  # type: ignore[attr-defined]
    t.join(timeout=1)


def test_reaper_drops_dead_threads():
    """Dead threads are pruned from the registry."""
    # Setup: poison the registry with a dead entry
    _active_syncs.clear()
    _active_syncs["sync_dead"] = _dead_thread()
    assert "sync_dead" in _active_syncs

    _reap_dead_syncs()

    assert "sync_dead" not in _active_syncs


def test_reaper_keeps_live_threads():
    """Live threads survive a reap."""
    _active_syncs.clear()
    live = _live_thread()
    try:
        _active_syncs["sync_live"] = live
        _reap_dead_syncs()
        assert "sync_live" in _active_syncs
    finally:
        _release(live)
        _active_syncs.clear()


def test_get_active_syncs_reaps_on_read():
    """``get_active_syncs`` returns only currently-alive syncs."""
    _active_syncs.clear()
    live = _live_thread()
    try:
        _active_syncs["sync_alive"] = live
        _active_syncs["sync_zombie"] = _dead_thread()

        result = get_active_syncs()

        assert result == ["sync_alive"]
        assert "sync_zombie" not in _active_syncs
    finally:
        _release(live)
        _active_syncs.clear()


def test_is_sync_running_reaps_on_read():
    """``is_sync_running`` returns False for a dead-thread entry, AND drops it."""
    _active_syncs.clear()
    _active_syncs["sync_zombie"] = _dead_thread()

    assert is_sync_running("sync_zombie") is False
    assert "sync_zombie" not in _active_syncs


def test_reaper_handles_empty_registry():
    """No-op on empty registry — guards against off-by-one bugs."""
    _active_syncs.clear()
    _reap_dead_syncs()
    assert _active_syncs == {}


def test_reaper_handles_mixed_state():
    """Mixed live/dead registry: only the dead are pruned, live remain."""
    _active_syncs.clear()
    live1 = _live_thread()
    live2 = _live_thread()
    try:
        _active_syncs["a_live"] = live1
        _active_syncs["b_dead"] = _dead_thread()
        _active_syncs["c_live"] = live2
        _active_syncs["d_dead"] = _dead_thread()

        _reap_dead_syncs()

        assert set(_active_syncs.keys()) == {"a_live", "c_live"}
    finally:
        _release(live1)
        _release(live2)
        _active_syncs.clear()
