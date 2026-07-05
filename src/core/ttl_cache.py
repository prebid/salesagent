"""Thread-safe TTL cache shared by the resolver-layer caches."""

from __future__ import annotations

import hashlib
import hmac
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")

_DEFAULT_MAXSIZE = 10_000


def cache_partition_token(secret: str | None, purpose: bytes) -> str:
    """Non-reversible 16-hex cache-partition token derived from a credential.

    The credential is the HMAC KEY over a constant ``purpose`` label (a keyed
    PRF / HKDF-extract shape), so the plaintext secret never persists as a
    process-global cache key while two callers holding different credentials
    partition into separate entries. A missing secret maps to a stable
    sentinel so unauthenticated callers share one partition.

    Shared by the property-list resolver and the Kevel site resolver, which
    previously hand-rolled this derivation separately — a copy-paste of a
    security primitive is a single point of failure for cache isolation, where
    a change to one copy silently diverges the other.
    """
    return hmac.new((secret or "").encode(), purpose, hashlib.sha256).hexdigest()[:16]


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
    - cardinality is bounded by ``maxsize`` (FIFO eviction on ``store``): expiry
      is lazy (an entry is dropped only when its own key is read after expiry),
      so a cache with caller/buyer-controlled keys would otherwise grow without
      bound on a long-lived process; the cap makes growth independent of caller
      behavior.

    TTL policy stays caller-side: callers compute ``expires_at`` (fixed TTL,
    service-supplied ``cache_valid_until``, …) and the cache only enforces it.
    """

    def __init__(self, maxsize: int | None = _DEFAULT_MAXSIZE) -> None:
        self._entries: OrderedDict[K, tuple[V, datetime]] = OrderedDict()
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def get(self, key: K) -> V | None:
        """Return the cached value, dropping and missing on expiry.

        The returned value is the SHARED stored reference, not a copy: treat it as
        immutable. Mutating it corrupts the entry for every other reader (the cache
        is shared across threads/event loops). All current callers read-only.
        """
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
            self._entries.move_to_end(key)  # freshest entry last
            # Bound cardinality: expiry is lazy (pop-on-read only), so a cache
            # whose keys are caller/buyer-controlled — e.g. the property-list
            # cache keyed by (agent_url, list_id), both buyer-supplied — would
            # otherwise grow without bound. Evict the oldest entries (FIFO).
            if self._maxsize is not None:
                while len(self._entries) > self._maxsize:
                    self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
