"""Canonical test suite for UC-004: Deliver Media Buy Metrics.

This module maps every test obligation from docs/test-obligations/UC-004-deliver-media-buy-metrics.md
to either a real test or a skip stub. It covers:
- Main flow: polling delivery metrics (single/multi buy, identification modes)
- Status filtering (active, completed, paused, all)
- Custom date ranges
- PricingOption lookup correctness (3.6 upgrade -- CRITICAL)
- Serialization and schema compatibility
- Auth/error extensions (*a through *g)
- Webhook delivery contract (BR-RULE-029)
- Circuit breaker behavior

Cross-references:
- test_delivery_behavioral.py: impl-layer behavioral tests (ported here as COVERED)
- test_webhook_delivery_service.py: webhook payload/sequence tests (referenced for WH- obligations)
- test_webhook_delivery.py: webhook retry/backoff tests (referenced for EXT-G obligations)
- test_delivery_metrics.py: GAM adapter-level tests (kept separate)
- test_delivery_simulator.py: simulator service tests (kept separate)
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    ReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.services.webhook_delivery_service import CircuitBreaker, CircuitState

# ---------------------------------------------------------------------------
# Fixtures (shared across all test classes)
# ---------------------------------------------------------------------------

_PATCH_PREFIX = "src.core.tools.media_buy_delivery"


def _make_identity(
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
    testing_context: AdCPTestContext | None = None,
) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id},
        protocol="mcp",
        testing_context=testing_context
        or AdCPTestContext(
            dry_run=False,
            mock_time=None,
            jump_to_event=None,
            test_session_id=None,
        ),
    )


def _make_mock_media_buy(
    media_buy_id: str = "mb_001",
    budget: float = 10000.0,
    currency: str = "USD",
    start_date: date | None = None,
    end_date: date | None = None,
    raw_request: dict | None = None,
    buyer_ref: str | None = None,
    start_time=None,
    end_time=None,
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
) -> MagicMock:
    buy = MagicMock()
    buy.media_buy_id = media_buy_id
    buy.budget = Decimal(str(budget))
    buy.currency = currency
    buy.start_date = start_date or date(2025, 1, 1)
    buy.end_date = end_date or date(2025, 12, 31)
    buy.start_time = start_time
    buy.end_time = end_time
    buy.buyer_ref = buyer_ref
    buy.principal_id = principal_id
    buy.tenant_id = tenant_id
    buy.raw_request = raw_request or {
        "packages": [
            {"package_id": "pkg_001", "product_id": "prod_1"},
        ],
        "buyer_ref": buyer_ref,
    }
    return buy


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


def _standard_patches(
    principal_id: str = "test_principal",
    principal_obj: MagicMock | None = None,
    adapter: MagicMock | None = None,
    target_buys: list | None = None,
    pricing_options: dict | None = None,
):
    if principal_obj is None:
        principal_obj = MagicMock()
        principal_obj.principal_id = principal_id
        principal_obj.platform_mappings = {}

    if adapter is None:
        adapter = MagicMock()

    if target_buys is None:
        target_buys = []

    return {
        "principal_obj": patch(
            f"{_PATCH_PREFIX}.get_principal_object",
            return_value=principal_obj,
        ),
        "adapter": patch(
            f"{_PATCH_PREFIX}.get_adapter",
            return_value=adapter,
        ),
        "tenant": patch(
            "src.core.helpers.context_helpers.ensure_tenant_context",
            return_value={"tenant_id": "test_tenant", "name": "Test"},
        ),
        "target_buys": patch(
            f"{_PATCH_PREFIX}._get_target_media_buys",
            return_value=target_buys,
        ),
        "pricing_options": patch(
            f"{_PATCH_PREFIX}._get_pricing_options",
            return_value=pricing_options or {},
        ),
        "db_session": patch(
            f"{_PATCH_PREFIX}.get_db_session",
        ),
    }


def _run_impl_with_patches(
    req: GetMediaBuyDeliveryRequest,
    identity: ResolvedIdentity | None = None,
    adapter: MagicMock | None = None,
    target_buys: list | None = None,
    pricing_options: dict | None = None,
    principal_obj: MagicMock | None = None,
) -> GetMediaBuyDeliveryResponse:
    """Helper to run _get_media_buy_delivery_impl with standard mocking."""
    if identity is None:
        identity = _make_identity()

    mock_adapter = adapter or MagicMock()
    patches = _standard_patches(
        adapter=mock_adapter,
        target_buys=target_buys or [],
        pricing_options=pricing_options,
        principal_obj=principal_obj,
    )

    mock_inner_session = MagicMock()
    mock_inner_session.scalars.return_value.all.return_value = []

    with (
        patches["principal_obj"],
        patches["adapter"],
        patches["tenant"],
        patches["target_buys"],
        patches["pricing_options"],
        patches["db_session"] as mock_db,
    ):
        mock_db.return_value.__enter__.return_value = mock_inner_session
        return _get_media_buy_delivery_impl(req, identity)


# ===========================================================================
# 1. Main Flow: Single Buy Polling (UC-004-MAIN-01, MAIN-07, MAIN-08, MAIN-09, MAIN-10)
# ===========================================================================


class TestDeliveryPollingSingleBuy:
    """UC-004-MAIN: happy path for a single media buy delivery query."""

    def test_single_buy_returns_complete_response(self):
        """UC-004-MAIN-01: Happy path fetch delivery for single media buy by media_buy_id.

        Verifies: reporting_period, currency, aggregated_totals, media_buy_deliveries[0]
        with totals and by_package, and status.
        """
        buy = _make_mock_media_buy(
            media_buy_id="mb_single",
            budget=10000.0,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            raw_request={
                "packages": [{"package_id": "pkg_a", "product_id": "prod_1"}],
                "buyer_ref": "buyer_1",
            },
            buyer_ref="buyer_1",
        )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_single",
            impressions=8000,
            spend=400.0,
            clicks=80,
            packages=[{"package_id": "pkg_a", "impressions": 8000, "spend": 400.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_single"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_single", buy)],
        )

        # UC-004-MAIN-07: reporting_period matches provided dates
        assert response.reporting_period.start.year == 2025
        assert response.reporting_period.start.month == 1
        assert response.reporting_period.end.month == 6

        # UC-004-MAIN-08: currency present
        assert response.currency == "USD"

        # aggregated_totals
        assert response.aggregated_totals.impressions == 8000.0
        assert response.aggregated_totals.spend == 400.0
        assert response.aggregated_totals.media_buy_count == 1

        # media_buy_deliveries
        assert len(response.media_buy_deliveries) == 1
        delivery = response.media_buy_deliveries[0]
        assert delivery.media_buy_id == "mb_single"
        assert delivery.buyer_ref == "buyer_1"

        # UC-004-MAIN-09: totals
        assert delivery.totals.impressions == 8000
        assert delivery.totals.spend == 400.0

        # by_package
        assert len(delivery.by_package) == 1
        assert delivery.by_package[0].package_id == "pkg_a"

        # UC-004-MAIN-10: status computed correctly (2025-06-30 between start/end)
        assert delivery.status == "active"

        # no errors
        assert response.errors is None


# ===========================================================================
# 2. Main Flow: Multi Buy Aggregation (UC-004-MAIN-03, MAIN-11)
# ===========================================================================


class TestDeliveryPollingMultiBuy:
    """UC-004-MAIN: multiple buys with aggregated totals."""

    def test_two_buys_aggregate_correctly(self):
        """UC-004-MAIN-03, MAIN-11: aggregated_totals sum across multiple buys."""
        buy1 = _make_mock_media_buy(
            media_buy_id="mb_agg_1",
            budget=5000.0,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            raw_request={
                "packages": [{"package_id": "pkg_1a", "product_id": "prod_1"}],
                "buyer_ref": "ref_1",
            },
            buyer_ref="ref_1",
        )
        buy2 = _make_mock_media_buy(
            media_buy_id="mb_agg_2",
            budget=8000.0,
            start_date=date(2025, 3, 1),
            end_date=date(2025, 12, 31),
            raw_request={
                "packages": [{"package_id": "pkg_2a", "product_id": "prod_2"}],
                "buyer_ref": "ref_2",
            },
            buyer_ref="ref_2",
        )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = [
            _make_adapter_response(
                media_buy_id="mb_agg_1",
                impressions=3000,
                spend=150.0,
                clicks=30,
                packages=[{"package_id": "pkg_1a", "impressions": 3000, "spend": 150.0}],
            ),
            _make_adapter_response(
                media_buy_id="mb_agg_2",
                impressions=7000,
                spend=350.0,
                clicks=70,
                packages=[{"package_id": "pkg_2a", "impressions": 7000, "spend": 350.0}],
            ),
        ]

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_agg_1", "mb_agg_2"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_agg_1", buy1), ("mb_agg_2", buy2)],
        )

        # Sum invariants
        assert response.aggregated_totals.impressions == 10000.0
        assert response.aggregated_totals.spend == 500.0
        assert response.aggregated_totals.media_buy_count == 2

        assert len(response.media_buy_deliveries) == 2
        ids = {d.media_buy_id for d in response.media_buy_deliveries}
        assert ids == {"mb_agg_1", "mb_agg_2"}
        assert response.errors is None


# ===========================================================================
# 3. Identification Modes (UC-004-MAIN-02, MAIN-04, MAIN-05, MAIN-14, MAIN-15)
# ===========================================================================


class TestDeliveryIdentificationModes:
    """UC-004 BR-RULE-030: media_buy_ids vs buyer_refs vs both vs neither."""

    def test_media_buy_ids_only(self):
        """UC-004-MAIN-01: media_buy_ids provided, buyer_refs absent."""
        buy = _make_mock_media_buy(media_buy_id="mb_id1")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_id1",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        patches = _standard_patches(adapter=mock_adapter, target_buys=[("mb_id1", buy)])
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_id1"])
        identity = _make_identity()

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, identity)

        assert len(response.media_buy_deliveries) == 1
        mock_target.assert_called_once()
        call_req = mock_target.call_args[0][0]
        assert call_req.media_buy_ids == ["mb_id1"]

    def test_buyer_refs_only(self):
        """UC-004-MAIN-02: buyer_refs provided, media_buy_ids absent."""
        buy = _make_mock_media_buy(media_buy_id="mb_ref1", buyer_ref="buyer_A")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_ref1",
            impressions=200,
            spend=20.0,
            packages=[{"package_id": "pkg_001", "impressions": 200, "spend": 20.0}],
        )

        patches = _standard_patches(adapter=mock_adapter, target_buys=[("mb_ref1", buy)])
        req = GetMediaBuyDeliveryRequest(buyer_refs=["buyer_A"])
        identity = _make_identity()

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, identity)

        assert len(response.media_buy_deliveries) == 1
        call_req = mock_target.call_args[0][0]
        assert call_req.buyer_refs == ["buyer_A"]

    def test_both_provided_media_buy_ids_wins(self):
        """UC-004-MAIN-05: media_buy_ids takes precedence over buyer_refs."""
        buy = _make_mock_media_buy(media_buy_id="mb_priority")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_priority",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        patches = _standard_patches(adapter=mock_adapter, target_buys=[("mb_priority", buy)])
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_priority"],
            buyer_refs=["should_be_ignored"],
        )
        identity = _make_identity()

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, identity)

        call_req = mock_target.call_args[0][0]
        assert call_req.media_buy_ids == ["mb_priority"]
        assert len(response.media_buy_deliveries) == 1

    def test_neither_provided_fetches_all(self):
        """UC-004-MAIN-04: neither identifiers fetches all principal buys."""
        buy = _make_mock_media_buy(media_buy_id="mb_all1")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_all1",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        patches = _standard_patches(adapter=mock_adapter, target_buys=[("mb_all1", buy)])
        req = GetMediaBuyDeliveryRequest()
        identity = _make_identity()

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, identity)

        call_req = mock_target.call_args[0][0]
        assert call_req.media_buy_ids is None
        assert call_req.buyer_refs is None
        assert len(response.media_buy_deliveries) == 1

    def test_partial_ids_returns_only_valid(self):
        """UC-004-MAIN-14: partial resolution returns found buys only."""
        buy = _make_mock_media_buy(media_buy_id="mb_valid")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_valid",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_valid", "mb_nonexistent"])

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_valid", buy)],
        )

        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_valid"
        assert response.errors is None

    def test_all_ids_invalid_returns_empty_no_error(self):
        """UC-004-MAIN-15: zero identifiers resolve returns empty array."""
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_ghost1", "mb_ghost2"])

        response = _run_impl_with_patches(req, target_buys=[])

        assert len(response.media_buy_deliveries) == 0
        assert response.errors is None
        assert response.aggregated_totals.media_buy_count == 0


# ===========================================================================
# 4. Status Filtering (UC-004-FILT-01 through FILT-07)
# ===========================================================================


class TestDeliveryStatusFilter:
    """UC-004-FILT: status filtering via _get_target_media_buys."""

    def test_status_filter_all_returns_all_statuses(self):
        """UC-004-FILT-06: status_filter='all' returns buys of any status."""
        from src.core.tools.media_buy_delivery import _get_target_media_buys

        ref_date = date(2025, 6, 15)
        buy_ready = _make_mock_media_buy(
            media_buy_id="mb_ready",
            start_date=date(2025, 7, 1),
            end_date=date(2025, 12, 31),
        )
        buy_active = _make_mock_media_buy(
            media_buy_id="mb_active",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        buy_completed = _make_mock_media_buy(
            media_buy_id="mb_completed",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 5, 1),
        )

        mock_req = MagicMock()
        mock_req.media_buy_ids = ["mb_ready", "mb_active", "mb_completed"]
        mock_req.buyer_refs = None
        mock_status = MagicMock()
        mock_status.value = "all"
        mock_req.status_filter = mock_status

        mock_session = MagicMock()
        mock_session.scalars.return_value.all.return_value = [buy_ready, buy_active, buy_completed]
        tenant = {"tenant_id": "test_tenant"}

        with patch(f"{_PATCH_PREFIX}.get_db_session") as mock_db:
            mock_db.return_value.__enter__.return_value = mock_session
            result = _get_target_media_buys(mock_req, "test_principal", tenant, ref_date)

        assert len(result) == 3
        returned_ids = {buy_id for buy_id, _ in result}
        assert returned_ids == {"mb_ready", "mb_active", "mb_completed"}

    def test_status_filter_default_is_active(self):
        """UC-004-FILT-05: no status_filter defaults to active."""
        patches = _standard_patches(target_buys=[])
        req = GetMediaBuyDeliveryRequest()
        identity = _make_identity()

        with (
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["db_session"],
        ):
            _get_media_buy_delivery_impl(req, identity)

        call_req = mock_target.call_args[0][0]
        assert call_req.status_filter is None  # None -> impl defaults to ["active"]

    def test_default_filter_only_returns_active_buys(self):
        """UC-004-FILT-01 (partial): default filter returns only active buys."""
        from src.core.tools.media_buy_delivery import _get_target_media_buys

        ref_date = date(2025, 6, 15)
        buy_active = _make_mock_media_buy(
            media_buy_id="mb_active",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        buy_completed = _make_mock_media_buy(
            media_buy_id="mb_done",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 5, 1),
        )

        mock_req = MagicMock()
        mock_req.media_buy_ids = None
        mock_req.buyer_refs = None
        mock_req.status_filter = None

        mock_session = MagicMock()
        mock_session.scalars.return_value.all.return_value = [buy_active, buy_completed]
        tenant = {"tenant_id": "test_tenant"}

        with patch(f"{_PATCH_PREFIX}.get_db_session") as mock_db:
            mock_db.return_value.__enter__.return_value = mock_session
            result = _get_target_media_buys(mock_req, "test_principal", tenant, ref_date)

        assert len(result) == 1
        assert result[0][0] == "mb_active"

    @pytest.mark.skip(reason="STUB: UC-004-FILT-02 -- filter by status completed")
    def test_status_filter_completed(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-FILT-03 -- filter by status paused")
    def test_status_filter_paused(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-FILT-04 -- no media buys match filter returns empty result")
    def test_status_filter_no_match_returns_empty(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-FILT-07 -- valid status enum values accepted by schema")
    def test_valid_status_enum_values_accepted(self):
        pass


# ===========================================================================
# 5. Custom Date Range (UC-004-DATE-01 through DATE-04, MAIN-06)
# ===========================================================================


class TestDeliveryDateRange:
    """UC-004-DATE: custom date range handling."""

    def test_custom_date_range_reflected_in_reporting_period(self):
        """UC-004-DATE-01: both start and end provided."""
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-15",
            end_date="2025-04-15",
        )

        response = _run_impl_with_patches(req, target_buys=[])

        assert response.reporting_period.start == datetime(2025, 3, 15, tzinfo=UTC)
        assert response.reporting_period.end == datetime(2025, 4, 15, tzinfo=UTC)

    def test_no_date_range_defaults_to_last_30_days(self):
        """UC-004-MAIN-06: no dates defaults to last 30 days."""
        req = GetMediaBuyDeliveryRequest()

        response = _run_impl_with_patches(req, target_buys=[])

        now = datetime.now(UTC)
        assert abs((response.reporting_period.end - now).total_seconds()) < 5
        expected_start = now - timedelta(days=30)
        assert abs((response.reporting_period.start - expected_start).total_seconds()) < 5

    @pytest.mark.skip(reason="STUB: UC-004-DATE-02 -- only start_date provided, end defaults to now")
    def test_only_start_date_end_defaults_to_now(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-DATE-03 -- only end_date provided, start defaults to creation date")
    def test_only_end_date_start_defaults_to_creation(self):
        pass

    @pytest.mark.skip(
        reason="STUB: UC-004-DATE-04 -- custom date range overrides default 30-day window (verify explicitly)"
    )
    def test_custom_range_overrides_default(self):
        pass


# ===========================================================================
# 6. PricingOption Lookup Correctness (UC-004-UPG-01, UPG-02) -- CRITICAL
# ===========================================================================


class TestDeliveryPricingOptionLookup:
    """UC-004-UPG: pricing_option_id type safety for 3.6 upgrade.

    CRITICAL: salesagent-mq3n identified that _get_pricing_options compares
    string pricing_option_id from JSON to integer PK column, which always
    silently fails. These tests validate the fix.
    """

    @pytest.mark.skip(
        reason="STUB: UC-004-UPG-01 -- CRITICAL: pricing_option_id lookup must use string field, not integer PK. See salesagent-mq3n"
    )
    def test_pricing_option_lookup_uses_string_field_not_integer_pk(self):
        """_get_pricing_options must resolve pricing_option_id through the string field."""
        pass

    @pytest.mark.skip(
        reason="STUB: UC-004-UPG-02 -- CRITICAL: delivery spend computed correctly when pricing lookup succeeds"
    )
    def test_delivery_spend_correct_with_cpm_pricing(self):
        """CPM pricing: 10,000 impressions at $5.00 CPM = $50.00 spend."""
        pass

    @pytest.mark.skip(reason="STUB: UC-004-UPG-02 -- CPC pricing: 500 clicks at $0.50 = $250.00 spend")
    def test_delivery_spend_correct_with_cpc_pricing(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-UPG-02 -- FLAT_RATE pricing: total rate $5,000")
    def test_delivery_spend_correct_with_flat_rate_pricing(self):
        pass


# ===========================================================================
# 7. 3.6 Upgrade Compatibility (UC-004-UPG-03, UPG-04, UPG-05)
# ===========================================================================


class TestDeliveryUpgradeCompat:
    """UC-004-UPG: 3.6 upgrade schema compatibility."""

    def test_buyer_ref_present_in_delivery_entries(self):
        """UC-004-UPG-03: buyer_ref present in media_buy_deliveries."""
        buy = _make_mock_media_buy(
            media_buy_id="mb_ref",
            buyer_ref="buyer_camp_1",
            raw_request={
                "packages": [{"package_id": "pkg_1", "product_id": "prod_1"}],
                "buyer_ref": "buyer_camp_1",
            },
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_ref",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_1", "impressions": 100, "spend": 10.0}],
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_ref"])

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_ref", buy)],
        )

        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].buyer_ref == "buyer_camp_1"

    @pytest.mark.skip(
        reason="STUB: UC-004-UPG-04 -- GetMediaBuyDeliveryResponse nested serialization with NestedModelSerializerMixin"
    )
    def test_nested_serialization_model_dump(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-UPG-05 -- delivery response preserves ext fields")
    def test_ext_fields_preserved(self):
        pass


# ===========================================================================
# 8. Auth Errors (UC-004-EXT-A1, EXT-A2, EXT-B1)
# ===========================================================================


class TestDeliveryAuthErrors:
    """UC-004-EXT-A/B: authentication and principal errors."""

    def test_missing_principal_id_returns_error(self):
        """UC-004-EXT-A1: no principal_id returns principal_id_missing error."""
        identity = ResolvedIdentity(
            principal_id="",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant"},
            protocol="mcp",
            testing_context=AdCPTestContext(dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None),
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_x"])
        response = _get_media_buy_delivery_impl(req, identity)

        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "principal_id_missing"
        assert response.media_buy_deliveries == []

    def test_principal_not_found_returns_error(self):
        """UC-004-EXT-B1: principal ID not in tenant returns principal_not_found."""
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_x"])
        identity = _make_identity(principal_id="ghost_principal")

        with patch(f"{_PATCH_PREFIX}.get_principal_object", return_value=None):
            response = _get_media_buy_delivery_impl(req, identity)

        assert response.errors is not None
        assert response.errors[0].code == "principal_not_found"
        assert "ghost_principal" in response.errors[0].message
        assert response.media_buy_deliveries == []

    @pytest.mark.skip(reason="STUB: UC-004-EXT-A2 -- system state unchanged on auth failure (read-only op)")
    def test_auth_failure_no_state_change(self):
        pass


# ===========================================================================
# 9. Media Buy Not Found (UC-004-EXT-C1, EXT-C2, EXT-C3)
# ===========================================================================


class TestDeliveryMediaBuyNotFound:
    """UC-004-EXT-C: media buy resolution failures."""

    @pytest.mark.skip(
        reason="STUB: UC-004-EXT-C1 -- media_buy_id not found returns media_buy_not_found error (current impl returns empty, spec says error)"
    )
    def test_media_buy_not_found_returns_error(self):
        pass

    def test_partial_ids_returns_found_only(self):
        """UC-004-EXT-C2: partial failure returns only found buys (BR-RULE-030 INV-5)."""
        buy = _make_mock_media_buy(media_buy_id="mb_found")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_found",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_found", "mb_missing"])

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_found", buy)],
        )

        assert len(response.media_buy_deliveries) == 1
        assert response.errors is None

    @pytest.mark.skip(reason="STUB: UC-004-EXT-C3 -- buyer_ref does not resolve returns media_buy_not_found")
    def test_buyer_ref_not_found_returns_error(self):
        pass


# ===========================================================================
# 10. Ownership Security (UC-004-EXT-D1, EXT-D2, EXT-D3)
# ===========================================================================


class TestDeliveryOwnership:
    """UC-004-EXT-D: ownership mismatch security.

    SECURITY: must return media_buy_not_found (not ownership_mismatch)
    to prevent information leakage about existence of other buyers' data.
    """

    @pytest.mark.skip(
        reason="STUB: UC-004-EXT-D1 -- SECURITY: principal does not own media buy returns media_buy_not_found"
    )
    def test_ownership_mismatch_returns_not_found(self):
        pass

    @pytest.mark.skip(
        reason="STUB: UC-004-EXT-D2 -- SECURITY: error is media_buy_not_found not ownership_mismatch (no info leakage)"
    )
    def test_no_info_leakage_on_ownership_error(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-EXT-D3 -- mixed ownership: some owned, some not")
    def test_mixed_ownership_behavior(self):
        pass


# ===========================================================================
# 11. Invalid Date Range (UC-004-EXT-E1, EXT-E2, EXT-E3)
# ===========================================================================


class TestDeliveryInvalidDateRange:
    """UC-004-EXT-E: invalid date range validation."""

    def test_start_date_equals_end_date_returns_error(self):
        """UC-004-EXT-E1: start_date == end_date returns invalid_date_range."""
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-15",
            end_date="2025-03-15",
        )

        response = _run_impl_with_patches(req)

        assert response.errors is not None
        assert response.errors[0].code == "invalid_date_range"
        assert response.media_buy_deliveries == []

    def test_start_date_after_end_date_returns_error(self):
        """UC-004-EXT-E2: start_date > end_date returns invalid_date_range."""
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-20",
            end_date="2025-03-10",
        )

        response = _run_impl_with_patches(req)

        assert response.errors is not None
        assert response.errors[0].code == "invalid_date_range"
        assert response.media_buy_deliveries == []

    @pytest.mark.skip(reason="STUB: UC-004-EXT-E3 -- state unchanged on date range error (read-only op)")
    def test_date_range_error_no_state_change(self):
        pass


# ===========================================================================
# 12. Adapter Errors (UC-004-EXT-F1, EXT-F2, EXT-F3, EXT-F4)
# ===========================================================================


class TestDeliveryAdapterError:
    """UC-004-EXT-F: adapter failure handling."""

    def test_adapter_exception_returns_adapter_error(self):
        """UC-004-EXT-F1: adapter raises Exception -> adapter_error code."""
        buy = _make_mock_media_buy(media_buy_id="mb_err")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = RuntimeError("GAM API timeout")

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_err"])

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_err", buy)],
        )

        assert response.errors is not None
        assert response.errors[0].code == "adapter_error"
        assert "mb_err" in response.errors[0].message
        assert response.media_buy_deliveries == []
        assert response.aggregated_totals.impressions == 0.0
        assert response.aggregated_totals.spend == 0.0

    def test_adapter_error_preserves_reporting_period(self):
        """UC-004-EXT-F2: adapter error still includes correct reporting_period."""
        buy = _make_mock_media_buy(media_buy_id="mb_err2", currency="EUR")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = ConnectionError("Network down")

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_err2"],
            start_date="2025-03-01",
            end_date="2025-03-31",
        )

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_err2", buy)],
        )

        assert response.reporting_period.start.month == 3
        assert response.reporting_period.end.month == 3
        assert response.errors[0].code == "adapter_error"

    @pytest.mark.skip(reason="STUB: UC-004-EXT-F3 -- adapter failure logged to audit trail (NFR-003)")
    def test_adapter_failure_audit_logged(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-EXT-F4 -- state unchanged on adapter error (verify no DB writes)")
    def test_adapter_error_no_state_change(self):
        pass


# ===========================================================================
# 13. Webhook Happy Path (UC-004-WH-01 through WH-12)
# Covered by: test_webhook_delivery_service.py (sequence, payload, auth)
# ===========================================================================


class TestDeliveryWebhookHappyPath:
    """UC-004-WH: webhook delivery contract (BR-RULE-029).

    Most scenarios are covered by test_webhook_delivery_service.py.
    This class provides stubs for gaps and references for covered obligations.
    """

    # WH-01, WH-02, WH-03: Covered by test_webhook_delivery_service.py::test_adcp_payload_structure
    # WH-04: Covered by test_webhook_delivery_service.py::test_final_notification_type
    # WH-05: Covered by test_webhook_delivery_service.py::test_sequence_number_increments
    # WH-08: Covered by test_webhook_delivery_service.py::test_authentication_headers
    # WH-12: Covered by test_webhook_delivery.py::test_successful_delivery_first_attempt

    @pytest.mark.skip(reason="STUB: UC-004-WH-06 -- next_expected_at computed for non-final deliveries")
    def test_next_expected_at_computed(self):
        pass

    @pytest.mark.skip(
        reason="STUB: UC-004-WH-07 -- webhook payload signed with HMAC-SHA256 (verify X-ADCP-Signature + X-ADCP-Timestamp)"
    )
    def test_hmac_sha256_signature_headers(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-WH-09 -- webhook does NOT include aggregated_totals")
    def test_webhook_excludes_aggregated_totals(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-WH-10 -- webhook filters to requested_metrics only")
    def test_webhook_filters_requested_metrics(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-WH-11 -- only active media buys trigger webhook delivery")
    def test_only_active_trigger_webhook(self):
        pass


# ===========================================================================
# 14. Webhook Retry and Circuit Breaker (UC-004-EXT-G1 through EXT-G7)
# Covered by: test_webhook_delivery.py (retry), test_delivery_behavioral.py (circuit breaker)
# ===========================================================================


class TestDeliveryWebhookRetry:
    """UC-004-EXT-G: webhook failure handling and circuit breaker.

    Retry logic covered by test_webhook_delivery.py.
    Circuit breaker covered by test_delivery_behavioral.py.
    """

    def test_five_failures_opens_circuit_breaker(self):
        """UC-004-EXT-G3: 5 consecutive failures transitions to OPEN state."""
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)

        assert cb.state == CircuitState.CLOSED
        for _ in range(4):
            cb.record_failure()
            assert cb.state == CircuitState.CLOSED

        cb.record_failure()  # 5th
        assert cb.state == CircuitState.OPEN

    def test_open_circuit_rejects_requests(self):
        """UC-004-EXT-G3: OPEN circuit -> can_attempt() returns False."""
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)
        for _ in range(5):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert cb.can_attempt() is False

    def test_open_transitions_to_half_open_after_timeout(self):
        """UC-004-EXT-G4: OPEN state + timeout -> HALF_OPEN."""
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)
        for _ in range(5):
            cb.record_failure()

        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)

        assert cb.can_attempt() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_recovers_after_success_threshold(self):
        """UC-004-EXT-G4: HALF_OPEN + 2 successes -> CLOSED."""
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)
        for _ in range(5):
            cb.record_failure()
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)
        cb.can_attempt()

        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_returns_to_open(self):
        """UC-004-EXT-G4: HALF_OPEN + failure -> back to OPEN."""
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)
        for _ in range(5):
            cb.record_failure()
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)
        cb.can_attempt()

        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    # EXT-G1: Covered by test_webhook_delivery.py::test_retry_on_500_error
    # EXT-G2: Covered by test_webhook_delivery.py::test_successful_delivery_after_retry
    # EXT-G5: Covered by test_webhook_delivery.py::test_no_retry_on_400_error

    @pytest.mark.skip(reason="STUB: UC-004-EXT-G6 -- 401/403 auth rejection marks webhook as failed, no retry")
    def test_auth_rejection_marks_webhook_failed(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-EXT-G7 -- webhook failures produce no synchronous error to buyer")
    def test_webhook_failures_no_synchronous_error(self):
        pass


# ===========================================================================
# 15. Protocol and Schema (UC-004-MAIN-12, MAIN-13, MAIN-16, MAIN-17)
# ===========================================================================


class TestDeliveryProtocol:
    """UC-004-MAIN: protocol envelope and schema completeness."""

    @pytest.mark.skip(reason="STUB: UC-004-MAIN-12 -- response wrapped in protocol envelope with status=completed")
    def test_protocol_envelope_status_completed(self):
        pass

    @pytest.mark.skip(reason="STUB: UC-004-MAIN-13 -- MCP ToolResult contains both content and structured_content")
    def test_mcp_toolresult_content_and_structured(self):
        pass

    @pytest.mark.skip(
        reason="STUB: UC-004-MAIN-16 -- delivery metrics include all standard fields (impressions, spend, clicks, ctr, video, conversions, viewability)"
    )
    def test_delivery_metrics_all_standard_fields(self):
        pass

    @pytest.mark.skip(
        reason="STUB: UC-004-MAIN-17 -- unpopulated fields (daily_breakdown, effective_rate, viewability, creative_breakdowns) handled gracefully"
    )
    def test_unpopulated_fields_handled_gracefully(self):
        pass
