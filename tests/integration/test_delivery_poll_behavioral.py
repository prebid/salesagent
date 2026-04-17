"""Integration behavioral tests for UC-004 delivery polling (_get_media_buy_delivery_impl).

Migrated from tests/unit/test_delivery_poll_behavioral.py — these tests use real
PostgreSQL (via integration_db fixture) and factory_boy instead of inline @patch.
Only the adapter is mocked (external ad server).

Each test targets exactly one obligation ID and follows the 6 hard rules:
1. MUST import from src.
2. MUST call production function
3. MUST assert production output
4. MUST have Covers: tag
5. MUST use factory_boy factories for data setup
6. MUST NOT be mock-echo only
"""

from __future__ import annotations

from datetime import date

import pytest

from src.core.schemas import GetMediaBuyDeliveryResponse

# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookNotificationTypeScheduled:
    """Normal periodic delivery sets notification_type to 'scheduled'.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
    """

    def test_periodic_delivery_sets_scheduled_type(self, integration_db):
        """Normal periodic delivery should auto-set notification_type to 'scheduled'.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
            env.set_adapter_response(buy.media_buy_id, impressions=5000)

            response = env.call_impl(media_buy_ids=[buy.media_buy_id])

            dumped = response.model_dump(mode="json")
            assert dumped["notification_type"] == "scheduled"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-04
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookNotificationTypeFinal:
    """Completed campaign sets notification_type to 'final' with no next_expected_at.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-04
    """

    def test_completed_campaign_sets_final_type(self, integration_db):
        """Completed campaign should set notification_type='final' and omit next_expected_at.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-04
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                start_date=date(2025, 1, 1),
                end_date=date(2025, 6, 30),
            )
            env.set_adapter_response(buy.media_buy_id, impressions=5000)

            response = env.call_impl(media_buy_ids=[buy.media_buy_id])

            dumped = response.model_dump(mode="json")
            assert dumped["notification_type"] == "final"
            assert dumped["next_expected_at"] is None


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-05
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookSequenceNumber:
    """Monotonically increasing sequence_number per media buy.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-05
    """

    def test_sequence_number_auto_assigned(self, integration_db):
        """Delivery response should auto-assign sequence_number starting from 1.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-05
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
            env.set_adapter_response(buy.media_buy_id, impressions=5000)

            response = env.call_impl(media_buy_ids=[buy.media_buy_id])

            assert response.sequence_number is not None, "sequence_number should be auto-assigned"
            assert response.sequence_number >= 1


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-06
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookNextExpectedAt:
    """next_expected_at computed for non-final deliveries.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-06
    """

    def test_next_expected_at_set_for_active_delivery(self, integration_db):
        """Scheduled delivery for active buy should compute next_expected_at.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-06
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
            env.set_adapter_response(buy.media_buy_id, impressions=5000)

            response = env.call_impl(media_buy_ids=[buy.media_buy_id])

            assert response.next_expected_at is not None, "next_expected_at must be set for non-final delivery"


# ---------------------------------------------------------------------------
# UC-004-EXT-C-01
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestNonexistentMediaBuyIdReturnsNotFoundError:
    """Requesting delivery for a nonexistent media_buy_id returns media_buy_not_found error.

    Covers: UC-004-EXT-C-01
    """

    def test_nonexistent_id_produces_media_buy_not_found_error(self, integration_db):
        """When media_buy_ids contains an ID absent from the DB, response.errors includes
        media_buy_not_found with the unresolved identifier.

        Covers: UC-004-EXT-C-01
        """
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            response = env.call_impl(media_buy_ids=["nonexistent_id"])

            assert isinstance(response, GetMediaBuyDeliveryResponse)
            assert response.errors is not None
            assert len(response.errors) == 1
            error = response.errors[0]
            assert error.code == "media_buy_not_found"
            assert "nonexistent_id" in error.message


# ---------------------------------------------------------------------------
# UC-004-EXT-C-02
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestPartialMediaBuyIdsNotFound:
    """Mixed request: some IDs exist, some do not.

    Covers: UC-004-EXT-C-02

    SPEC CONFLICT NOTE:
    - BR-RULE-030 (INV-5) says partial results should be returned.
    - ext-c says an error should be returned for not-found IDs.
    - ACTUAL PRODUCTION BEHAVIOR: BOTH -- partial results (mb_1 delivery data)
      are returned in media_buy_deliveries, AND a media_buy_not_found error
      for mb_999 is placed in the errors list.
    """

    def test_partial_ids_returns_found_buy_and_not_found_error(self, integration_db):
        """When some IDs exist and some don't, returns delivery for found IDs
        and a media_buy_not_found error for missing IDs.

        Covers: UC-004-EXT-C-02
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_1",
                start_date=date(2020, 1, 1),
                end_date=date(2030, 12, 31),
            )
            env.set_adapter_response(buy.media_buy_id, impressions=5000)

            response = env.call_impl(media_buy_ids=["mb_1", "mb_999"])

            assert isinstance(response, GetMediaBuyDeliveryResponse)
            assert len(response.media_buy_deliveries) == 1
            assert response.media_buy_deliveries[0].media_buy_id == "mb_1"

            assert response.errors is not None
            assert len(response.errors) == 1
            not_found_error = response.errors[0]
            assert not_found_error.code == "media_buy_not_found"
            assert "mb_999" in not_found_error.message

            assert all("mb_1" not in e.message for e in response.errors)


# ---------------------------------------------------------------------------
# UC-004-EXT-E-01
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestEqualDateRangeReturnsInvalidDateRangeError:
    """Equal start and end dates return invalid_date_range error.

    Covers: UC-004-EXT-E-01

    BR-RULE-013: start_date >= end_date is invalid.
    """

    def test_equal_dates_returns_invalid_date_range(self, integration_db):
        """Covers: UC-004-EXT-E-01"""
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            response = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2026-03-15",
                end_date="2026-03-15",
            )

            assert isinstance(response, GetMediaBuyDeliveryResponse)
            assert response.media_buy_deliveries == []
            assert len(response.errors) == 1
            assert response.errors[0].code == "invalid_date_range"


# ---------------------------------------------------------------------------
# UC-004-EXT-E-02
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestStartDateAfterEndDateReturnsInvalidDateRangeError:
    """Start date after end date returns invalid_date_range error.

    Covers: UC-004-EXT-E-02

    BR-RULE-013: start_date >= end_date is invalid.
    """

    def test_start_after_end_returns_invalid_date_range(self, integration_db):
        """Covers: UC-004-EXT-E-02"""
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            response = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2026-03-20",
                end_date="2026-03-10",
            )

            assert isinstance(response, GetMediaBuyDeliveryResponse)
            assert response.media_buy_deliveries == []
            assert len(response.errors) == 1
            assert response.errors[0].code == "invalid_date_range"


# ---------------------------------------------------------------------------
# UC-004-EXT-E-03
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestInvalidDateRangeDoesNotFetchDeliveryData:
    """Invalid date range causes no delivery data to be fetched.

    Covers: UC-004-EXT-E-03

    POST-F1: No delivery data is fetched or returned on date range error.
    This proves the read-only property — the adapter is never invoked.
    """

    def test_invalid_date_range_does_not_call_adapter(self, integration_db):
        """Covers: UC-004-EXT-E-03"""
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            response = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2026-03-20",
                end_date="2026-03-10",
            )

            assert response.media_buy_deliveries == []
            assert len(response.errors) == 1
            assert response.errors[0].code == "invalid_date_range"

            # Verify adapter's delivery method was never called (no data fetched)
            env.mock["adapter"].return_value.get_media_buy_delivery.assert_not_called()


# ---------------------------------------------------------------------------
# UC-004-EXT-F-01
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestAdapterUnavailableReturnsAdapterError:
    """Adapter unavailable (network error) returns adapter_error.

    Covers: UC-004-EXT-F-01

    POST-F2: buyer knows delivery data could not be retrieved.
    """

    def test_adapter_connection_error_returns_adapter_error(self, integration_db):
        """Covers: UC-004-EXT-F-01"""
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_001",
                start_date=date(2020, 1, 1),
                end_date=date(2030, 12, 31),
            )

            env.set_adapter_error(ConnectionError("Connection refused"))

            response = env.call_impl(media_buy_ids=["mb_001"])

            assert isinstance(response, GetMediaBuyDeliveryResponse)
            error_codes = [e.code for e in response.errors]
            assert "adapter_error" in error_codes
            adapter_error = next(e for e in response.errors if e.code == "adapter_error")
            assert "mb_001" in adapter_error.message


# ---------------------------------------------------------------------------
# UC-004-EXT-F-02
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestAdapterInternalServerErrorReturnsAdapterError:
    """Adapter 500 internal server error returns adapter_error.

    Covers: UC-004-EXT-F-02

    Ext-f step 7b: ad server returns 500 → buyer gets adapter_error.
    """

    def test_adapter_500_error_returns_adapter_error(self, integration_db):
        """Covers: UC-004-EXT-F-02"""
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_001",
                start_date=date(2020, 1, 1),
                end_date=date(2030, 12, 31),
            )

            env.set_adapter_error(RuntimeError("500 Internal Server Error"))

            response = env.call_impl(media_buy_ids=["mb_001"])

            assert isinstance(response, GetMediaBuyDeliveryResponse)
            error_codes = [e.code for e in response.errors]
            assert "adapter_error" in error_codes
            adapter_error = next(e for e in response.errors if e.code == "adapter_error")
            assert "mb_001" in adapter_error.message


# ---------------------------------------------------------------------------
# UC-004-EXT-F-03
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestAdapterFailureAuditTrail:
    """Adapter failure is logged to the audit trail (NFR-003).

    Covers: UC-004-EXT-F-03
    """

    def test_adapter_failure_writes_audit_log(self, integration_db):
        """When adapter.get_media_buy_delivery raises, the failure is audit-logged.

        Covers: UC-004-EXT-F-03
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AuditLog
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_fail",
            )

            env.set_adapter_error(RuntimeError("GAM API timeout"))

            response = env.call_impl(
                media_buy_ids=["mb_fail"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert response is not None
            assert isinstance(response, GetMediaBuyDeliveryResponse)
            assert any(e.code == "adapter_error" for e in response.errors)

            # Check real audit log table for records
            from sqlalchemy import select

            with get_db_session() as session:
                audit_records = session.scalars(select(AuditLog)).all()
                assert len(audit_records) > 0, (
                    "No AuditLog records written to DB. Adapter failure must be recorded in audit trail per NFR-003."
                )


# ---------------------------------------------------------------------------
# UC-004-EXT-F-04
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestAdapterErrorNoStateMutation:
    """Adapter error returns error response without modifying any state.

    Covers: UC-004-EXT-F-04
    """

    def test_adapter_error_returns_error_without_state_modification(self, integration_db):
        """When adapter raises, response has adapter_error and zero deliveries.

        Covers: UC-004-EXT-F-04
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_err",
            )

            env.set_adapter_error(RuntimeError("GAM API timeout"))

            result = env.call_impl(
                media_buy_ids=["mb_err"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert isinstance(result, GetMediaBuyDeliveryResponse)
            assert result.errors is not None
            assert len(result.errors) == 1
            assert result.errors[0].code == "adapter_error"
            assert "mb_err" in result.errors[0].message
            assert result.media_buy_deliveries == []
            assert result.aggregated_totals.impressions == 0.0
            assert result.aggregated_totals.spend == 0.0
            assert result.aggregated_totals.media_buy_count == 0


# ---------------------------------------------------------------------------
# UC-004-MAIN-03
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestMultipleMediaBuyDelivery:
    """Array-based identification returns delivery for all requested media buys.

    Covers: UC-004-MAIN-03
    """

    def test_three_media_buys_returns_all_deliveries_and_aggregated_totals(self, integration_db):
        """Given 3 media buys, when requesting all 3, get all 3 back with aggregated totals.

        Covers: UC-004-MAIN-03
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")

            for i, mb_id in enumerate(["mb_1", "mb_2", "mb_3"]):
                MediaBuyFactory(
                    tenant=tenant,
                    principal=principal,
                    media_buy_id=mb_id,
                )
                env.set_adapter_response(
                    mb_id,
                    impressions=1000 * (i + 1),
                    spend=100.0 * (i + 1),
                    package_id=f"pkg_{mb_id}",
                )

            response = env.call_impl(
                media_buy_ids=["mb_1", "mb_2", "mb_3"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert isinstance(response, GetMediaBuyDeliveryResponse)
            assert len(response.media_buy_deliveries) == 3
            returned_ids = {d.media_buy_id for d in response.media_buy_deliveries}
            assert returned_ids == {"mb_1", "mb_2", "mb_3"}

            delivery_map = {d.media_buy_id: d for d in response.media_buy_deliveries}
            assert delivery_map["mb_1"].totals.impressions == 1000
            assert delivery_map["mb_1"].totals.spend == 100.0
            assert delivery_map["mb_2"].totals.impressions == 2000
            assert delivery_map["mb_2"].totals.spend == 200.0
            assert delivery_map["mb_3"].totals.impressions == 3000
            assert delivery_map["mb_3"].totals.spend == 300.0

            agg = response.aggregated_totals
            assert agg.media_buy_count == 3
            assert agg.impressions == 6000.0
            assert agg.spend == 600.0


# ---------------------------------------------------------------------------
# UC-004-MAIN-04
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestNoIdentifiersReturnAll:
    """No identifiers provided returns delivery data for ALL principal's media buys.

    Covers: UC-004-MAIN-04
    """

    def test_all_five_media_buys_returned_when_no_identifiers(self, integration_db):
        """When media_buy_ids is not provided, response contains
        delivery data for ALL 5 media buys owned by the principal.

        Covers: UC-004-MAIN-04
        """
        from datetime import timedelta

        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        today = date.today()
        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")

            for i in range(1, 6):
                mb_id = f"mb_{i:03d}"
                MediaBuyFactory(
                    tenant=tenant,
                    principal=principal,
                    media_buy_id=mb_id,
                    start_date=today - timedelta(days=30),
                    end_date=today + timedelta(days=30),
                    budget=10000.0 + i * 1000,
                )
                env.set_adapter_response(
                    mb_id,
                    impressions=1000 * i,
                    spend=100.0 * i,
                    package_id=f"pkg_{mb_id}",
                )

            response = env.call_impl()

            assert isinstance(response, GetMediaBuyDeliveryResponse)
            assert len(response.media_buy_deliveries) == 5
            assert response.aggregated_totals.media_buy_count == 5

            returned_ids = {d.media_buy_id for d in response.media_buy_deliveries}
            expected_ids = {f"mb_{i:03d}" for i in range(1, 6)}
            assert returned_ids == expected_ids
            assert response.errors is None

    def test_aggregated_totals_sum_across_all_buys(self, integration_db):
        """Aggregated totals reflect the sum of delivery across all 5 media buys.

        Covers: UC-004-MAIN-04
        """
        from datetime import timedelta

        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        today = date.today()
        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")

            for i in range(1, 6):
                mb_id = f"mb_{i:03d}"
                MediaBuyFactory(
                    tenant=tenant,
                    principal=principal,
                    media_buy_id=mb_id,
                    start_date=today - timedelta(days=30),
                    end_date=today + timedelta(days=30),
                )
                env.set_adapter_response(
                    mb_id,
                    impressions=1000,
                    spend=100.0,
                    package_id=f"pkg_{mb_id}",
                )

            response = env.call_impl()

            assert response.aggregated_totals.impressions == 5000.0
            assert response.aggregated_totals.spend == 500.0
            assert response.aggregated_totals.media_buy_count == 5


# ---------------------------------------------------------------------------
# UC-004-MAIN-09
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestPackageLevelBreakdowns:
    """Media buy delivery includes per-package breakdowns with metrics.

    Covers: UC-004-MAIN-09
    """

    def test_two_packages_each_have_own_metrics(self, integration_db):
        """Two packages in a media buy each get distinct impressions, spend, and metric fields.

        Covers: UC-004-MAIN-09
        """
        from src.core.schemas import GetMediaBuyDeliveryResponse
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_two_pkg",
                start_date=date(2025, 3, 1),
                end_date=date(2025, 3, 31),
                raw_request={
                    "packages": [
                        {"package_id": "pkg_A", "product_id": "prod_A"},
                        {"package_id": "pkg_B", "product_id": "prod_B"},
                    ],
                },
            )

            env.set_adapter_response(
                "mb_two_pkg",
                packages=[
                    {"package_id": "pkg_A", "impressions": 10000, "spend": 500.0},
                    {"package_id": "pkg_B", "impressions": 5000, "spend": 250.0},
                ],
            )

            result = env.call_impl(
                media_buy_ids=["mb_two_pkg"],
                start_date="2025-03-01",
                end_date="2025-03-31",
            )

            assert isinstance(result, GetMediaBuyDeliveryResponse)
            assert len(result.media_buy_deliveries) == 1

            delivery = result.media_buy_deliveries[0]
            assert len(delivery.by_package) == 2

            pkg_map = {p.package_id: p for p in delivery.by_package}
            assert "pkg_A" in pkg_map
            assert "pkg_B" in pkg_map

            assert pkg_map["pkg_A"].impressions == 10000.0
            assert pkg_map["pkg_A"].spend == 500.0
            assert pkg_map["pkg_B"].impressions == 5000.0
            assert pkg_map["pkg_B"].spend == 250.0

    def test_package_breakdowns_include_pacing_for_active_buy(self, integration_db):
        """Active media buy packages report pacing_index=1.0.

        Covers: UC-004-MAIN-09
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_active",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                raw_request={
                    "packages": [
                        {"package_id": "pkg_X", "product_id": "prod_X"},
                        {"package_id": "pkg_Y", "product_id": "prod_Y"},
                    ],
                },
            )

            env.set_adapter_response(
                "mb_active",
                packages=[
                    {"package_id": "pkg_X", "impressions": 5000, "spend": 250.0},
                    {"package_id": "pkg_Y", "impressions": 3000, "spend": 150.0},
                ],
            )

            result = env.call_impl(
                media_buy_ids=["mb_active"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            delivery = result.media_buy_deliveries[0]
            assert delivery.status == "active"

            for pkg in delivery.by_package:
                assert pkg.pacing_index == 1.0

    def test_totals_reflect_sum_of_package_metrics(self, integration_db):
        """Media buy totals are consistent with the sum of package-level metrics.

        Covers: UC-004-MAIN-09
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_sum",
                start_date=date(2025, 4, 1),
                end_date=date(2025, 4, 30),
                raw_request={
                    "packages": [
                        {"package_id": "pkg_1", "product_id": "prod_1"},
                        {"package_id": "pkg_2", "product_id": "prod_2"},
                    ],
                },
            )

            env.set_adapter_response(
                "mb_sum",
                packages=[
                    {"package_id": "pkg_1", "impressions": 7000, "spend": 350.0},
                    {"package_id": "pkg_2", "impressions": 5000, "spend": 250.0},
                ],
            )

            result = env.call_impl(
                media_buy_ids=["mb_sum"],
                start_date="2025-04-01",
                end_date="2025-04-30",
            )

            delivery = result.media_buy_deliveries[0]
            assert delivery.totals.impressions == 12000.0
            assert delivery.totals.spend == 600.0

            pkg_impressions = sum(p.impressions for p in delivery.by_package)
            pkg_spend = sum(p.spend for p in delivery.by_package)
            assert pkg_impressions == delivery.totals.impressions
            assert pkg_spend == delivery.totals.spend


# ---------------------------------------------------------------------------
# UC-004-MAIN-10
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestPackageDeliveryStatus:
    """Media buy status computation based on package delivery states.

    The production code computes media-buy-level status (ready/active/completed)
    based on date comparison against the request end_date (reference_date).

    Covers: UC-004-MAIN-10
    """

    def test_rq1_buy_before_start_has_ready_status(self, integration_db):
        """Media buy before its start date gets status 'ready'.

        Covers: UC-004-MAIN-10
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_future",
                start_date=date(2025, 6, 1),
                end_date=date(2025, 12, 31),
            )
            env.set_adapter_response("mb_future", impressions=0, spend=0.0)

            from adcp.types import MediaBuyStatus

            resp = env.call_impl(
                media_buy_ids=["mb_future"],
                status_filter=[MediaBuyStatus.pending_activation],
                start_date="2025-01-01",
                end_date="2025-03-15",
            )

            assert len(resp.media_buy_deliveries) == 1
            assert resp.media_buy_deliveries[0].status == "ready"

    def test_rq2_buy_in_flight_has_active_status(self, integration_db):
        """Media buy within its flight dates gets status 'active'.

        Covers: UC-004-MAIN-10
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_active",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            env.set_adapter_response("mb_active", impressions=1000, spend=50.0)

            resp = env.call_impl(
                media_buy_ids=["mb_active"],
                start_date="2025-01-01",
                end_date="2025-06-15",
            )

            assert len(resp.media_buy_deliveries) == 1
            assert resp.media_buy_deliveries[0].status == "active"

    def test_rq3_buy_past_end_has_completed_status(self, integration_db):
        """Media buy past its end date gets status 'completed'.

        Covers: UC-004-MAIN-10
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_past",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
            )
            env.set_adapter_response("mb_past", impressions=5000, spend=250.0)

            from adcp.types import MediaBuyStatus

            resp = env.call_impl(
                media_buy_ids=["mb_past"],
                status_filter=[MediaBuyStatus.completed],
                start_date="2025-01-01",
                end_date="2025-06-15",
            )

            assert len(resp.media_buy_deliveries) == 1
            assert resp.media_buy_deliveries[0].status == "completed"

    def test_rq4_multiple_buys_different_statuses(self, integration_db):
        """Multiple media buys return their respective date-based statuses.

        Covers: UC-004-MAIN-10
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_future",
                start_date=date(2025, 9, 1),
                end_date=date(2025, 12, 31),
            )
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_active",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_completed",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 3, 31),
            )
            env.set_adapter_response("mb_future", impressions=0, spend=0.0)
            env.set_adapter_response("mb_active", impressions=1000, spend=50.0)
            env.set_adapter_response("mb_completed", impressions=5000, spend=250.0)

            from adcp.types import MediaBuyStatus

            resp = env.call_impl(
                media_buy_ids=["mb_future", "mb_active", "mb_completed"],
                status_filter=[
                    MediaBuyStatus.pending_activation,
                    MediaBuyStatus.active,
                    MediaBuyStatus.completed,
                ],
                start_date="2025-01-01",
                end_date="2025-06-15",
            )

            assert len(resp.media_buy_deliveries) == 3
            status_map = {d.media_buy_id: d.status for d in resp.media_buy_deliveries}
            assert status_map["mb_future"] == "ready"
            assert status_map["mb_active"] == "active"
            assert status_map["mb_completed"] == "completed"

    def test_rq5_package_delivery_has_no_delivery_status_field(self):
        """PackageDelivery lacks delivery_status -- obligation gap.

        Covers: UC-004-MAIN-10
        """
        from src.core.schemas.delivery import DeliveryStatus, PackageDelivery

        assert DeliveryStatus.delivering.value == "delivering"
        assert DeliveryStatus.completed.value == "completed"
        assert DeliveryStatus.budget_exhausted.value == "budget_exhausted"
        assert DeliveryStatus.flight_ended.value == "flight_ended"
        assert DeliveryStatus.goal_met.value == "goal_met"

        field_names = set(PackageDelivery.model_fields.keys())
        assert "delivery_status" not in field_names, (
            "If this fails, delivery_status was added to PackageDelivery -- "
            "update this test to PASS and verify the computation logic."
        )


# ---------------------------------------------------------------------------
# UC-004-MAIN-11
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestAggregatedTotalsMultipleBuys:
    """Aggregated totals are correctly summed across three media buys.

    Covers: UC-004-MAIN-11
    """

    def test_aggregated_totals_sum_across_three_buys(self, integration_db):
        """Three media buys with known metrics produce correct aggregated totals.

        Covers: UC-004-MAIN-11
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_1",
                budget=5000.0,
            )
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_2",
                budget=10000.0,
            )
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_3",
                budget=2500.0,
            )
            env.set_adapter_response("mb_1", impressions=1000, spend=50.0)
            env.set_adapter_response("mb_2", impressions=2000, spend=100.0)
            env.set_adapter_response("mb_3", impressions=500, spend=25.0)

            response = env.call_impl(
                media_buy_ids=["mb_1", "mb_2", "mb_3"],
                start_date="2025-01-01",
                end_date="2025-06-30",
            )

            assert isinstance(response, GetMediaBuyDeliveryResponse)

            agg = response.aggregated_totals
            assert agg.impressions == 3500.0
            assert agg.spend == 175.0
            assert agg.media_buy_count == 3

            assert len(response.media_buy_deliveries) == 3
            delivery_ids = {d.media_buy_id for d in response.media_buy_deliveries}
            assert delivery_ids == {"mb_1", "mb_2", "mb_3"}

    def test_per_buy_totals_match_individual_adapter_data(self, integration_db):
        """Each media_buy_delivery has correct individual totals from its adapter response.

        Covers: UC-004-MAIN-11
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_1",
                budget=5000.0,
            )
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_2",
                budget=10000.0,
            )
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_3",
                budget=2500.0,
            )
            env.set_adapter_response("mb_1", impressions=1000, spend=50.0)
            env.set_adapter_response("mb_2", impressions=2000, spend=100.0)
            env.set_adapter_response("mb_3", impressions=500, spend=25.0)

            response = env.call_impl(
                media_buy_ids=["mb_1", "mb_2", "mb_3"],
                start_date="2025-01-01",
                end_date="2025-06-30",
            )

            by_id = {d.media_buy_id: d for d in response.media_buy_deliveries}

            assert by_id["mb_1"].totals.impressions == 1000.0
            assert by_id["mb_1"].totals.spend == 50.0
            assert by_id["mb_2"].totals.impressions == 2000.0
            assert by_id["mb_2"].totals.spend == 100.0
            assert by_id["mb_3"].totals.impressions == 500.0
            assert by_id["mb_3"].totals.spend == 25.0

            agg = response.aggregated_totals
            sum_impressions = sum(d.totals.impressions for d in response.media_buy_deliveries)
            sum_spend = sum(d.totals.spend for d in response.media_buy_deliveries)
            assert agg.impressions == sum_impressions
            assert agg.spend == sum_spend


# ---------------------------------------------------------------------------
# UC-004-MAIN-12
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestProtocolEnvelopeStatusCompleted:
    """Successful delivery query returns a well-formed response (protocol envelope).

    Covers: UC-004-MAIN-12
    """

    def test_successful_query_returns_response_type(self, integration_db):
        """_impl returns GetMediaBuyDeliveryResponse on success.

        Covers: UC-004-MAIN-12
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=1000, spend=50.0)

            response = env.call_impl(media_buy_ids=["mb_001"])
            assert isinstance(response, GetMediaBuyDeliveryResponse)

    def test_successful_query_has_no_errors(self, integration_db):
        """Successful delivery query returns errors=None.

        Covers: UC-004-MAIN-12
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=1000, spend=50.0)

            response = env.call_impl(media_buy_ids=["mb_001"])
            assert response.errors is None

    def test_successful_query_contains_delivery_data(self, integration_db):
        """Successful query populates media_buy_deliveries.

        Covers: UC-004-MAIN-12
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=1000, spend=50.0)

            response = env.call_impl(media_buy_ids=["mb_001"])
            assert len(response.media_buy_deliveries) == 1
            assert response.media_buy_deliveries[0].media_buy_id == "mb_001"

    def test_successful_query_has_required_envelope_fields(self, integration_db):
        """Protocol envelope includes all required top-level fields.

        Covers: UC-004-MAIN-12
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=1000, spend=50.0)

            response = env.call_impl(media_buy_ids=["mb_001"])
            assert response.reporting_period is not None
            assert response.currency is not None
            assert response.aggregated_totals is not None
            assert response.media_buy_deliveries is not None


# ---------------------------------------------------------------------------
# UC-004-MAIN-15
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliverySpendComputation:
    """CPM spend computation: impressions / 1000 * rate propagates through delivery.

    Covers: UC-004-MAIN-15
    """

    def test_cpm_spend_propagated_to_totals_and_aggregated(self, integration_db):
        """Adapter returns CPM-computed spend ($50 for 10k imps at $5 CPM);
        _impl propagates it to media-buy totals AND aggregated_totals.

        Covers: UC-004-MAIN-15
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        cpm_rate = 5.00
        impressions = 10_000
        expected_spend = impressions / 1000 * cpm_rate  # $50.00

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_cpm",
                start_date=date(2025, 6, 1),
                end_date=date(2025, 6, 30),
                budget=500.0,
            )

            # Configure adapter with CPM delivery data
            env.set_adapter_response("mb_cpm", impressions=impressions, spend=expected_spend)

            response = env.call_impl(
                media_buy_ids=["mb_cpm"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert len(response.media_buy_deliveries) == 1
            mb_delivery = response.media_buy_deliveries[0]

            assert mb_delivery.totals.spend == expected_spend
            assert mb_delivery.totals.impressions == impressions

            assert response.aggregated_totals.spend == expected_spend
            assert response.aggregated_totals.impressions == float(impressions)

            assert len(mb_delivery.by_package) == 1
            pkg = mb_delivery.by_package[0]
            assert pkg.package_id == "pkg_001"
            assert pkg.spend == expected_spend
            assert pkg.impressions == float(impressions)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# UC-004-MAIN-17
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestPartialResolutionMissingIds:
    """Partial resolution returns found buys only, reports missing as errors.

    Covers: UC-004-MAIN-17
    """

    def test_missing_id_excluded_from_deliveries_with_error(self, integration_db):
        """When some media_buy_ids don't exist, return data for found ones
        and report missing IDs in the errors array.

        Covers: UC-004-MAIN-17
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_1",
            )
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_2",
            )
            env.set_adapter_response("mb_1", impressions=1000, spend=50.0)
            env.set_adapter_response("mb_2", impressions=2000, spend=100.0)

            response = env.call_impl(
                media_buy_ids=["mb_1", "mb_999", "mb_2"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            # Delivery data returned for mb_1 and mb_2 only
            returned_ids = {d.media_buy_id for d in response.media_buy_deliveries}
            assert returned_ids == {"mb_1", "mb_2"}

            # mb_999 is NOT in deliveries
            assert "mb_999" not in returned_ids

            # Errors array reports mb_999 as not found
            assert response.errors is not None
            error_messages = [e.message for e in response.errors]
            assert any("mb_999" in msg for msg in error_messages)

            # Aggregated totals reflect only the 2 found buys
            assert response.aggregated_totals.media_buy_count == 2


# ---------------------------------------------------------------------------
# UC-004-MAIN-20
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestUnpopulatedFieldsGraceful:
    """Verify unpopulated schema fields (gaps G42, G44) handled without error.

    Covers: UC-004-MAIN-20
    """

    def test_daily_breakdown_is_none_without_error(self, integration_db):
        """Production sets daily_breakdown=None; response assembles without error.

        Covers: UC-004-MAIN-20
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=1000, spend=50.0)

            result = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert isinstance(result, GetMediaBuyDeliveryResponse)
            assert len(result.media_buy_deliveries) == 1
            delivery = result.media_buy_deliveries[0]
            # daily_breakdown is explicitly None (gap G42) — no error raised
            assert delivery.daily_breakdown is None

    def test_delivery_totals_schema_lacks_effective_rate(self):
        """DeliveryTotals does not have effective_rate field (gap G44).

        Covers: UC-004-MAIN-20
        """
        from src.core.schemas.delivery import DeliveryTotals

        totals = DeliveryTotals(
            impressions=5000.0,
            spend=250.0,
            clicks=0,
            ctr=None,
            video_completions=None,
            completion_rate=None,
        )
        assert not hasattr(totals, "effective_rate") or "effective_rate" not in DeliveryTotals.model_fields
        # viewability is now present on DeliveryTotals (salesagent-2s79)
        assert "viewability" in DeliveryTotals.model_fields
        assert totals.impressions == 5000.0
        assert totals.spend == 250.0

    def test_package_delivery_schema_lacks_creative_level_breakdowns(self):
        """PackageDelivery does not have by_creative / creative_level_breakdowns (gap G42).

        Covers: UC-004-MAIN-20
        """
        from src.core.schemas.delivery import PackageDelivery

        pkg = PackageDelivery(
            package_id="pkg_001",
            impressions=5000.0,
            spend=250.0,
            clicks=None,
            video_completions=None,
            pacing_index=1.0,
            pricing_model=None,
            rate=None,
            currency=None,
        )
        assert "by_creative" not in PackageDelivery.model_fields
        assert pkg.package_id == "pkg_001"
        assert pkg.impressions == 5000.0

    def test_full_response_assembles_with_all_gap_fields_absent(self, integration_db):
        """End-to-end: _impl returns valid response despite gap fields being absent.

        Covers: UC-004-MAIN-20
        """
        from src.core.schemas.delivery import DeliveryTotals
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=1000, spend=50.0)

            result = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert isinstance(result, GetMediaBuyDeliveryResponse)
            delivery = result.media_buy_deliveries[0]

            # Gap G42: daily_breakdown is None
            assert delivery.daily_breakdown is None

            # Gap G44: effective_rate not on local DeliveryTotals
            assert "effective_rate" not in DeliveryTotals.model_fields

            # viewability is now present on DeliveryTotals (salesagent-2s79)
            assert "viewability" in DeliveryTotals.model_fields

            # Gap G42: creative_level_breakdowns (by_creative) not on PackageDelivery
            for pkg in delivery.by_package:
                assert "by_creative" not in type(pkg).model_fields

            # Response serializes cleanly
            dumped = result.model_dump()
            assert "media_buy_deliveries" in dumped
            assert "daily_breakdown" not in dumped["media_buy_deliveries"][0]
            assert "effective_rate" not in dumped["media_buy_deliveries"][0].get("totals", {})
            assert "viewability" not in dumped["media_buy_deliveries"][0].get("totals", {})


# ---------------------------------------------------------------------------
# UC-004-MAIN-14
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestPricingOptionStringLookup:
    """Verify pricing_option_id string field is used for lookup, not integer PK.

    Bug: salesagent-mq3n -- string-to-integer comparison silently drops pricing
    context, resulting in silent data loss (no clicks calculated for CPC buys).

    Covers: UC-004-MAIN-14
    """

    def test_get_pricing_options_uses_string_id_not_integer_pk(self, integration_db):
        """_get_pricing_options should return dict keyed by string pricing_option_id.

        Covers: UC-004-MAIN-14
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import PricingOption, Product, Tenant
        from src.core.database.repositories.product import ProductRepository
        from src.core.tools.media_buy_delivery import _get_pricing_options

        with get_db_session() as session:
            tenant = Tenant(
                tenant_id="t1",
                name="Test",
                subdomain="t1",
                is_active=True,
                ad_server="mock",
            )
            session.add(tenant)
            session.flush()
            product = Product(
                tenant_id="t1",
                product_id="prod1",
                name="Test Product",
                format_ids=[],
                property_tags=["all_inventory"],
                targeting_template={},
                delivery_type="standard",
            )
            session.add(product)
            session.flush()
            session.add(
                PricingOption(
                    tenant_id="t1",
                    product_id="prod1",
                    pricing_model="cpm",
                    rate=5.00,
                    currency="USD",
                    is_fixed=True,
                )
            )
            session.commit()

        with get_db_session() as session:
            product_repo = ProductRepository(session, "t1")
            result = _get_pricing_options(["cpm_usd_fixed"], tenant_id="t1", product_repo=product_repo)

        assert "cpm_usd_fixed" in result, (
            f"Expected key 'cpm_usd_fixed', got keys: {list(result.keys())}. "
            f"_get_pricing_options incorrectly uses integer PK."
        )

    def test_non_numeric_pricing_option_id_is_not_silently_discarded(self, integration_db):
        """Non-numeric string pricing_option_ids must not be dropped.

        Covers: UC-004-MAIN-14
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import PricingOption, Product, Tenant
        from src.core.database.repositories.product import ProductRepository
        from src.core.tools.media_buy_delivery import _get_pricing_options

        with get_db_session() as session:
            tenant = Tenant(
                tenant_id="t1",
                name="Test",
                subdomain="t1",
                is_active=True,
                ad_server="mock",
            )
            session.add(tenant)
            session.flush()
            product = Product(
                tenant_id="t1",
                product_id="prod1",
                name="Test Product",
                format_ids=[],
                property_tags=["all_inventory"],
                targeting_template={},
                delivery_type="standard",
            )
            session.add(product)
            session.flush()
            session.add(
                PricingOption(
                    tenant_id="t1",
                    product_id="prod1",
                    pricing_model="cpm",
                    rate=5.00,
                    currency="USD",
                    is_fixed=True,
                )
            )
            session.commit()

        with get_db_session() as session:
            product_repo = ProductRepository(session, "t1")
            result = _get_pricing_options(["cpm_usd_fixed"], tenant_id="t1", product_repo=product_repo)

        assert len(result) > 0, "Non-numeric pricing_option_id 'cpm_usd_fixed' was silently discarded."


# ---------------------------------------------------------------------------
# UC-004-MAIN-19
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliveryMetricsFieldPresence:
    """Tests that delivery metrics include the required schema fields.

    Covers: UC-004-MAIN-19
    """

    def test_totals_include_impressions_spend_clicks_ctr(self, integration_db):
        """Delivery totals include impressions, spend, clicks, and ctr fields.

        Covers: UC-004-MAIN-19
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000, spend=250.0)

            result = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert isinstance(result, GetMediaBuyDeliveryResponse)
            assert len(result.media_buy_deliveries) == 1

            delivery = result.media_buy_deliveries[0]
            totals = delivery.totals

            assert totals.impressions == 5000.0
            assert totals.spend == 250.0
            assert totals.clicks is not None or hasattr(totals, "clicks")
            assert hasattr(totals, "ctr")

    def test_totals_include_video_completions_field(self, integration_db):
        """Delivery totals include video_completions field (where applicable).

        Covers: UC-004-MAIN-19
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000, spend=250.0)

            result = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            delivery = result.media_buy_deliveries[0]
            assert hasattr(delivery.totals, "video_completions")
            assert delivery.totals.video_completions is None

    def test_totals_include_conversions_field(self, integration_db):
        """Delivery totals include conversions metric field.

        Covers: UC-004-MAIN-19
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000, spend=250.0)

            result = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            delivery = result.media_buy_deliveries[0]
            assert hasattr(delivery.totals, "conversions")

    def test_totals_include_viewability_field(self, integration_db):
        """Delivery totals include viewability metric field.

        Covers: UC-004-MAIN-19
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000, spend=250.0)

            result = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            delivery = result.media_buy_deliveries[0]
            assert hasattr(delivery.totals, "viewability")

    def test_aggregated_totals_include_core_metrics(self, integration_db):
        """Response aggregated_totals include impressions, spend, clicks fields.

        Covers: UC-004-MAIN-19
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000, spend=250.0)

            result = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            agg = result.aggregated_totals
            assert agg.impressions == 5000.0
            assert agg.spend == 250.0
            assert agg.media_buy_count == 1
            assert hasattr(agg, "clicks")


# ---------------------------------------------------------------------------
# UC-004-PRICINGOPTION-TYPE-CONSISTENCY-02
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestPricingOptionStringToIntComparisonRejected:
    """PricingOption string-to-integer comparison is detected and rejected.

    Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-02
    """

    def test_pricing_options_keyed_by_string_id_not_integer_pk(self, integration_db):
        """_get_pricing_options maps by string pricing_option_id, not integer PK.

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-02
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import PricingOption, Product, Tenant
        from src.core.database.repositories.product import ProductRepository
        from src.core.tools.media_buy_delivery import _get_pricing_options

        with get_db_session() as session:
            tenant = Tenant(
                tenant_id="t1",
                name="Test",
                subdomain="t1",
                is_active=True,
                ad_server="mock",
            )
            session.add(tenant)
            session.flush()
            product = Product(
                tenant_id="t1",
                product_id="prod1",
                name="Test Product",
                format_ids=[],
                property_tags=["all_inventory"],
                targeting_template={},
                delivery_type="standard",
            )
            session.add(product)
            session.flush()
            po = PricingOption(
                tenant_id="t1",
                product_id="prod1",
                pricing_model="cpm",
                rate=5.00,
                currency="USD",
                is_fixed=True,
            )
            session.add(po)
            session.commit()
            po_id = po.id

        with get_db_session() as session:
            product_repo = ProductRepository(session, "t1")
            result = _get_pricing_options(
                tenant_id="t1",
                pricing_option_ids=["cpm_usd_fixed"],
                product_repo=product_repo,
            )

        # Key assertion: the map uses the string pricing_option_id, NOT the int PK
        assert "cpm_usd_fixed" in result
        assert po_id not in result

    def test_integer_pk_lookup_returns_none(self, integration_db):
        """Looking up pricing option by integer PK returns None (type mismatch caught).

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-02
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import PricingOption, Product, Tenant
        from src.core.database.repositories.product import ProductRepository
        from src.core.tools.media_buy_delivery import _get_pricing_options

        with get_db_session() as session:
            tenant = Tenant(
                tenant_id="t1",
                name="Test",
                subdomain="t1",
                is_active=True,
                ad_server="mock",
            )
            session.add(tenant)
            session.flush()
            product = Product(
                tenant_id="t1",
                product_id="prod1",
                name="Test Product",
                format_ids=[],
                property_tags=["all_inventory"],
                targeting_template={},
                delivery_type="standard",
            )
            session.add(product)
            session.flush()
            session.add(
                PricingOption(
                    tenant_id="t1",
                    product_id="prod1",
                    pricing_model="cpc",
                    rate=2.50,
                    currency="USD",
                    is_fixed=True,
                )
            )
            session.commit()

        with get_db_session() as session:
            product_repo = ProductRepository(session, "t1")
            result = _get_pricing_options(
                tenant_id="t1",
                pricing_option_ids=["cpc_usd_fixed"],
                product_repo=product_repo,
            )

        # Only the string pricing_option_id should work
        assert result.get("cpc_usd_fixed") is not None


# ---------------------------------------------------------------------------
# UC-004-PRICINGOPTION-TYPE-CONSISTENCY-03
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestEndToEndDeliveryMetricsCpmPricing:
    """End-to-end delivery metrics with CPM pricing.

    Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-03
    """

    def test_cpm_spend_computed_correctly(self, integration_db):
        """CPM: 10,000 impressions at $2.50 CPM -> spend $25.00.

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-03
        """
        from tests.factories import (
            MediaBuyFactory,
            MediaPackageFactory,
            PrincipalFactory,
            TenantFactory,
        )
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_cpm",
                raw_request={
                    "packages": [
                        {
                            "package_id": "pkg_cpm",
                            "product_id": "prod_cpm",
                            "pricing_option_id": "cpm_usd_fixed",
                        }
                    ],
                },
            )
            MediaPackageFactory(
                media_buy=buy,
                package_id="pkg_cpm",
                package_config={
                    "package_id": "pkg_cpm",
                    "product_id": "prod_cpm",
                    "pricing_info": {
                        "pricing_model": "cpm",
                        "rate": 2.50,
                        "currency": "USD",
                    },
                },
            )
            env.set_adapter_response(
                "mb_cpm",
                impressions=10000,
                spend=25.0,
                package_id="pkg_cpm",
            )

            result = env.call_impl(
                media_buy_ids=["mb_cpm"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert result.aggregated_totals.media_buy_count == 1
            delivery = result.media_buy_deliveries[0]
            assert delivery.totals.spend == 25.0
            assert delivery.totals.impressions == 10000.0

    def test_cpm_pricing_option_identified_in_response(self, integration_db):
        """CPM pricing option should be identifiable in the delivery response.

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-03
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_cpm2",
                raw_request={
                    "packages": [
                        {
                            "package_id": "pkg_cpm2",
                            "product_id": "prod_cpm2",
                            "pricing_option_id": "cpm_usd_fixed",
                        }
                    ],
                },
            )
            env.set_adapter_response(
                "mb_cpm2",
                impressions=10000,
                spend=25.0,
                package_id="pkg_cpm2",
            )

            result = env.call_impl(
                media_buy_ids=["mb_cpm2"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            delivery = result.media_buy_deliveries[0]
            assert hasattr(delivery, "pricing_options") or any(
                hasattr(pkg, "pricing_option_id") and pkg.pricing_option_id == "cpm_usd_fixed"
                for pkg in delivery.by_package
            )


# ---------------------------------------------------------------------------
# UC-004-PRICINGOPTION-TYPE-CONSISTENCY-04
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestEndToEndDeliveryMetricsCpcPricing:
    """End-to-end delivery metrics with CPC pricing.

    Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-04
    """

    @pytest.mark.xfail(
        reason=(
            "Production code does not compute clicks from CPC spend/rate. "
            "Adapter returns clicks=None and production passes it through "
            "without deriving clicks = floor(spend / rate)."
        ),
        strict=True,
    )
    def test_cpc_clicks_calculated_from_spend_and_rate(self, integration_db):
        """CPC: $250.00 spend at $0.50 CPC -> 500 clicks (floor(spend/rate)).

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-04
        """
        from decimal import Decimal

        from tests.factories import (
            MediaBuyFactory,
            PricingOptionFactory,
            PrincipalFactory,
            ProductFactory,
            TenantFactory,
        )
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            product = ProductFactory(tenant=tenant)
            po = PricingOptionFactory(
                product=product,
                pricing_model="cpc",
                rate=Decimal("0.50"),
            )
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_cpc",
                raw_request={
                    "pricing_option_id": str(po.id),
                    "packages": [
                        {
                            "package_id": "pkg_cpc",
                            "product_id": product.product_id,
                            "pricing_option_id": str(po.id),
                        }
                    ],
                },
            )
            env.set_adapter_response(
                "mb_cpc",
                impressions=5000,
                spend=250.0,
                package_id="pkg_cpc",
            )

            result = env.call_impl(
                media_buy_ids=["mb_cpc"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert result.aggregated_totals.media_buy_count == 1
            delivery = result.media_buy_deliveries[0]
            assert delivery.totals.spend == 250.0
            # CPC click calculation: floor(spend / rate) = floor(250 / 0.50) = 500
            assert delivery.by_package[0].clicks == 500

    def test_cpc_pricing_option_identified_in_response(self, integration_db):
        """CPC pricing option should be identifiable in the delivery response.

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-04
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_cpc2",
                raw_request={
                    "packages": [
                        {
                            "package_id": "pkg_cpc2",
                            "product_id": "prod_cpc2",
                            "pricing_option_id": "cpc_usd_standard",
                        }
                    ],
                },
            )
            env.set_adapter_response(
                "mb_cpc2",
                impressions=5000,
                spend=250.0,
                package_id="pkg_cpc2",
            )

            result = env.call_impl(
                media_buy_ids=["mb_cpc2"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            delivery = result.media_buy_deliveries[0]
            assert hasattr(delivery, "pricing_options") or any(
                hasattr(pkg, "pricing_option_id") and pkg.pricing_option_id == "cpc_usd_standard"
                for pkg in delivery.by_package
            )


# ---------------------------------------------------------------------------
# UC-004-PRICINGOPTION-TYPE-CONSISTENCY-05
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliveryMetricsFlatRatePricing:
    """End-to-end delivery metrics with FLAT_RATE pricing.

    Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-05
    """

    def test_flat_rate_spend_reflects_rate_correctly(self, integration_db):
        """FLAT_RATE pricing: adapter reports spend=$5,000 which flows through.

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-05
        """
        from tests.factories import (
            MediaBuyFactory,
            MediaPackageFactory,
            PrincipalFactory,
            TenantFactory,
        )
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_flat",
                raw_request={
                    "packages": [
                        {
                            "package_id": "pkg_flat",
                            "product_id": "prod_flat",
                            "pricing_option_id": "flat_rate_5k",
                        }
                    ],
                },
            )
            MediaPackageFactory(
                media_buy=buy,
                package_id="pkg_flat",
                package_config={
                    "package_id": "pkg_flat",
                    "product_id": "prod_flat",
                    "pricing_info": {
                        "pricing_model": "flat_rate",
                        "rate": 5000.0,
                        "currency": "USD",
                    },
                },
            )
            env.set_adapter_response(
                "mb_flat",
                impressions=50000,
                spend=5000.0,
                package_id="pkg_flat",
            )

            result = env.call_impl(
                media_buy_ids=["mb_flat"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert result.aggregated_totals.media_buy_count == 1
            delivery = result.media_buy_deliveries[0]
            assert delivery.totals.spend == 5000.0
            assert delivery.totals.impressions == 50000.0
            pkg = delivery.by_package[0]
            assert pkg.spend == 5000.0

    def test_flat_rate_pricing_option_identified_in_response(self, integration_db):
        """FLAT_RATE pricing option should be identifiable in the delivery response.

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-05
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_flat2",
                raw_request={
                    "packages": [
                        {
                            "package_id": "pkg_flat2",
                            "product_id": "prod_flat2",
                            "pricing_option_id": "flat_rate_premium",
                        }
                    ],
                },
            )
            env.set_adapter_response(
                "mb_flat2",
                impressions=50000,
                spend=5000.0,
                package_id="pkg_flat2",
            )

            result = env.call_impl(
                media_buy_ids=["mb_flat2"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            delivery = result.media_buy_deliveries[0]
            assert hasattr(delivery, "pricing_options") or any(
                hasattr(pkg, "pricing_option_id") and pkg.pricing_option_id == "flat_rate_premium"
                for pkg in delivery.by_package
            )


# ---------------------------------------------------------------------------
# UC-004-RESPONSE-SERIALIZATION-SALESAGENT-02
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliveryResponsePreservesExtFields:
    """Delivery response should preserve ext fields from adapter.

    Covers: UC-004-RESPONSE-SERIALIZATION-SALESAGENT-02
    """

    def test_ext_fields_preserved_in_delivery_data(self, integration_db):
        """ext fields from adapter response should flow through to MediaBuyDeliveryData.

        Covers: UC-004-RESPONSE-SERIALIZATION-SALESAGENT-02
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_ext",
            )
            env.set_adapter_response("mb_ext", impressions=1000, spend=50.0)

            result = env.call_impl(
                media_buy_ids=["mb_ext"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert len(result.media_buy_deliveries) == 1
            delivery = result.media_buy_deliveries[0]
            assert hasattr(delivery, "ext") and delivery.ext is not None

    def test_ext_fields_preserved_in_model_dump(self, integration_db):
        """ext fields should survive model_dump() serialization.

        Covers: UC-004-RESPONSE-SERIALIZATION-SALESAGENT-02
        """
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_ext2",
            )
            env.set_adapter_response("mb_ext2", impressions=1000, spend=50.0)

            result = env.call_impl(
                media_buy_ids=["mb_ext2"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            dumped = result.model_dump()
            delivery_dumped = dumped["media_buy_deliveries"][0]
            assert "ext" in delivery_dumped


# ---------------------------------------------------------------------------
# UC-004-ALT-CUSTOM-DATE-RANGE-01
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestCustomDateRangeBothProvided:
    """Custom date range with both start and end provided.

    Covers: UC-004-ALT-CUSTOM-DATE-RANGE-01
    """

    def test_reporting_period_matches_requested_dates(self, integration_db):
        """When start_date and end_date are provided, reporting_period matches them.

        Covers: UC-004-ALT-CUSTOM-DATE-RANGE-01
        """
        from datetime import UTC, date, datetime

        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                start_date=date(2026, 3, 1),
                end_date=date(2026, 3, 7),
            )
            env.set_adapter_response(impressions=1000)

            response = env.call_impl(
                media_buy_ids=[principal.media_buys[0].media_buy_id] if hasattr(principal, "media_buys") else None,
                start_date="2026-03-01",
                end_date="2026-03-07",
            )
            assert response.reporting_period.start == datetime(2026, 3, 1, tzinfo=UTC)
            assert response.reporting_period.end == datetime(2026, 3, 7, tzinfo=UTC)


# ---------------------------------------------------------------------------
# UC-004-ALT-CUSTOM-DATE-RANGE-04
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestCustomDateRangeOverridesDefault:
    """Custom date range overrides default 30-day window.

    Covers: UC-004-ALT-CUSTOM-DATE-RANGE-04
    """

    def test_ninety_day_range_not_truncated(self, integration_db):
        """A 90-day custom range is used in full — 30-day default NOT applied.

        Covers: UC-004-ALT-CUSTOM-DATE-RANGE-04
        """
        from datetime import date

        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            env.set_adapter_response(buy.media_buy_id, impressions=1000)

            response = env.call_impl(
                media_buy_ids=[buy.media_buy_id],
                start_date="2025-01-01",
                end_date="2025-04-01",
            )
            delta = response.reporting_period.end - response.reporting_period.start
            assert delta.days == 90, f"Expected 90-day range, got {delta.days} days"


# ---------------------------------------------------------------------------
# UC-004-EXT-B-01
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestPrincipalNotFoundReturnsError:
    """When principal does not exist in DB, response contains principal_not_found error.

    Covers: UC-004-EXT-B-01
    """

    def test_principal_not_found_returns_error_in_response(self, integration_db):
        """Valid token but principal not in DB returns principal_not_found error.

        Covers: UC-004-EXT-B-01
        """
        from tests.factories import TenantFactory
        from tests.harness import DeliveryPollEnv

        # Create tenant but NO principal — principal_id won't exist in DB
        with DeliveryPollEnv(tenant_id="t1", principal_id="ghost_principal") as env:
            TenantFactory(tenant_id="t1")
            # Don't create any principal — ghost_principal doesn't exist

            response = env.call_impl()

        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "principal_not_found"
        assert response.media_buy_deliveries == []


# ---------------------------------------------------------------------------
# UC-004-MAIN-18
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestNonexistentMediaBuyIdsReturnEmptyDeliveries:
    """Nonexistent media_buy_ids resolve to empty deliveries array.

    Covers: UC-004-MAIN-18
    """

    def test_nonexistent_ids_return_empty_media_buy_deliveries(self, integration_db):
        """Requesting delivery for nonexistent media_buy_ids returns empty deliveries.

        Covers: UC-004-MAIN-18
        """
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")
            # Don't create any media buys — nonexistent_1 won't exist

            result = env.call_impl(
                media_buy_ids=["nonexistent_1"],
                start_date="2025-01-01",
                end_date="2025-12-31",
            )

        assert result.media_buy_deliveries == []
        assert result.aggregated_totals.media_buy_count == 0
        assert result.aggregated_totals.impressions == 0.0
        assert result.aggregated_totals.spend == 0.0


# ---------------------------------------------------------------------------
# UC-004-EXT-G-03 (integration — real circuit breaker)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestCircuitBreakerReportingDelayed:
    """Open circuit breaker marks delivery status as 'reporting_delayed'.

    Integration version: exercises the REAL _is_circuit_breaker_open() code
    path by injecting an OPEN CircuitBreaker into the global singleton.

    Covers: UC-004-EXT-G-03
    """

    def test_open_circuit_breaker_sets_reporting_delayed_status(self, integration_db):
        """When a circuit breaker is OPEN for the tenant, active media buys
        get status='reporting_delayed' instead of 'active'.

        Covers: UC-004-EXT-G-03
        """
        from src.services.webhook_delivery_service import (
            CircuitBreaker,
            CircuitState,
            webhook_delivery_service,
        )
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        endpoint_key = "t1:https://example.com/webhook"
        try:
            # Inject an OPEN circuit breaker into the global singleton
            cb = CircuitBreaker(failure_threshold=3)
            cb.state = CircuitState.OPEN
            webhook_delivery_service._circuit_breakers[endpoint_key] = cb

            with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
                tenant = TenantFactory(tenant_id="t1")
                principal = PrincipalFactory(tenant=tenant, principal_id="p1")
                buy = MediaBuyFactory(
                    tenant=tenant,
                    principal=principal,
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 12, 31),
                )
                env.set_adapter_response(buy.media_buy_id, impressions=5000)

                response = env.call_impl(media_buy_ids=[buy.media_buy_id])

                assert len(response.media_buy_deliveries) == 1
                assert response.media_buy_deliveries[0].status == "reporting_delayed"
        finally:
            # Clean up the injected circuit breaker
            webhook_delivery_service._circuit_breakers.pop(endpoint_key, None)

    def test_closed_circuit_breaker_does_not_affect_status(self, integration_db):
        """When circuit breaker is CLOSED, status remains 'active' (not degraded).

        Covers: UC-004-EXT-G-03
        """
        from src.services.webhook_delivery_service import (
            CircuitBreaker,
            CircuitState,
            webhook_delivery_service,
        )
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        endpoint_key = "t1:https://example.com/webhook"
        try:
            cb = CircuitBreaker(failure_threshold=3)
            assert cb.state == CircuitState.CLOSED
            webhook_delivery_service._circuit_breakers[endpoint_key] = cb

            with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
                tenant = TenantFactory(tenant_id="t1")
                principal = PrincipalFactory(tenant=tenant, principal_id="p1")
                buy = MediaBuyFactory(
                    tenant=tenant,
                    principal=principal,
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 12, 31),
                )
                env.set_adapter_response(buy.media_buy_id, impressions=5000)

                response = env.call_impl(media_buy_ids=[buy.media_buy_id])

                assert len(response.media_buy_deliveries) == 1
                assert response.media_buy_deliveries[0].status == "active"
        finally:
            webhook_delivery_service._circuit_breakers.pop(endpoint_key, None)


# ---------------------------------------------------------------------------
# Partial failure tolerance (coverage: media_buy_delivery.py lines 485-487)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestPartialFailureTolerance:
    """When one media buy's processing raises an exception in the outer loop,
    the response still includes delivery data for the other successful buys.

    Coverage target: media_buy_delivery.py outer except handler (lines 485-487).
    """

    def test_one_buy_fails_other_still_returned(self, integration_db):
        """Given 2 media buys, when processing of buy_2 raises an exception,
        buy_1's delivery data is still present in the response.
        """
        from unittest.mock import patch

        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy_1 = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_ok",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
            buy_2 = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_fail",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
            env.set_adapter_response("mb_ok", impressions=5000, spend=250.0)
            env.set_adapter_response("mb_fail", impressions=3000, spend=150.0)

            # Patch _is_circuit_breaker_open to raise on the second call,
            # triggering the outer except handler (lines 485-487) for buy_2
            # while buy_1 processes normally.
            call_count = {"n": 0}

            def circuit_breaker_side_effect(tenant_id):
                call_count["n"] += 1
                if call_count["n"] == 2:
                    raise RuntimeError("Simulated processing error for buy_2")
                return False

            with patch(
                "src.core.tools.media_buy_delivery._is_circuit_breaker_open",
                side_effect=circuit_breaker_side_effect,
            ):
                response = env.call_impl(media_buy_ids=["mb_ok", "mb_fail"])

            assert isinstance(response, GetMediaBuyDeliveryResponse)
            # buy_1 should be present in the response
            returned_ids = {d.media_buy_id for d in response.media_buy_deliveries}
            assert "mb_ok" in returned_ids, f"Expected mb_ok in deliveries, got: {returned_ids}"
            # buy_2 should be absent (skipped due to outer exception)
            assert "mb_fail" not in returned_ids, f"Expected mb_fail to be absent from deliveries, got: {returned_ids}"


# ---------------------------------------------------------------------------
# Domain business rule: CPC package-level click derivation
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestCpcPackageClicksDerivation:
    """Domain business rule: for CPC pricing, package clicks = floor(spend / rate).

    This is NOT mandated by the AdCP spec — delivery-metrics.json defines clicks
    as optional. This is our product decision: when the adapter doesn't return
    clicks but we know the CPC rate, we derive clicks to give buyers better data.

    The formula: clicks = floor(total_spend / cpc_rate)

    Covers: media_buy_delivery.py line 386
    """

    def test_cpc_package_clicks_derived_from_spend_and_rate(self, integration_db):
        """CPC package with $250 spend at $0.50/click -> 500 clicks.

        Business rule: package_clicks = floor(spend / cpc_rate)
        """
        from decimal import Decimal
        from math import floor

        from tests.factories import (
            MediaBuyFactory,
            PricingOptionFactory,
            PrincipalFactory,
            ProductFactory,
            TenantFactory,
        )
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            product = ProductFactory(tenant=tenant)
            PricingOptionFactory(
                product=product,
                pricing_model="cpc",
                rate=Decimal("0.50"),
                currency="USD",
                is_fixed=True,
            )

            # Use SYNTHETIC pricing_option_id (how _get_pricing_options keys results)
            synthetic_po_id = "cpc_usd_fixed"

            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_cpc",
                raw_request={
                    "packages": [
                        {
                            "package_id": "pkg_cpc",
                            "product_id": product.product_id,
                            "pricing_option_id": synthetic_po_id,
                        }
                    ],
                },
            )

            env.set_adapter_response(
                "mb_cpc",
                impressions=5000,
                spend=250.0,
                package_id="pkg_cpc",
            )

            result = env.call_impl(
                media_buy_ids=["mb_cpc"],
                start_date="2025-06-01",
                end_date="2025-06-30",
            )

            assert result.aggregated_totals.media_buy_count == 1
            delivery = result.media_buy_deliveries[0]
            pkg = delivery.by_package[0]
            assert pkg.package_id == "pkg_cpc"
            # Domain business rule: clicks = floor(spend / cpc_rate)
            assert pkg.clicks == floor(250.0 / 0.50)  # 500


# ---------------------------------------------------------------------------
# Data migration strategy: start_time preferred over start_date for status
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestStartTimeFallbackForStatus:
    """Data migration strategy: start_time (AdCP spec field, nullable) is preferred
    over start_date (legacy NOT NULL column) when determining media buy status.

    Media buys created before the start_time column was added have start_time=None
    and rely on start_date. Newer media buys have both. The delivery code must
    handle both cases correctly.

    Covers: media_buy_delivery.py lines 743, 748
    """

    def test_start_time_used_for_status_when_present(self, integration_db):
        """When start_time is set, status comparison uses start_time.date(),
        not start_date.

        Covers: media_buy_delivery.py line 743, 748
        """
        from datetime import UTC, datetime

        from tests.factories import (
            MediaBuyFactory,
            PrincipalFactory,
            TenantFactory,
        )
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")

            # start_date says 2025-01-01..2027-12-31 (active for any reasonable date)
            # but start_time says 2028-01-01..2028-12-31 (not yet started)
            # If start_time is used, status should be "ready" (not yet active)
            buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_time",
                start_date=date(2025, 1, 1),
                end_date=date(2027, 12, 31),
                start_time=datetime(2028, 1, 1, tzinfo=UTC),
                end_time=datetime(2028, 12, 31, tzinfo=UTC),
            )

            env.set_adapter_response("mb_time", impressions=0, spend=0.0)

            # Query for "active" only — if start_time is respected, mb_time
            # should NOT appear (it's "ready", not "active")
            result = env.call_impl(
                media_buy_ids=[buy.media_buy_id],
                status_filter="active",
            )

            # The media buy should be filtered out because start_time makes it "ready"
            returned_ids = {d.media_buy_id for d in result.media_buy_deliveries}
            assert "mb_time" not in returned_ids
