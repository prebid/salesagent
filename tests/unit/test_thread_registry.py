"""Unit tests for src.core.thread_registry.ThreadRegistry.

ThreadRegistry consolidates the dead-thread reaper pattern that was
duplicated across 5 sites (PR #1264 + follow-ups). It combines dict + lock +
reaper + accessors so callers never see the lock and dead-thread entries
never leak past one observation.
"""

from __future__ import annotations

import threading

from src.core.thread_registry import ThreadRegistry

from ._thread_registry_helpers import dead_thread as _dead_thread
from ._thread_registry_helpers import live_thread as _live_thread
from ._thread_registry_helpers import release as _release


def test_add_then_list_active_returns_key():
    """add() a live thread, list_active() returns its key."""
    reg = ThreadRegistry()
    t, release = _live_thread()
    try:
        reg.add("k1", t)
        assert reg.list_active() == ["k1"]
    finally:
        _release(t, release)


def test_dead_thread_reaped_on_list_active():
    """A dead thread is dropped on list_active() (reap on read)."""
    reg = ThreadRegistry()
    reg.add("dead", _dead_thread())
    assert reg.list_active() == []


def test_dead_thread_reaped_on_contains():
    """contains() reaps and returns False for a dead-thread entry."""
    reg = ThreadRegistry()
    reg.add("dead", _dead_thread())
    assert reg.contains("dead") is False


def test_contains_substring_partial_match():
    """contains_substring() finds keys by partial match (is_approval_task_running)."""
    reg = ThreadRegistry()
    t, release = _live_thread()
    try:
        reg.add("approval_order_42_xyz", t)
        assert reg.contains_substring("order_42") is True
        assert reg.contains_substring("order_99") is False
    finally:
        _release(t, release)


def test_contains_substring_reaps_dead():
    """contains_substring() reaps dead entries before matching."""
    reg = ThreadRegistry()
    reg.add("approval_order_7", _dead_thread())
    assert reg.contains_substring("order_7") is False


def test_remove_is_idempotent():
    """remove(key) does not raise on a missing key."""
    reg = ThreadRegistry()
    reg.remove("never_added")  # no KeyError
    t, release = _live_thread()
    try:
        reg.add("k", t)
        reg.remove("k")
        reg.remove("k")  # second remove is a no-op
        assert reg.list_active() == []
    finally:
        _release(t, release)


def test_concurrent_add_does_not_lose_entries():
    """N threads each add() once; the lock prevents lost entries."""
    reg = ThreadRegistry()
    threads_and_events: list[tuple[threading.Thread, threading.Event]] = [_live_thread() for _ in range(20)]
    barrier = threading.Barrier(20)

    def adder(i: int) -> None:
        barrier.wait()
        reg.add(f"k{i}", threads_and_events[i][0])

    adders = [threading.Thread(target=adder, args=(i,)) for i in range(20)]
    for a in adders:
        a.start()
    for a in adders:
        a.join()

    try:
        assert sorted(reg.list_active()) == sorted(f"k{i}" for i in range(20))
    finally:
        for t, release in threads_and_events:
            _release(t, release)


def test_reap_callback_invoked_with_reaped_key():
    """add_reap_callback() callbacks fire with each reaped key (dual-dict sibling cleanup)."""
    reg = ThreadRegistry()
    reaped: list[str] = []
    reg.add_reap_callback(reaped.append)

    reg.add("dead1", _dead_thread())
    reg.add("dead2", _dead_thread())
    t, release = _live_thread()
    try:
        reg.add("live", t)
        # Triggering any read reaps both dead entries.
        active = reg.list_active()
        assert active == ["live"]
        assert sorted(reaped) == ["dead1", "dead2"]
    finally:
        _release(t, release)


def test_public_reap_dead_runs_callbacks_and_locks_internally():
    """reap_dead() is a public method that reaps + fires callbacks, locking internally."""
    reg = ThreadRegistry()
    reaped: list[str] = []
    reg.add_reap_callback(reaped.append)
    reg.add("gone", _dead_thread())

    reg.reap_dead()

    assert reg.list_active() == []
    assert reaped == ["gone"]


def test_get_returns_thread_or_none_and_reaps():
    """get() returns the live thread, None for missing, and reaps dead first."""
    reg = ThreadRegistry()
    assert reg.get("missing") is None

    t, release = _live_thread()
    try:
        reg.add("k", t)
        assert reg.get("k") is t
    finally:
        _release(t, release)

    reg.add("dead", _dead_thread())
    assert reg.get("dead") is None  # reaped on read


def test_add_replaces_existing_key():
    """add() with an existing key replaces the thread (last-writer-wins)."""
    reg = ThreadRegistry()
    t1, r1 = _live_thread()
    t2, r2 = _live_thread()
    try:
        reg.add("k", t1)
        reg.add("k", t2)
        assert reg.list_active() == ["k"]
    finally:
        _release(t1, r1)
        _release(t2, r2)
