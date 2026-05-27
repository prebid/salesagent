"""Regression tests for dead-thread reaping in DeliverySimulator.

DeliverySimulator carries a ThreadRegistry (``_active_simulations``) plus a
parallel ``_stop_signals`` Event dict. salesagent-x2h.3 wired the registry's
reap callback (``_on_simulation_reaped``) so a dead simulation thread, when
reaped on any registry read, also drops its matching ``_stop_signals`` entry
— dual-dict atomic cleanup. Before #1264's deferred fixes, abnormal thread
exits (KeyboardInterrupt / SystemExit / signal-killed) left dead-thread refs
that pinned DB sessions and webhook payloads (memory-leak triage).

These tests pin that invariant directly on a real ``DeliverySimulator``
instance. Mirrors ``tests/unit/test_background_sync_reaper.py`` structure.
"""

from __future__ import annotations

import threading

from src.services.delivery_simulator import DeliverySimulator

from ._thread_registry_helpers import dead_thread as _dead_thread
from ._thread_registry_helpers import live_thread as _live_thread
from ._thread_registry_helpers import release as _release


def _poison(sim: DeliverySimulator, key: str, thread: threading.Thread) -> threading.Event:
    """Inject a registry entry + a parallel _stop_signals entry, as production does."""
    signal = threading.Event()
    sim._stop_signals[key] = signal
    sim._active_simulations.add(key, thread)
    return signal


def test_dead_simulation_thread_reaped_on_read():
    """A dead _active_simulations entry is dropped on the next registry read."""
    sim = DeliverySimulator()
    _poison(sim, "buy_dead", _dead_thread())

    assert sim._active_simulations.contains("buy_dead") is False
    assert sim._active_simulations.list_active() == []


def test_live_simulation_thread_survives_reap():
    """A live simulation entry survives reaping."""
    sim = DeliverySimulator()
    t, release = _live_thread()
    try:
        _poison(sim, "buy_live", t)
        assert sim._active_simulations.contains("buy_live") is True
        assert sim._active_simulations.list_active() == ["buy_live"]
    finally:
        _release(t, release)


def test_mixed_state_only_dead_reaped():
    """Mixed live/dead registry: only dead entries are reaped."""
    sim = DeliverySimulator()
    live1, r1 = _live_thread()
    live2, r2 = _live_thread()
    try:
        _poison(sim, "a_live", live1)
        _poison(sim, "b_dead", _dead_thread())
        _poison(sim, "c_live", live2)
        _poison(sim, "d_dead", _dead_thread())

        assert set(sim._active_simulations.list_active()) == {"a_live", "c_live"}
    finally:
        _release(live1, r1)
        _release(live2, r2)


def test_dual_dict_invariant_dead_entry_drops_stop_signal():
    """Reaping a dead _active_simulations entry also drops its _stop_signals entry.

    This is the dual-dict invariant: the two dicts never drift. The dead
    thread's _stop_signals Event must be gone after the reap so it cannot
    pin memory past the thread's life.
    """
    sim = DeliverySimulator()
    signal = _poison(sim, "buy_zombie", _dead_thread())
    assert "buy_zombie" in sim._stop_signals  # precondition
    assert signal is sim._stop_signals["buy_zombie"]

    # Any registry read reaps the dead entry and fires _on_simulation_reaped.
    assert sim._active_simulations.contains("buy_zombie") is False

    # Dual-dict cleanup: the parallel _stop_signals entry is gone too.
    assert "buy_zombie" not in sim._stop_signals


def test_dual_dict_invariant_live_entry_keeps_stop_signal():
    """A live entry keeps BOTH its registry slot and its _stop_signals entry."""
    sim = DeliverySimulator()
    t, release = _live_thread()
    try:
        signal = _poison(sim, "buy_alive", t)
        sim._active_simulations.list_active()  # triggers a reap pass
        assert sim._active_simulations.contains("buy_alive") is True
        assert sim._stop_signals.get("buy_alive") is signal
    finally:
        _release(t, release)


def test_reap_callback_only_drops_reaped_keys():
    """Reaping one dead entry must not disturb a co-resident live entry's signal."""
    sim = DeliverySimulator()
    live, release = _live_thread()
    try:
        live_signal = _poison(sim, "keep", live)
        _poison(sim, "drop", _dead_thread())

        assert sim._active_simulations.list_active() == ["keep"]
        assert "drop" not in sim._stop_signals
        assert sim._stop_signals.get("keep") is live_signal
    finally:
        _release(live, release)
