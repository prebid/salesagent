"""Integration tests for UC-004: Deliver Media Buy Metrics.

Tests the delivery poll flow (_get_media_buy_delivery_impl) with real PostgreSQL.
Derived from unit test stubs in tests/unit/test_delivery.py (UNSPECIFIED subset).

Iron Rule: The unit test stub defines WHAT to test. These integration tests verify
the SAME behavior with real database. If a test fails, fix production code.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    CurrencyLimit,
    MediaBuy,
    MediaPackage,
    PricingOption,
    Principal,
    Product,
    PropertyTag,
    Tenant,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    ReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATCH_PREFIX = "src.core.tools.media_buy_delivery"


def _make_identity(
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id, "name": "Test Tenant"},
        protocol="mcp",
        testing_context=AdCPTestContext(
            dry_run=False,
            mock_time=None,
            jump_to_event=None,
            test_session_id=None,
        ),
    )


def _make_adapter_response(
    media_buy_id: str = "mb_001",
    impressions: int = 5000,
    spend: float = 250.0,
    clicks: int = 50,
    packages: list[dict] | None = None,
) -> AdapterGetMediaBuyDeliveryResponse:
    if packages is None:
        packages = [{"package_id": "pkg_001", "impressions": impressions, "spend": spend}]

    by_package = [
        AdapterPackageDelivery(
            package_id=p["package_id"],
            impressions=p["impressions"],
            spend=p["spend"],
        )
        for p in packages
    ]

    return AdapterGetMediaBuyDeliveryResponse(
        media_buy_id=media_buy_id,
        reporting_period=ReportingPeriod(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 12, 31, tzinfo=UTC),
        ),
        totals=DeliveryTotals(
            impressions=float(impressions),
            spend=spend,
            clicks=float(clicks),
        ),
        by_package=by_package,
        currency="USD",
    )


def _setup_base_state(session) -> dict:
    """Create minimum viable DB state for delivery integration tests.

    Returns dict with tenant_id, principal_id, product_id, pricing_option_id.
    """
    now = datetime.now(UTC)
    tenant_id = "test_tenant"
    principal_id = "test_principal"

    tenant = Tenant(
        tenant_id=tenant_id,
        name="Test Tenant",
        subdomain="test",
        is_active=True,
        ad_server="mock",
        auth_setup_mode=False,
        auto_approve_format_ids=[],
        human_review_required=False,
        policy_settings={},
        authorized_emails=["test@example.com"],
        created_at=now,
        updated_at=now,
    )
    session.add(tenant)
    session.flush()

    currency_limit = CurrencyLimit(
        tenant_id=tenant_id,
        currency_code="USD",
        min_package_budget=1.00,
        max_daily_package_spend=100000.00,
    )
    session.add(currency_limit)

    property_tag = PropertyTag(
        tenant_id=tenant_id,
        tag_id="all_inventory",
        name="All Inventory",
        description="All available inventory",
    )
    session.add(property_tag)

    principal = Principal(
        tenant_id=tenant_id,
        principal_id=principal_id,
        name="Test Principal",
        access_token="test_token_12345",
        platform_mappings={"mock": {"id": "test_advertiser"}},
        created_at=now,
    )
    session.add(principal)

    product = Product(
        tenant_id=tenant_id,
        product_id="prod_display",
        name="Display Ads",
        description="Standard display",
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        targeting_template={},
        delivery_type="guaranteed",
        property_tags=["all_inventory"],
        is_custom=False,
        countries=["US"],
    )
    session.add(product)
    session.flush()

    pricing_option = PricingOption(
        tenant_id=tenant_id,
        product_id="prod_display",
        pricing_model="cpm",
        rate=Decimal("5.00"),
        currency="USD",
        is_fixed=True,
    )
    session.add(pricing_option)
    session.flush()

    session.commit()

    return {
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        "product_id": "prod_display",
        "pricing_option_id": pricing_option.id,  # auto-increment int
    }


def _create_media_buy(
    session,
    *,
    media_buy_id: str,
    tenant_id: str = "test_tenant",
    principal_id: str = "test_principal",
    buyer_ref: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    budget: Decimal = Decimal("10000.00"),
    currency: str = "USD",
    raw_request: dict | None = None,
    pricing_option_id: int | None = None,
) -> MediaBuy:
    """Create a MediaBuy row with sensible defaults."""
    s_date = start_date or date(2025, 1, 1)
    e_date = end_date or date(2025, 12, 31)

    if raw_request is None:
        packages = [{"package_id": f"pkg_{media_buy_id}", "product_id": "prod_display"}]
        if pricing_option_id is not None:
            packages[0]["pricing_option_id"] = str(pricing_option_id)
        raw_request = {
            "packages": packages,
            "buyer_ref": buyer_ref,
        }

    buy = MediaBuy(
        media_buy_id=media_buy_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        buyer_ref=buyer_ref,
        order_name=f"Order {media_buy_id}",
        advertiser_name="Test Advertiser",
        budget=budget,
        currency=currency,
        start_date=s_date,
        end_date=e_date,
        status="active",
        raw_request=raw_request,
    )
    session.add(buy)

    # Also create MediaPackage rows for package delivery queries
    for pkg_data in raw_request.get("packages", []):
        pkg_id = pkg_data.get("package_id", f"pkg_{media_buy_id}")
        media_pkg = MediaPackage(
            media_buy_id=media_buy_id,
            package_id=pkg_id,
            package_config=pkg_data,
        )
        session.add(media_pkg)

    return buy


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.requires_db
class TestDeliverySingleBuyIntegration:
    """Integration: single buy delivery poll with real DB."""

    def test_single_buy_delivery_via_media_buy_id(self, integration_db):
        """UC-004-MAIN-01: happy path fetch delivery for single media buy by media_buy_id.

        Covers: UC-004-MAIN-01
        Verifies real DB lookup of media buy, adapter call, and response assembly.
        Corresponds to unit test: TestDeliveryPollingSingleBuy.test_single_buy_returns_complete_response
        """
        with get_db_session() as session:
            base = _setup_base_state(session)

            _create_media_buy(
                session,
                media_buy_id="mb_int_single",
                buyer_ref="buyer_1",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                raw_request={
                    "packages": [{"package_id": "pkg_a", "product_id": "prod_display"}],
                    "buyer_ref": "buyer_1",
                },
            )
            session.commit()

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_int_single",
            impressions=8000,
            spend=400.0,
            clicks=80,
            packages=[{"package_id": "pkg_a", "impressions": 8000, "spend": 400.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_int_single"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        identity = _make_identity()

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=mock_adapter):
            response = _get_media_buy_delivery_impl(req, identity)

        # reporting_period matches provided dates
        assert response.reporting_period.start.year == 2025
        assert response.reporting_period.start.month == 1
        assert response.reporting_period.end.month == 6

        # currency present
        assert response.currency == "USD"

        # aggregated_totals
        assert response.aggregated_totals.impressions == 8000.0
        assert response.aggregated_totals.spend == 400.0
        assert response.aggregated_totals.media_buy_count == 1

        # media_buy_deliveries
        assert len(response.media_buy_deliveries) == 1
        delivery = response.media_buy_deliveries[0]
        assert delivery.media_buy_id == "mb_int_single"
        assert delivery.buyer_ref == "buyer_1"

        # totals
        assert delivery.totals.impressions == 8000
        assert delivery.totals.spend == 400.0

        # by_package
        assert len(delivery.by_package) == 1
        assert delivery.by_package[0].package_id == "pkg_a"

        # status computed correctly (2025-06-30 between start/end)
        assert delivery.status == "active"

        # no errors
        assert response.errors is None


@pytest.mark.requires_db
class TestDeliveryIdentificationModesIntegration:
    """Integration: media_buy_ids vs buyer_refs with real DB."""

    def test_media_buy_ids_precedence_over_buyer_refs(self, integration_db):
        """UC-004-MAIN-05: media_buy_ids takes precedence when both provided.

        Covers: UC-004-MAIN-05
        Spec: UNSPECIFIED. With real DB, verifies that the query uses media_buy_ids
        and ignores buyer_refs.
        """
        with get_db_session() as session:
            _setup_base_state(session)

            _create_media_buy(
                session,
                media_buy_id="mb_priority",
                buyer_ref="buyer_should_ignore",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            # Create a second buy reachable only by buyer_ref
            _create_media_buy(
                session,
                media_buy_id="mb_other",
                buyer_ref="buyer_alt",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            session.commit()

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_priority",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_mb_priority", "impressions": 100, "spend": 10.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_priority"],
            buyer_refs=["buyer_alt"],  # should be ignored
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        identity = _make_identity()

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=mock_adapter):
            response = _get_media_buy_delivery_impl(req, identity)

        # Only the buy matching media_buy_ids is returned
        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_priority"

    def test_media_buy_ids_lookup(self, integration_db):
        """UC-004-MAIN-01: media_buy_ids resolves correctly from real DB.

        Covers: UC-004-MAIN-01
        Spec: CONFIRMED. Validates that the DB query by media_buy_id works correctly
        with real PostgreSQL and returns the matching buy.
        """
        with get_db_session() as session:
            _setup_base_state(session)

            _create_media_buy(
                session,
                media_buy_id="mb_lookup_1",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            session.commit()

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_lookup_1",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_mb_lookup_1", "impressions": 100, "spend": 10.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_lookup_1"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        identity = _make_identity()

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=mock_adapter):
            response = _get_media_buy_delivery_impl(req, identity)

        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_lookup_1"


@pytest.mark.requires_db
class TestDeliveryStatusFilterIntegration:
    """Integration: status filter with real DB date-based status derivation."""

    def test_status_filter_all_via_explicit_ids(self, integration_db):
        """UC-004-FILT-06: requesting all buys returns buys of any date-derived status.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-06
        Spec: UNSPECIFIED. With real DB, creates buys with different date ranges
        so they derive ready/active/completed status, then requests all by explicit IDs
        with status_filter including all possible statuses.
        """
        with get_db_session() as session:
            _setup_base_state(session)

            # ready: start in future relative to reference_date (2025-06-15)
            _create_media_buy(
                session,
                media_buy_id="mb_ready",
                buyer_ref="ready_ref",
                start_date=date(2025, 7, 1),
                end_date=date(2025, 12, 31),
            )
            # active: reference_date between start and end
            _create_media_buy(
                session,
                media_buy_id="mb_active",
                buyer_ref="active_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            # completed: end before reference_date
            _create_media_buy(
                session,
                media_buy_id="mb_completed",
                buyer_ref="completed_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 5, 1),
            )
            session.commit()

        mock_adapter = MagicMock()
        # Adapter will be called for each buy found
        mock_adapter.get_media_buy_delivery.side_effect = [
            _make_adapter_response(
                media_buy_id=mid,
                impressions=100,
                spend=10.0,
                packages=[{"package_id": f"pkg_{mid}", "impressions": 100, "spend": 10.0}],
            )
            for mid in ["mb_active", "mb_completed", "mb_ready"]
        ]

        # Use media_buy_ids to request all 3 + status_filter as list of all statuses.
        # The joxr fix handles RootModel-wrapped StatusFilter properly, so we can
        # now pass a real list of MediaBuyStatus values instead of a MagicMock.
        from adcp.types import MediaBuyStatus

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_ready", "mb_active", "mb_completed"],
            status_filter=[
                MediaBuyStatus.pending_activation,
                MediaBuyStatus.active,
                MediaBuyStatus.completed,
            ],
            start_date="2025-01-01",
            end_date="2025-06-15",
        )

        identity = _make_identity()

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=mock_adapter):
            response = _get_media_buy_delivery_impl(req, identity)

        returned_ids = {d.media_buy_id for d in response.media_buy_deliveries}
        assert returned_ids == {"mb_ready", "mb_active", "mb_completed"}

    def test_default_filter_active_only(self, integration_db):
        """UC-004-FILT-01: default filter returns only active buys.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-05
        Spec: UNSPECIFIED. With real DB, verifies that without status_filter,
        only buys whose dates span the reference date are returned.
        """
        with get_db_session() as session:
            _setup_base_state(session)

            # active: reference_date between start/end
            _create_media_buy(
                session,
                media_buy_id="mb_active_only",
                buyer_ref="active_only_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            # completed: end before reference_date
            _create_media_buy(
                session,
                media_buy_id="mb_done",
                buyer_ref="done_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 5, 1),
            )
            session.commit()

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_active_only",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_mb_active_only", "impressions": 100, "spend": 10.0}],
        )

        # No status_filter + end_date=2025-06-15 (reference_date)
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-01-01",
            end_date="2025-06-15",
        )
        identity = _make_identity()

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=mock_adapter):
            response = _get_media_buy_delivery_impl(req, identity)

        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_active_only"

    def test_status_filter_no_match_returns_empty(self, integration_db):
        """UC-004-FILT-04: no buys match filter returns empty result.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-04
        Spec: UNSPECIFIED. With real DB, all buys are active but filter=completed.
        """
        with get_db_session() as session:
            _setup_base_state(session)

            _create_media_buy(
                session,
                media_buy_id="mb_only_active",
                buyer_ref="only_active_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            session.commit()

        from adcp.types import MediaBuyStatus

        req = GetMediaBuyDeliveryRequest(
            status_filter=MediaBuyStatus.completed,
            start_date="2025-01-01",
            end_date="2025-06-15",
        )
        identity = _make_identity()

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=MagicMock()):
            response = _get_media_buy_delivery_impl(req, identity)

        assert response.media_buy_deliveries == []
        assert response.aggregated_totals.media_buy_count == 0


@pytest.mark.requires_db
class TestDeliveryPricingOptionIntegration:
    """Integration: pricing_option_id type safety with real DB (CRIT-2)."""

    def test_pricing_option_roundtrip(self, integration_db):
        """_get_pricing_options resolves string pricing_option_id to real PricingOption row.

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-01
        Spec: UNSPECIFIED. CRITICAL: validates the int() cast at the boundary
        (salesagent-mq3n). Creates a PricingOption with auto-increment int PK,
        stores the string ID in raw_request, and verifies delivery resolves it.
        """
        with get_db_session() as session:
            base = _setup_base_state(session)
            po_id = base["pricing_option_id"]  # int PK

            _create_media_buy(
                session,
                media_buy_id="mb_pricing",
                buyer_ref="pricing_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                raw_request={
                    "packages": [
                        {
                            "package_id": "pkg_priced",
                            "product_id": "prod_display",
                            "pricing_option_id": str(po_id),
                        }
                    ],
                    "buyer_ref": "pricing_ref",
                },
            )
            session.commit()

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_pricing",
            impressions=10000,
            spend=50.0,
            clicks=0,
            packages=[{"package_id": "pkg_priced", "impressions": 10000, "spend": 50.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_pricing"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        identity = _make_identity()

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=mock_adapter):
            response = _get_media_buy_delivery_impl(req, identity)

        # Pricing option resolved successfully
        assert len(response.media_buy_deliveries) == 1
        assert response.aggregated_totals.impressions == 10000.0
        assert response.aggregated_totals.spend == 50.0


@pytest.mark.requires_db
class TestDeliveryOwnershipIntegration:
    """Integration: SECURITY ownership isolation with real DB."""

    def test_ownership_isolation(self, integration_db):
        """UC-004-EXT-D1: principal_id filtering hides other principals' buys.

        Covers: UC-004-EXT-D-01
        Spec: UNSPECIFIED. With real DB, creates buys for two principals.
        Requesting as principal_A should not see principal_B's buys.
        """
        with get_db_session() as session:
            base = _setup_base_state(session)
            now = datetime.now(UTC)

            # Create second principal
            principal_b = Principal(
                tenant_id="test_tenant",
                principal_id="other_principal",
                name="Other Principal",
                access_token="other_token",
                platform_mappings={"mock": {"id": "other_advertiser"}},
                created_at=now,
            )
            session.add(principal_b)
            session.flush()

            # Buy owned by test_principal
            _create_media_buy(
                session,
                media_buy_id="mb_mine",
                principal_id="test_principal",
                buyer_ref="mine_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            # Buy owned by other_principal
            _create_media_buy(
                session,
                media_buy_id="mb_theirs",
                principal_id="other_principal",
                buyer_ref="theirs_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            session.commit()

        # Request as test_principal for other_principal's buy
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_theirs"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        identity = _make_identity(principal_id="test_principal")

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=MagicMock()):
            response = _get_media_buy_delivery_impl(req, identity)

        # Not found (ownership isolation)
        assert response.media_buy_deliveries == []
        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "media_buy_not_found"

    def test_ownership_no_info_leakage(self, integration_db):
        """UC-004-EXT-D2: SECURITY: error is media_buy_not_found not ownership_mismatch.

        Covers: UC-004-EXT-D-02
        Spec: UNSPECIFIED. Prevents information leakage about existence of other
        principals' buys. Same error as genuinely nonexistent buy.
        """
        with get_db_session() as session:
            base = _setup_base_state(session)
            now = datetime.now(UTC)

            principal_b = Principal(
                tenant_id="test_tenant",
                principal_id="secret_principal",
                name="Secret Principal",
                access_token="secret_token",
                platform_mappings={"mock": {"id": "secret"}},
                created_at=now,
            )
            session.add(principal_b)
            session.flush()

            _create_media_buy(
                session,
                media_buy_id="mb_secret",
                principal_id="secret_principal",
                buyer_ref="secret_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            session.commit()

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_secret"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        identity = _make_identity(principal_id="test_principal")

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=MagicMock()):
            response = _get_media_buy_delivery_impl(req, identity)

        assert response.errors is not None
        assert response.errors[0].code == "media_buy_not_found"
        # Must NOT reveal ownership
        assert "ownership" not in response.errors[0].message.lower()

    def test_mixed_ownership(self, integration_db):
        """UC-004-EXT-D3: mixed ownership: owned returned, non-owned as errors.

        Covers: UC-004-EXT-D-03
        Spec: UNSPECIFIED. With real DB, request 2 IDs: one owned, one not.
        """
        with get_db_session() as session:
            base = _setup_base_state(session)
            now = datetime.now(UTC)

            principal_b = Principal(
                tenant_id="test_tenant",
                principal_id="other_principal",
                name="Other Principal",
                access_token="other_token_2",
                platform_mappings={"mock": {"id": "other"}},
                created_at=now,
            )
            session.add(principal_b)
            session.flush()

            _create_media_buy(
                session,
                media_buy_id="mb_owned",
                principal_id="test_principal",
                buyer_ref="owned_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            _create_media_buy(
                session,
                media_buy_id="mb_not_owned",
                principal_id="other_principal",
                buyer_ref="not_owned_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
            session.commit()

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_owned",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_mb_owned", "impressions": 100, "spend": 10.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_owned", "mb_not_owned"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        identity = _make_identity(principal_id="test_principal")

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=mock_adapter):
            response = _get_media_buy_delivery_impl(req, identity)

        # Owned buy returned
        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_owned"

        # Non-owned reported as not found (no info leakage)
        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "media_buy_not_found"
        assert "mb_not_owned" in response.errors[0].message


@pytest.mark.requires_db
class TestDeliverySerializationIntegration:
    """Integration: nested serialization roundtrip with real data."""

    def test_nested_serialization_roundtrip(self, integration_db):
        """UC-004-UPG-04: model_dump() correctly serializes nested delivery response.

        Covers: UC-004-RESPONSE-SERIALIZATION-SALESAGENT-01
        Spec: UNSPECIFIED. With real DB, verifies that the response from _impl
        with real DB objects serializes correctly through model_dump(mode='json').
        """
        with get_db_session() as session:
            _setup_base_state(session)

            _create_media_buy(
                session,
                media_buy_id="mb_serial",
                buyer_ref="serial_ref",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                raw_request={
                    "packages": [{"package_id": "pkg_serial", "product_id": "prod_display"}],
                    "buyer_ref": "serial_ref",
                },
            )
            session.commit()

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_serial",
            impressions=2000,
            spend=100.0,
            clicks=20,
            packages=[{"package_id": "pkg_serial", "impressions": 2000, "spend": 100.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_serial"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        identity = _make_identity()

        with patch(f"{_PATCH_PREFIX}.get_adapter", return_value=mock_adapter):
            response = _get_media_buy_delivery_impl(req, identity)

        # Serialize via model_dump
        data = response.model_dump(mode="json")

        # Verify nested structures are properly serialized as dicts/lists
        assert isinstance(data["media_buy_deliveries"], list)
        assert len(data["media_buy_deliveries"]) == 1
        delivery_dict = data["media_buy_deliveries"][0]
        assert isinstance(delivery_dict, dict)
        assert delivery_dict["media_buy_id"] == "mb_serial"

        # Nested totals serialized
        assert isinstance(delivery_dict["totals"], dict)
        assert "impressions" in delivery_dict["totals"]
        assert "spend" in delivery_dict["totals"]

        # Nested by_package serialized
        assert isinstance(delivery_dict["by_package"], list)
        assert len(delivery_dict["by_package"]) == 1
        assert isinstance(delivery_dict["by_package"][0], dict)
        assert delivery_dict["by_package"][0]["package_id"] == "pkg_serial"

        # Aggregated totals serialized
        assert isinstance(data["aggregated_totals"], dict)
        assert "impressions" in data["aggregated_totals"]
