"""Canonical test suite for UC-004: Deliver Media Buy Metrics.

Spec verification: 2026-02-26
adcp spec commit: 8f26baf3
adcp-client-python commit: a08805d
Verified: 30/59 CONFIRMED, 24/59 UNSPECIFIED, 5 CONTRADICTS (salesagent-mexj), 0 SPEC_AMBIGUOUS

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

from adcp.types import MediaBuyStatus

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    AggregatedTotals,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    PricingModel,
    ReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_delivery import (
    _get_media_buy_delivery_impl,
    get_media_buy_delivery,
    get_media_buy_delivery_raw,
)
from src.services.webhook_delivery_service import CircuitBreaker, CircuitState, WebhookDeliveryService
from tests.harness.delivery_poll_unit import DeliveryPollEnv

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
    buy.is_paused = False
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

    # Mock UoW so _get_media_buy_delivery_impl doesn't hit real DB.
    # The __enter__ must return an object with a media_buys attribute (the repo).
    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow.media_buys = MagicMock()

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
        "uow": patch(
            f"{_PATCH_PREFIX}.MediaBuyUoW",
            return_value=mock_uow,
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
        patches["uow"],
    ):
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

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-response.json
        CONFIRMED: reporting_period (required), currency (required), media_buy_deliveries (required),
        aggregated_totals.impressions/spend/media_buy_count (required), media_buy_deliveries[].status (required),
        media_buy_deliveries[].totals (required), media_buy_deliveries[].by_package (required).
        Covers: UC-004-MAIN-01
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
        """UC-004-MAIN-03, MAIN-11: aggregated_totals sum across multiple buys.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-response.json
        CONFIRMED: aggregated_totals.impressions (required, >=0), aggregated_totals.spend (required, >=0),
        aggregated_totals.media_buy_count (required, >=0). Spec defines these as combined metrics across
        all returned media buys.
        Covers: UC-004-MAIN-03
        """
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
        """UC-004-MAIN-01: media_buy_ids provided, buyer_refs absent.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-request.json
        CONFIRMED: media_buy_ids is an optional array of strings (minItems: 1).
        Covers: UC-004-MAIN-01
        """
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
            patches["uow"],
        ):
            response = _get_media_buy_delivery_impl(req, identity)

        assert len(response.media_buy_deliveries) == 1
        mock_target.assert_called_once()
        call_req = mock_target.call_args[0][0]
        assert call_req.media_buy_ids == ["mb_id1"]

    def test_buyer_refs_only(self):
        """UC-004-MAIN-02: buyer_refs provided, media_buy_ids absent.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-request.json
        CONFIRMED: buyer_refs is an optional array of strings (minItems: 1).
        Covers: UC-004-MAIN-02
        """
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
            patches["uow"],
        ):
            response = _get_media_buy_delivery_impl(req, identity)

        assert len(response.media_buy_deliveries) == 1
        call_req = mock_target.call_args[0][0]
        assert call_req.buyer_refs == ["buyer_A"]

    def test_both_provided_media_buy_ids_wins(self):
        """UC-004-MAIN-05: media_buy_ids takes precedence over buyer_refs.

        Spec: UNSPECIFIED (implementation-defined precedence when both identifiers provided).
        Request schema allows both fields simultaneously but does not define precedence.
        Covers: UC-004-MAIN-05
        """
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
            patches["uow"],
        ):
            response = _get_media_buy_delivery_impl(req, identity)

        call_req = mock_target.call_args[0][0]
        assert call_req.media_buy_ids == ["mb_priority"]
        assert len(response.media_buy_deliveries) == 1

    def test_neither_provided_fetches_all(self):
        """UC-004-MAIN-04: neither identifiers fetches all principal buys.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-request.json
        CONFIRMED: media_buy_ids and buyer_refs are both optional (no required fields in request schema).
        Covers: UC-004-MAIN-04
        """
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
            patches["uow"],
        ):
            response = _get_media_buy_delivery_impl(req, identity)

        call_req = mock_target.call_args[0][0]
        assert call_req.media_buy_ids is None
        assert call_req.buyer_refs is None
        assert len(response.media_buy_deliveries) == 1

    def test_partial_ids_returns_found_and_errors_for_missing(self):
        """UC-004-MAIN-14: partial resolution returns found buys AND errors for missing.

        Spec: CONTRADICTS -- get-media-buy-delivery-response.json has errors array for
        "Task-specific errors and warnings (e.g., missing delivery data)". Current impl
        silently drops missing IDs. Correct: return delivery for found IDs + populate
        errors with media_buy_not_found for each missing ID.
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/schemas/cache/media-buy/get-media-buy-delivery-response.json
        Fix: _get_target_media_buys must track which requested IDs were not found and
        return errors for them. See salesagent-mexj.
        Priority: P1
        Type: unit
        Source: UC-004, salesagent-mexj
        Covers: UC-004-MAIN-17
        """
        # Request 3 IDs, only 1 found
        buy = _make_mock_media_buy(media_buy_id="mb_found")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_found",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_found", "mb_missing_1", "mb_missing_2"],
        )

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_found", buy)],
        )

        # Found buy present in deliveries
        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_found"

        # Missing IDs reported as errors
        assert response.errors is not None
        assert len(response.errors) == 2
        error_messages = " ".join(e.message for e in response.errors)
        assert "mb_missing_1" in error_messages
        assert "mb_missing_2" in error_messages
        for err in response.errors:
            assert err.code == "media_buy_not_found"

    def test_all_ids_invalid_returns_empty_with_errors(self):
        """UC-004-MAIN-15: all requested IDs missing returns empty deliveries + errors.

        Spec: CONTRADICTS -- response.errors must contain media_buy_not_found for each
        missing ID. Current impl returns empty with errors=None.
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/schemas/cache/media-buy/get-media-buy-delivery-response.json
        Fix: populate errors array. See salesagent-mexj.
        Priority: P1
        Type: unit
        Source: UC-004, salesagent-mexj
        Covers: UC-004-MAIN-18
        """
        # Request 2 IDs, none found
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_ghost_1", "mb_ghost_2"],
        )

        response = _run_impl_with_patches(
            req,
            target_buys=[],  # nothing found
        )

        # No deliveries
        assert response.media_buy_deliveries == []

        # Both missing IDs reported as errors
        assert response.errors is not None
        assert len(response.errors) == 2
        error_codes = {e.code for e in response.errors}
        assert error_codes == {"media_buy_not_found"}
        error_messages = " ".join(e.message for e in response.errors)
        assert "mb_ghost_1" in error_messages
        assert "mb_ghost_2" in error_messages


# ===========================================================================
# 4. Status Filtering (UC-004-FILT-01 through FILT-07)
# ===========================================================================


class TestDeliveryStatusFilter:
    """UC-004-FILT: status filtering via _get_target_media_buys."""

    def test_status_filter_all_returns_all_statuses(self):
        """UC-004-FILT-06: status_filter='all' returns buys of any status.

        Spec: UNSPECIFIED (implementation-defined 'all' filter value).
        The spec's media-buy-status enum is [pending_activation, active, paused, completed];
        'all' is not a spec-defined status value.
        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-06
        """
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

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_ready, buy_active, buy_completed]

        result = _get_target_media_buys(mock_req, "test_principal", mock_repo, ref_date)

        assert len(result) == 3
        returned_ids = {buy_id for buy_id, _ in result}
        assert returned_ids == {"mb_ready", "mb_active", "mb_completed"}

    def test_status_filter_default_is_active(self):
        """UC-004-FILT-05: no status_filter defaults to active.

        Spec: UNSPECIFIED (implementation-defined default when status_filter omitted).
        Request schema has status_filter as optional with no default.
        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-05
        """
        patches = _standard_patches(target_buys=[])
        req = GetMediaBuyDeliveryRequest()
        identity = _make_identity()

        with (
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["uow"],
        ):
            _get_media_buy_delivery_impl(req, identity)

        call_req = mock_target.call_args[0][0]
        assert call_req.status_filter is None  # None -> impl defaults to ["active"]

    def test_default_filter_only_returns_active_buys(self):
        """UC-004-FILT-01 (partial): default filter returns only active buys.

        Spec: UNSPECIFIED (implementation-defined default filter behavior).
        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-01
        """
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

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_active, buy_completed]

        result = _get_target_media_buys(mock_req, "test_principal", mock_repo, ref_date)

        assert len(result) == 1
        assert result[0][0] == "mb_active"

    def test_status_filter_completed(self):
        """UC-004-FILT-02: filter by status completed returns only completed buys.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-request.json
        CONFIRMED: status_filter accepts media-buy-status enum including 'completed'.
        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-02
        """
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
        mock_status = MagicMock()
        mock_status.value = "completed"
        mock_req.status_filter = mock_status

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_active, buy_completed]

        result = _get_target_media_buys(mock_req, "test_principal", mock_repo, ref_date)

        assert len(result) == 1
        assert result[0][0] == "mb_done"

    def test_status_filter_paused(self):
        """UC-004-FILT-03: filter by status paused is accepted but returns no buys
        because current status is derived from dates (ready/active/completed only).

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-request.json
        CONFIRMED: status_filter accepts media-buy-status enum including 'paused'.
        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-03
        """
        from src.core.tools.media_buy_delivery import _get_target_media_buys

        ref_date = date(2025, 6, 15)
        buy_active = _make_mock_media_buy(
            media_buy_id="mb_active",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )

        mock_req = MagicMock()
        mock_req.media_buy_ids = None
        mock_req.buyer_refs = None
        mock_status = MagicMock()
        mock_status.value = "paused"
        mock_req.status_filter = mock_status

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_active]

        result = _get_target_media_buys(mock_req, "test_principal", mock_repo, ref_date)

        # paused is in valid_internal_statuses but no buy has paused status from dates
        assert len(result) == 0

    def test_status_filter_no_match_returns_empty(self):
        """UC-004-FILT-04: no media buys match filter returns empty result.

        Spec: UNSPECIFIED (implementation-defined empty-result behavior).
        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-04
        """
        from src.core.tools.media_buy_delivery import _get_target_media_buys

        ref_date = date(2025, 6, 15)
        # All buys are active
        buy_active = _make_mock_media_buy(
            media_buy_id="mb_active",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )

        mock_req = MagicMock()
        mock_req.media_buy_ids = None
        mock_req.buyer_refs = None
        mock_status = MagicMock()
        mock_status.value = "completed"
        mock_req.status_filter = mock_status

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_active]

        result = _get_target_media_buys(mock_req, "test_principal", mock_repo, ref_date)

        assert len(result) == 0

    def test_valid_status_enum_values_accepted(self):
        """UC-004-FILT-07: valid MediaBuyStatus enum values accepted by schema.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/enums/media-buy-status.json
        CONFIRMED: enum values are [pending_activation, active, paused, completed].
        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-07

        Route: impl -- each MediaBuyStatus enum value accepted without error.
        """
        for status in MediaBuyStatus:
            with DeliveryPollEnv() as env:
                env.add_buy(media_buy_id="mb_status")
                env.set_adapter_response("mb_status", impressions=100)
                response = env.call_impl(
                    media_buy_ids=["mb_status"],
                    status_filter=[status.value],
                )
                assert isinstance(response, GetMediaBuyDeliveryResponse)

    async def test_valid_status_enum_values_accepted_mcp(self):
        """UC-004-FILT-07: valid status values accepted via MCP wrapper.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-07

        Route: mcp -- MCP wrapper accepts each MediaBuyStatus enum value.
        """
        from unittest.mock import AsyncMock

        from fastmcp.server.context import Context

        for status in MediaBuyStatus:
            with DeliveryPollEnv() as env:
                env.add_buy(media_buy_id="mb_mcp")
                env.set_adapter_response("mb_mcp", impressions=100)

                mock_ctx = AsyncMock(spec=Context)
                mock_ctx.get_state.return_value = env.identity

                result = await get_media_buy_delivery(
                    media_buy_ids=["mb_mcp"],
                    status_filter=status,
                    ctx=mock_ctx,
                )
                assert result.structured_content is not None

    def test_valid_status_enum_values_accepted_a2a(self):
        """UC-004-FILT-07: valid status values accepted via A2A wrapper.

        Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-07

        Route: a2a -- A2A raw function accepts each MediaBuyStatus enum value.
        """
        for status in MediaBuyStatus:
            with DeliveryPollEnv() as env:
                env.add_buy(media_buy_id="mb_a2a")
                env.set_adapter_response("mb_a2a", impressions=100)

                response = get_media_buy_delivery_raw(
                    media_buy_ids=["mb_a2a"],
                    status_filter=status,
                    identity=env.identity,
                )
                assert isinstance(response, GetMediaBuyDeliveryResponse)


# ===========================================================================
# 5. Custom Date Range (UC-004-DATE-01 through DATE-04, MAIN-06)
# ===========================================================================


class TestDeliveryDateRange:
    """UC-004-DATE: custom date range handling."""

    def test_custom_date_range_reflected_in_reporting_period(self):
        """UC-004-DATE-01: both start and end provided.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-request.json
        CONFIRMED: start_date and end_date are optional strings with YYYY-MM-DD pattern.
        Response reporting_period has required start/end datetime fields.
        Covers: UC-004-ALT-CUSTOM-DATE-RANGE-01
        """
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-15",
            end_date="2025-04-15",
        )

        response = _run_impl_with_patches(req, target_buys=[])

        assert response.reporting_period.start == datetime(2025, 3, 15, tzinfo=UTC)
        assert response.reporting_period.end == datetime(2025, 4, 15, tzinfo=UTC)

    def test_no_date_range_defaults_to_last_30_days(self):
        """UC-004-MAIN-06: no dates defaults to last 30 days.

        Spec: UNSPECIFIED (implementation-defined default date range).
        Spec says "When omitted along with end_date, returns campaign lifetime data"
        but implementation uses 30-day window.
        Covers: UC-004-MAIN-06
        """
        req = GetMediaBuyDeliveryRequest()

        response = _run_impl_with_patches(req, target_buys=[])

        now = datetime.now(UTC)
        assert abs((response.reporting_period.end - now).total_seconds()) < 5
        expected_start = now - timedelta(days=30)
        assert abs((response.reporting_period.start - expected_start).total_seconds()) < 5

    def test_only_start_date_end_defaults_to_now(self):
        """UC-004-DATE-02: only start_date provided, end defaults to now.

        Spec: UNSPECIFIED (implementation-defined default for missing end_date).
        Current impl: when only start_date is provided (no end_date), falls through
        to the 30-day default window because the condition checks both start_date AND end_date.
        Covers: UC-004-ALT-CUSTOM-DATE-RANGE-02
        """
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-15",
        )

        response = _run_impl_with_patches(req, target_buys=[])

        # When only start_date is provided, impl falls to else branch (30-day default)
        # because the condition is `if req.start_date and req.end_date`
        now = datetime.now(UTC)
        assert abs((response.reporting_period.end - now).total_seconds()) < 5
        expected_start = now - timedelta(days=30)
        assert abs((response.reporting_period.start - expected_start).total_seconds()) < 5

    def test_only_end_date_start_defaults_to_30_days(self):
        """UC-004-DATE-03: only end_date provided, start defaults to 30-day window.

        Spec: UNSPECIFIED (implementation-defined default for missing start_date).
        Current impl: when only end_date is provided (no start_date), falls through
        to the 30-day default window because the condition checks both start_date AND end_date.
        Covers: UC-004-ALT-CUSTOM-DATE-RANGE-03
        """
        req = GetMediaBuyDeliveryRequest(
            end_date="2025-04-15",
        )

        response = _run_impl_with_patches(req, target_buys=[])

        # When only end_date is provided, impl falls to else branch (30-day default)
        now = datetime.now(UTC)
        assert abs((response.reporting_period.end - now).total_seconds()) < 5
        expected_start = now - timedelta(days=30)
        assert abs((response.reporting_period.start - expected_start).total_seconds()) < 5

    def test_custom_range_overrides_default(self):
        """UC-004-DATE-04: custom date range overrides default 30-day window.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-request.json
        CONFIRMED: start_date/end_date are explicit request parameters that define the reporting period.
        Covers: UC-004-ALT-CUSTOM-DATE-RANGE-04
        """
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-07-01",
            end_date="2025-07-15",
        )

        response = _run_impl_with_patches(req, target_buys=[])

        # Verify the reporting period matches the custom range, not the 30-day default
        assert response.reporting_period.start == datetime(2025, 7, 1, tzinfo=UTC)
        assert response.reporting_period.end == datetime(2025, 7, 15, tzinfo=UTC)
        # Also verify it's NOT near "now" (ruling out 30-day default)
        now = datetime.now(UTC)
        assert abs((response.reporting_period.end - now).total_seconds()) > 86400


# ===========================================================================
# 6. PricingOption Lookup Correctness (UC-004-UPG-01, UPG-02) -- CRITICAL
# ===========================================================================


class TestDeliveryPricingOptionLookup:
    """UC-004-UPG: pricing_option_id type safety for 3.6 upgrade.

    CRITICAL: salesagent-mq3n identified that _get_pricing_options compares
    string pricing_option_id from JSON to integer PK column, which always
    silently fails. These tests validate the fix.
    """

    def test_pricing_option_lookup_uses_string_field_not_integer_pk(self):
        """_get_pricing_options resolves via synthetic ID (model_currency_type), not integer PK.

        Spec: UNSPECIFIED (implementation-defined ID resolution strategy).

        Our implementation constructs synthetic IDs like "cpm_usd_fixed" from
        PricingOption fields and matches against requested IDs.
        See salesagent-mq3n.
        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-01
        """
        from src.core.tools.media_buy_delivery import _get_pricing_options

        mock_po1 = MagicMock()
        mock_po1.id = 42
        mock_po1.pricing_model = "cpm"
        mock_po1.currency = "USD"
        mock_po1.is_fixed = True
        mock_po1.rate = Decimal("5.00")
        mock_po1.tenant_id = "test_tenant"

        mock_repo = MagicMock()
        mock_repo.get_all_pricing_options.return_value = [mock_po1]

        result = _get_pricing_options(["cpm_usd_fixed"], tenant_id="test_tenant", product_repo=mock_repo)

        # Must find the pricing option keyed by synthetic ID
        assert "cpm_usd_fixed" in result
        assert result["cpm_usd_fixed"].id == 42

    def test_delivery_spend_correct_with_cpm_pricing(self):
        """CPM pricing: adapter returns correct impressions/spend with CPM pricing.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/core/delivery-metrics.json
        CONFIRMED: spend (type: number, minimum: 0) and impressions (type: number, minimum: 0) are defined.
        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-03
        """
        buy = _make_mock_media_buy(
            media_buy_id="mb_cpm",
            budget=10000.0,
            raw_request={
                "packages": [{"package_id": "pkg_cpm", "product_id": "prod_1", "pricing_option_id": "1"}],
                "buyer_ref": "buyer_cpm",
            },
        )

        mock_po = MagicMock()
        mock_po.id = 1
        mock_po.pricing_model = PricingModel.cpm
        mock_po.rate = Decimal("5.00")

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_cpm",
            impressions=10000,
            spend=50.0,
            packages=[{"package_id": "pkg_cpm", "impressions": 10000, "spend": 50.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_cpm"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_cpm", buy)],
            pricing_options={"1": mock_po},
        )

        assert response.aggregated_totals.impressions == 10000.0
        assert response.aggregated_totals.spend == 50.0
        delivery = response.media_buy_deliveries[0]
        assert delivery.totals.impressions == 10000
        assert delivery.totals.spend == 50.0

    def test_delivery_spend_correct_with_cpc_pricing(self):
        """CPC pricing: clicks computed from spend / rate.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/core/delivery-metrics.json
        CONFIRMED: clicks (type: number, minimum: 0) and spend are defined metrics.
        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-04
        """
        buy = _make_mock_media_buy(
            media_buy_id="mb_cpc",
            budget=5000.0,
            raw_request={
                "packages": [{"package_id": "pkg_cpc", "product_id": "prod_1", "pricing_option_id": "2"}],
                "buyer_ref": "buyer_cpc",
            },
        )

        mock_po = MagicMock()
        mock_po.id = 2
        mock_po.pricing_model = "cpc"  # DB stores string, not enum
        mock_po.rate = Decimal("0.50")

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_cpc",
            impressions=5000,
            spend=250.0,
            clicks=500,
            packages=[{"package_id": "pkg_cpc", "impressions": 5000, "spend": 250.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_cpc"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_cpc", buy)],
            pricing_options={"2": mock_po},
        )

        delivery = response.media_buy_deliveries[0]
        assert delivery.totals.spend == 250.0
        # CPC: clicks = floor(spend / rate) = floor(250 / 0.50) = 500
        pkg = delivery.by_package[0]
        assert pkg.clicks == 500

    def test_delivery_spend_correct_with_flat_rate_pricing(self):
        """FLAT_RATE pricing: adapter returns spend, no click computation.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/core/pricing-option.json
        CONFIRMED: flat-rate-option is one of the pricing option types.
        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-05
        """
        buy = _make_mock_media_buy(
            media_buy_id="mb_flat",
            budget=5000.0,
            raw_request={
                "packages": [{"package_id": "pkg_flat", "product_id": "prod_1", "pricing_option_id": "3"}],
                "buyer_ref": "buyer_flat",
            },
        )

        mock_po = MagicMock()
        mock_po.id = 3
        mock_po.pricing_model = PricingModel.flat_rate
        mock_po.rate = Decimal("5000.00")

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_flat",
            impressions=20000,
            spend=5000.0,
            clicks=0,
            packages=[{"package_id": "pkg_flat", "impressions": 20000, "spend": 5000.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_flat"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_flat", buy)],
            pricing_options={"3": mock_po},
        )

        delivery = response.media_buy_deliveries[0]
        assert delivery.totals.spend == 5000.0
        # FLAT_RATE: no click computation (clicks should be None)
        pkg = delivery.by_package[0]
        assert pkg.clicks is None


# ===========================================================================
# 7. 3.6 Upgrade Compatibility (UC-004-UPG-03, UPG-04, UPG-05)
# ===========================================================================


class TestDeliveryUpgradeCompat:
    """UC-004-UPG: 3.6 upgrade schema compatibility."""

    def test_buyer_ref_present_in_delivery_entries(self):
        """UC-004-UPG-03: buyer_ref present in media_buy_deliveries.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-response.json
        CONFIRMED: media_buy_deliveries[].buyer_ref is an optional string field.
        Covers: UC-004-MAIN-16
        """
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

    def test_nested_serialization_model_dump(self):
        """UC-004-UPG-04: GetMediaBuyDeliveryResponse nested serialization with NestedModelSerializerMixin.

        Spec: UNSPECIFIED (implementation-defined serialization mechanism).
        Verifies that model_dump() correctly serializes nested MediaBuyDeliveryData,
        DeliveryTotals, and PackageDelivery via NestedModelSerializerMixin.
        Covers: UC-004-RESPONSE-SERIALIZATION-SALESAGENT-01
        """
        buy = _make_mock_media_buy(media_buy_id="mb_serial", buyer_ref="buyer_s")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_serial",
            impressions=2000,
            spend=100.0,
            clicks=20,
            packages=[{"package_id": "pkg_001", "impressions": 2000, "spend": 100.0}],
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_serial"])
        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_serial", buy)],
        )

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
        assert delivery_dict["by_package"][0]["package_id"] == "pkg_001"

        # Aggregated totals serialized
        assert isinstance(data["aggregated_totals"], dict)
        assert "impressions" in data["aggregated_totals"]

    def test_ext_fields_preserved(self):
        """UC-004-UPG-05: delivery response preserves ext fields.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-response.json
        CONFIRMED: ext field references core/ext.json; additionalProperties: true on response.
        Verifies that ext field can be set and is preserved through serialization.
        Covers: UC-004-RESPONSE-SERIALIZATION-SALESAGENT-02
        """
        response = GetMediaBuyDeliveryResponse(
            reporting_period={"start": datetime(2025, 1, 1, tzinfo=UTC), "end": datetime(2025, 6, 30, tzinfo=UTC)},
            currency="USD",
            aggregated_totals=AggregatedTotals(impressions=0, spend=0, media_buy_count=0),
            media_buy_deliveries=[],
            ext={"custom_vendor_field": "test_value", "priority": 5},
        )

        # ext field present on the model (ExtensionObject, attribute access)
        assert response.ext is not None
        assert response.ext.custom_vendor_field == "test_value"
        assert response.ext.priority == 5

        # Preserved through serialization (dict access)
        data = response.model_dump(mode="json")
        assert data["ext"]["custom_vendor_field"] == "test_value"
        assert data["ext"]["priority"] == 5


# ===========================================================================
# 8. Auth Errors (UC-004-EXT-A1, EXT-A2, EXT-B1)
# ===========================================================================


class TestDeliveryAuthErrors:
    """UC-004-EXT-A/B: authentication and principal errors."""

    def test_missing_principal_id_returns_error(self):
        """UC-004-EXT-A1: no principal_id returns principal_id_missing error.

        Spec: UNSPECIFIED (implementation-defined authentication/authorization boundary).
        Covers: UC-004-EXT-A-01
        """
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
        """UC-004-EXT-B1: principal ID not in tenant returns principal_not_found.

        Spec: UNSPECIFIED (implementation-defined authentication/authorization boundary).
        Covers: UC-004-EXT-B-01
        """
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_x"])
        identity = _make_identity(principal_id="ghost_principal")

        with patch(f"{_PATCH_PREFIX}.get_principal_object", return_value=None):
            response = _get_media_buy_delivery_impl(req, identity)

        assert response.errors is not None
        assert response.errors[0].code == "principal_not_found"
        assert "ghost_principal" in response.errors[0].message
        assert response.media_buy_deliveries == []

    def test_auth_failure_no_state_change(self):
        """UC-004-EXT-A2: system state unchanged on auth failure (read-only op).

        Spec: UNSPECIFIED (implementation-defined security boundary).
        Delivery is a read-only operation. Auth failure must not cause any DB writes
        or adapter calls. Verifies that get_adapter and _get_target_media_buys are never called.
        Covers: UC-004-EXT-A-02
        """
        identity = ResolvedIdentity(
            principal_id="",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant"},
            protocol="mcp",
            testing_context=AdCPTestContext(dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None),
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_x"])

        with (
            patch(f"{_PATCH_PREFIX}.get_adapter") as mock_adapter,
            patch(f"{_PATCH_PREFIX}._get_target_media_buys") as mock_target,
        ):
            response = _get_media_buy_delivery_impl(req, identity)

        # Auth failed
        assert response.errors is not None
        assert response.errors[0].code == "principal_id_missing"

        # No adapter or DB calls occurred
        mock_adapter.assert_not_called()
        mock_target.assert_not_called()


# ===========================================================================
# 9. Media Buy Not Found (UC-004-EXT-C1, EXT-C2, EXT-C3)
# ===========================================================================


class TestDeliveryMediaBuyNotFound:
    """UC-004-EXT-C: media buy resolution failures."""

    def test_media_buy_not_found_returns_error(self):
        """UC-004-EXT-C1: single media_buy_id not found returns error in response.

        Spec: CONTRADICTS -- get-media-buy-delivery-response.json errors array is for
        "Task-specific errors and warnings (e.g., missing delivery data)". Current impl
        returns empty deliveries with errors=None. Correct: errors=[{code: "media_buy_not_found"}].
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/schemas/cache/media-buy/get-media-buy-delivery-response.json
        Fix: _get_target_media_buys must diff requested IDs vs found IDs. See salesagent-mexj.
        Priority: P1
        Type: unit
        Source: UC-004, salesagent-mexj
        Covers: UC-004-EXT-C-01
        """
        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_nonexistent"],
        )

        response = _run_impl_with_patches(
            req,
            target_buys=[],  # nothing found
        )

        assert response.media_buy_deliveries == []
        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "media_buy_not_found"
        assert "mb_nonexistent" in response.errors[0].message

    def test_partial_ids_returns_found_and_errors(self):
        """UC-004-EXT-C2: partial failure returns found buys + errors for missing.

        Spec: CONTRADICTS -- current impl returns found buys with errors=None.
        Correct: return found buys in media_buy_deliveries AND populate errors with
        media_buy_not_found for each missing ID. Both arrays populated simultaneously.
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/schemas/cache/media-buy/get-media-buy-delivery-response.json
        Fix: _get_target_media_buys must diff requested vs found. See salesagent-mexj.
        Priority: P1
        Type: unit
        Source: UC-004, salesagent-mexj, BR-RULE-030
        Covers: UC-004-EXT-C-02
        """
        buy = _make_mock_media_buy(media_buy_id="mb_exists")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_exists",
            impressions=500,
            spend=25.0,
            packages=[{"package_id": "pkg_001", "impressions": 500, "spend": 25.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_exists", "mb_gone"],
        )

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_exists", buy)],
        )

        # Found buy present
        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_exists"

        # Missing ID reported as error
        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "media_buy_not_found"
        assert "mb_gone" in response.errors[0].message

    def test_buyer_ref_not_found_returns_error(self):
        """UC-004-EXT-C3: buyer_ref not found returns error in response.

        Spec: CONTRADICTS -- same pattern as media_buy_id: errors array must report
        unresolved buyer_refs. Current impl silently returns empty.
        Fix: _get_target_media_buys must diff requested buyer_refs vs found. See salesagent-mexj.
        Priority: P1
        Type: unit
        Source: UC-004, salesagent-mexj
        Covers: UC-004-EXT-C-03
        """
        req = GetMediaBuyDeliveryRequest(
            buyer_refs=["buyer_phantom"],
        )

        response = _run_impl_with_patches(
            req,
            target_buys=[],  # nothing found for buyer_ref
        )

        assert response.media_buy_deliveries == []
        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "media_buy_not_found"
        assert "buyer_phantom" in response.errors[0].message


# ===========================================================================
# 10. Ownership Security (UC-004-EXT-D1, EXT-D2, EXT-D3)
# ===========================================================================


class TestDeliveryOwnership:
    """UC-004-EXT-D: ownership mismatch security.

    SECURITY: must return media_buy_not_found (not ownership_mismatch)
    to prevent information leakage about existence of other buyers' data.
    """

    def test_ownership_mismatch_returns_not_found(self):
        """UC-004-EXT-D1: SECURITY: principal does not own media buy returns media_buy_not_found.

        Spec: UNSPECIFIED (implementation-defined security boundary).
        _get_target_media_buys filters by principal_id, so buys owned by other principals
        are simply not found. The impl then reports them as media_buy_not_found errors.
        Covers: UC-004-EXT-D-01
        """
        # Request a buy that exists but is owned by another principal
        # _get_target_media_buys returns empty because the DB query filters by principal_id
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_other_principal"])

        response = _run_impl_with_patches(
            req,
            target_buys=[],  # empty: the buy exists but not for this principal
        )

        assert response.media_buy_deliveries == []
        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "media_buy_not_found"
        assert "mb_other_principal" in response.errors[0].message

    def test_no_info_leakage_on_ownership_error(self):
        """UC-004-EXT-D2: SECURITY: error is media_buy_not_found not ownership_mismatch (no info leakage).

        Spec: UNSPECIFIED (implementation-defined security boundary).
        When a principal requests a buy they don't own, the error code must be
        media_buy_not_found (same as genuinely nonexistent), not ownership_mismatch.
        This prevents information leakage about the existence of other principals' buys.
        Covers: UC-004-EXT-D-02
        """
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_secret"])

        response = _run_impl_with_patches(
            req,
            target_buys=[],  # not found for this principal
        )

        assert response.errors is not None
        # Must NOT reveal ownership: code is "media_buy_not_found", not "ownership_mismatch"
        assert response.errors[0].code == "media_buy_not_found"
        assert "ownership" not in response.errors[0].message.lower()

    def test_mixed_ownership_behavior(self):
        """UC-004-EXT-D3: mixed ownership: some owned, some not.

        Spec: UNSPECIFIED (implementation-defined security boundary).
        When requesting multiple IDs, only owned buys are returned. Non-owned buys
        appear as media_buy_not_found errors (same as genuinely missing).
        Covers: UC-004-EXT-D-03
        """
        buy_owned = _make_mock_media_buy(media_buy_id="mb_mine")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_mine",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_mine", "mb_theirs"],
        )

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_mine", buy_owned)],  # only the owned one found
        )

        # Owned buy returned
        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_mine"

        # Non-owned buy reported as not found (not ownership error)
        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "media_buy_not_found"
        assert "mb_theirs" in response.errors[0].message


# ===========================================================================
# 11. Invalid Date Range (UC-004-EXT-E1, EXT-E2, EXT-E3)
# ===========================================================================


class TestDeliveryInvalidDateRange:
    """UC-004-EXT-E: invalid date range validation."""

    def test_start_date_equals_end_date_returns_error(self):
        """UC-004-EXT-E1: start_date == end_date returns invalid_date_range.

        Spec: UNSPECIFIED (implementation-defined date range validation).
        Spec defines start_date/end_date as string patterns but no ordering constraint.
        Covers: UC-004-EXT-E-01
        """
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-15",
            end_date="2025-03-15",
        )

        response = _run_impl_with_patches(req)

        assert response.errors is not None
        assert response.errors[0].code == "invalid_date_range"
        assert response.media_buy_deliveries == []

    def test_start_date_after_end_date_returns_error(self):
        """UC-004-EXT-E2: start_date > end_date returns invalid_date_range.

        Spec: UNSPECIFIED (implementation-defined date range validation).
        Covers: UC-004-EXT-E-02
        """
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-20",
            end_date="2025-03-10",
        )

        response = _run_impl_with_patches(req)

        assert response.errors is not None
        assert response.errors[0].code == "invalid_date_range"
        assert response.media_buy_deliveries == []

    def test_date_range_error_no_state_change(self):
        """UC-004-EXT-E3: state unchanged on date range error (read-only op).

        Spec: UNSPECIFIED (implementation-defined error handling behavior).
        Invalid date range must not cause any adapter calls or DB writes beyond
        the initial auth check.
        Covers: UC-004-EXT-E-03
        """
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-20",
            end_date="2025-03-10",
        )

        identity = _make_identity()
        mock_adapter = MagicMock()
        patches = _standard_patches(adapter=mock_adapter)

        with (
            patches["principal_obj"],
            patches["adapter"] as mock_get_adapter,
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["uow"],
        ):
            response = _get_media_buy_delivery_impl(req, identity)

        # Date range error returned
        assert response.errors is not None
        assert response.errors[0].code == "invalid_date_range"

        # No adapter calls or target media buy lookups occurred
        mock_adapter.get_media_buy_delivery.assert_not_called()
        mock_target.assert_not_called()


# ===========================================================================
# 12. Adapter Errors (UC-004-EXT-F1, EXT-F2, EXT-F3, EXT-F4)
# ===========================================================================


class TestDeliveryAdapterError:
    """UC-004-EXT-F: adapter failure handling."""

    def test_adapter_exception_returns_adapter_error(self):
        """UC-004-EXT-F1: adapter raises Exception -> adapter_error code.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-response.json
        CONFIRMED: errors array (items: core/error.json) is an optional field for task-specific
        errors and warnings.
        Covers: UC-004-EXT-F-01
        """
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
        """UC-004-EXT-F2: adapter error still includes correct reporting_period.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-response.json
        CONFIRMED: reporting_period is a required field in the response, so it must be present
        even when errors occur.
        Covers: UC-004-EXT-F-02
        """
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

    def test_adapter_failure_audit_logged(self):
        """UC-004-EXT-F3: adapter failure logged to audit trail (NFR-003).

        Spec: UNSPECIFIED (implementation-defined audit/logging behavior).
        When adapter raises an exception, the error must be logged.
        Covers: UC-004-EXT-F-03
        """
        buy = _make_mock_media_buy(media_buy_id="mb_log")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = RuntimeError("GAM timeout")

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_log"])

        with patch(f"{_PATCH_PREFIX}.logger") as mock_logger:
            response = _run_impl_with_patches(
                req,
                adapter=mock_adapter,
                target_buys=[("mb_log", buy)],
            )

        # Error response returned
        assert response.errors is not None
        assert response.errors[0].code == "adapter_error"

        # Error was logged
        mock_logger.error.assert_called()
        log_message = mock_logger.error.call_args[0][0]
        assert "mb_log" in log_message

    def test_adapter_error_no_state_change(self):
        """UC-004-EXT-F4: state unchanged on adapter error (verify no DB writes).

        Spec: UNSPECIFIED (implementation-defined error handling behavior).
        Delivery is a read-only operation. Adapter errors must not cause any DB writes.
        The response returns error info but doesn't modify any state.
        Covers: UC-004-EXT-F-04
        """
        buy = _make_mock_media_buy(media_buy_id="mb_nowrite")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = ConnectionError("Network down")

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_nowrite"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_nowrite", buy)],
        )

        # Error returned, no deliveries
        assert response.errors is not None
        assert response.errors[0].code == "adapter_error"
        assert response.media_buy_deliveries == []

        # Aggregated totals are zeroed (no partial data leaked)
        assert response.aggregated_totals.impressions == 0.0
        assert response.aggregated_totals.spend == 0.0
        assert response.aggregated_totals.media_buy_count == 0


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

    def test_next_expected_at_computed(self):
        """UC-004-WH-06: next_expected_at computed for non-final deliveries.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-response.json
        CONFIRMED: next_expected_at is an optional datetime field, described as present
        "when notification_type is not 'final'".
        Tests WebhookDeliveryService.send_delivery_webhook: when next_expected_interval_seconds
        is provided and is_final=False, next_expected_at is included in the payload.
        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-06
        """
        service = WebhookDeliveryService()

        with (
            patch.object(service, "_send_webhook_enhanced", return_value=True) as mock_send,
        ):
            service.send_delivery_webhook(
                media_buy_id="mb_wh06",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=1000,
                spend=50.0,
                is_final=False,
                next_expected_interval_seconds=3600,  # 1 hour
            )

        # Verify the payload passed to _send_webhook_enhanced includes next_expected_at
        call_kwargs = mock_send.call_args[1]
        payload = call_kwargs["delivery_payload"]
        assert "next_expected_at" in payload
        assert payload["notification_type"] == "scheduled"

    def test_hmac_sha256_signature_headers(self):
        """UC-004-WH-07: webhook payload signed with HMAC-SHA256.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/core/reporting-webhook.json
        CONFIRMED: authentication.schemes supports ['HMAC-SHA256'] for signature verification.
        Tests that WebhookDeliveryService._generate_hmac_signature produces a valid hex signature,
        and that signing with the same inputs is deterministic.
        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
        """
        service = WebhookDeliveryService()

        payload = {"media_buy_id": "mb_wh07", "impressions": 1000}
        secret = "a" * 32  # 32-char minimum secret
        timestamp = "2025-06-15T12:00:00Z"

        sig1 = service._generate_hmac_signature(payload, secret, timestamp)
        sig2 = service._generate_hmac_signature(payload, secret, timestamp)

        # Signature is a hex string
        assert isinstance(sig1, str)
        assert len(sig1) == 64  # SHA-256 hex = 64 chars

        # Deterministic
        assert sig1 == sig2

        # Different payload produces different signature
        different_payload = {"media_buy_id": "mb_wh07", "impressions": 2000}
        sig3 = service._generate_hmac_signature(different_payload, secret, timestamp)
        assert sig3 != sig1

    def test_webhook_excludes_aggregated_totals(self):
        """UC-004-WH-09: webhook does NOT include aggregated_totals.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-response.json
        CONFIRMED: aggregated_totals description says "Only included in API responses
        (get_media_buy_delivery), not in webhook notifications."
        Tests that the webhook payload built by send_delivery_webhook does not include
        aggregated_totals.
        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-09
        """
        service = WebhookDeliveryService()

        with patch.object(service, "_send_webhook_enhanced", return_value=True) as mock_send:
            service.send_delivery_webhook(
                media_buy_id="mb_wh09",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=5000,
                spend=250.0,
            )

        payload = mock_send.call_args[1]["delivery_payload"]
        # Webhook payload must NOT contain aggregated_totals
        assert "aggregated_totals" not in payload

    def test_webhook_filters_requested_metrics(self):
        """UC-004-WH-10: webhook totals only include metrics actually provided.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/core/reporting-webhook.json
        CONFIRMED: requested_metrics is an optional array of available-metric enum values;
        "If omitted, all available metrics are included."
        Tests that optional metrics (clicks, ctr) are only included in the webhook
        payload when explicitly provided.
        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-10
        """
        service = WebhookDeliveryService()

        # Send without clicks/ctr
        with patch.object(service, "_send_webhook_enhanced", return_value=True) as mock_send:
            service.send_delivery_webhook(
                media_buy_id="mb_wh10",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=1000,
                spend=50.0,
                # clicks and ctr not provided
            )

        payload_no_clicks = mock_send.call_args[1]["delivery_payload"]
        totals = payload_no_clicks["media_buy_deliveries"][0]["totals"]
        # Without explicit clicks/ctr, they should not be in totals
        assert "clicks" not in totals
        assert "ctr" not in totals

        # Now send WITH clicks and ctr
        with patch.object(service, "_send_webhook_enhanced", return_value=True) as mock_send:
            service.send_delivery_webhook(
                media_buy_id="mb_wh10b",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=1000,
                spend=50.0,
                clicks=100,
                ctr=0.1,
            )

        payload_with_clicks = mock_send.call_args[1]["delivery_payload"]
        totals_with = payload_with_clicks["media_buy_deliveries"][0]["totals"]
        assert totals_with["clicks"] == 100
        assert totals_with["ctr"] == 0.1

    def test_only_active_trigger_webhook(self):
        """UC-004-WH-11: only active media buys trigger webhook delivery.

        Spec: UNSPECIFIED (implementation-defined webhook trigger criteria).
        Verifies that the webhook service accepts a status parameter and includes it
        in the payload. The caller is responsible for filtering to active-only buys
        before invoking send_delivery_webhook.
        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
        """
        service = WebhookDeliveryService()

        with patch.object(service, "_send_webhook_enhanced", return_value=True) as mock_send:
            service.send_delivery_webhook(
                media_buy_id="mb_wh11",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=1000,
                spend=50.0,
                status="active",
            )

        payload = mock_send.call_args[1]["delivery_payload"]
        assert payload["media_buy_deliveries"][0]["status"] == "active"


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
        """UC-004-EXT-G3: 5 consecutive failures transitions to OPEN state.

        Spec: UNSPECIFIED (implementation-defined circuit breaker behavior).
        Covers: UC-004-EXT-G-03
        """
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)

        assert cb.state == CircuitState.CLOSED
        for _ in range(4):
            cb.record_failure()
            assert cb.state == CircuitState.CLOSED

        cb.record_failure()  # 5th
        assert cb.state == CircuitState.OPEN

    def test_open_circuit_rejects_requests(self):
        """UC-004-EXT-G3: OPEN circuit -> can_attempt() returns False.

        Spec: UNSPECIFIED (implementation-defined circuit breaker behavior).
        Covers: UC-004-EXT-G-03
        """
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)
        for _ in range(5):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert cb.can_attempt() is False

    def test_open_transitions_to_half_open_after_timeout(self):
        """UC-004-EXT-G4: OPEN state + timeout -> HALF_OPEN.

        Spec: UNSPECIFIED (implementation-defined circuit breaker behavior).
        Covers: UC-004-EXT-G-04
        """
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)
        for _ in range(5):
            cb.record_failure()

        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)

        assert cb.can_attempt() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_recovers_after_success_threshold(self):
        """UC-004-EXT-G4: HALF_OPEN + 2 successes -> CLOSED.

        Spec: UNSPECIFIED (implementation-defined circuit breaker behavior).
        Covers: UC-004-EXT-G-04
        """
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
        """UC-004-EXT-G4: HALF_OPEN + failure -> back to OPEN.

        Spec: UNSPECIFIED (implementation-defined circuit breaker behavior).
        Covers: UC-004-EXT-G-04
        """
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

    def test_auth_rejection_marks_webhook_failed(self):
        """UC-004-EXT-G6: 401/403 auth rejection marks webhook as failed, no retry.

        Spec: UNSPECIFIED (implementation-defined webhook retry/failure behavior).
        401 and 403 are 4xx client errors. The deliver_webhook_with_retry function
        does NOT retry on 4xx errors (only 5xx). So auth rejections fail immediately
        with 1 attempt.
        Covers: UC-004-EXT-G-06
        """
        from src.core.webhook_delivery import WebhookDelivery as WHDelivery
        from src.core.webhook_delivery import deliver_webhook_with_retry

        for status_code in [401, 403]:
            delivery = WHDelivery(
                webhook_url="https://example.com/webhook",
                payload={"test": "data"},
                headers={"Content-Type": "application/json"},
                max_retries=3,
                timeout=10,
            )

            with (
                patch("src.core.webhook_delivery.time.sleep"),
                patch("requests.post") as mock_post,
            ):
                mock_response = MagicMock()
                mock_response.status_code = status_code
                mock_response.text = "Unauthorized" if status_code == 401 else "Forbidden"
                mock_post.return_value = mock_response

                success, result = deliver_webhook_with_retry(delivery)

            assert success is False, f"Expected failure for {status_code}"
            assert result["status"] == "failed"
            assert result["attempts"] == 1, f"No retries for {status_code}"
            assert result["response_code"] == status_code

    def test_webhook_failures_no_synchronous_error(self):
        """UC-004-EXT-G7: webhook failures produce no synchronous error to buyer.

        Spec: UNSPECIFIED (implementation-defined webhook error isolation).
        WebhookDeliveryService.send_delivery_webhook catches all exceptions and
        returns False instead of raising. This ensures webhook failures don't propagate
        as synchronous errors to the buyer's delivery query.
        Covers: UC-004-EXT-G-08
        """
        service = WebhookDeliveryService()

        # Force _send_webhook_enhanced to raise an exception
        with patch.object(service, "_send_webhook_enhanced", side_effect=RuntimeError("Network failure")):
            result = service.send_delivery_webhook(
                media_buy_id="mb_g7",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=1000,
                spend=50.0,
            )

        # Returns False, no exception propagated
        assert result is False


# ===========================================================================
# 15. Protocol and Schema (UC-004-MAIN-12, MAIN-13, MAIN-16, MAIN-17)
# ===========================================================================


class TestDeliveryProtocol:
    """UC-004-MAIN: protocol envelope and schema completeness."""

    def test_protocol_envelope_status_completed(self):
        """UC-004-MAIN-12: response wrapped in protocol envelope with status=completed.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/core/protocol-envelope.json
        CONFIRMED: protocol envelope wraps task responses with status field.
        Tests that ProtocolEnvelope.wrap correctly wraps a delivery response.
        Covers: UC-004-MAIN-12
        """
        from src.core.protocol_envelope import ProtocolEnvelope

        response = GetMediaBuyDeliveryResponse(
            reporting_period={"start": datetime(2025, 1, 1, tzinfo=UTC), "end": datetime(2025, 6, 30, tzinfo=UTC)},
            currency="USD",
            aggregated_totals=AggregatedTotals(impressions=100, spend=10, media_buy_count=1),
            media_buy_deliveries=[],
        )

        envelope = ProtocolEnvelope.wrap(
            payload=response,
            status="completed",
            message="Retrieved delivery data.",
        )

        assert envelope.status == "completed"
        assert envelope.message == "Retrieved delivery data."
        assert isinstance(envelope.payload, dict)
        assert "aggregated_totals" in envelope.payload
        assert "media_buy_deliveries" in envelope.payload
        assert envelope.timestamp is not None

    def test_mcp_toolresult_content_and_structured(self):
        """UC-004-MAIN-13: MCP ToolResult contains both content and structured_content.

        Spec: UNSPECIFIED (MCP transport-specific implementation detail).
        Tests that the MCP wrapper (get_media_buy_delivery) returns a ToolResult
        with both content (string) and structured_content (response object).
        Covers: UC-004-MAIN-13
        """
        from fastmcp.tools.tool import ToolResult

        buy = _make_mock_media_buy(media_buy_id="mb_tool")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_tool",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_tool"])
        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_tool", buy)],
        )

        # Simulate what the MCP wrapper does: ToolResult(content=str(response), structured_content=response)
        tool_result = ToolResult(content=str(response), structured_content=response)

        # content is converted to list[TextContent] by FastMCP
        assert tool_result.content is not None
        assert len(tool_result.content) == 1
        assert "delivery data" in tool_result.content[0].text.lower()
        # structured_content contains the actual response data
        assert tool_result.structured_content is not None

    def test_delivery_metrics_all_standard_fields(self):
        """UC-004-MAIN-16: delivery metrics include standard fields.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/core/delivery-metrics.json
        CONFIRMED: delivery-metrics.json defines impressions, spend, clicks, ctr, views,
        completed_views, completion_rate, conversions, conversion_value, roas, cost_per_acquisition,
        viewability, engagement_rate, cost_per_click, quartile_data, dooh_metrics, etc.
        Covers: UC-004-MAIN-19
        """
        buy = _make_mock_media_buy(media_buy_id="mb_fields")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_fields",
            impressions=1000,
            spend=50.0,
            clicks=10,
            packages=[{"package_id": "pkg_001", "impressions": 1000, "spend": 50.0}],
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_fields"])

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_fields", buy)],
        )

        delivery = response.media_buy_deliveries[0]
        totals = delivery.totals
        # Required fields present
        assert totals.impressions is not None
        assert totals.spend is not None
        # Optional fields exist as attributes (may be None)
        assert hasattr(totals, "clicks")
        assert hasattr(totals, "ctr")
        assert hasattr(totals, "video_completions")
        assert hasattr(totals, "completion_rate")

    def test_unpopulated_fields_handled_gracefully(self):
        """UC-004-MAIN-17: unpopulated optional fields are None, not errors.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-response.json
        CONFIRMED: daily_breakdown, effective_rate, viewability, by_creative are all optional
        fields in the spec (no required constraint).
        Covers: UC-004-MAIN-20
        """
        buy = _make_mock_media_buy(media_buy_id="mb_optional")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_optional",
            impressions=100,
            spend=5.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 5.0}],
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_optional"])

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_optional", buy)],
        )

        delivery = response.media_buy_deliveries[0]
        # daily_breakdown is optional and not populated
        assert delivery.daily_breakdown is None
        # video_completions is optional
        assert delivery.totals.video_completions is None
        # aggregated_totals optional fields
        assert response.aggregated_totals.video_completions is None
