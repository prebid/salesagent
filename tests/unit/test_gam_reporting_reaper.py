"""Regression tests for dead-thread reaping in GAMReportingManager.

GAMReportingManager carries a ThreadRegistry (``_active_reports``) plus a
parallel ``_stop_signals`` Event dict. salesagent-x2h.3 wired the registry's
reap callback (``_on_report_reaped``) so a dead reporting thread, when reaped
on any registry read, also drops its matching ``_stop_signals`` entry —
dual-dict atomic cleanup. Lower volume than the simulator (one entry per
active media-buy report, long-lived) but subject to the same
KeyboardInterrupt / SystemExit failure modes that bypass the ``finally``
block (PR #1264 deferred fix).

Mirrors ``tests/unit/test_delivery_simulator_reaper.py``.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from src.adapters.gam.managers.reporting import GAMReportingManager

from ._thread_registry_helpers import dead_thread, live_thread, release


def _manager() -> GAMReportingManager:
    """A GAMReportingManager with stub deps (constructor only stores them)."""
    return GAMReportingManager(gam_client=MagicMock(), config={})


def _poison(mgr: GAMReportingManager, key: str, thread: threading.Thread) -> threading.Event:
    """Inject a registry entry + a parallel _stop_signals entry, as production does."""
    signal = threading.Event()
    mgr._stop_signals[key] = signal
    mgr._active_reports.add(key, thread)
    return signal


def test_dead_report_thread_reaped_on_read():
    """A dead _active_reports entry is dropped on the next registry read."""
    mgr = _manager()
    _poison(mgr, "buy_dead", dead_thread())

    assert mgr._active_reports.contains("buy_dead") is False
    assert mgr._active_reports.list_active() == []


def test_live_report_thread_survives_reap():
    """A live reporting entry survives reaping."""
    mgr = _manager()
    t, ev = live_thread()
    try:
        _poison(mgr, "buy_live", t)
        assert mgr._active_reports.contains("buy_live") is True
        assert mgr._active_reports.list_active() == ["buy_live"]
    finally:
        release(t, ev)


def test_mixed_state_only_dead_reaped():
    """Mixed live/dead registry: only dead entries are reaped."""
    mgr = _manager()
    live1, ev1 = live_thread()
    live2, ev2 = live_thread()
    try:
        _poison(mgr, "a_live", live1)
        _poison(mgr, "b_dead", dead_thread())
        _poison(mgr, "c_live", live2)
        _poison(mgr, "d_dead", dead_thread())

        assert set(mgr._active_reports.list_active()) == {"a_live", "c_live"}
    finally:
        release(live1, ev1)
        release(live2, ev2)


def test_dual_dict_invariant_dead_entry_drops_stop_signal():
    """Reaping a dead _active_reports entry also drops its _stop_signals entry."""
    mgr = _manager()
    signal = _poison(mgr, "buy_zombie", dead_thread())
    assert "buy_zombie" in mgr._stop_signals  # precondition
    assert signal is mgr._stop_signals["buy_zombie"]

    # Any registry read reaps the dead entry and fires _on_report_reaped.
    assert mgr._active_reports.contains("buy_zombie") is False

    # Dual-dict cleanup: the parallel _stop_signals entry is gone too.
    assert "buy_zombie" not in mgr._stop_signals


def test_dual_dict_invariant_live_entry_keeps_stop_signal():
    """A live entry keeps BOTH its registry slot and its _stop_signals entry."""
    mgr = _manager()
    t, ev = live_thread()
    try:
        signal = _poison(mgr, "buy_alive", t)
        mgr._active_reports.list_active()  # triggers a reap pass
        assert mgr._active_reports.contains("buy_alive") is True
        assert mgr._stop_signals.get("buy_alive") is signal
    finally:
        release(t, ev)


def test_reap_callback_only_drops_reaped_keys():
    """Reaping one dead entry must not disturb a co-resident live entry's signal."""
    mgr = _manager()
    live, ev = live_thread()
    try:
        live_signal = _poison(mgr, "keep", live)
        _poison(mgr, "drop", dead_thread())

        assert mgr._active_reports.list_active() == ["keep"]
        assert "drop" not in mgr._stop_signals
        assert mgr._stop_signals.get("keep") is live_signal
    finally:
        release(live, ev)
