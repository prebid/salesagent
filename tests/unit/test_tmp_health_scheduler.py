"""Unit tests for the TMP health-check background scheduler.

Tests the scheduler in src/services/tmp_health_scheduler.py which polls
each active/draining TMP provider's /health endpoint and writes the result
to health_status / last_health_checked_at columns.

Covers:
- _check_provider_health: healthy on 200, unhealthy on non-200, error on exception
- tick(): multi-provider fan-out, skip when no providers, per-provider probe isolation
  and per-TENANT write isolation
- Scheduler lifecycle: start/stop, singleton pattern, CancelledError handling
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.core.security.egress.policy import OutboundRequestBlocked
from src.core.security.outbound_http import OperatorEndpoint
from src.services.tmp_health_scheduler import (
    TMPHealthScheduler,
    _check_provider_health,
    get_tmp_health_scheduler,
)
from tests.helpers.tmp_provider_http import make_delivery_failed, make_seam_result
from tests.unit._tmp_helpers import make_db_context, make_mock_provider, make_tmp_repo_uow

# ── Shared helpers ──────────────────────────────────────────────────


class TestCheckProviderHealth:
    """_check_provider_health probes a single provider's /health endpoint.

    Doubles ``asend`` — the egress seam function production calls since #1802 —
    rather than an ``httpx.AsyncClient``. The three-way answer is the subject:
    "healthy" and "unhealthy" both mean the provider ANSWERED, and "error" means
    nothing did, so the seam's raise-on-non-2xx must not collapse the middle
    case into the last one.
    """

    _ENDPOINT = "https://provider.example.com/tmp"

    @staticmethod
    def _seam(**kwargs):
        """Patch the seam's async send for the duration of a probe."""
        return patch("src.services.tmp_health_scheduler.asend", new=AsyncMock(**kwargs))

    @pytest.mark.asyncio
    async def test_returns_healthy_on_200(self):
        """200 response → 'healthy'."""
        with self._seam(return_value=make_seam_result(200)) as seam:
            result = await _check_provider_health(self._ENDPOINT)

        assert result == "healthy"
        assert seam.call_args[0][0] == "https://provider.example.com/tmp/health"
        assert seam.call_args.kwargs["method"] == "GET"

    @pytest.mark.asyncio
    async def test_returns_unhealthy_on_a_delivered_non_200(self):
        """A 2xx-but-not-200 the seam delivers → 'unhealthy'."""
        with self._seam(return_value=make_seam_result(204)):
            assert await _check_provider_health(self._ENDPOINT) == "unhealthy"

    @pytest.mark.asyncio
    async def test_returns_unhealthy_when_the_provider_answered_an_error_status(self):
        """A 503 reaches the caller as OutboundDeliveryFailed CARRYING the status.

        This is the case the migration could have silently lost: the seam raises
        on a non-2xx, and a bare ``except`` would have reported a provider that
        answered "I am unwell" as though nothing answered at all.
        """
        with self._seam(side_effect=make_delivery_failed(503)):
            assert await _check_provider_health(self._ENDPOINT) == "unhealthy"

    @pytest.mark.asyncio
    async def test_returns_error_when_nothing_answered(self):
        """A transport failure (no status) → 'error'."""
        with self._seam(side_effect=make_delivery_failed(None)):
            assert await _check_provider_health(self._ENDPOINT) == "error"

    @pytest.mark.asyncio
    async def test_returns_error_when_the_seam_refuses_the_address(self):
        """A policy refusal → 'error', not a raise out of the coroutine."""
        with self._seam(side_effect=OutboundRequestBlocked(field=None)):
            assert await _check_provider_health("https://10.0.0.1/tmp") == "error"

    @pytest.mark.asyncio
    async def test_strips_trailing_slash_from_endpoint(self):
        """Trailing slash on endpoint is stripped before appending /health."""
        with self._seam(return_value=make_seam_result(200)) as seam:
            await _check_provider_health("https://provider.example.com/tmp/")

        assert seam.call_args[0][0] == "https://provider.example.com/tmp/health"

    @pytest.mark.asyncio
    async def test_returns_error_on_arbitrary_exception(self):
        """Any other exception (e.g. socket.gaierror) → 'error', not a raise."""
        with self._seam(side_effect=OSError("Name or service not known")):
            assert await _check_provider_health("https://bad-hostname.invalid") == "error"

    @pytest.mark.asyncio
    async def test_probe_sends_exactly_one_request(self):
        """max_attempts=1: a scheduled probe reports what ONE request saw.

        Replaces a ``follow_redirects=False`` assertion — that flag is the seam's
        default now, not this call site's argument. What IS still this call
        site's decision is opting out of the seam's 3-attempt retry, which would
        otherwise turn each tick into three requests per provider and report the
        last (#1802 migration).
        """
        with self._seam(return_value=make_seam_result(200)) as seam:
            await _check_provider_health("https://provider.example.com")

        assert seam.call_args.kwargs["max_attempts"] == 1

    @pytest.mark.asyncio
    async def test_provenance_is_an_operator_endpoint_naming_no_address(self):
        """The seam is told whose URL this is, as a role rather than an address."""
        with self._seam(return_value=make_seam_result(200)) as seam:
            await _check_provider_health("https://provider.example.com")

        provenance = seam.call_args.kwargs["provenance"]
        assert isinstance(provenance, OperatorEndpoint)
        assert "://" not in provenance.name

    @pytest.mark.asyncio
    async def test_logs_exception_on_error(self):
        """Exceptions are logged before mapping to 'error' — no silent failures."""
        with (
            self._seam(side_effect=OSError("DNS failure")),
            patch("src.services.tmp_health_scheduler.logger") as mock_logger,
        ):
            result = await _check_provider_health("https://bad-hostname.invalid")

        assert result == "error"
        mock_logger.exception.assert_called_once_with(
            "[TMP health] Health probe failed for %s", "https://bad-hostname.invalid"
        )


class TickMocks(NamedTuple):
    """What :func:`_run_tick` yields — every seam ``tick()`` touches, named."""

    repo: MagicMock
    probe: AsyncMock
    uow_cls: MagicMock
    read_ctx: MagicMock


@asynccontextmanager
async def _run_tick(
    providers: list,
    *,
    probe: Any = "healthy",
    repo: MagicMock | None = None,
    uow: MagicMock | None = None,
    read_ctx: MagicMock | None = None,
) -> AsyncIterator[TickMocks]:
    """Arrange the four patches ``tick()`` needs, run it, and yield the seams.

    The same four-patch block (read session, repository, write UoW, probe) was
    re-typed in seven tests, so a change to the scheduler's dependencies meant
    seven edits (#1197 review).

    Every parameter exists because a test needed to vary that one seam, so the
    exceptions are expressed as arguments rather than as a hand-rolled copy of
    the block with a comment explaining why it is a copy:

    ``probe``   what ``_check_provider_health`` does — a value, a list of
                per-provider values, an exception, or a **callable** for the
                cases that branch on the endpoint or record call order.
    ``repo``    the write repository, when the caller builds the UoW itself.
    ``uow``     a custom ``TMPProviderUoW`` stand-in: pass one to observe the
                constructor arguments (one UoW per tenant) or to make a
                tenant's ``__enter__`` raise.
    ``read_ctx`` the read phase's session context manager, for asserting it is
                closed before the probes run.
    """
    repo = repo if repo is not None else MagicMock()
    uow_cls = uow if uow is not None else make_tmp_repo_uow(repo)
    read_ctx = read_ctx if read_ctx is not None else make_db_context(MagicMock())
    needs_side_effect = isinstance(probe, list | Exception) or callable(probe)
    probe_mock = AsyncMock(side_effect=probe) if needs_side_effect else AsyncMock(return_value=probe)

    with (
        patch("src.services.tmp_health_scheduler.get_db_session", return_value=read_ctx),
        patch("src.services.tmp_health_scheduler.TMPProviderRepository") as mock_repo_cls,
        patch("src.services.tmp_health_scheduler.TMPProviderUoW", uow_cls),
        patch("src.services.tmp_health_scheduler._check_provider_health", new=probe_mock),
    ):
        mock_repo_cls.get_all_syncable.return_value = providers
        await TMPHealthScheduler().tick()
        yield TickMocks(repo=repo, probe=probe_mock, uow_cls=uow_cls, read_ctx=read_ctx)


class TestCheckAllProviders:
    """tick() polls every active/draining provider and persists results."""

    @pytest.mark.asyncio
    async def test_updates_health_status_for_each_provider(self):
        """Each provider gets its health_status updated via UoW with correct values."""
        provider_a = make_mock_provider(provider_id="prov_a", tenant_id="tenant-1", endpoint="https://a.example.com")
        provider_b = make_mock_provider(provider_id="prov_b", tenant_id="tenant-2", endpoint="https://b.example.com")

        async with _run_tick([provider_a, provider_b], probe=["unhealthy", "error"]) as tick:
            pass
        mock_repo, mock_check = tick.repo, tick.probe

        # Verify probes were called with correct endpoints
        mock_check.assert_has_calls(
            [call("https://a.example.com"), call("https://b.example.com")],
            any_order=True,
        )
        assert mock_check.call_count == 2

        # Verify health status was written with correct provider_id and status values
        mock_repo.update_health_status.assert_has_calls(
            [
                call("prov_a", "unhealthy"),
                call("prov_b", "error"),
            ],
            any_order=True,
        )
        assert mock_repo.update_health_status.call_count == 2

    @pytest.mark.asyncio
    async def test_healthy_status_written_on_200(self):
        """A provider returning 200 gets health_status='healthy' written."""
        provider = make_mock_provider(
            provider_id="prov_healthy", tenant_id="tenant-1", endpoint="https://healthy.example.com"
        )

        async with _run_tick([provider], probe="healthy") as tick:
            pass
        mock_repo = tick.repo

        mock_repo.update_health_status.assert_called_once_with("prov_healthy", "healthy")

    @pytest.mark.asyncio
    async def test_skips_when_no_providers(self):
        """No active providers → no HTTP calls, no UoW opened."""
        # A bare MagicMock for the UoW class: the assertion is that it is never
        # CONSTRUCTED, so it must not be the context-manager-shaped default.
        async with _run_tick([], uow=MagicMock()) as tick:
            pass

        tick.probe.assert_not_called()
        tick.uow_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_closed_before_probes(self):
        """DB session from the read phase is closed before HTTP probes run."""
        provider = make_mock_provider(provider_id="prov_x", tenant_id="tenant-1", endpoint="https://x.example.com")

        call_order: list[str] = []

        def track_exit(*_args: object) -> bool:
            call_order.append("session_closed")
            return False

        async def track_probe(endpoint: str) -> str:
            call_order.append("probe_called")
            return "healthy"

        read_ctx = make_db_context(MagicMock())
        read_ctx.__exit__ = MagicMock(side_effect=track_exit)

        async with _run_tick([provider], probe=track_probe, read_ctx=read_ctx):
            pass

        # The read session must be closed BEFORE any probe runs
        assert call_order.index("session_closed") < call_order.index("probe_called")

    @pytest.mark.asyncio
    async def test_bad_endpoint_does_not_cancel_other_probes(self):
        """return_exceptions=True: one probe raising does not cancel the rest."""
        provider_a = make_mock_provider(provider_id="prov_a", tenant_id="tenant-1", endpoint="https://bad.invalid")
        provider_b = make_mock_provider(provider_id="prov_b", tenant_id="tenant-1", endpoint="https://good.example.com")

        # _check_provider_health already maps all exceptions to "error", but
        # simulate a raw exception escaping to test the gather guard. A callable
        # probe is how the seam expresses "branches on the endpoint".
        async def probe_side_effect(endpoint: str) -> str:
            if "bad" in endpoint:
                raise OSError("DNS failure")
            return "healthy"

        # Must not raise — gather(return_exceptions=True) + coercion handles it.
        async with _run_tick([provider_a, provider_b], probe=probe_side_effect) as tick:
            pass

        # Both providers must have a status written
        assert tick.repo.update_health_status.call_count == 2
        calls = {c.args for c in tick.repo.update_health_status.call_args_list}
        assert ("prov_a", "error") in calls
        assert ("prov_b", "healthy") in calls

    @pytest.mark.asyncio
    async def test_providers_grouped_by_tenant_one_uow_per_tenant(self):
        """Providers from different tenants each get their own UoW (one commit per tenant)."""
        provider_a = make_mock_provider(provider_id="prov_a", tenant_id="tenant-1", endpoint="https://a.example.com")
        provider_b = make_mock_provider(provider_id="prov_b", tenant_id="tenant-2", endpoint="https://b.example.com")
        provider_c = make_mock_provider(provider_id="prov_c", tenant_id="tenant-1", endpoint="https://c.example.com")

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

        async with _run_tick(
            [provider_a, provider_b, provider_c],
            repo=mock_repo,
            uow=MagicMock(side_effect=make_uow),
        ):
            pass

        # Exactly 2 UoW instances: one per unique tenant
        assert sorted(uow_tenant_ids) == ["tenant-1", "tenant-2"]
        # All 3 providers got a status written
        assert mock_repo.update_health_status.call_count == 3


class TestWritePhaseIsIsolatedPerTenant:
    """One tenant's failed write does not skip the tenants after it.

    The probe phase already isolated per provider (``return_exceptions=True``);
    the write phase did not, so one tenant's UoW failure — a lock timeout, a row
    deleted mid-cycle — silently skipped every tenant later in the iteration
    order. Both phases now fail per item (#1197 review).
    """

    @pytest.mark.asyncio
    async def test_later_tenants_are_still_written(self):
        provider_a = make_mock_provider(provider_id="prov_a", tenant_id="tenant-1", endpoint="https://a.example.com")
        provider_b = make_mock_provider(provider_id="prov_b", tenant_id="tenant-2", endpoint="https://b.example.com")

        mock_repo = MagicMock()
        # One UoW class whose __enter__ raises for tenant-1 and yields a working
        # UoW for tenant-2 — the scheduler opens one UoW per tenant, so the
        # constructor argument is what distinguishes them.
        working_uow = MagicMock()
        working_uow.tmp_providers = mock_repo

        def _enter_for(tenant_id, *_args, **_kwargs):
            cm = MagicMock()
            if tenant_id == "tenant-1":
                cm.__enter__ = MagicMock(side_effect=RuntimeError("lock timeout"))
            else:
                cm.__enter__ = MagicMock(return_value=working_uow)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        async with _run_tick(
            [provider_a, provider_b],
            probe=["healthy", "healthy"],
            repo=mock_repo,
            uow=MagicMock(side_effect=_enter_for),
        ):
            pass

        # tenant-2's write happened even though tenant-1's UoW blew up first.
        mock_repo.update_health_status.assert_called_once_with("prov_b", "healthy")


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
