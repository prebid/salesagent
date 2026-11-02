"""Canonical test suite for UC-004: Deliver Media Buy Metrics.

Spec verification: 2026-02-26
adcp spec commit: 8f26baf3
adcp-client-python commit: a08805d
Verified: 30/59 CONFIRMED, 24/59 UNSPECIFIED, 5 CONTRADICTS , 0 SPEC_AMBIGUOUS

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
from adcp.types import MediaBuyStatus

from src.core.exceptions import AdCPValidationError
from src.core.helpers import enum_value
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
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.services.webhook_delivery_service import CircuitBreaker, CircuitState, WebhookDeliveryService
from tests.factories.media_buy import (
    default_request_packages,
    pricing_options_for,
    pricing_options_named,
    request_package,
)
from tests.factories.principal import PrincipalFactory
from tests.factories.product import PricingOptionFactory
from tests.harness.delivery_poll_unit import DeliveryPollEnv

# ---------------------------------------------------------------------------
# Fixtures (shared across all test classes)
# ---------------------------------------------------------------------------

_PATCH_PREFIX = "src.core.tools.media_buy_delivery"


def _make_identity(
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
) -> ResolvedIdentity:
    """Build a test ResolvedIdentity via the canonical factory.

    Delegates to PrincipalFactory.make_identity (the single source of truth
    per tests/CLAUDE.md) instead of constructing ResolvedIdentity inline.

    The ``testing_context`` parameter is gone with the channel it carried: commit
    a1b79d22d deleted ``src/core/testing_hooks`` and took the testing context off the
    identity ("requests carry no testing headers"), so there is nothing to override and
    nothing downstream that would read one. The ``model_copy`` that applied it was also
    the one thing ``.ast-grep/rules/resolved-identity-constructed-only-by-its-owners.yml``
    forbids of an identity.
    """
    return PrincipalFactory.make_identity(principal_id=principal_id, tenant_id=tenant_id)


def _make_mock_media_buy(
    media_buy_id: str = "mb_001",
    budget: float = 10000.0,
    currency: str = "USD",
    start_date: date | None = None,
    end_date: date | None = None,
    raw_request: dict | None = None,
    start_time=None,
    end_time=None,
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
    status: str = "active",
) -> MagicMock:
    buy = MagicMock()
    buy.media_buy_id = media_buy_id
    buy.budget = Decimal(str(budget))
    buy.currency = currency
    buy.start_date = start_date or date(2025, 1, 1)
    buy.end_date = end_date or date(2025, 12, 31)
    buy.start_time = start_time
    buy.end_time = end_time
    buy.status = status  # generic "active" is date-refined by _get_target_media_buys
    buy.principal_id = principal_id
    buy.tenant_id = tenant_id
    buy.is_paused = False
    # Packages name a pricing option, because ``package-request.json`` REQUIRES one on
    # every package — a buy without it is a shape ``create_media_buy`` cannot store, and
    # the impl rightly refuses to report delivery it cannot price.
    buy.raw_request = raw_request or {"packages": default_request_packages()}
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
        "adapter": patch(
            f"{_PATCH_PREFIX}.get_adapter",
            return_value=adapter,
        ),
        "target_buys": patch(
            f"{_PATCH_PREFIX}._get_target_media_buys",
            return_value=target_buys,
        ),
        # Answers about the ids production asked for, derived the way the real lookup
        # derives them, unless the caller pins its own map. The delivery report REQUIRES
        # pricing_model/rate/currency per package (get-media-buy-delivery-response.json)
        # and these mock buys have no MediaPackage row, so this is their pricing source.
        "pricing_options": patch(
            f"{_PATCH_PREFIX}._get_pricing_options",
            # NOT ``{}`` — the pin REQUIRES pricing_model/rate/currency on every
            # by_package entry, so an empty map makes the response unbuildable.
            side_effect=lambda option_ids, **_: (
                pricing_options if pricing_options is not None else pricing_options_for(option_ids)
            ),
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
        patches["adapter"],
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
            raw_request={"packages": [request_package(package_id="pkg_a", product_id="prod_1")]},
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
            raw_request={"packages": [request_package(package_id="pkg_1a", product_id="prod_1")]},
        )
        buy2 = _make_mock_media_buy(
            media_buy_id="mb_agg_2",
            budget=8000.0,
            start_date=date(2025, 3, 1),
            end_date=date(2025, 12, 31),
            raw_request={"packages": [request_package(package_id="pkg_2a", product_id="prod_2")]},
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
    """UC-004 BR-RULE-030: media_buy_ids identification (provided vs neither)."""

    def test_partial_ids_returns_found_and_errors_for_missing(self):
        """UC-004-MAIN-14: partial resolution returns found buys AND errors for missing.

        Spec: CONTRADICTS -- get-media-buy-delivery-response.json has errors array for
        "Task-specific errors and warnings (e.g., missing delivery data)". Current impl
        silently drops missing IDs. Correct: return delivery for found IDs + populate
        errors with media_buy_not_found for each missing ID.
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/schemas/cache/media-buy/get-media-buy-delivery-response.json
        Fix: _get_target_media_buys must track which requested IDs were not found and
        return errors for them.
        Priority: P1
        Type: unit
        Source: UC-004,
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
        # WHICH buys were missing travels in details, not in the sentence: message is
        # derived from the code, so every MEDIA_BUY_NOT_FOUND advisory reads identically.
        reported = {e.details["media_buy_id"] for e in response.errors if e.details}
        assert reported == {"mb_missing_1", "mb_missing_2"}
        for err in response.errors:
            assert err.code == "MEDIA_BUY_NOT_FOUND"

    def test_all_ids_invalid_returns_empty_with_errors(self):
        """UC-004-MAIN-15: all requested IDs missing returns empty deliveries + errors.

        Spec: CONTRADICTS -- response.errors must contain media_buy_not_found for each
        missing ID. Current impl returns empty with errors=None.
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/schemas/cache/media-buy/get-media-buy-delivery-response.json
        Fix: populate errors array.
        Priority: P1
        Type: unit
        Source: UC-004,
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
        assert error_codes == {"MEDIA_BUY_NOT_FOUND"}
        # WHICH buys were missing travels in details, not in the sentence.
        reported = {e.details["media_buy_id"] for e in response.errors if e.details}
        assert reported == {"mb_ghost_1", "mb_ghost_2"}


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

        # "principal_obj" and "tenant" are no longer patch entries: the principal and the
        # tenant are carried BY the identity (the tool reads identity.principal /
        # identity.tenant and looks up neither), so _standard_patches has nothing to
        # stand in for. The patches that remain are the collaborators the tool calls.
        with (
            patches["adapter"],
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
        mock_status = MagicMock()
        mock_status.value = "completed"
        mock_req.status_filter = mock_status

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_active, buy_completed]

        result = _get_target_media_buys(mock_req, "test_principal", mock_repo, ref_date)

        assert len(result) == 1
        assert result[0][0] == "mb_done"

    def test_status_filter_paused(self):
        """UC-004-FILT-03: filter by status paused returns no buys when the
        only buy is persisted "active" and inside its flight window.

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
        mock_status = MagicMock()
        mock_status.value = "completed"
        mock_req.status_filter = mock_status

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy_active]

        result = _get_target_media_buys(mock_req, "test_principal", mock_repo, ref_date)

        assert len(result) == 0

    # test_simulated_clock_filter_matches_reported_status and
    # test_simulated_clock_filter_excludes_non_matching_status are REMOVED, not ported.
    # Their subject was the simulated clock: both passed a fifth argument (an
    # AdCPTestContext carrying mock_time) to _get_target_media_buys and asserted that the
    # status filter resolved against it. Commit a1b79d22d deleted the whole testing-hook
    # channel -- src/core/testing_hooks is gone, a request carries no x-mock-time header,
    # resolve_canonical_status lost its simulate parameter and _get_target_media_buys now
    # takes exactly (req, principal_id, repo, reference_date). There is no simulated clock
    # to disagree with the real one, so the #1545 O2 regression they guarded (filter path
    # on the real date, display path on mock_time) is unreachable by construction. The
    # sibling cases above still grade the filter itself against reference_date.

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

    CRITICAL: identified that _get_pricing_options compares
    string pricing_option_id from JSON to integer PK column, which always
    silently fails. These tests validate the fix.
    """

    def test_pricing_option_lookup_uses_string_field_not_integer_pk(self):
        """_get_pricing_options resolves via synthetic ID (model_currency_type), not integer PK.

        Spec: UNSPECIFIED (implementation-defined ID resolution strategy).

        Our implementation constructs synthetic IDs like "cpm_usd_fixed" from
        PricingOption fields and matches against requested IDs.

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-01
        """
        from src.core.tools.media_buy_delivery import _get_pricing_options

        # A real (unpersisted) row. A bare MagicMock fabricates every attribute it is
        # asked for, ``root`` included — so the RootModel unwrap production performs
        # returns a child mock and the id is built from Mock repr, not from these values.
        pricing_option = PricingOptionFactory.build(
            id=42, pricing_model="cpm", currency="USD", is_fixed=True, rate=Decimal("5.00")
        )
        pricing_option.tenant_id = "test_tenant"

        mock_repo = MagicMock()
        mock_repo.get_all_pricing_options.return_value = [pricing_option]

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
        # Real (unpersisted) rows, not MagicMocks: every by_package entry must state
        # currency, and a MagicMock attribute is not a string. The map's KEY is derived by
        # production's own ``synthetic_pricing_option_id``, and the package names that same
        # derived id — so the lookup the impl performs is the one this fixture answers.
        pricing_options = pricing_options_named(pricing_model="cpm", rate="5.00")
        [pricing_option_id] = pricing_options

        buy = _make_mock_media_buy(
            media_buy_id="mb_cpm",
            budget=10000.0,
            raw_request={
                "packages": [
                    request_package(package_id="pkg_cpm", product_id="prod_1", pricing_option_id=pricing_option_id)
                ]
            },
        )

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
            pricing_options=pricing_options,
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
        # "cpc" as a plain string: the DB column is String(20), not the enum.
        pricing_options = pricing_options_named(pricing_model="cpc", rate="0.50")
        [pricing_option_id] = pricing_options

        buy = _make_mock_media_buy(
            media_buy_id="mb_cpc",
            budget=5000.0,
            raw_request={
                "packages": [
                    request_package(package_id="pkg_cpc", product_id="prod_1", pricing_option_id=pricing_option_id)
                ]
            },
        )

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
            pricing_options=pricing_options,
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
        pricing_options = pricing_options_named(pricing_model="flat_rate", rate="5000.00")
        [pricing_option_id] = pricing_options

        buy = _make_mock_media_buy(
            media_buy_id="mb_flat",
            budget=5000.0,
            raw_request={
                "packages": [
                    request_package(package_id="pkg_flat", product_id="prod_1", pricing_option_id=pricing_option_id)
                ]
            },
        )

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
            pricing_options=pricing_options,
        )

        delivery = response.media_buy_deliveries[0]
        assert delivery.totals.spend == 5000.0
        # FLAT_RATE: no click computation (clicks should be None)
        pkg = delivery.by_package[0]
        assert pkg.clicks is None

    def test_by_package_carries_the_resolved_pricing_options_terms(self):
        """The three pin-REQUIRED pricing fields on by_package come from the resolved option.

        Spec: get-media-buy-delivery-response.json (AdCP 3.1, the pinned version) lists
        ``pricing_model``, ``rate`` and ``currency`` in the by_package item's ``required``
        set and types all three non-nullable (rate {type: number, minimum: 0}, currency
        {type: string, pattern: ^[A-Z]{3}$}).

        This grades the VALUES, which nothing else did: the sibling cases above assert
        clicks and spend, and the failure mode this pins -- ``_package_pricing`` refusing a
        package whose buy names no pricing option -- reaches every one of them as a crash,
        so a green suite never proved the writer put the right numbers on the wire.

        Covers: UC-004-PRICINGOPTION-TYPE-CONSISTENCY-02
        """
        pricing_options = pricing_options_named(pricing_model="cpm", rate="5.00", currency="USD")
        [pricing_option_id] = pricing_options

        buy = _make_mock_media_buy(
            media_buy_id="mb_terms",
            raw_request={"packages": [request_package(package_id="pkg_terms", pricing_option_id=pricing_option_id)]},
        )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_terms",
            impressions=20000,
            spend=100.0,
            packages=[{"package_id": "pkg_terms", "impressions": 20000, "spend": 100.0}],
        )

        response = _run_impl_with_patches(
            GetMediaBuyDeliveryRequest(media_buy_ids=["mb_terms"]),
            adapter=mock_adapter,
            target_buys=[("mb_terms", buy)],
            pricing_options=pricing_options,
        )

        pkg = response.media_buy_deliveries[0].by_package[0]
        assert (pkg.pricing_model, pkg.rate, pkg.currency) == (PricingModel.cpm, 5.0, "USD")
        # ...and they SURVIVE serialization. The SDK base dumps with exclude_none=True, so
        # an unset one is dropped rather than emitted as null, which is how a delivery
        # response went schema-invalid without any model complaining (GH #2130).
        dumped = response.model_dump()["media_buy_deliveries"][0]["by_package"][0]
        assert {"pricing_model", "rate", "currency"} <= dumped.keys()


# ===========================================================================
# 7. 3.6 Upgrade Compatibility (UC-004-UPG-03, UPG-04, UPG-05)
# ===========================================================================


class TestDeliveryUpgradeCompat:
    """UC-004-UPG: 3.6 upgrade schema compatibility."""

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
# 8. Auth Errors (UC-004-EXT-A1, EXT-A2, EXT-B1) — REMOVED, not ported
# ===========================================================================
#
# TestDeliveryAuthErrors held three cases (test_missing_principal_id_returns_error,
# test_principal_not_found_returns_error, test_auth_failure_no_state_change) and all
# three graded a re-check inside the tool: hand _get_media_buy_delivery_impl an identity
# with no usable principal, or patch src.core.auth.get_principal_object to return None,
# and expect AdCPAuthenticationError from the tool.
#
# That re-check is deliberately gone, and so is the thing it checked:
#
# * commit 47d57e5d6 deleted get_principal_object (with LazyTenantContext, the
#   admin-token fallback and resolve_principal_or_raise), because the identity now
#   CARRIES the Principal and TenantContext the resolver loaded — there is no second
#   lookup for a test to stub, and the commit removed "the tests that pinned them";
# * a protected tool declares ``identity: ResolvedIdentity``, which by type cannot be
#   anonymous, and the resolver refuses a missing or rejected credential before the tool
#   runs (critical pattern #5: "a tool never re-checks what the boundary decided").
#   ``ruff-boundary.toml`` bans raising AdCPAuthRequiredError / AdCPAuthenticationError
#   anywhere but the resolver, so the exception these cases expected cannot be raised
#   from media_buy_delivery at all.
#
# So the obligation (an unauthenticated delivery read is refused, and nothing is read or
# called on the way out) is not a delivery-tool obligation any more; it is the resolver's,
# graded once there and on the wire by the transport-blind auth scenarios rather than
# once per tool. Reinstating a tool-level version would require reinstating the re-check.
#
# ===========================================================================
# 9. Media Buy Not Found (UC-004-EXT-C1, EXT-C2, EXT-C3)
# ===========================================================================


class TestDeliveryMediaBuyNotFound:
    """UC-004-EXT-C: media buy resolution failures."""

    def test_media_buy_not_found_returns_error(self):
        """UC-004-EXT-C1: single media_buy_id not found returns error in response.

        Spec: CONTRADICTS -- get-media-buy-delivery-response.json errors array is for
        "Task-specific errors and warnings (e.g., missing delivery data)". Current impl
        returns empty deliveries with errors=None. Correct: errors=[{code: "MEDIA_BUY_NOT_FOUND"}].
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/schemas/cache/media-buy/get-media-buy-delivery-response.json
        Fix: _get_target_media_buys must diff requested IDs vs found IDs.
        Priority: P1
        Type: unit
        Source: UC-004,
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
        assert response.errors[0].code == "MEDIA_BUY_NOT_FOUND"
        assert response.errors[0].details == {"media_buy_id": "mb_nonexistent"}


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
        assert response.errors[0].code == "MEDIA_BUY_NOT_FOUND"
        assert response.errors[0].details == {"media_buy_id": "mb_other_principal"}

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
        # Must NOT reveal ownership: code is "MEDIA_BUY_NOT_FOUND", not "ownership_mismatch"
        assert response.errors[0].code == "MEDIA_BUY_NOT_FOUND"
        assert "ownership" not in response.errors[0].message.lower()  # table sentence, never ownership-revealing


# ===========================================================================
# 11. Invalid Date Range (UC-004-EXT-E1, EXT-E2, EXT-E3)
# ===========================================================================


class TestDeliveryInvalidDateRange:
    """UC-004-EXT-E: invalid date range validation."""

    def test_start_date_equals_end_date_returns_error(self):
        """UC-004-EXT-E1: start_date == end_date raises AdCPValidationError.

        Spec: UNSPECIFIED (implementation-defined date range validation).
        Spec defines start_date/end_date as string patterns but no ordering constraint.
        Covers: UC-004-EXT-E-01
        """
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-15",
            end_date="2025-03-15",
        )

        with pytest.raises(AdCPValidationError):
            _run_impl_with_patches(req)

    def test_start_date_after_end_date_returns_error(self):
        """UC-004-EXT-E2: start_date > end_date raises AdCPValidationError.

        Spec: UNSPECIFIED (implementation-defined date range validation).
        Covers: UC-004-EXT-E-02
        """
        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-20",
            end_date="2025-03-10",
        )

        with pytest.raises(AdCPValidationError):
            _run_impl_with_patches(req)

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
            patches["adapter"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["uow"],
        ):
            with pytest.raises(AdCPValidationError):
                _get_media_buy_delivery_impl(req, identity)

        # No adapter calls or target media buy lookups occurred
        mock_adapter.get_media_buy_delivery.assert_not_called()
        mock_target.assert_not_called()


# ===========================================================================
# 12. Adapter Errors (UC-004-EXT-F1, EXT-F2, EXT-F3, EXT-F4)
# ===========================================================================


class TestDeliveryAdapterError:
    """UC-004-EXT-F: adapter failure handling."""

    def test_adapter_exception_returns_adapter_error(self):
        """UC-004-EXT-F1: adapter raises Exception -> error RETURNED in response.errors[].

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/media-buy/get-media-buy-delivery-response.json
        Per the obligation, a per-buy adapter failure degrades: the response still returns
        with an advisory error in errors[] so the other buys' data is preserved.
        Covers: UC-004-EXT-F-01
        """
        buy = _make_mock_media_buy(media_buy_id="mb_err")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = RuntimeError("GAM API timeout")

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_err"])

        result = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_err", buy)],
        )

        assert isinstance(result, GetMediaBuyDeliveryResponse)
        assert result.errors is not None
        assert any(e.code == "SERVICE_UNAVAILABLE" for e in result.errors)

    def test_adapter_error_returns_adapter_error(self):
        """UC-004-EXT-F2: adapter exception -> error RETURNED in response.errors[].

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

        result = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_err2", buy)],
        )

        assert isinstance(result, GetMediaBuyDeliveryResponse)
        assert result.errors is not None
        assert any(e.code == "SERVICE_UNAVAILABLE" for e in result.errors)

    def test_non_adapter_processing_failure_surfaces_advisory(self):
        """A per-buy failure OUTSIDE the adapter call must not drop the buy silently.

        Regression (#1545 K2): the outer per-buy handler logged and continued with
        no errors[] advisory, so a failure in the status/model-construction path made
        the buy vanish from the response with no signal. It must append an advisory.

        EXPECTATION REVERSED by salesagent-tay20, on the strength of this test's own
        name. The advisory was SERVICE_UNAVAILABLE "mirroring the adapter handler" --
        the copied justification that ticket exists to remove -- and the adapter
        handler cannot be the model here, because the adapter cannot reach this branch:
        its call has its OWN try whose handler advises and `continue`s. What reaches
        here is a crash in our per-buy processing, exactly as this test's name says,
        and SERVICE_UNAVAILABLE is pinned transient -- it tells the buyer to retry
        something no retry fixes.

        The stated reason for preferring it was that INTERNAL_ERROR is "internal-only".
        That is no longer true: INTERNAL_ERROR is in CODE_TABLE, resolves to
        recovery=transient with a buyer-facing sentence, and production already emits
        it on the wire (salesagent-45d27, graded across mcp/a2a/rest). A platform code
        reaching the buyer untranslated is what this epic established.

        Everything else this test asserts -- the surviving buy still reported, the
        failed buy surfacing rather than vanishing, one advisory naming which buy --
        is unchanged.
        """
        good = _make_mock_media_buy(media_buy_id="mb_good")
        bad = _make_mock_media_buy(media_buy_id="mb_bad")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(media_buy_id="mb_good")

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_good", "mb_bad"])

        def _resolve(buy, *args, **kwargs):
            if buy.media_buy_id == "mb_bad":
                raise RuntimeError("status resolution blew up")
            return "active"

        with patch(f"{_PATCH_PREFIX}.resolve_canonical_status", side_effect=_resolve):
            result = _run_impl_with_patches(
                req,
                adapter=mock_adapter,
                target_buys=[("mb_good", good), ("mb_bad", bad)],
            )

        # Surviving buy is still reported.
        assert [d.media_buy_id for d in result.media_buy_deliveries] == ["mb_good"]
        # Failed buy surfaces an advisory instead of vanishing.
        assert result.errors is not None
        advisory_errors = [e for e in result.errors if e.code == "INTERNAL_ERROR"]
        assert len(advisory_errors) == 1
        assert advisory_errors[0].details == {"media_buy_id": "mb_bad"}
        # The adapter's own code must NOT be borrowed for our crash.
        assert not [e for e in result.errors if e.code == "SERVICE_UNAVAILABLE"]

    def test_advisory_normalization_carries_the_declared_code_verbatim(self):
        """A hand-built advisory reaches the buyer carrying the code its author declared.

        EXPECTATION REVERSED TWICE. salesagent-3dawm.6 stopped the normalizer re-coding
        INTERNAL_ERROR to SERVICE_UNAVAILABLE, because AdCP 3.1.1 core/error.json makes the
        vocabulary OPEN: error.code is a wire-typed string, the published codes are
        documentary, senders MAY emit codes outside that set, and receivers MUST decode an
        unknown code by reading error.recovery. Collapsing a code DISCARDED information the
        spec asks senders to keep.

        salesagent-3dawm.14 then deleted the normalizer outright. Derivation moved INTO the
        Error model, so an advisory is correct at construction rather than corrected on the
        way out — there is no longer a window in which a wrong one exists. This test now
        grades that: the code survives verbatim, and message/suggestion/recovery are
        FUNCTIONS of it (ADR-010), so an authored message is discarded rather than carried.
        """
        from src.core.errors.codes import CODE_TABLE
        from src.core.schemas import Error

        out = [
            Error(code="SERVICE_UNAVAILABLE", message="internal adapter detail for mb_x"),
            Error(code="INTERNAL_ERROR", message="platform code for mb_y"),
            Error(code="MEDIA_BUY_NOT_FOUND", message="already standard for mb_z"),
        ]
        # The declared code reaches the buyer verbatim -- no collapse onto a published one.
        assert [e.code for e in out] == ["SERVICE_UNAVAILABLE", "INTERNAL_ERROR", "MEDIA_BUY_NOT_FOUND"]
        # The authored sentence is DISCARDED: message is a function of the code.
        assert out[0].message == CODE_TABLE["SERVICE_UNAVAILABLE"].message
        assert "internal adapter detail" not in out[0].message
        # And every entry carries the recovery its code means, so an unknown code stays decodable.
        assert [e.recovery for e in out] == ["transient", "transient", "correctable"]

    def test_adapter_failure_audit_logged(self):
        """UC-004-EXT-F3: adapter failure logged to audit trail (NFR-003).

        Spec: UNSPECIFIED (implementation-defined audit/logging behavior).
        When the adapter fails, the error must be logged before the advisory error is returned.
        Covers: UC-004-EXT-F-03
        """
        buy = _make_mock_media_buy(media_buy_id="mb_log")
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = RuntimeError("GAM timeout")

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_log"])

        with patch(f"{_PATCH_PREFIX}.logger") as mock_logger:
            result = _run_impl_with_patches(
                req,
                adapter=mock_adapter,
                target_buys=[("mb_log", buy)],
            )

        assert isinstance(result, GetMediaBuyDeliveryResponse)
        # Error was logged before the advisory error was returned
        mock_logger.error.assert_called()
        # Lazy logging: the media_buy_id is a %-arg, not baked into the format string.
        call = mock_logger.error.call_args
        rendered = call[0][0] % call[0][1:] if len(call[0]) > 1 else call[0][0]
        assert "mb_log" in rendered

    def test_adapter_error_no_state_change(self):
        """UC-004-EXT-F4: adapter error is returned (degrade); operation stays read-only.

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

        result = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_nowrite", buy)],
        )

        assert isinstance(result, GetMediaBuyDeliveryResponse)
        assert result.errors is not None


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

        with patch.object(service, "_send_webhook_enhanced", return_value=True) as mock_send:
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
        # The delivery REPORT, which is what this case grades. The sender takes it and
        # wraps it per registration, so the report is the argument and the envelope is
        # built one layer down (AdCP 3.1.1 L3/webhooks.mdx :217).
        payload = mock_send.call_args[1]["result"]
        assert "next_expected_at" in payload
        assert payload["notification_type"] == "scheduled"

    # UC-004-WH-07 (HMAC-SHA256 signature headers) is MOVED, not retired. It stood here
    # and bound a real socket through CircuitBreakerEnv, which a unit test cannot
    # legitimately do: the only address it can bind is loopback, production's egress gate
    # refuses loopback, and so the case ran only because ADCP_OUTBOUND_ALLOW_PRIVATE was
    # open. docker-compose.e2e.yml:988 records that hatch as "considered and rejected" for
    # precisely this purpose — it "opens 127.0.0.1, host.docker.internal and all of RFC1918
    # for whatever sets it". Its own docstring said the quiet part: the assertion is only
    # meaningful "running against an origin the real policy admits", and in unit there is
    # no such origin.
    #
    # The obligation is graded where an origin IS admitted on its own terms — the e2e
    # network is allocated outside the private ranges for exactly that
    # (scripts/dev/alloc-e2e-subnet.sh):
    #
    #   tests/bdd/features/BR-UC-004-deliver-media-buy-metrics.feature:284
    #     @T-UC-004-webhook-hmac — "HMAC-SHA256 signed webhook payload", across transports,
    #     asserting the X-ADCP-Signature header and the timestamp.payload concatenation.

    # UC-004-WH-09 (webhook excludes aggregated_totals) is RETIRED, not moved. It asserted
    # `"aggregated_totals" not in payload` against a dict this function builds key by key
    # from its own parameters -- a dict that has never had the key and structurally cannot
    # grow one, so the assertion could not fail whatever production did. The prohibition it
    # cited is also not in the pinned contract: 3.1/media-buy/media-buy-delivery-webhook
    # -result.json sets `additionalProperties: true` and does not declare the field, so an
    # emitted `aggregated_totals` is schema-VALID. What replaced it is the shape assertion
    # one level up -- the POST body is the mcp-webhook-payload envelope, graded by the
    # UC-004 webhook-compliance scenarios against the pinned schema. (The other side's copy
    # also read `call_args[1]["delivery_payload"]`, a kwarg `_send_webhook_enhanced` no
    # longer takes -- it is `result=` now, as WH-06 above reads.)

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

        payload_no_clicks = mock_send.call_args[1]["result"]
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

        payload_with_clicks = mock_send.call_args[1]["result"]
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

        payload = mock_send.call_args[1]["result"]
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
        from tests.harness.delivery_webhook_unit import WebhookEnv

        for status_code in [401, 403]:
            # Repointed onto the real local origin (salesagent-4fya.11). This used to
            # patch requests.post; delivery is on the egress seam now, so that patch
            # would have gone inert and the test would have issued a genuine request
            # to example.com — which is exactly what it did until this was fixed.
            # deliver_webhook_with_retry is called directly so the graded production
            # entry point is visible in the test body rather than behind a harness alias.
            with WebhookEnv() as env:
                env.set_http_status(status_code, "Unauthorized" if status_code == 401 else "Forbidden")

                success, result = deliver_webhook_with_retry(
                    WHDelivery(
                        webhook_url=env.webhook_url,
                        payload={"test": "data"},
                        headers={"Content-Type": "application/json"},
                        max_retries=3,
                        timeout=10,
                    )
                )

                assert env.delivery_attempts == 1

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
        """UC-004-MAIN-12: the response IS the protocol envelope, and says status=completed.

        Spec: https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/dist/schemas/3.0.0-beta.3/core/protocol-envelope.json
        CONFIRMED: every response schema composes core/protocol-envelope.json at its root
        with allOf, so the envelope's fields are part of the response's own contract.
        Covers: UC-004-MAIN-12

        This used to call ``ProtocolEnvelope.wrap(payload=response, status="completed")``
        and read the fields back off the wrapper. Both the wrapper and its module are gone
        (commits d8a39a661, 1d04b782c, 0d3cab2d2): a root allOf reaches every branch
        unconditionally, so a response does not get wrapped in an envelope -- it inherits
        one, and the envelope status can no longer be chosen independently of the body.
        The obligation survives the wrapper, so it is graded here against the response the
        buyer actually receives: the field is at the ROOT of the wire body, not nested
        under a ``payload`` key that no longer exists.
        """
        from src.core.tools._wire import to_wire

        response = GetMediaBuyDeliveryResponse(
            reporting_period={"start": datetime(2025, 1, 1, tzinfo=UTC), "end": datetime(2025, 6, 30, tzinfo=UTC)},
            currency="USD",
            aggregated_totals=AggregatedTotals(impressions=100, spend=10, media_buy_count=1),
            media_buy_deliveries=[],
        )

        wire = to_wire(response)

        assert wire["status"] == "completed"
        # The domain fields sit beside the envelope's, at the root.
        assert "aggregated_totals" in wire
        assert "media_buy_deliveries" in wire
        assert "payload" not in wire

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
        # completed_views is optional
        assert delivery.totals.completed_views is None
        # aggregated_totals optional fields
        assert response.aggregated_totals.completed_views is None


class TestCircuitBreakerDoesNotMaskTerminalStatus:
    """An open reporting circuit breaker may only overwrite an actively serving buy.

    "reporting_delayed" tells the buyer data is temporarily unavailable and a
    later report will follow. That is a lie for a terminal buy (canceled,
    rejected, failed, completed), a paused buy, or a pending buy that has never
    served — none are awaiting fresh delivery data. The override is scoped to
    status == "active".

    Covers: UC-004-EXT-G-03
    """

    def test_open_breaker_leaves_canceled_status_intact(self):
        buy = _make_mock_media_buy(
            media_buy_id="mb_cancelled",
            status="canceled",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(media_buy_id="mb_cancelled")

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_cancelled"])

        with patch(f"{_PATCH_PREFIX}._is_circuit_breaker_open", return_value=True):
            response = _run_impl_with_patches(
                req,
                adapter=mock_adapter,
                target_buys=[("mb_cancelled", buy)],
            )

        assert response.media_buy_deliveries[0].status == "canceled"

    def test_open_breaker_still_marks_active_reporting_delayed(self):
        """The override is still applied to a genuinely serving (active) buy."""
        buy = _make_mock_media_buy(
            media_buy_id="mb_live",
            status="active",
            start_date=date(2025, 1, 1),
            end_date=date(2027, 12, 31),
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(media_buy_id="mb_live")

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_live"])

        with patch(f"{_PATCH_PREFIX}._is_circuit_breaker_open", return_value=True):
            response = _run_impl_with_patches(
                req,
                adapter=mock_adapter,
                target_buys=[("mb_live", buy)],
            )

        assert response.media_buy_deliveries[0].status == "reporting_delayed"

    def test_open_breaker_leaves_pending_status_intact(self):
        """A never-served pending buy is not masked as reporting_delayed (#1545 O3).

        pending_creatives / pending_start buys have no delivery data pending, so
        an open breaker must not relabel them — that would promise a report that
        never comes and contradict a status_filter that selected the pending buy.
        """
        buy = _make_mock_media_buy(
            media_buy_id="mb_pending",
            status="pending_creatives",
            start_date=date(2025, 1, 1),
            end_date=date(2027, 12, 31),
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(media_buy_id="mb_pending")

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_pending"])

        with patch(f"{_PATCH_PREFIX}._is_circuit_breaker_open", return_value=True):
            response = _run_impl_with_patches(
                req,
                adapter=mock_adapter,
                target_buys=[("mb_pending", buy)],
            )

        assert response.media_buy_deliveries[0].status == "pending_creatives"


class TestNotificationTypeTerminality:
    """notification_type derives from NO_MORE_DATA_STATUSES, not just "completed".

    Regression (#1552, made blocking in the #1545 follow-up review): terminal
    statuses are returned verbatim since #1545, but notification_type was
    "final" only for all-completed — so a rejected/canceled/failed buy reported
    "scheduled" with a next_expected_at 24h out, forever, telling the buyer to
    keep polling a buy that will never report again (and persisting that
    phantom schedule to the webhook log). Spec: next_expected_at is "only
    present ... when notification_type is not 'final'"
    (get-media-buy-delivery-response.json @ v3.1-04f59d2d5).

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING
    """

    @staticmethod
    def _poll_single(status: str) -> GetMediaBuyDeliveryResponse:
        buy = _make_mock_media_buy(
            media_buy_id=f"mb_{status}",
            status=status,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(media_buy_id=f"mb_{status}")
        req = GetMediaBuyDeliveryRequest(media_buy_ids=[f"mb_{status}"])
        return _run_impl_with_patches(req, adapter=mock_adapter, target_buys=[(f"mb_{status}", buy)])

    @pytest.mark.parametrize("status", ["rejected", "canceled", "failed", "completed"])
    def test_no_more_data_status_is_final_with_no_next_expected_at(self, status):
        response = self._poll_single(status)

        assert enum_value(response.media_buy_deliveries[0].status) == status
        assert enum_value(response.notification_type) == "final"
        assert response.next_expected_at is None

    def test_paused_buy_still_schedules_a_next_report(self):
        """paused is terminal for date-refinement but NOT for notifications —
        a paused buy may resume, so the next scheduled report is a truthful
        promise."""
        response = self._poll_single("paused")

        assert enum_value(response.media_buy_deliveries[0].status) == "paused"
        assert enum_value(response.notification_type) == "scheduled"
        assert response.next_expected_at is not None

    def test_mixed_terminal_and_active_is_scheduled(self):
        """One buy that will still report keeps the response non-final."""
        buy_done = _make_mock_media_buy(
            media_buy_id="mb_done",
            status="canceled",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        buy_live = _make_mock_media_buy(
            media_buy_id="mb_live",
            status="active",
            start_date=date(2025, 1, 1),
            end_date=date(2027, 12, 31),
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = [
            _make_adapter_response(media_buy_id="mb_done"),
            _make_adapter_response(media_buy_id="mb_live"),
        ]
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_done", "mb_live"])

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_done", buy_done), ("mb_live", buy_live)],
        )

        assert enum_value(response.notification_type) == "scheduled"
        assert response.next_expected_at is not None

    def test_all_terminal_mix_is_final(self):
        """completed + canceled together: nothing will report again -> final."""
        buy_a = _make_mock_media_buy(
            media_buy_id="mb_a",
            status="completed",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
        )
        buy_b = _make_mock_media_buy(
            media_buy_id="mb_b",
            status="canceled",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = [
            _make_adapter_response(media_buy_id="mb_a"),
            _make_adapter_response(media_buy_id="mb_b"),
        ]
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_a", "mb_b"])

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_a", buy_a), ("mb_b", buy_b)],
        )

        assert enum_value(response.notification_type) == "final"
        assert response.next_expected_at is None


class TestPersistedLifecycleIsAuthoritative:
    """A non-serving buy reports the status the row holds, whatever the dates say.

    This class used to be ``TestTimeSimulationReachesFinalNotification`` and its first
    three cases drove the delivery report from a SIMULATED clock — an ``AdCPTestContext``
    carrying ``mock_time`` or ``jump_to_event``, reached in production through the
    ``X-Mock-Time`` / ``X-Jump-To-Event`` request headers. That whole channel is gone
    (commit a1b79d22d): ``src/core/testing_hooks`` is deleted, a request carries no
    testing headers, the identity has no testing context, and ``apply_testing_hooks`` /
    ``NextEventCalculator`` / ``TimeSimulator`` do not exist — so "a buy created as
    pending_creatives can reach completed under simulation" is not a behaviour the
    server has any more, and neither are the two naive-vs-aware ``TypeError``
    regressions (#1545 K1) that only the simulated clock could reach.

    The fourth case is the one that graded the real clock, and it is the whole
    behaviour now: the persisted lifecycle is authoritative.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING
    """

    def test_without_simulation_persisted_pending_status_is_authoritative(self):
        """A pending buy, queried normally, keeps its persisted pending status."""
        buy = _make_mock_media_buy(
            media_buy_id="mb_real",
            status="pending_creatives",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
        )
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(media_buy_id="mb_real")

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_real"], start_date="2025-05-01", end_date="2025-06-01")

        response = _run_impl_with_patches(
            req,
            adapter=mock_adapter,
            target_buys=[("mb_real", buy)],
        )

        assert response.media_buy_deliveries[0].status == "pending_creatives"
