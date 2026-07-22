"""Unit tests for the TMP health-check background scheduler.

Tests the scheduler in src/services/tmp_health_scheduler.py which polls
each active/draining TMP provider's /health endpoint and writes the result
to health_status / last_health_checked_at columns.

Covers:
- _check_provider_health: healthy on 200, unhealthy on non-200, error on exception
- _check_all_providers (tick): multi-provider fan-out, skip when no providers, error isolation
- Scheduler lifecycle: start/stop, singleton pattern, CancelledError handling
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from src.services.tmp_health_scheduler import (
    TMPHealthScheduler,
    _check_provider_health,
    get_tmp_health_scheduler,
)

# ── Shared helpers ──────────────────────────────────────────────────


def _make_async_http_client(
    *, get_return: MagicMock | None = None, get_side_effect: Exception | None = None
) -> AsyncMock:
    """Build a mock ``httpx.AsyncClient`` usable as an async context manager.

    Exactly one of *get_return* or *get_side_effect* must be provided.
    """
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=get_return, side_effect=get_side_effect)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _make_db_context(session: MagicMock) -> MagicMock:
    """Return a ``MagicMock`` that behaves like ``get_db_session()``'s context manager."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _make_mock_provider(provider_id: str, tenant_id: str, endpoint: str) -> MagicMock:
    """Return a lightweight mock TMPProvider with the three fields tick() reads.

    Named ``_make_mock_provider`` (not ``_make_provider``) to distinguish it from
    ``tests.unit._tmp_helpers._make_provider``, which returns a real ``TMPProvider``
    ORM instance.  The health scheduler only needs the three fields that ``tick()``
    reads (provider_id, tenant_id, endpoint) — a MagicMock is sufficient and avoids
    the DetachedInstanceError risk that real ORM instances carry outside a session.
    """
    p = MagicMock()
    p.provider_id = provider_id
    p.tenant_id = tenant_id
    p.endpoint = endpoint
    return p


def _make_tmp_uow_cls(mock_repo: MagicMock) -> MagicMock:
    """Return a mock TMPProviderUoW class whose instances expose tmp_providers=mock_repo."""
    mock_uow = MagicMock()
    mock_uow.tmp_providers = mock_repo
    mock_uow_cls = MagicMock()
    mock_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_uow_cls


class TestCheckProviderHealth:
    """_check_provider_health probes a single provider's /health endpoint."""

    @pytest.mark.asyncio
    async def test_returns_healthy_on_200(self):
        """200 response → 'healthy'."""
        mock_resp = MagicMock(status_code=200)
        mock_client = _make_async_http_client(get_return=mock_resp)

        with patch("src.services.tmp_health_scheduler.httpx.AsyncClient", return_value=mock_client):
            result = await _check_provider_health("https://provider.example.com/tmp")

        assert result == "healthy"
        mock_client.get.assert_called_once_with("https://provider.example.com/tmp/health")

    @pytest.mark.asyncio
    async def test_returns_unhealthy_on_non_200(self):
        """Non-200 response → 'unhealthy'."""
        mock_resp = MagicMock(status_code=503)
        mock_client = _make_async_http_client(get_return=mock_resp)

        with patch("src.services.tmp_health_scheduler.httpx.AsyncClient", return_value=mock_client):
            result = await _check_provider_health("https://provider.example.com/tmp")

        assert result == "unhealthy"

    @pytest.mark.asyncio
    async def test_returns_error_on_connection_failure(self):
        """ConnectError → 'error'."""
        mock_client = _make_async_http_client(get_side_effect=httpx.ConnectError("Connection refused"))

        with patch("src.services.tmp_health_scheduler.httpx.AsyncClient", return_value=mock_client):
            result = await _check_provider_health("https://provider.example.com/tmp")

        assert result == "error"

    @pytest.mark.asyncio
    async def test_returns_error_on_timeout(self):
        """TimeoutException → 'error'."""
        mock_client = _make_async_http_client(get_side_effect=httpx.TimeoutException("Read timed out"))

        with patch("src.services.tmp_health_scheduler.httpx.AsyncClient", return_value=mock_client):
            result = await _check_provider_health("https://provider.example.com/tmp")

        assert result == "error"

    @pytest.mark.asyncio
    async def test_strips_trailing_slash_from_endpoint(self):
        """Trailing slash on endpoint is stripped before appending /health."""
        mock_resp = MagicMock(status_code=200)
        mock_client = _make_async_http_client(get_return=mock_resp)

        with patch("src.services.tmp_health_scheduler.httpx.AsyncClient", return_value=mock_client):
            await _check_provider_health("https://provider.example.com/tmp/")

        mock_client.get.assert_called_once_with("https://provider.example.com/tmp/health")

    @pytest.mark.asyncio
    async def test_returns_error_on_arbitrary_exception(self):
        """Any non-httpx exception (e.g. socket.gaierror) → 'error', not a raise."""
        mock_client = _make_async_http_client(get_side_effect=OSError("Name or service not known"))

        with patch("src.services.tmp_health_scheduler.httpx.AsyncClient", return_value=mock_client):
            result = await _check_provider_health("https://bad-hostname.invalid")

        assert result == "error"

    @pytest.mark.asyncio
    async def test_follow_redirects_false_prevents_ssrf(self):
        """follow_redirects=False is always passed to prevent SSRF via open-redirect."""
        mock_resp = MagicMock(status_code=200)
        mock_client = _make_async_http_client(get_return=mock_resp)

        with patch("src.services.tmp_health_scheduler.httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await _check_provider_health("https://provider.example.com")

        _, kwargs = mock_cls.call_args
        assert kwargs.get("follow_redirects") is False

    @pytest.mark.asyncio
    async def test_logs_exception_on_error(self):
        """Exceptions are logged before mapping to 'error' — no silent failures."""
        mock_client = _make_async_http_client(get_side_effect=OSError("DNS failure"))

        with (
            patch("src.services.tmp_health_scheduler.httpx.AsyncClient", return_value=mock_client),
            patch("src.services.tmp_health_scheduler.logger") as mock_logger,
        ):
            result = await _check_provider_health("https://bad-hostname.invalid")

        assert result == "error"
        mock_logger.exception.assert_called_once_with(
            "[TMP health] Health probe failed for %s", "https://bad-hostname.invalid"
        )


class TestCheckAllProviders:
    """tick() polls every active/draining provider and persists results."""

    @pytest.mark.asyncio
    async def test_updates_health_status_for_each_provider(self):
        """Each provider gets its health_status updated via UoW with correct values."""
        provider_a = _make_mock_provider("uuid-a", "tenant-1", "https://a.example.com")
        provider_b = _make_mock_provider("uuid-b", "tenant-2", "https://b.example.com")

        mock_session_read = MagicMock()
        mock_repo = MagicMock()
        mock_uow_cls = _make_tmp_uow_cls(mock_repo)

        with (
            patch(
                "src.services.tmp_health_scheduler.get_db_session",
                return_value=_make_db_context(mock_session_read),
            ),
            patch("src.services.tmp_health_scheduler.TMPProviderRepository") as mock_repo_cls,
            patch("src.services.tmp_health_scheduler.TMPProviderUoW", mock_uow_cls),
            patch(
                "src.services.tmp_health_scheduler._check_provider_health",
                new=AsyncMock(side_effect=["unhealthy", "error"]),
            ) as mock_check,
        ):
            mock_repo_cls.get_all_syncable.return_value = [provider_a, provider_b]

            scheduler = TMPHealthScheduler()
            await scheduler.tick()

        # Verify probes were called with correct endpoints
        mock_check.assert_has_calls(
            [call("https://a.example.com"), call("https://b.example.com")],
            any_order=True,
        )
        assert mock_check.call_count == 2

        # Verify health status was written with correct provider_id and status values
        mock_repo.update_health_status.assert_has_calls(
            [
                call("uuid-a", "unhealthy"),
                call("uuid-b", "error"),
            ],
            any_order=True,
        )
        assert mock_repo.update_health_status.call_count == 2

    @pytest.mark.asyncio
    async def test_healthy_status_written_on_200(self):
        """A provider returning 200 gets health_status='healthy' written."""
        provider = _make_mock_provider("uuid-healthy", "tenant-1", "https://healthy.example.com")

        mock_session_read = MagicMock()
        mock_repo = MagicMock()
        mock_uow_cls = _make_tmp_uow_cls(mock_repo)

        with (
            patch(
                "src.services.tmp_health_scheduler.get_db_session",
                return_value=_make_db_context(mock_session_read),
            ),
            patch("src.services.tmp_health_scheduler.TMPProviderRepository") as mock_repo_cls,
            patch("src.services.tmp_health_scheduler.TMPProviderUoW", mock_uow_cls),
            patch(
                "src.services.tmp_health_scheduler._check_provider_health",
                new=AsyncMock(return_value="healthy"),
            ),
        ):
            mock_repo_cls.get_all_syncable.return_value = [provider]

            scheduler = TMPHealthScheduler()
            await scheduler.tick()

        mock_repo.update_health_status.assert_called_once_with("uuid-healthy", "healthy")

    @pytest.mark.asyncio
    async def test_skips_when_no_providers(self):
        """No active providers → no HTTP calls, no UoW opened."""
        mock_session = MagicMock()
        mock_uow_cls = MagicMock()

        with (
            patch(
                "src.services.tmp_health_scheduler.get_db_session",
                return_value=_make_db_context(mock_session),
            ),
            patch("src.services.tmp_health_scheduler.TMPProviderRepository") as mock_repo_cls,
            patch("src.services.tmp_health_scheduler.TMPProviderUoW", mock_uow_cls),
            patch(
                "src.services.tmp_health_scheduler._check_provider_health",
                new=AsyncMock(),
            ) as mock_check,
        ):
            mock_repo_cls.get_all_syncable.return_value = []

            scheduler = TMPHealthScheduler()
            await scheduler.tick()

        mock_check.assert_not_called()
        # No UoW opened when there are no providers
        mock_uow_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_closed_before_probes(self):
        """DB session from the read phase is closed before HTTP probes run."""
        provider = _make_mock_provider("uuid-x", "tenant-1", "https://x.example.com")

        call_order: list[str] = []

        mock_session_read = MagicMock()
        mock_repo = MagicMock()
        mock_uow_cls = _make_tmp_uow_cls(mock_repo)

        def track_exit(*_args: object) -> bool:
            call_order.append("session_closed")
            return False

        async def track_probe(endpoint: str) -> str:
            call_order.append("probe_called")
            return "healthy"

        read_ctx = _make_db_context(mock_session_read)
        read_ctx.__exit__ = MagicMock(side_effect=track_exit)

        with (
            patch(
                "src.services.tmp_health_scheduler.get_db_session",
                return_value=read_ctx,
            ),
            patch("src.services.tmp_health_scheduler.TMPProviderRepository") as mock_repo_cls,
            patch("src.services.tmp_health_scheduler.TMPProviderUoW", mock_uow_cls),
            patch("src.services.tmp_health_scheduler._check_provider_health", side_effect=track_probe),
        ):
            mock_repo_cls.get_all_syncable.return_value = [provider]

            scheduler = TMPHealthScheduler()
            await scheduler.tick()

        # The read session must be closed BEFORE any probe runs
        assert call_order.index("session_closed") < call_order.index("probe_called")

    @pytest.mark.asyncio
    async def test_bad_endpoint_does_not_cancel_other_probes(self):
        """return_exceptions=True: one probe raising does not cancel the rest."""
        provider_a = _make_mock_provider("uuid-a", "tenant-1", "https://bad.invalid")
        provider_b = _make_mock_provider("uuid-b", "tenant-1", "https://good.example.com")

        mock_session_read = MagicMock()
        mock_repo = MagicMock()
        mock_uow_cls = _make_tmp_uow_cls(mock_repo)

        # _check_provider_health already maps all exceptions to "error",
        # but simulate a raw exception escaping to test the gather guard.
        async def probe_side_effect(endpoint: str) -> str:
            if "bad" in endpoint:
                raise OSError("DNS failure")
            return "healthy"

        with (
            patch(
                "src.services.tmp_health_scheduler.get_db_session",
                return_value=_make_db_context(mock_session_read),
            ),
            patch("src.services.tmp_health_scheduler.TMPProviderRepository") as mock_repo_cls,
            patch("src.services.tmp_health_scheduler.TMPProviderUoW", mock_uow_cls),
            patch("src.services.tmp_health_scheduler._check_provider_health", side_effect=probe_side_effect),
        ):
            mock_repo_cls.get_all_syncable.return_value = [provider_a, provider_b]

            # Should not raise — gather(return_exceptions=True) + coercion handles it
            scheduler = TMPHealthScheduler()
            await scheduler.tick()

        # Both providers must have a status written
        assert mock_repo.update_health_status.call_count == 2
        calls = {c.args for c in mock_repo.update_health_status.call_args_list}
        assert ("uuid-a", "error") in calls
        assert ("uuid-b", "healthy") in calls

    @pytest.mark.asyncio
    async def test_providers_grouped_by_tenant_one_uow_per_tenant(self):
        """Providers from different tenants each get their own UoW (one commit per tenant)."""
        provider_a = _make_mock_provider("uuid-a", "tenant-1", "https://a.example.com")
        provider_b = _make_mock_provider("uuid-b", "tenant-2", "https://b.example.com")
        provider_c = _make_mock_provider("uuid-c", "tenant-1", "https://c.example.com")

        mock_session_read = MagicMock()
        mock_repo = MagicMock()

        # Track which tenant_ids TMPProviderUoW was constructed with
        uow_tenant_ids: list[str] = []

        def make_uow(tenant_id: str) -> MagicMock:
            uow_tenant_ids.append(tenant_id)
            mock_uow = MagicMock()
            mock_uow.tmp_providers = mock_repo
            mock_uow.__enter__ = MagicMock(return_value=mock_uow)
            mock_uow.__exit__ = MagicMock(return_value=False)
            return mock_uow

        with (
            patch(
                "src.services.tmp_health_scheduler.get_db_session",
                return_value=_make_db_context(mock_session_read),
            ),
            patch("src.services.tmp_health_scheduler.TMPProviderRepository") as mock_repo_cls,
            patch("src.services.tmp_health_scheduler.TMPProviderUoW", side_effect=make_uow),
            patch(
                "src.services.tmp_health_scheduler._check_provider_health",
                new=AsyncMock(return_value="healthy"),
            ),
        ):
            mock_repo_cls.get_all_syncable.return_value = [provider_a, provider_b, provider_c]

            scheduler = TMPHealthScheduler()
            await scheduler.tick()

        # Exactly 2 UoW instances: one per unique tenant
        assert sorted(uow_tenant_ids) == ["tenant-1", "tenant-2"]
        # All 3 providers got a status written
        assert mock_repo.update_health_status.call_count == 3


class TestSchedulerLifecycle:
    """Scheduler start/stop and singleton pattern."""

    @pytest.mark.asyncio
    async def test_start_creates_background_task(self):
        """start() sets is_running and creates an asyncio task."""
        # Use a fresh instance — never mutate the module-level singleton
        scheduler = TMPHealthScheduler()

        with (
            patch.object(scheduler, "tick", new=AsyncMock(return_value=None)),
            patch.object(scheduler, "_interval_seconds", 0),
        ):
            await scheduler.start()

            assert scheduler.is_running is True
            assert scheduler._task is not None

            # Clean up
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        """stop() sets is_running=False and cancels the task."""
        # Use a fresh instance — never mutate the module-level singleton
        scheduler = TMPHealthScheduler()

        with (
            patch.object(scheduler, "tick", new=AsyncMock(return_value=None)),
            patch.object(scheduler, "_interval_seconds", 0),
        ):
            await scheduler.start()
            await scheduler.stop()

        assert scheduler.is_running is False

    @pytest.mark.asyncio
    async def test_cancelled_error_exits_loop_cleanly(self):
        """Cancellation arriving *inside* tick() must not delay shutdown by the inter-tick interval.

        ``_scheduler_base.py`` places ``asyncio.sleep`` *outside* the
        try/except block so that a ``CancelledError`` raised inside ``tick()``
        is re-raised immediately rather than being absorbed and then followed
        by a full inter-tick sleep.

        This test pins that contract by making ``tick()`` block on a long
        ``await asyncio.sleep(60)`` — so the cancel always lands inside
        ``tick()``, not in the inter-tick sleep.  Under the correct
        implementation the task exits in ~0 s.  Under the broken shape
        (``finally: sleep(interval)``), the cancel would be absorbed by the
        ``except`` clause and the task would sleep for the full 10-second
        interval before exiting, causing ``elapsed ≈ 10`` and failing the
        ``< 1.0`` assertion.
        """
        import contextlib

        from src.services._scheduler_base import IntervalScheduler

        class _TestScheduler(IntervalScheduler):
            def __init__(self) -> None:
                # Long inter-tick interval: if cancellation is swallowed the
                # task sleeps 10 s before exiting, making elapsed ≈ 10.
                super().__init__(interval_seconds=10, name="test-cancel")
                self.tick_started = asyncio.Event()

            async def tick(self) -> None:
                self.tick_started.set()
                # Long await so the cancel always lands inside tick(), not in
                # the inter-tick sleep.
                await asyncio.sleep(60)

        sched = _TestScheduler()
        sched.is_running = True

        # Start the loop as a real task (not via start() to avoid the lock).
        task = asyncio.create_task(sched._run_scheduler())

        # Wait until tick() has started (and is blocked on its inner sleep).
        await asyncio.wait_for(sched.tick_started.wait(), timeout=2.0)

        # Cancel while tick() is awaiting — cancel lands inside tick(), not in the inter-tick sleep.
        t0 = asyncio.get_event_loop().time()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        elapsed = asyncio.get_event_loop().time() - t0

        assert task.done(), "scheduler task did not exit after cancellation"
        assert elapsed < 1.0, (
            f"cancellation took {elapsed:.2f}s — CancelledError inside tick() "
            "was absorbed and the inter-tick sleep ran to completion"
        )

    @pytest.mark.asyncio
    async def test_exception_in_tick_does_not_kill_loop(self):
        """An unhandled exception in tick() is logged but the loop continues."""
        # Use a fresh instance — never mutate the module-level singleton
        scheduler = TMPHealthScheduler()

        call_count = 0
        recovered = asyncio.Event()

        async def flaky_tick() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient failure")
            recovered.set()
            # Stop after second call to avoid infinite loop in test
            scheduler.is_running = False

        with (
            patch.object(scheduler, "tick", side_effect=flaky_tick),
            patch.object(scheduler, "_interval_seconds", 0),
        ):
            await scheduler.start()
            await asyncio.wait_for(recovered.wait(), timeout=2.0)
            await scheduler.stop()

        assert call_count >= 2

    def test_singleton_returns_same_instance(self):
        """get_tmp_health_scheduler() returns the same instance on repeated calls."""
        s1 = get_tmp_health_scheduler()
        s2 = get_tmp_health_scheduler()
        assert s1 is s2
