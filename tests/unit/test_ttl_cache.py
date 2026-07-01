"""Unit tests for ThreadSafeTTLCache cardinality bound (FIFO eviction) + expiry.

The ``maxsize`` cap is the cache's only defense against unbounded memory growth:
expiry is lazy (an entry is dropped only when its own key is read after expiry),
so a cache whose keys are buyer-controlled (the property-list cache keyed by
``(agent_url, list_id, auth_partition)``) would otherwise grow without bound on a
long-lived process. These pins exercise the eviction the production cache relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.ttl_cache import ThreadSafeTTLCache

pytestmark = pytest.mark.unit


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def test_store_evicts_oldest_when_over_maxsize():
    # Storing past maxsize drops the OLDEST entry (FIFO), keeps the newest.
    cache: ThreadSafeTTLCache[str, int] = ThreadSafeTTLCache(maxsize=2)
    cache.store("a", 1, _future())
    cache.store("b", 2, _future())
    cache.store("c", 3, _future())  # exceeds maxsize=2 -> evict oldest ("a")

    assert cache.get("a") is None, "oldest entry should have been FIFO-evicted"
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_restoring_existing_key_refreshes_recency():
    # Re-storing a key moves it to the freshest position, so it is NOT the next one
    # evicted (kills a mutant that evicts the newest, i.e. popitem(last=True)).
    cache: ThreadSafeTTLCache[str, int] = ThreadSafeTTLCache(maxsize=2)
    cache.store("a", 1, _future())
    cache.store("b", 2, _future())
    cache.store("a", 10, _future())  # refresh "a" -> "b" is now the oldest
    cache.store("c", 3, _future())  # evict oldest ("b")

    assert cache.get("b") is None
    assert cache.get("a") == 10
    assert cache.get("c") == 3


def test_unbounded_when_maxsize_is_none():
    # maxsize=None disables the cap (the caller opts out of the cardinality bound).
    cache: ThreadSafeTTLCache[int, int] = ThreadSafeTTLCache(maxsize=None)
    for i in range(50):
        cache.store(i, i, _future())
    assert all(cache.get(i) == i for i in range(50))


def test_expired_entry_is_dropped_on_read():
    cache: ThreadSafeTTLCache[str, int] = ThreadSafeTTLCache()
    cache.store("k", 1, datetime.now(UTC) - timedelta(seconds=1))  # already expired
    assert cache.get("k") is None
