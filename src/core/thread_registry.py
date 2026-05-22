"""Registry of named background threads with built-in dead-thread reaping.

Consolidates the dict + lock + reaper + accessors pattern that PR #1264
duplicated across 5 sites (3 module-level services + 2 instance-level
adapters/services). Per the CLAUDE.md DRY invariant: duplicated code is a
defect — a bug fixed in one copy is missed in the others (this happened in
#1264).

The reaper drops entries whose threads are not ``is_alive()``. A thread's
own ``finally`` block normally cleans up, but abnormal exits
(KeyboardInterrupt, SystemExit, signal-killed workers) leave dead-thread
refs that pin DB sessions, GAM clients, and result payloads. Reaping runs on
every read so dead-thread entries never leak past one observation.

Callers never see the lock; every public method takes/releases it
internally.

Dual-dict sites (delivery_simulator, gam/reporting) keep a parallel
``_stop_signals`` dict. ``add_reap_callback`` lets them drop the parallel
entry in lockstep when a key is reaped — the callback is invoked OUTSIDE
the registry lock with each reaped key, and must itself be lock-free or
acquire only locks that are never held while calling into this registry
(avoids ABBA deadlock).
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class ThreadRegistry:
    """Thread-safe registry of named background threads that reaps dead ones."""

    def __init__(self) -> None:
        self._reg: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._reap_callbacks: list[Callable[[str], None]] = []

    def add_reap_callback(self, callback: Callable[[str], None]) -> None:
        """Register a callback fired (once per key) when a dead entry is reaped.

        Used by dual-dict sites to drop their parallel ``_stop_signals`` entry.
        Callbacks run OUTSIDE the registry lock; keep them lock-free.
        """
        self._reap_callbacks.append(callback)

    def add(self, key: str, thread: threading.Thread) -> None:
        """Register ``thread`` under ``key`` (last-writer-wins)."""
        with self._lock:
            self._reg[key] = thread

    def remove(self, key: str) -> None:
        """Drop ``key`` from the registry. Idempotent — no error if missing."""
        with self._lock:
            self._reg.pop(key, None)

    def list_active(self) -> list[str]:
        """Reap dead entries, then return the live keys."""
        reaped = self._reap_locked()
        self._fire_callbacks(reaped)
        with self._lock:
            return list(self._reg.keys())

    def contains(self, key: str) -> bool:
        """Reap dead entries, then report whether ``key`` is registered."""
        reaped = self._reap_locked()
        self._fire_callbacks(reaped)
        with self._lock:
            return key in self._reg

    def get(self, key: str) -> threading.Thread | None:
        """Reap dead entries, then return the thread for ``key`` (or None).

        Mainly for callers that need to ``join()`` the underlying thread.
        """
        reaped = self._reap_locked()
        self._fire_callbacks(reaped)
        with self._lock:
            return self._reg.get(key)

    def contains_substring(self, needle: str) -> bool:
        """Reap dead entries, then report whether any key contains ``needle``.

        Mirrors ``background_approval_service.is_approval_task_running``'s
        ``any(order_id in task_id ...)`` semantics.
        """
        reaped = self._reap_locked()
        self._fire_callbacks(reaped)
        with self._lock:
            return any(needle in key for key in self._reg)

    def reap_dead(self) -> None:
        """Public reap trigger — drop dead entries and fire callbacks.

        Lets dual-dict sites force a coordinated reap from their own
        accessors before reading the parallel ``_stop_signals`` dict.
        """
        reaped = self._reap_locked()
        self._fire_callbacks(reaped)

    def _reap_locked(self) -> list[str]:
        """Drop entries whose threads are dead. Returns the reaped keys."""
        with self._lock:
            dead = [key for key, t in self._reg.items() if not t.is_alive()]
            for key in dead:
                self._reg.pop(key, None)
        return dead

    def _fire_callbacks(self, reaped: list[str]) -> None:
        """Invoke reap callbacks for each reaped key, outside the registry lock."""
        if not reaped or not self._reap_callbacks:
            return
        for key in reaped:
            for callback in self._reap_callbacks:
                callback(key)
