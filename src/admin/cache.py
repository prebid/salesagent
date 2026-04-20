"""L0-13 STUB (Red commit) — real implementation lands in Green commit.

Canonical spec: flask-to-fastapi-foundation-modules.md §11.15.
"""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import FastAPI


class CacheBackend(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def delete(self, key: str) -> bool: ...


class SimpleAppCache:
    """STUB — no storage, .get() always returns default, .set() is no-op."""

    def __init__(self, maxsize: int = 1024, ttl: int = 300) -> None:
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key: str, default: Any = None) -> Any:
        return default

    def set(self, key: str, value: Any) -> None:
        return None

    def delete(self, key: str) -> bool:
        return False

    def clear(self) -> None:
        return None

    @property
    def stats(self) -> dict[str, int]:
        return {"size": 0, "maxsize": self._maxsize, "ttl": self._ttl}


class _NullAppCache:
    def get(self, key: str, default: Any = None) -> Any:
        return default

    def set(self, key: str, value: Any) -> None:
        return None

    def delete(self, key: str) -> bool:
        return False


_APP_CACHE: SimpleAppCache | _NullAppCache = _NullAppCache()


def install_app_cache(app: FastAPI) -> SimpleAppCache:  # pragma: no cover
    """STUB — does not actually register on app.state."""
    return SimpleAppCache()


def get_app_cache() -> SimpleAppCache | _NullAppCache:
    return _APP_CACHE


def _reset_app_cache_for_tests() -> None:  # pragma: no cover
    global _APP_CACHE
    _APP_CACHE = _NullAppCache()
