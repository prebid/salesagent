"""In-process inventory cache for admin routes and background-sync threads.

Replaces ``flask-caching`` ``SimpleCache`` with a thread-safe wrapper over
``cachetools.TTLCache``. v2.0 is hard-constrained to single-worker uvicorn,
so a single per-process instance is semantically identical to the Flask
version. v2.2 will swap in a Redis-backed ``CacheBackend`` Protocol
implementation.

Access paths:
  - FastAPI admin handlers:  ``request.app.state.inventory_cache``
  - Background ``threading.Thread`` workers: ``get_app_cache()``

Sync L0-L4 foundation per ``.claude/notes/flask-to-fastapi/CLAUDE.md``
Invariant #4. Lock is ``threading.RLock`` (NOT ``asyncio.Lock``) per
Decision 6 — the background sync thread is sync and cannot ``await``.

Canonical spec: ``flask-to-fastapi-foundation-modules.md §11.15``.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Protocol

from cachetools import TTLCache
from fastapi import FastAPI

logger = logging.getLogger(__name__)

_DEFAULT_MAXSIZE = 1024
_DEFAULT_TTL_SECONDS = 300


class CacheBackend(Protocol):
    """Minimal backend interface — v2.2 Redis swap conforms to this."""

    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def delete(self, key: str) -> bool: ...


class SimpleAppCache:
    """Thread-safe TTL cache with a dict-like get/set/delete API.

    Invariants:
      - All access guarded by ``threading.RLock``. Safe under concurrent access
        from the asyncio event loop thread AND anyio threadpool workers AND
        background ``threading.Thread`` workers. ``RLock`` (not ``Lock``) so
        nested helpers can safely re-enter.
      - Single uniform TTL per cache instance; per-key TTL intentionally NOT
        supported — all consumer sites use 300s.
      - LRU-evicted when maxsize exceeded; every cached entry is
        reconstructible from the database.
      - ``.get()`` on missing/expired key returns default, never raises.
      - ``.delete()`` on missing key returns ``False``, never raises.
    """

    def __init__(self, maxsize: int = _DEFAULT_MAXSIZE, ttl: int = _DEFAULT_TTL_SECONDS) -> None:
        self._cache: TTLCache[str, Any] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = threading.RLock()
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            try:
                return self._cache[key]
            except KeyError:
                return default

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value

    def delete(self, key: str) -> bool:
        with self._lock:
            try:
                del self._cache[key]
                return True
            except KeyError:
                return False

    def clear(self) -> None:
        """Test-only helper; not called from production code."""
        with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"size": len(self._cache), "maxsize": self._maxsize, "ttl": self._ttl}


class _NullAppCache:
    """No-op fallback returned by ``get_app_cache()`` when
    ``install_app_cache()`` has not yet run (lifespan startup race window).
    Mirrors the latent-broken behavior of the pre-migration Flask code where
    ``background_sync_service.py``'s try/except silently ate invalidation
    failures.
    """

    def get(self, key: str, default: Any = None) -> Any:
        return default

    def set(self, key: str, value: Any) -> None:
        return None

    def delete(self, key: str) -> bool:
        return False


_APP_CACHE: SimpleAppCache | _NullAppCache = _NullAppCache()
_INSTALL_LOCK = threading.Lock()


def install_app_cache(app: FastAPI) -> SimpleAppCache:
    """Create the process-wide ``SimpleAppCache`` and attach it to the app.

    MUST be called from the FastAPI lifespan startup block BEFORE ``yield``.
    Safe to call multiple times (idempotent — the second call returns the
    same instance).
    """
    global _APP_CACHE
    with _INSTALL_LOCK:
        if isinstance(_APP_CACHE, SimpleAppCache):
            # Keep app.state in sync even on a repeat install (e.g., pytest
            # FastAPI fixtures create a fresh app but reuse the global cache).
            app.state.inventory_cache = _APP_CACHE
            return _APP_CACHE
        maxsize = int(os.environ.get("ADCP_INVENTORY_CACHE_MAXSIZE", _DEFAULT_MAXSIZE))
        ttl = int(os.environ.get("ADCP_INVENTORY_CACHE_TTL", _DEFAULT_TTL_SECONDS))
        cache = SimpleAppCache(maxsize=maxsize, ttl=ttl)
        _APP_CACHE = cache
        app.state.inventory_cache = cache
        logger.info(
            "SimpleAppCache installed (maxsize=%d, ttl=%ds) at app.state.inventory_cache",
            maxsize,
            ttl,
        )
        return cache


def get_app_cache() -> SimpleAppCache | _NullAppCache:
    """Return the process-wide cache. Safe to call from any thread.

    Returns a ``_NullAppCache`` stub if ``install_app_cache()`` has not yet
    run (lifespan startup race window). Callers MUST NOT check the type —
    treat the return value as opaque and rely on the get/set/delete
    contract.
    """
    return _APP_CACHE


def _reset_app_cache_for_tests() -> None:
    """Test-only: reset the module global between tests. NOT for production."""
    global _APP_CACHE
    with _INSTALL_LOCK:
        _APP_CACHE = _NullAppCache()
