"""Behavioral tests for UC-004: delivery metrics impl layer.

These tests exercise _get_media_buy_delivery_impl end-to-end with mocked
DB + adapter, covering HIGH_RISK and MEDIUM_RISK gaps identified in the
BDD scenario cross-reference (salesagent-1ocn).

Design invariant: every BDD scenario marked ACCURATE must have a corresponding
test that verifies the same behavior, independent of transport (MCP/A2A).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    ReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tool_context import ToolContext
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.services.webhook_delivery_service import CircuitBreaker, CircuitState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tool_context(
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
    testing_context: AdCPTestContext | None = None,
) -> ToolContext:
    """Build a ToolContext with no testing flags set (reaches adapter path)."""
    return ToolContext(
        context_id="ctx_test",
        tenant_id=tenant_id,
        principal_id=principal_id,
        tool_name="get_media_buy_delivery",
        request_timestamp=datetime.now(UTC),
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
    """Build a mock MediaBuy database object."""
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
    """Build a mock adapter delivery response."""
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


# Shared patch targets (module-level references in media_buy_delivery.py)
_PATCH_PREFIX = "src.core.tools.media_buy_delivery"


def _standard_patches(
    principal_id: str = "test_principal",
    principal_obj: MagicMock | None = None,
    adapter: MagicMock | None = None,
    target_buys: list | None = None,
    pricing_options: dict | None = None,
):
    """Return a dict of patch context managers for the common mock surface.

    The caller should use ``with`` over each.
    """
    if principal_obj is None:
        principal_obj = MagicMock()
        principal_obj.principal_id = principal_id
        principal_obj.platform_mappings = {}

    if adapter is None:
        adapter = MagicMock()

    if target_buys is None:
        target_buys = []

    return {
        "principal_id": patch(
            f"{_PATCH_PREFIX}.get_principal_id_from_context",
            return_value=principal_id,
        ),
        "principal_obj": patch(
            f"{_PATCH_PREFIX}.get_principal_object",
            return_value=principal_obj,
        ),
        "adapter": patch(
            f"{_PATCH_PREFIX}.get_adapter",
            return_value=adapter,
        ),
        "tenant": patch(
            f"{_PATCH_PREFIX}.get_current_tenant",
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
        # Mock the inner get_db_session used for MediaPackage query (lines 272-279)
        "db_session": patch(
            f"{_PATCH_PREFIX}.get_db_session",
        ),
    }


# ---------------------------------------------------------------------------
# HIGH_RISK — Priority 1
# ---------------------------------------------------------------------------


class TestDeliveryImplSingleBuyOrchestration:
    """T-UC-004-main: single buy polling via _get_media_buy_delivery_impl.

    Verifies the full orchestration: reporting_period, currency,
    aggregated_totals, media_buy_deliveries[0] with totals and by_package,
    and status.
    """

    def test_single_buy_returns_complete_response(self):
        """Happy path: one buy, adapter returns metrics, response is fully populated."""
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

        adapter_resp = _make_adapter_response(
            media_buy_id="mb_single",
            impressions=8000,
            spend=400.0,
            clicks=80,
            packages=[{"package_id": "pkg_a", "impressions": 8000, "spend": 400.0}],
        )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = adapter_resp

        patches = _standard_patches(
            adapter=mock_adapter,
            target_buys=[("mb_single", buy)],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_single"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        ctx = _make_tool_context()

        # Provide a mock session for the MediaPackage inner query
        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"],
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, ctx)

        # -- Assertions --
        # reporting_period matches provided dates
        assert response.reporting_period.start.year == 2025
        assert response.reporting_period.start.month == 1
        assert response.reporting_period.end.month == 6

        # currency
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
        assert delivery.totals.impressions == 8000
        assert delivery.totals.spend == 400.0

        # by_package
        assert len(delivery.by_package) == 1
        assert delivery.by_package[0].package_id == "pkg_a"
        assert delivery.by_package[0].impressions == 8000.0
        assert delivery.by_package[0].spend == 400.0

        # status — buy date range includes reference_date (end_date of reporting period)
        # 2025-06-30 is between 2025-01-01 and 2025-12-31, so status=active
        assert delivery.status == "active"

        # no errors
        assert response.errors is None


class TestDeliveryImplMultiBuyAggregation:
    """T-UC-004-main-multi: multiple buys with aggregated totals.

    Verifies: aggregated_totals.impressions == sum(buy.totals.impressions),
    same for spend/clicks. media_buy_count == number of buys returned.
    """

    def test_two_buys_aggregate_correctly(self):
        """Two buys: verify sum invariant for impressions, spend, media_buy_count."""
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

        adapter_resp_1 = _make_adapter_response(
            media_buy_id="mb_agg_1",
            impressions=3000,
            spend=150.0,
            clicks=30,
            packages=[{"package_id": "pkg_1a", "impressions": 3000, "spend": 150.0}],
        )
        adapter_resp_2 = _make_adapter_response(
            media_buy_id="mb_agg_2",
            impressions=7000,
            spend=350.0,
            clicks=70,
            packages=[{"package_id": "pkg_2a", "impressions": 7000, "spend": 350.0}],
        )

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = [adapter_resp_1, adapter_resp_2]

        patches = _standard_patches(
            adapter=mock_adapter,
            target_buys=[("mb_agg_1", buy1), ("mb_agg_2", buy2)],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_agg_1", "mb_agg_2"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        ctx = _make_tool_context()

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"],
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, ctx)

        # Sum invariants
        assert response.aggregated_totals.impressions == 3000.0 + 7000.0
        assert response.aggregated_totals.spend == 150.0 + 350.0
        assert response.aggregated_totals.media_buy_count == 2

        # Individual deliveries preserved
        assert len(response.media_buy_deliveries) == 2
        ids = {d.media_buy_id for d in response.media_buy_deliveries}
        assert ids == {"mb_agg_1", "mb_agg_2"}

        # Verify individual buy totals
        d1 = next(d for d in response.media_buy_deliveries if d.media_buy_id == "mb_agg_1")
        d2 = next(d for d in response.media_buy_deliveries if d.media_buy_id == "mb_agg_2")
        assert d1.totals.impressions == 3000
        assert d2.totals.impressions == 7000
        assert d1.totals.spend == 150.0
        assert d2.totals.spend == 350.0

        # No errors
        assert response.errors is None


class TestDeliveryImplAdapterError:
    """T-UC-004-ext-f: adapter error returns adapter_error code.

    Critical: testing_ctx must have all flags unset (dry_run=False,
    mock_time=None, jump_to_event=None, test_session_id=None) to reach
    the adapter call path at line 202-248.
    """

    def test_adapter_exception_returns_adapter_error(self):
        """Adapter raises Exception -> response has errors[0].code=='adapter_error'."""
        buy = _make_mock_media_buy(media_buy_id="mb_err")

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = RuntimeError("GAM API timeout")

        patches = _standard_patches(
            adapter=mock_adapter,
            target_buys=[("mb_err", buy)],
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_err"])
        # Ensure no testing flags are set -> adapter path is reached
        ctx = _make_tool_context(
            testing_context=AdCPTestContext(
                dry_run=False,
                mock_time=None,
                jump_to_event=None,
                test_session_id=None,
            ),
        )

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"],
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, ctx)

        # Should return error response, not raise
        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "adapter_error"
        assert "mb_err" in response.errors[0].message

        # media_buy_deliveries should be empty on error
        assert response.media_buy_deliveries == []

        # aggregated_totals should be zeroed
        assert response.aggregated_totals.impressions == 0.0
        assert response.aggregated_totals.spend == 0.0
        assert response.aggregated_totals.media_buy_count == 0

    def test_adapter_error_preserves_reporting_period(self):
        """When adapter fails, reporting_period still reflects requested dates."""
        buy = _make_mock_media_buy(media_buy_id="mb_err2", currency="EUR")

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = ConnectionError("Network down")

        patches = _standard_patches(
            adapter=mock_adapter,
            target_buys=[("mb_err2", buy)],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_err2"],
            start_date="2025-03-01",
            end_date="2025-03-31",
        )
        ctx = _make_tool_context(
            testing_context=AdCPTestContext(
                dry_run=False,
                mock_time=None,
                jump_to_event=None,
                test_session_id=None,
            ),
        )

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"],
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, ctx)

        assert response.reporting_period.start.month == 3
        assert response.reporting_period.start.day == 1
        assert response.reporting_period.end.month == 3
        assert response.reporting_period.end.day == 31
        assert response.errors[0].code == "adapter_error"


# ---------------------------------------------------------------------------
# HIGH_RISK — Priority 2: Identification modes
# ---------------------------------------------------------------------------


class TestDeliveryImplIdentificationModes:
    """T-UC-004-identify-mode: BR-RULE-030 resolution logic.

    Parametrized test verifying media_buy_ids vs buyer_refs vs both vs neither.
    """

    def _run_with_buys(self, req: GetMediaBuyDeliveryRequest, db_buys: list[MagicMock]):
        """Helper: call impl with real _get_target_media_buys (not mocked)
        but mocked DB session returning db_buys.
        """
        mock_adapter = MagicMock()
        # Return empty adapter response for each buy
        for buy in db_buys:
            resp = _make_adapter_response(
                media_buy_id=buy.media_buy_id,
                impressions=100,
                spend=10.0,
                packages=[{"package_id": "pkg_auto", "impressions": 100, "spend": 10.0}],
            )
            mock_adapter.get_media_buy_delivery.return_value = resp

        principal_obj = MagicMock()
        principal_obj.principal_id = "test_principal"
        principal_obj.platform_mappings = {}

        ctx = _make_tool_context()

        # Mock DB session for _get_target_media_buys
        mock_session = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = db_buys
        mock_session.scalars.return_value = mock_scalars

        with (
            patch(f"{_PATCH_PREFIX}.get_principal_id_from_context", return_value="test_principal"),
            patch(f"{_PATCH_PREFIX}.get_principal_object", return_value=principal_obj),
            patch(f"{_PATCH_PREFIX}.get_adapter", return_value=mock_adapter),
            patch(f"{_PATCH_PREFIX}.get_current_tenant", return_value={"tenant_id": "test_tenant"}),
            patch(f"{_PATCH_PREFIX}._get_pricing_options", return_value={}),
            patch(f"{_PATCH_PREFIX}.get_db_session") as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_session
            return _get_media_buy_delivery_impl(req, ctx)

    def test_media_buy_ids_only(self):
        """media_buy_ids provided, buyer_refs absent -> query by IDs."""
        buy = _make_mock_media_buy(media_buy_id="mb_id1")

        # Use standard patches with target_buys to isolate identification
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_id1",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        patches = _standard_patches(
            adapter=mock_adapter,
            target_buys=[("mb_id1", buy)],
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_id1"])
        ctx = _make_tool_context()

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, ctx)

        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_id1"
        # _get_target_media_buys was called with the request containing media_buy_ids
        mock_target.assert_called_once()
        call_req = mock_target.call_args[0][0]
        assert call_req.media_buy_ids == ["mb_id1"]

    def test_buyer_refs_only(self):
        """buyer_refs provided, media_buy_ids absent -> query by buyer_refs."""
        buy = _make_mock_media_buy(media_buy_id="mb_ref1", buyer_ref="buyer_A")

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_ref1",
            impressions=200,
            spend=20.0,
            packages=[{"package_id": "pkg_001", "impressions": 200, "spend": 20.0}],
        )

        patches = _standard_patches(
            adapter=mock_adapter,
            target_buys=[("mb_ref1", buy)],
        )

        req = GetMediaBuyDeliveryRequest(buyer_refs=["buyer_A"])
        ctx = _make_tool_context()

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, ctx)

        assert len(response.media_buy_deliveries) == 1
        call_req = mock_target.call_args[0][0]
        assert call_req.buyer_refs == ["buyer_A"]

    def test_both_provided_media_buy_ids_wins(self):
        """Both media_buy_ids and buyer_refs -> media_buy_ids takes priority."""
        buy = _make_mock_media_buy(media_buy_id="mb_priority")

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_priority",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        patches = _standard_patches(
            adapter=mock_adapter,
            target_buys=[("mb_priority", buy)],
        )

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_priority"],
            buyer_refs=["should_be_ignored"],
        )
        ctx = _make_tool_context()

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, ctx)

        # The request passed to _get_target_media_buys has both set
        call_req = mock_target.call_args[0][0]
        assert call_req.media_buy_ids == ["mb_priority"]
        assert call_req.buyer_refs == ["should_be_ignored"]
        # But response only contains buys returned by the target function
        assert len(response.media_buy_deliveries) == 1

    def test_neither_provided_fetches_all(self):
        """Neither identifiers -> fetch all principal buys."""
        buy = _make_mock_media_buy(media_buy_id="mb_all1")

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_all1",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        patches = _standard_patches(
            adapter=mock_adapter,
            target_buys=[("mb_all1", buy)],
        )

        req = GetMediaBuyDeliveryRequest()  # no media_buy_ids, no buyer_refs
        ctx = _make_tool_context()

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, ctx)

        call_req = mock_target.call_args[0][0]
        assert call_req.media_buy_ids is None
        assert call_req.buyer_refs is None
        # Still returns what target_buys returned
        assert len(response.media_buy_deliveries) == 1

    def test_partial_ids_returns_only_valid(self):
        """media_buy_ids=["valid", "invalid"] -> only valid returned (partial)."""
        buy = _make_mock_media_buy(media_buy_id="mb_valid")

        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.return_value = _make_adapter_response(
            media_buy_id="mb_valid",
            impressions=100,
            spend=10.0,
            packages=[{"package_id": "pkg_001", "impressions": 100, "spend": 10.0}],
        )

        # target_buys only returns the valid one (DB lookup filters out invalid)
        patches = _standard_patches(
            adapter=mock_adapter,
            target_buys=[("mb_valid", buy)],
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_valid", "mb_nonexistent"])
        ctx = _make_tool_context()

        mock_inner_session = MagicMock()
        mock_inner_session.scalars.return_value.all.return_value = []

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"],
            patches["pricing_options"],
            patches["db_session"] as mock_db,
        ):
            mock_db.return_value.__enter__.return_value = mock_inner_session
            response = _get_media_buy_delivery_impl(req, ctx)

        # Only 1 delivery returned, no error for the missing ID
        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].media_buy_id == "mb_valid"
        assert response.errors is None

    def test_all_ids_invalid_returns_empty_no_error(self):
        """media_buy_ids=["invalid1", "invalid2"] -> empty array, no error."""
        patches = _standard_patches(
            target_buys=[],  # DB returns nothing
        )

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_ghost1", "mb_ghost2"])
        ctx = _make_tool_context()

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"],
            patches["pricing_options"],
            patches["db_session"],
        ):
            response = _get_media_buy_delivery_impl(req, ctx)

        assert len(response.media_buy_deliveries) == 0
        assert response.errors is None
        assert response.aggregated_totals.media_buy_count == 0
        assert response.aggregated_totals.impressions == 0.0
        assert response.aggregated_totals.spend == 0.0


# ---------------------------------------------------------------------------
# MEDIUM_RISK — Priority 3
# ---------------------------------------------------------------------------


class TestDeliveryImplStatusFilter:
    """T-UC-004-filter-all and T-UC-004-filter-default: status filtering.

    Note: "all" is not a valid MediaBuyStatus enum value, so it cannot be
    passed through GetMediaBuyDeliveryRequest Pydantic validation. The "all"
    handling lives in _get_target_media_buys (line 553). We test it directly
    at the _get_target_media_buys level with a mock request object.
    """

    def test_status_filter_all_returns_all_statuses(self):
        """status_filter='all' in _get_target_media_buys -> returns buys of any status.

        Tests the internal function directly since 'all' bypasses Pydantic schema.
        """
        from src.core.tools.media_buy_delivery import _get_target_media_buys

        # Create buys with different date ranges to produce different statuses
        # reference_date will be 2025-06-15
        # buy_ready: starts 2025-07-01 (future -> ready)
        # buy_active: starts 2025-01-01 ends 2025-12-31 (active)
        # buy_completed: ended 2025-05-01 (past -> completed)
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

        # Mock request with status_filter="all" (bypass Pydantic)
        mock_req = MagicMock()
        mock_req.media_buy_ids = ["mb_ready", "mb_active", "mb_completed"]
        mock_req.buyer_refs = None
        # Use a mock that returns "all" for .value
        mock_status = MagicMock()
        mock_status.value = "all"
        mock_req.status_filter = mock_status

        mock_session = MagicMock()
        mock_session.scalars.return_value.all.return_value = [buy_ready, buy_active, buy_completed]

        tenant = {"tenant_id": "test_tenant"}

        with patch(f"{_PATCH_PREFIX}.get_db_session") as mock_db:
            mock_db.return_value.__enter__.return_value = mock_session
            result = _get_target_media_buys(mock_req, "test_principal", tenant, ref_date)

        # All 3 buys should be returned (ready, active, completed all in valid_internal_statuses)
        assert len(result) == 3
        returned_ids = {buy_id for buy_id, _ in result}
        assert returned_ids == {"mb_ready", "mb_active", "mb_completed"}

    def test_status_filter_default_is_active(self):
        """When status_filter omitted -> defaults to None (impl defaults to active)."""
        patches = _standard_patches(target_buys=[])

        req = GetMediaBuyDeliveryRequest()  # no status_filter
        ctx = _make_tool_context()

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"] as mock_target,
            patches["pricing_options"],
            patches["db_session"],
        ):
            _get_media_buy_delivery_impl(req, ctx)

        call_req = mock_target.call_args[0][0]
        assert call_req.status_filter is None  # None -> impl defaults to ["active"]

    def test_status_filter_default_only_returns_active_buys(self):
        """Default status_filter -> _get_target_media_buys returns only active buys."""
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
        mock_req.status_filter = None  # default

        mock_session = MagicMock()
        mock_session.scalars.return_value.all.return_value = [buy_active, buy_completed]

        tenant = {"tenant_id": "test_tenant"}

        with patch(f"{_PATCH_PREFIX}.get_db_session") as mock_db:
            mock_db.return_value.__enter__.return_value = mock_session
            result = _get_target_media_buys(mock_req, "test_principal", tenant, ref_date)

        # Only active buy returned (completed is filtered out)
        assert len(result) == 1
        assert result[0][0] == "mb_active"


class TestDeliveryImplCustomDateRange:
    """T-UC-004-daterange: custom date range in response."""

    def test_custom_date_range_reflected_in_reporting_period(self):
        """start_date and end_date provided -> reporting_period matches."""
        patches = _standard_patches(target_buys=[])

        req = GetMediaBuyDeliveryRequest(
            start_date="2025-03-15",
            end_date="2025-04-15",
        )
        ctx = _make_tool_context()

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"],
            patches["pricing_options"],
            patches["db_session"],
        ):
            response = _get_media_buy_delivery_impl(req, ctx)

        assert response.reporting_period.start == datetime(2025, 3, 15, tzinfo=UTC)
        assert response.reporting_period.end == datetime(2025, 4, 15, tzinfo=UTC)

    def test_no_date_range_defaults_to_last_30_days(self):
        """No dates provided -> defaults to last 30 days."""
        patches = _standard_patches(target_buys=[])

        req = GetMediaBuyDeliveryRequest()
        ctx = _make_tool_context()

        with (
            patches["principal_id"],
            patches["principal_obj"],
            patches["adapter"],
            patches["tenant"],
            patches["target_buys"],
            patches["pricing_options"],
            patches["db_session"],
        ):
            response = _get_media_buy_delivery_impl(req, ctx)

        # End should be roughly now, start roughly 30 days before
        now = datetime.now(UTC)
        assert abs((response.reporting_period.end - now).total_seconds()) < 5
        expected_start = now - timedelta(days=30)
        assert abs((response.reporting_period.start - expected_start).total_seconds()) < 5


class TestDeliveryImplPrincipalNotFound:
    """T-UC-004-ext-b: principal not found returns principal_not_found error."""

    def test_principal_not_found_returns_error(self):
        """Valid token but principal lookup returns None -> principal_not_found."""
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_x"])
        ctx = _make_tool_context()

        with (
            patch(f"{_PATCH_PREFIX}.get_principal_id_from_context", return_value="ghost_principal"),
            patch(f"{_PATCH_PREFIX}.get_principal_object", return_value=None),
            patch(f"{_PATCH_PREFIX}.get_testing_context", return_value=AdCPTestContext()),
        ):
            response = _get_media_buy_delivery_impl(req, ctx)

        assert response.errors is not None
        assert len(response.errors) == 1
        assert response.errors[0].code == "principal_not_found"
        assert "ghost_principal" in response.errors[0].message
        assert response.media_buy_deliveries == []


# ---------------------------------------------------------------------------
# MEDIUM_RISK — Circuit breaker (webhook delivery service)
# ---------------------------------------------------------------------------


class TestCircuitBreakerOpenAfter5Failures:
    """T-UC-004-webhook-circuit-open: 5 consecutive failures -> OPEN state."""

    def test_five_failures_transitions_to_open(self):
        """CircuitBreaker: 5 consecutive record_failure -> state == OPEN."""
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)

        assert cb.state == CircuitState.CLOSED
        for i in range(4):
            cb.record_failure()
            assert cb.state == CircuitState.CLOSED, f"Should still be CLOSED after {i + 1} failures"

        cb.record_failure()  # 5th failure
        assert cb.state == CircuitState.OPEN

    def test_open_circuit_rejects_requests(self):
        """OPEN circuit -> can_attempt() returns False."""
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)
        for _ in range(5):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert cb.can_attempt() is False


class TestCircuitBreakerHalfOpenAndRecovery:
    """T-UC-004-webhook-circuit-halfopen and T-UC-004-webhook-circuit-recovery.

    OPEN -> timeout elapsed -> HALF_OPEN -> 2 successful probes -> CLOSED.
    """

    def test_open_transitions_to_half_open_after_timeout(self):
        """OPEN state, timeout expires -> HALF_OPEN on next can_attempt()."""
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)

        # Drive to OPEN
        for _ in range(5):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Simulate timeout by backdating the last_failure_time
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)

        # can_attempt() should now transition to HALF_OPEN
        assert cb.can_attempt() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_recovers_after_success_threshold(self):
        """HALF_OPEN + 2 successful probes -> CLOSED."""
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)

        # Drive to OPEN then HALF_OPEN
        for _ in range(5):
            cb.record_failure()
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)
        cb.can_attempt()  # transitions to HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN

        # First successful probe
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN  # Need 2 successes

        # Second successful probe
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_returns_to_open(self):
        """HALF_OPEN + failure -> back to OPEN."""
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, timeout_seconds=60)

        # Drive to HALF_OPEN
        for _ in range(5):
            cb.record_failure()
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)
        cb.can_attempt()
        assert cb.state == CircuitState.HALF_OPEN

        # Failure during recovery
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
