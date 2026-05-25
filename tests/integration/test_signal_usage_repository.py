"""SignalUsageRepository scans active media buys for signal_id references.

Verifies the repository walks raw_request correctly across the shapes
buyers actually send: signals in audience_include, in audience_exclude,
spread across multiple packages, and the active/inactive status filter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _SignalUsageEnv(IntegrationEnv):
    """Bare integration env — repository scan needs no external mocks."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


def _buy_with_signals(
    *,
    tenant,
    principal,
    media_buy_id: str,
    status: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    packages: int = 1,
):
    """Make a MediaBuy whose raw_request references the given signal_ids."""
    pkg_payloads = []
    for i in range(packages):
        overlay: dict[str, list[str]] = {}
        if include:
            overlay["audience_include"] = list(include)
        if exclude:
            overlay["audience_exclude"] = list(exclude)
        pkg_payloads.append(
            {
                "package_id": f"pkg_{i:02d}",
                "product_id": "prod_001",
                "targeting_overlay": overlay,
            }
        )
    return MediaBuyFactory(
        tenant=tenant,
        principal=principal,
        media_buy_id=media_buy_id,
        status=status,
        raw_request={"packages": pkg_payloads},
    )


class TestUsageIndexCountsActiveReferences:
    """``usage_index`` aggregates references across the tenant's active buys."""

    def test_includes_buys_in_active_and_approved_status(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t1")
            principal = PrincipalFactory(tenant=tenant)
            _buy_with_signals(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_active",
                status="active",
                include=["sports_fans"],
            )
            _buy_with_signals(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_approved",
                status="approved",
                include=["sports_fans"],
            )
            session = env.get_session()
            index = SignalUsageRepository(session, "usage_t1").usage_index()

        assert index["sports_fans"].active_buy_count == 2

    def test_excludes_terminal_status_buys(self, integration_db):
        """``completed`` / ``canceled`` / ``cancelled`` / ``rejected`` /
        ``failed`` are terminal — references are historical."""
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t2")
            principal = PrincipalFactory(tenant=tenant)
            for i, terminal in enumerate(("completed", "canceled", "cancelled", "rejected", "failed")):
                _buy_with_signals(
                    tenant=tenant,
                    principal=principal,
                    media_buy_id=f"mb_term_{i}",
                    status=terminal,
                    include=["sports_fans"],
                )
            session = env.get_session()
            index = SignalUsageRepository(session, "usage_t2").usage_index()

        assert "sports_fans" not in index

    def test_includes_paused_and_pending_buys(self, integration_db):
        """Paused and pending_* buys still reference the signal — they
        will (or could) re-serve. Deleting under them breaks live work."""
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t2b")
            principal = PrincipalFactory(tenant=tenant)
            for i, live in enumerate(("paused", "pending_creatives", "pending_start", "pending_approval", "draft")):
                _buy_with_signals(
                    tenant=tenant,
                    principal=principal,
                    media_buy_id=f"mb_live_{i}",
                    status=live,
                    include=["sports_fans"],
                )
            session = env.get_session()
            index = SignalUsageRepository(session, "usage_t2b").usage_index()

        # Five live buys all reference the same signal.
        assert index["sports_fans"].active_buy_count == 5

    def test_counts_audience_exclude_references(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t3")
            principal = PrincipalFactory(tenant=tenant)
            _buy_with_signals(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_exclude",
                status="active",
                exclude=["competitors"],
            )
            session = env.get_session()
            index = SignalUsageRepository(session, "usage_t3").usage_index()

        assert index["competitors"].active_buy_count == 1

    def test_deduplicates_signal_within_single_buy(self, integration_db):
        """Same signal in two packages of one buy still counts as one buy."""
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t4")
            principal = PrincipalFactory(tenant=tenant)
            _buy_with_signals(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_multi_pkg",
                status="active",
                include=["sports_fans"],
                packages=2,
            )
            session = env.get_session()
            index = SignalUsageRepository(session, "usage_t4").usage_index()

        assert index["sports_fans"].active_buy_count == 1

    def test_isolates_by_tenant(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            t_a = TenantFactory(tenant_id="usage_ta")
            p_a = PrincipalFactory(tenant=t_a)
            _buy_with_signals(
                tenant=t_a,
                principal=p_a,
                media_buy_id="mb_a",
                status="active",
                include=["other_tenant_signal"],
            )
            t_b = TenantFactory(tenant_id="usage_tb")
            session = env.get_session()
            index = SignalUsageRepository(session, "usage_tb").usage_index()

        assert index == {}

    def test_tracks_last_referenced_as_max_updated_at(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t5")
            principal = PrincipalFactory(tenant=tenant)
            older = _buy_with_signals(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_older",
                status="active",
                include=["shared"],
            )
            newer = _buy_with_signals(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_newer",
                status="active",
                include=["shared"],
            )
            # Set distinct updated_at values directly on the ORM rows.
            session = env.get_session()
            now = datetime.now(UTC)
            older.updated_at = now - timedelta(days=3)
            newer.updated_at = now
            session.flush()
            index = SignalUsageRepository(session, "usage_t5").usage_index()

        assert index["shared"].active_buy_count == 2
        # Compare without microseconds — Postgres rounds.
        assert index["shared"].last_referenced_at.replace(microsecond=0) == now.replace(microsecond=0)


class TestCountReferences:
    """``count_references`` is a single-signal convenience wrapper."""

    def test_returns_zero_for_unreferenced_signal(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            TenantFactory(tenant_id="usage_t6")
            session = env.get_session()
            count = SignalUsageRepository(session, "usage_t6").count_references("never_used")

        assert count == 0

    def test_returns_zero_for_empty_signal_id(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            TenantFactory(tenant_id="usage_t7")
            session = env.get_session()
            count = SignalUsageRepository(session, "usage_t7").count_references("")

        assert count == 0

    def test_counts_one_signal_across_two_buys(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t8")
            principal = PrincipalFactory(tenant=tenant)
            _buy_with_signals(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_x",
                status="active",
                include=["sports_fans"],
            )
            _buy_with_signals(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_y",
                status="active",
                include=["sports_fans", "other"],
            )
            session = env.get_session()
            count = SignalUsageRepository(session, "usage_t8").count_references("sports_fans")

        assert count == 2

    def test_count_uses_targeted_query_for_supported_shapes(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository
        from tests.factories import MediaBuyFactory

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t8b")
            principal = PrincipalFactory(tenant=tenant)
            _buy_with_signals(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_pkg",
                status="active",
                include=["sports_fans"],
            )
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_top",
                status="active",
                raw_request={"targeting_overlay": {"audience_exclude": ["sports_fans"]}},
            )
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_done",
                status="completed",
                raw_request={"targeting_overlay": {"audience_include": ["sports_fans"]}},
            )
            session = env.get_session()
            repo = SignalUsageRepository(session, "usage_t8b")
            with patch.object(SignalUsageRepository, "usage_index", side_effect=AssertionError("full scan")):
                count = repo.count_references("sports_fans")

        assert count == 2


class TestRawRequestEdgeCases:
    """Defensive walking — older / malformed payloads must not error."""

    def test_missing_packages_yields_empty_index(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t9")
            principal = PrincipalFactory(tenant=tenant)
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_legacy",
                status="active",
                raw_request={"buyer_ref": "legacy_payload"},
            )
            session = env.get_session()
            index = SignalUsageRepository(session, "usage_t9").usage_index()

        assert index == {}

    def test_package_without_targeting_overlay_is_skipped(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t10")
            principal = PrincipalFactory(tenant=tenant)
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_no_overlay",
                status="active",
                raw_request={"packages": [{"package_id": "p1", "product_id": "prod_001"}]},
            )
            session = env.get_session()
            index = SignalUsageRepository(session, "usage_t10").usage_index()

        assert index == {}

    def test_top_level_targeting_overlay_is_walked(self, integration_db):
        """Update-shape: ``targeting_overlay`` may sit at request root,
        not only under ``packages[*]``. Both must be honored."""
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t_top")
            principal = PrincipalFactory(tenant=tenant)
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_top_overlay",
                status="active",
                raw_request={
                    "targeting_overlay": {
                        "audience_include": ["top_level_sig"],
                        "audience_exclude": ["top_level_neg"],
                    }
                },
            )
            session = env.get_session()
            index = SignalUsageRepository(session, "usage_t_top").usage_index()

        assert index["top_level_sig"].active_buy_count == 1
        assert index["top_level_neg"].active_buy_count == 1

    def test_null_audience_include_is_skipped(self, integration_db):
        from src.core.database.repositories.signal_usage import SignalUsageRepository

        with _SignalUsageEnv() as env:
            tenant = TenantFactory(tenant_id="usage_t11")
            principal = PrincipalFactory(tenant=tenant)
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_null_overlay",
                status="active",
                raw_request={
                    "packages": [
                        {
                            "package_id": "p1",
                            "product_id": "prod_001",
                            "targeting_overlay": {
                                "audience_include": None,
                                "audience_exclude": None,
                            },
                        }
                    ]
                },
            )
            session = env.get_session()
            index = SignalUsageRepository(session, "usage_t11").usage_index()

        assert index == {}
