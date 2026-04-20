"""L0-13 — SimpleAppCache obligation tests (Pattern a: stub-first + semantic).

Red state: stub returns default on .get(), no-op on .set(); tests fail
because set→get round-trip does not return the stored value. Green state:
real TTLCache-backed impl per foundation-modules.md §11.15.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI


class TestSetGetRoundtrip:
    def test_set_then_get_returns_value(self) -> None:
        from src.admin.cache import SimpleAppCache

        cache = SimpleAppCache(maxsize=128, ttl=60)
        cache.set("key1", {"payload": "hello"})
        assert cache.get("key1") == {"payload": "hello"}

    def test_get_missing_key_returns_default(self) -> None:
        from src.admin.cache import SimpleAppCache

        cache = SimpleAppCache(maxsize=128, ttl=60)
        assert cache.get("absent") is None
        assert cache.get("absent", default="fallback") == "fallback"

    def test_delete_removes_key(self) -> None:
        from src.admin.cache import SimpleAppCache

        cache = SimpleAppCache(maxsize=128, ttl=60)
        cache.set("k", 1)
        assert cache.delete("k") is True
        assert cache.get("k") is None

    def test_delete_missing_key_returns_false(self) -> None:
        from src.admin.cache import SimpleAppCache

        cache = SimpleAppCache(maxsize=128, ttl=60)
        assert cache.delete("never_set") is False


class TestTTLExpiry:
    def test_expired_entry_is_not_returned(self) -> None:
        from src.admin.cache import SimpleAppCache

        cache = SimpleAppCache(maxsize=128, ttl=1)
        cache.set("k", "v")
        assert cache.get("k") == "v"
        time.sleep(1.05)
        # TTLCache evicts on access after ttl elapsed.
        assert cache.get("k") is None


class TestLRUEviction:
    def test_maxsize_evicts_oldest(self) -> None:
        from src.admin.cache import SimpleAppCache

        cache = SimpleAppCache(maxsize=2, ttl=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        # "a" is the oldest; expect it evicted.
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3


class TestThreadSafety:
    def test_concurrent_set_get_no_race(self) -> None:
        """4 threads interleave set/get on shared keys; no exception, no
        corruption.

        RLock is non-reentrancy-fatal and must guard every read/write path.
        """
        from src.admin.cache import SimpleAppCache

        cache = SimpleAppCache(maxsize=1024, ttl=60)
        errors: list[Exception] = []

        def worker(worker_id: int) -> None:
            try:
                for i in range(500):
                    key = f"w{worker_id}_k{i % 32}"
                    cache.set(key, f"v{i}")
                    cache.get(key)
                    if i % 10 == 0:
                        cache.delete(key)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


class TestInstallAppCache:
    def test_attaches_to_app_state(self, monkeypatch) -> None:
        """``install_app_cache(app)`` MUST write to ``app.state.inventory_cache``
        (this is the access path admin handlers rely on)."""
        from src.admin.cache import SimpleAppCache, _reset_app_cache_for_tests, install_app_cache

        _reset_app_cache_for_tests()
        app = FastAPI()
        cache = install_app_cache(app)

        assert isinstance(cache, SimpleAppCache)
        assert getattr(app.state, "inventory_cache", None) is cache

    def test_is_idempotent(self) -> None:
        """Calling install twice returns the same instance (module global is
        not re-created on second call)."""
        from src.admin.cache import _reset_app_cache_for_tests, install_app_cache

        _reset_app_cache_for_tests()
        app = FastAPI()
        c1 = install_app_cache(app)
        c2 = install_app_cache(app)
        assert c1 is c2

    def test_honors_env_overrides(self, monkeypatch) -> None:
        """ADCP_INVENTORY_CACHE_MAXSIZE / ADCP_INVENTORY_CACHE_TTL env vars
        override the defaults."""
        from src.admin.cache import _reset_app_cache_for_tests, install_app_cache

        monkeypatch.setenv("ADCP_INVENTORY_CACHE_MAXSIZE", "42")
        monkeypatch.setenv("ADCP_INVENTORY_CACHE_TTL", "7")

        _reset_app_cache_for_tests()
        app = FastAPI()
        cache = install_app_cache(app)

        stats = cache.stats
        assert stats["maxsize"] == 42
        assert stats["ttl"] == 7


class TestGetAppCacheFallback:
    def test_returns_null_cache_before_install(self) -> None:
        """Before ``install_app_cache`` runs, ``get_app_cache()`` returns a
        ``_NullAppCache`` stub. This is the lifespan startup-race window."""
        from src.admin.cache import _NullAppCache, _reset_app_cache_for_tests, get_app_cache

        _reset_app_cache_for_tests()
        cache = get_app_cache()
        assert isinstance(cache, _NullAppCache)

    def test_null_cache_absorbs_operations(self) -> None:
        from src.admin.cache import _NullAppCache

        null = _NullAppCache()
        null.set("k", "v")
        assert null.get("k") is None
        assert null.delete("k") is False

    def test_returns_real_cache_after_install(self) -> None:
        from src.admin.cache import SimpleAppCache, _reset_app_cache_for_tests, get_app_cache, install_app_cache

        _reset_app_cache_for_tests()
        app = FastAPI()
        install_app_cache(app)
        cache = get_app_cache()
        assert isinstance(cache, SimpleAppCache)


class TestCacheBackendProtocol:
    def test_simple_app_cache_satisfies_protocol(self) -> None:
        """``SimpleAppCache`` is usable wherever a ``CacheBackend`` is
        expected (v2.2 Redis swap target)."""
        from src.admin.cache import CacheBackend, SimpleAppCache

        cache: CacheBackend = SimpleAppCache()
        cache.set("k", 1)
        assert cache.get("k") == 1


@pytest.fixture(autouse=True)
def _cleanup_global() -> None:
    """Reset the module-level ``_APP_CACHE`` between tests so no test sees
    another test's state."""
    from src.admin.cache import _reset_app_cache_for_tests

    _reset_app_cache_for_tests()
    yield
    _reset_app_cache_for_tests()
