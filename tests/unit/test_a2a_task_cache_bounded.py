"""Unit tests for the A2A bounded task cache.

Replaces the previously-unbounded ``self.tasks = {}`` in
``AdCPRequestHandler``. The unbounded dict was the dominant production
memory leak (linear ramp to ~12 GB ceiling, ~3-4 day OOM cycle).

Tests pin the eviction contract so a future refactor can't silently
remove the bounds.

Covers:
- Bounded LRU eviction: oldest entry drops when ``max_entries`` exceeded
- TTL expiry: entries past ``ttl_seconds`` return ``default`` and are dropped
- Dict-shape duck-typing: ``__setitem__`` / ``get`` / ``__contains__`` /
  ``__len__`` so existing call sites continue to work without changes
- Constructor validation: zero or negative bounds raise ``ValueError``
- Env-var override path: ``ADCP_A2A_TASK_CACHE_SIZE`` /
  ``ADCP_A2A_TASK_CACHE_TTL_SECONDS`` flow through ``_build_task_cache()``
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.a2a_server.adcp_a2a_server import _BoundedTaskCache, _build_task_cache


def _fake_task(task_id: str = "t") -> MagicMock:
    """Stand-in for a real ``a2a.types.Task`` — only identity matters here."""
    m = MagicMock()
    m.id = task_id
    return m


# ---------------------------------------------------------------------------
# LRU bound — the load-bearing leak fix
# ---------------------------------------------------------------------------


class TestLruBound:
    def test_max_entries_is_a_hard_ceiling(self) -> None:
        cache = _BoundedTaskCache(max_entries=3, ttl_seconds=60)
        cache["a"] = _fake_task("a")
        cache["b"] = _fake_task("b")
        cache["c"] = _fake_task("c")
        cache["d"] = _fake_task("d")  # evicts oldest (a)

        assert len(cache) == 3
        assert "a" not in cache
        assert "b" in cache
        assert "c" in cache
        assert "d" in cache

    def test_lru_touch_on_read_keeps_entry_alive(self) -> None:
        """Reading an entry promotes it to MRU; older unread entries evict first."""
        cache = _BoundedTaskCache(max_entries=2, ttl_seconds=60)
        cache["a"] = _fake_task("a")
        cache["b"] = _fake_task("b")
        cache.get("a")  # 'a' becomes MRU; 'b' is now LRU
        cache["c"] = _fake_task("c")  # evicts 'b', not 'a'

        assert "a" in cache
        assert "b" not in cache
        assert "c" in cache

    def test_overwrite_does_not_grow_past_ceiling(self) -> None:
        """Writing the same key twice doesn't drift the cache size."""
        cache = _BoundedTaskCache(max_entries=2, ttl_seconds=60)
        cache["a"] = _fake_task("a-v1")
        cache["a"] = _fake_task("a-v2")
        cache["b"] = _fake_task("b")
        cache["c"] = _fake_task("c")  # evicts 'a' (now LRU)

        assert len(cache) == 2
        assert "b" in cache
        assert "c" in cache


# ---------------------------------------------------------------------------
# TTL — the secondary line of defence (lazy eviction on read)
# ---------------------------------------------------------------------------


class TestTtlEviction:
    def test_expired_entry_returns_default_and_drops(self, monkeypatch) -> None:
        """Entry past ``ttl_seconds`` returns the ``default`` and is removed
        from the cache (lazy on read; no background sweeper)."""
        clock = [1000.0]

        def fake_monotonic() -> float:
            return clock[0]

        monkeypatch.setattr("src.a2a_server.adcp_a2a_server.time.monotonic", fake_monotonic)

        cache = _BoundedTaskCache(max_entries=8, ttl_seconds=60)
        task = _fake_task("a")
        cache["a"] = task
        assert cache.get("a") is task

        clock[0] += 61  # past TTL
        assert cache.get("a") is None
        assert cache.get("a", "sentinel") == "sentinel"
        assert len(cache) == 0  # entry was dropped on expired read

    def test_within_ttl_returns_value(self, monkeypatch) -> None:
        clock = [1000.0]
        monkeypatch.setattr("src.a2a_server.adcp_a2a_server.time.monotonic", lambda: clock[0])

        cache = _BoundedTaskCache(max_entries=8, ttl_seconds=60)
        task = _fake_task("a")
        cache["a"] = task

        clock[0] += 30
        assert cache.get("a") is task
        assert "a" in cache


# ---------------------------------------------------------------------------
# Dict-shape duck-typing — call sites use [] / .get() / in / len()
# ---------------------------------------------------------------------------


class TestDictShape:
    def test_setitem_and_get(self) -> None:
        cache = _BoundedTaskCache(max_entries=8, ttl_seconds=60)
        task = _fake_task("a")
        cache["a"] = task
        assert cache.get("a") is task

    def test_get_default_for_missing_key(self) -> None:
        cache = _BoundedTaskCache(max_entries=8, ttl_seconds=60)
        assert cache.get("missing") is None
        assert cache.get("missing", "default") == "default"

    def test_contains_for_present_and_missing_keys(self) -> None:
        cache = _BoundedTaskCache(max_entries=8, ttl_seconds=60)
        cache["a"] = _fake_task("a")
        assert "a" in cache
        assert "missing" not in cache
        # Non-string keys (defensive — A2A task ids are always strings)
        assert 42 not in cache  # type: ignore[operator]

    def test_len_reflects_active_entries(self) -> None:
        cache = _BoundedTaskCache(max_entries=8, ttl_seconds=60)
        assert len(cache) == 0
        cache["a"] = _fake_task("a")
        cache["b"] = _fake_task("b")
        assert len(cache) == 2


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestConstructorValidation:
    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_max_entries_must_be_positive(self, bad: int) -> None:
        with pytest.raises(ValueError, match="max_entries"):
            _BoundedTaskCache(max_entries=bad, ttl_seconds=60)

    @pytest.mark.parametrize("bad", [0, -0.1, -60.0])
    def test_ttl_must_be_positive(self, bad: float) -> None:
        with pytest.raises(ValueError, match="ttl_seconds"):
            _BoundedTaskCache(max_entries=10, ttl_seconds=bad)


# ---------------------------------------------------------------------------
# Env-var override path used by AdCPRequestHandler.__init__
# ---------------------------------------------------------------------------


class TestEnvOverride:
    def test_defaults_when_env_absent(self, monkeypatch) -> None:
        monkeypatch.delenv("ADCP_A2A_TASK_CACHE_SIZE", raising=False)
        monkeypatch.delenv("ADCP_A2A_TASK_CACHE_TTL_SECONDS", raising=False)

        cache = _build_task_cache()
        assert cache._max == 10_000  # noqa: SLF001 — testing the bound directly
        assert cache._ttl == 86_400.0  # noqa: SLF001

    def test_env_overrides_apply(self, monkeypatch) -> None:
        monkeypatch.setenv("ADCP_A2A_TASK_CACHE_SIZE", "500")
        monkeypatch.setenv("ADCP_A2A_TASK_CACHE_TTL_SECONDS", "3600")

        cache = _build_task_cache()
        assert cache._max == 500  # noqa: SLF001
        assert cache._ttl == 3600.0  # noqa: SLF001
