"""Thread-safe TTL cache shared by the resolver-layer caches."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class ThreadSafeTTLCache(Generic[K, V]):
    """Expiring ``key -> (value, expires_at)`` map with locked dict operations.

    The locking contract its consumers (the property-list resolver and the
    Kevel site resolver) previously hand-rolled separately, decided once:

    - every read/write/clear holds the lock, so concurrent callers never see
      a torn entry;
    - the expiry-drop uses ``pop`` (not ``del``) so a concurrent caller that
      already refreshed or removed the key cannot raise ``KeyError``;
    - the slow fetch that produces a value happens OUTSIDE the cache (callers
      fetch, then ``store``) — a cold-cache double-fetch is acceptable (both
      fetches produce the same data) and the store is last-write-wins, which
      avoids the complexity of a fetch-in-progress sentinel.

    TTL policy stays caller-side: callers compute ``expires_at`` (fixed TTL,
    service-supplied ``cache_valid_until``, …) and the cache only enforces it.
    """

    def __init__(self) -> None:
        self._entries: dict[K, tuple[V, datetime]] = {}
        self._lock = threading.Lock()

    def get(self, key: K) -> V | None:
        """Return the cached value, dropping and missing on expiry."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if datetime.now(UTC) >= expires_at:
                self._entries.pop(key, None)
                return None
            return value

    def store(self, key: K, value: V, expires_at: datetime) -> None:
        """Store ``value`` under ``key`` until ``expires_at`` (last-write-wins)."""
        with self._lock:
            self._entries[key] = (value, expires_at)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
