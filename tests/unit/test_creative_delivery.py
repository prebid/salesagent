"""Unit tests for get_creative_delivery tool (GH #1030).

Covers: _get_creative_delivery_impl main flow, error cases,
adapter integration, and schema validation.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AdapterCreativeDeliveryItem,
    AdapterGetCreativeDeliveryResponse,
    CreativeDeliveryData,
    DeliveryMetrics,
    GetCreativeDeliveryRequest,
    GetCreativeDeliveryResponse,
    ReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.creative_delivery import _get_creative_delivery_impl

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PATCH_PREFIX = "src.core.tools.creative_delivery"


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


def _make_mock_buy(media_buy_id: str = "mb_1", buyer_ref: str | None = "ref_1") -> MagicMock:
    buy = MagicMock()
    buy.media_buy_id = media_buy_id
    buy.buyer_ref = buyer_ref
    buy.budget = Decimal("10000")
    buy.currency = "USD"
    buy.start_date = datetime(2025, 1, 1).date()
    buy.end_date = datetime(2025, 12, 31).date()
    buy.raw_request = {"buyer_ref": buyer_ref}
    return buy


def _make_adapter_response(
    media_buy_id: str = "mb_1",
    creative_ids: list[str] | None = None,
) -> AdapterGetCreativeDeliveryResponse:
    now = datetime.now(UTC)
    items = []
    for cid in creative_ids or ["cr_1", "cr_2"]:
        items.append(
            AdapterCreativeDeliveryItem(
                creative_id=cid,
                media_buy_id=media_buy_id,
                impressions=10000.0,
                clicks=200.0,
                spend=50.0,
                ctr=0.02,
            )
        )
    return AdapterGetCreativeDeliveryResponse(
        creatives=items,
        reporting_period=ReportingPeriod(start=now - timedelta(days=30), end=now),
        currency="USD",
    )


# ---------------------------------------------------------------------------
# Tests: Main flow
# ---------------------------------------------------------------------------


class TestCreativeDeliveryMainFlow:
    """Covers: main flow with media_buy_ids scoping."""

    @patch(f"{_PATCH_PREFIX}.get_adapter")
    @patch(f"{_PATCH_PREFIX}.MediaBuyUoW")
    @patch(f"{_PATCH_PREFIX}.get_principal_object")
    def test_returns_creative_delivery_for_media_buy_ids(self, mock_get_principal, mock_uow_cls, mock_get_adapter):
        # Covers: UC-010-CREATIVE-DELIVERY-MAIN-01
        mock_get_principal.return_value = MagicMock(principal_id="test_principal")

        # Setup UoW / repo
        mock_repo = MagicMock()
        mock_buy = _make_mock_buy("mb_1")
        mock_repo.get_by_principal.return_value = [mock_buy]
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        # Setup adapter
        mock_adapter = MagicMock()
        mock_adapter.get_creative_delivery.return_value = _make_adapter_response("mb_1")
        mock_get_adapter.return_value = mock_adapter

        req = GetCreativeDeliveryRequest(media_buy_ids=["mb_1"])
        identity = _make_identity()

        result = _get_creative_delivery_impl(req, identity)

        assert isinstance(result, GetCreativeDeliveryResponse)
        assert len(result.creatives) == 2
        assert result.creatives[0].creative_id == "cr_1"
        assert result.creatives[0].media_buy_id == "mb_1"
        assert result.creatives[0].totals is not None
        assert result.creatives[0].totals.impressions == 10000.0
        assert result.currency == "USD"
        assert result.errors is None

    @patch(f"{_PATCH_PREFIX}.get_adapter")
    @patch(f"{_PATCH_PREFIX}.MediaBuyUoW")
    @patch(f"{_PATCH_PREFIX}.get_principal_object")
    def test_returns_creative_delivery_for_buyer_refs(self, mock_get_principal, mock_uow_cls, mock_get_adapter):
        # Covers: UC-010-CREATIVE-DELIVERY-MAIN-02
        mock_get_principal.return_value = MagicMock(principal_id="test_principal")

        mock_repo = MagicMock()
        mock_buy = _make_mock_buy("mb_1", buyer_ref="ref_1")
        mock_repo.get_by_principal.return_value = [mock_buy]
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        mock_adapter = MagicMock()
        mock_adapter.get_creative_delivery.return_value = _make_adapter_response("mb_1")
        mock_get_adapter.return_value = mock_adapter

        req = GetCreativeDeliveryRequest(media_buy_buyer_refs=["ref_1"])
        identity = _make_identity()

        result = _get_creative_delivery_impl(req, identity)

        assert isinstance(result, GetCreativeDeliveryResponse)
        assert len(result.creatives) == 2
        # Verify repo was called with buyer_refs
        mock_repo.get_by_principal.assert_called_once_with("test_principal", buyer_refs=["ref_1"])

    @patch(f"{_PATCH_PREFIX}.get_adapter")
    @patch(f"{_PATCH_PREFIX}.MediaBuyUoW")
    @patch(f"{_PATCH_PREFIX}.get_principal_object")
    def test_filters_by_creative_ids(self, mock_get_principal, mock_uow_cls, mock_get_adapter):
        # Covers: UC-010-CREATIVE-DELIVERY-FILTER-01
        mock_get_principal.return_value = MagicMock(principal_id="test_principal")

        mock_repo = MagicMock()
        mock_buy = _make_mock_buy("mb_1")
        mock_repo.get_by_principal.return_value = [mock_buy]
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        # Adapter returns both cr_1 and cr_2, but we only want cr_1
        mock_adapter = MagicMock()
        mock_adapter.get_creative_delivery.return_value = _make_adapter_response("mb_1", ["cr_1", "cr_2"])
        mock_get_adapter.return_value = mock_adapter

        req = GetCreativeDeliveryRequest(media_buy_ids=["mb_1"], creative_ids=["cr_1"])
        identity = _make_identity()

        result = _get_creative_delivery_impl(req, identity)

        assert len(result.creatives) == 1
        assert result.creatives[0].creative_id == "cr_1"


# ---------------------------------------------------------------------------
# Tests: Error cases
# ---------------------------------------------------------------------------


class TestCreativeDeliveryErrors:
    """Covers: error handling and edge cases."""

    def test_no_identity_raises_validation_error(self):
        # Covers: UC-010-CREATIVE-DELIVERY-ERR-01
        from src.core.exceptions import AdCPValidationError

        req = GetCreativeDeliveryRequest(media_buy_ids=["mb_1"])
        with pytest.raises(AdCPValidationError):
            _get_creative_delivery_impl(req, None)

    @patch(f"{_PATCH_PREFIX}.get_principal_object")
    def test_missing_principal_returns_error_response(self, mock_get_principal):
        # Covers: UC-010-CREATIVE-DELIVERY-ERR-02
        mock_get_principal.return_value = None
        identity = _make_identity(principal_id="nonexistent")
        req = GetCreativeDeliveryRequest(media_buy_ids=["mb_1"])

        result = _get_creative_delivery_impl(req, identity)

        assert isinstance(result, GetCreativeDeliveryResponse)
        assert len(result.creatives) == 0
        assert result.errors is not None
        assert result.errors[0].code == "principal_not_found"

    def test_no_principal_id_returns_error_response(self):
        # Covers: UC-010-CREATIVE-DELIVERY-ERR-03
        identity = ResolvedIdentity(
            principal_id=None,
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant"},
            protocol="mcp",
        )
        req = GetCreativeDeliveryRequest(media_buy_ids=["mb_1"])

        result = _get_creative_delivery_impl(req, identity)

        assert result.errors is not None
        assert result.errors[0].code == "principal_id_missing"

    @patch(f"{_PATCH_PREFIX}.get_principal_object")
    def test_no_scoping_filter_raises_validation_error(self, mock_get_principal):
        # Covers: UC-010-CREATIVE-DELIVERY-ERR-04
        from src.core.exceptions import AdCPValidationError

        mock_get_principal.return_value = MagicMock()
        identity = _make_identity()
        req = GetCreativeDeliveryRequest()

        with pytest.raises(AdCPValidationError, match="(?i)scoping filter"):
            _get_creative_delivery_impl(req, identity)

    def test_no_tenant_raises_auth_error(self):
        # Covers: UC-010-CREATIVE-DELIVERY-ERR-05
        from src.core.exceptions import AdCPAuthenticationError

        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_tenant",
            tenant=None,
            protocol="mcp",
        )
        req = GetCreativeDeliveryRequest(media_buy_ids=["mb_1"])

        with patch(f"{_PATCH_PREFIX}.get_principal_object") as mock_get_principal:
            mock_get_principal.return_value = MagicMock()
            with pytest.raises(AdCPAuthenticationError):
                _get_creative_delivery_impl(req, identity)

    @patch(f"{_PATCH_PREFIX}.get_adapter")
    @patch(f"{_PATCH_PREFIX}.MediaBuyUoW")
    @patch(f"{_PATCH_PREFIX}.get_principal_object")
    def test_invalid_date_range_returns_error(self, mock_get_principal, mock_uow_cls, mock_get_adapter):
        # Covers: UC-010-CREATIVE-DELIVERY-ERR-06
        mock_get_principal.return_value = MagicMock()
        identity = _make_identity()
        req = GetCreativeDeliveryRequest(
            media_buy_ids=["mb_1"],
            start_date="2025-12-31",
            end_date="2025-01-01",
        )

        result = _get_creative_delivery_impl(req, identity)

        assert result.errors is not None
        assert result.errors[0].code == "invalid_date_range"

    @patch(f"{_PATCH_PREFIX}.get_adapter")
    @patch(f"{_PATCH_PREFIX}.MediaBuyUoW")
    @patch(f"{_PATCH_PREFIX}.get_principal_object")
    def test_media_buy_not_found_returns_error(self, mock_get_principal, mock_uow_cls, mock_get_adapter):
        # Covers: UC-010-CREATIVE-DELIVERY-ERR-07
        mock_get_principal.return_value = MagicMock(principal_id="test_principal")

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = []  # No buys found
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetCreativeDeliveryRequest(media_buy_ids=["nonexistent_mb"])
        identity = _make_identity()

        result = _get_creative_delivery_impl(req, identity)

        assert result.errors is not None
        assert any(e.code == "media_buy_not_found" for e in result.errors)
        assert len(result.creatives) == 0


# ---------------------------------------------------------------------------
# Tests: Adapter integration
# ---------------------------------------------------------------------------


class TestCreativeDeliveryAdapterIntegration:
    """Covers: adapter interaction patterns."""

    @patch(f"{_PATCH_PREFIX}.get_adapter")
    @patch(f"{_PATCH_PREFIX}.MediaBuyUoW")
    @patch(f"{_PATCH_PREFIX}.get_principal_object")
    def test_adapter_not_implemented_handled_gracefully(self, mock_get_principal, mock_uow_cls, mock_get_adapter):
        # Covers: UC-010-CREATIVE-DELIVERY-ADAPTER-01
        mock_get_principal.return_value = MagicMock(principal_id="test_principal")

        mock_repo = MagicMock()
        mock_buy = _make_mock_buy("mb_1")
        mock_repo.get_by_principal.return_value = [mock_buy]
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        mock_adapter = MagicMock()
        mock_adapter.get_creative_delivery.side_effect = NotImplementedError("Not supported")
        mock_get_adapter.return_value = mock_adapter

        req = GetCreativeDeliveryRequest(media_buy_ids=["mb_1"])
        identity = _make_identity()

        result = _get_creative_delivery_impl(req, identity)

        # Should return empty creatives without error (graceful degradation)
        assert isinstance(result, GetCreativeDeliveryResponse)
        assert len(result.creatives) == 0
        assert result.errors is None

    @patch(f"{_PATCH_PREFIX}.get_adapter")
    @patch(f"{_PATCH_PREFIX}.MediaBuyUoW")
    @patch(f"{_PATCH_PREFIX}.get_principal_object")
    def test_adapter_error_reports_error(self, mock_get_principal, mock_uow_cls, mock_get_adapter):
        # Covers: UC-010-CREATIVE-DELIVERY-ADAPTER-02
        mock_get_principal.return_value = MagicMock(principal_id="test_principal")

        mock_repo = MagicMock()
        mock_buy = _make_mock_buy("mb_1")
        mock_repo.get_by_principal.return_value = [mock_buy]
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        mock_adapter = MagicMock()
        mock_adapter.get_creative_delivery.side_effect = RuntimeError("API error")
        mock_get_adapter.return_value = mock_adapter

        req = GetCreativeDeliveryRequest(media_buy_ids=["mb_1"])
        identity = _make_identity()

        result = _get_creative_delivery_impl(req, identity)

        assert result.errors is not None
        assert any(e.code == "adapter_error" for e in result.errors)

    @patch(f"{_PATCH_PREFIX}.get_adapter")
    @patch(f"{_PATCH_PREFIX}.MediaBuyUoW")
    @patch(f"{_PATCH_PREFIX}.get_principal_object")
    def test_multiple_media_buys_aggregated(self, mock_get_principal, mock_uow_cls, mock_get_adapter):
        # Covers: UC-010-CREATIVE-DELIVERY-MAIN-03
        mock_get_principal.return_value = MagicMock(principal_id="test_principal")

        mock_repo = MagicMock()
        mock_buy_1 = _make_mock_buy("mb_1")
        mock_buy_2 = _make_mock_buy("mb_2")
        mock_repo.get_by_principal.return_value = [mock_buy_1, mock_buy_2]
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        mock_adapter = MagicMock()
        mock_adapter.get_creative_delivery.side_effect = [
            _make_adapter_response("mb_1", ["cr_1"]),
            _make_adapter_response("mb_2", ["cr_2"]),
        ]
        mock_get_adapter.return_value = mock_adapter

        req = GetCreativeDeliveryRequest(media_buy_ids=["mb_1", "mb_2"])
        identity = _make_identity()

        result = _get_creative_delivery_impl(req, identity)

        assert len(result.creatives) == 2
        creative_ids = {c.creative_id for c in result.creatives}
        assert creative_ids == {"cr_1", "cr_2"}


# ---------------------------------------------------------------------------
# Tests: Schema validation
# ---------------------------------------------------------------------------


class TestCreativeDeliverySchemas:
    """Covers: schema construction and serialization."""

    def test_get_creative_delivery_request_requires_scoping_filter(self):
        # Covers: UC-010-CREATIVE-DELIVERY-SCHEMA-01
        # All filters are optional at the Pydantic level (validation in _impl)
        req = GetCreativeDeliveryRequest()
        assert req.media_buy_ids is None
        assert req.media_buy_buyer_refs is None
        assert req.creative_ids is None

    def test_creative_delivery_data_construction(self):
        # Covers: UC-010-CREATIVE-DELIVERY-SCHEMA-02
        data = CreativeDeliveryData(
            creative_id="cr_1",
            media_buy_id="mb_1",
            totals=DeliveryMetrics(impressions=5000.0, clicks=100.0, spend=25.0, ctr=0.02),
            variant_count=0,
            variants=[],
        )
        assert data.creative_id == "cr_1"
        assert data.totals.impressions == 5000.0
        assert data.totals.ctr == 0.02

    def test_response_str_single_creative(self):
        # Covers: UC-010-CREATIVE-DELIVERY-SCHEMA-03
        now = datetime.now(UTC)
        resp = GetCreativeDeliveryResponse(
            reporting_period={"start": now, "end": now},
            currency="USD",
            creatives=[
                CreativeDeliveryData(
                    creative_id="cr_1",
                    variant_count=0,
                    variants=[],
                )
            ],
        )
        assert "1 creative" in str(resp)

    def test_response_str_multiple_creatives(self):
        # Covers: UC-010-CREATIVE-DELIVERY-SCHEMA-04
        now = datetime.now(UTC)
        resp = GetCreativeDeliveryResponse(
            reporting_period={"start": now, "end": now},
            currency="USD",
            creatives=[
                CreativeDeliveryData(creative_id="cr_1", variant_count=0, variants=[]),
                CreativeDeliveryData(creative_id="cr_2", variant_count=0, variants=[]),
            ],
        )
        assert "2 creatives" in str(resp)

    def test_response_str_no_creatives(self):
        # Covers: UC-010-CREATIVE-DELIVERY-SCHEMA-05
        now = datetime.now(UTC)
        resp = GetCreativeDeliveryResponse(
            reporting_period={"start": now, "end": now},
            currency="USD",
            creatives=[],
        )
        assert "No creative delivery data" in str(resp)

    def test_delivery_metrics_inherits_from_library(self):
        # Covers: UC-010-CREATIVE-DELIVERY-SCHEMA-06
        from adcp.types import DeliveryMetrics as LibraryDeliveryMetrics

        assert issubclass(DeliveryMetrics, LibraryDeliveryMetrics)

    def test_response_inherits_from_library(self):
        # Covers: UC-010-CREATIVE-DELIVERY-SCHEMA-07
        from adcp.types import GetCreativeDeliveryResponse as LibraryGetCreativeDeliveryResponse

        assert issubclass(GetCreativeDeliveryResponse, LibraryGetCreativeDeliveryResponse)
