"""Tests for get_media_buys tool implementation.

Covers:
- Status computation from date fields (pending_activation, active, completed)
- Status filtering (default: active only; explicit filters; multiple statuses)
- Filtering by media_buy_ids and buyer_refs
- Creative approval mapping (approved, rejected, pending_review)
- include_snapshot=True/False path
- Auth / missing principal handling
- Response structure matches GetMediaBuysResponse
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus
from pydantic import RootModel

from src.core.schemas import (
    ApprovalStatus,
    CreativeApproval,
    DeliveryStatus,
    GetMediaBuysMediaBuy,
    GetMediaBuysPackage,
    GetMediaBuysRequest,
    GetMediaBuysResponse,
    Snapshot,
    SnapshotUnavailableReason,
)
from src.core.tool_context import ToolContext
from src.core.tools.media_buy_list import (
    _compute_status,
    _fetch_target_media_buys,
    _get_media_buys_impl,
    _map_creative_status,
    _resolve_status_filter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tool_context(tenant_id="tenant_1", principal_id="principal_1"):
    return ToolContext(
        tenant_id=tenant_id,
        principal_id=principal_id,
        testing_context={},
        context_id="test-ctx",
        tool_name="get_media_buys",
        request_timestamp="2025-01-01T00:00:00Z",
    )


def make_media_buy(
    media_buy_id="buy_1",
    principal_id="principal_1",
    tenant_id="tenant_1",
    buyer_ref="ref_1",
    start_date=date(2025, 1, 1),
    end_date=date(2025, 12, 31),
    start_time=None,
    end_time=None,
    budget=Decimal("10000"),
    currency="USD",
    raw_request=None,
):
    buy = MagicMock()
    buy.media_buy_id = media_buy_id
    buy.principal_id = principal_id
    buy.tenant_id = tenant_id
    buy.buyer_ref = buyer_ref
    buy.start_date = start_date
    buy.end_date = end_date
    buy.start_time = start_time
    buy.end_time = end_time
    buy.budget = budget
    buy.currency = currency
    buy.raw_request = raw_request or {}
    buy.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    buy.updated_at = datetime(2025, 1, 1, tzinfo=UTC)
    return buy


def make_package(
    media_buy_id="buy_1",
    package_id="pkg_1",
    budget=Decimal("5000"),
    bid_price=None,
    package_config=None,
):
    pkg = MagicMock()
    pkg.media_buy_id = media_buy_id
    pkg.package_id = package_id
    pkg.budget = budget
    pkg.bid_price = bid_price
    pkg.package_config = package_config or {}
    return pkg


# ---------------------------------------------------------------------------
# Unit tests for pure helper functions
# ---------------------------------------------------------------------------


class TestComputeStatus:
    def test_pending_activation_when_before_start(self):
        buy = make_media_buy(start_date=date(2099, 1, 1), end_date=date(2099, 12, 31))
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.pending_activation

    def test_active_when_in_flight(self):
        buy = make_media_buy(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.active

    def test_completed_when_past_end(self):
        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2020, 12, 31))
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.completed

    def test_prefers_start_time_over_start_date(self):
        """start_time (if set) takes precedence over start_date."""
        buy = make_media_buy(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            start_time=datetime(2099, 1, 1, tzinfo=UTC),
            end_time=datetime(2099, 12, 31, tzinfo=UTC),
        )
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.pending_activation


class TestResolveStatusFilter:
    def test_none_returns_active_only(self):
        result = _resolve_status_filter(None)
        assert result == {MediaBuyStatus.active}

    def test_single_status(self):
        result = _resolve_status_filter(MediaBuyStatus.completed)
        assert result == {MediaBuyStatus.completed}

    def test_list_of_statuses(self):
        result = _resolve_status_filter([MediaBuyStatus.active, MediaBuyStatus.completed])
        assert result == {MediaBuyStatus.active, MediaBuyStatus.completed}

    def test_root_model_style(self):
        """Handles RootModel wrapping a list (adcp SDK StatusFilter style)."""

        class StatusFilter(RootModel[list[MediaBuyStatus]]):
            pass

        result = _resolve_status_filter(StatusFilter([MediaBuyStatus.pending_activation]))
        assert result == {MediaBuyStatus.pending_activation}


class TestFetchTargetMediaBuys:
    """status_filter applies consistently regardless of which filter key is used."""

    TENANT = {"tenant_id": "tenant_1"}
    TODAY = date(2025, 6, 15)

    def _run(self, req, buys):
        mock_session = MagicMock()
        mock_session.__enter__ = lambda s: s
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.scalars.return_value.all.return_value = buys
        with patch("src.core.tools.media_buy_list.get_db_session", return_value=mock_session):
            return _fetch_target_media_buys(req, "principal_1", self.TENANT, self.TODAY)

    def test_media_buy_ids_with_status_filter_excludes_non_matching(self):
        active = make_media_buy("buy_active", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        completed = make_media_buy("buy_done", start_date=date(2020, 1, 1), end_date=date(2020, 12, 31))
        req = GetMediaBuysRequest(
            media_buy_ids=["buy_active", "buy_done"],
            status_filter=MediaBuyStatus.active,
        )
        result = self._run(req, [active, completed])
        assert [b.media_buy_id for b in result] == ["buy_active"]

    def test_buyer_refs_with_status_filter_excludes_non_matching(self):
        active = make_media_buy("buy_active", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        completed = make_media_buy("buy_done", start_date=date(2020, 1, 1), end_date=date(2020, 12, 31))
        req = GetMediaBuysRequest(
            buyer_refs=["ref_1"],
            status_filter=MediaBuyStatus.active,
        )
        result = self._run(req, [active, completed])
        assert [b.media_buy_id for b in result] == ["buy_active"]

    def test_no_filter_defaults_to_active_only(self):
        active = make_media_buy("buy_active", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        completed = make_media_buy("buy_done", start_date=date(2020, 1, 1), end_date=date(2020, 12, 31))
        req = GetMediaBuysRequest()
        result = self._run(req, [active, completed])
        assert [b.media_buy_id for b in result] == ["buy_active"]


class TestMapCreativeStatus:
    def test_approved(self):
        assert _map_creative_status("approved") == ApprovalStatus.approved

    def test_rejected(self):
        assert _map_creative_status("rejected") == ApprovalStatus.rejected

    def test_unknown_maps_to_pending_review(self):
        assert _map_creative_status("under_review") == ApprovalStatus.pending_review
        assert _map_creative_status("") == ApprovalStatus.pending_review


# ---------------------------------------------------------------------------
# Integration-style tests for _get_media_buys_impl
# ---------------------------------------------------------------------------


class TestGetMediaBuysImpl:
    """Tests for _get_media_buys_impl using mocked database."""

    def _make_request(self, **kwargs):
        return GetMediaBuysRequest(**kwargs)

    @patch("src.core.tools.media_buy_list.get_principal_id_from_context")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list.get_current_tenant")
    @patch("src.core.tools.media_buy_list.get_testing_context")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_returns_active_media_buy(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_testing_ctx,
        mock_tenant,
        mock_principal_obj,
        mock_principal_id,
    ):
        """Basic happy path: one active media buy returned."""
        mock_principal_id.return_value = "principal_1"
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")
        mock_tenant.return_value = {"tenant_id": "tenant_1", "adapter_type": "mock"}
        mock_testing_ctx.return_value = None

        # Use clearly active dates (past start, far future end)
        buy = make_media_buy(
            media_buy_id="buy_active",
            start_date=date(2020, 1, 1),
            end_date=date(2099, 12, 31),
        )
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_active": [make_package(media_buy_id="buy_active")]}
        mock_fetch_approvals.return_value = {}

        req = self._make_request()
        response = _get_media_buys_impl(req, make_tool_context())

        assert len(response.media_buys) == 1
        assert response.media_buys[0].media_buy_id == "buy_active"

    @patch("src.core.tools.media_buy_list.get_principal_id_from_context")
    def test_missing_principal_returns_error(self, mock_principal_id):
        """If principal ID not in context, return empty list with error."""
        mock_principal_id.return_value = None

        with patch("src.core.tools.media_buy_list.get_testing_context", return_value=None):
            req = self._make_request()
            response = _get_media_buys_impl(req, make_tool_context())

        assert response.media_buys == []
        assert response.errors is not None
        assert len(response.errors) > 0

    @patch("src.core.tools.media_buy_list.get_principal_id_from_context")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list.get_current_tenant")
    @patch("src.core.tools.media_buy_list.get_testing_context")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_snapshot_not_requested_when_false(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_testing_ctx,
        mock_tenant,
        mock_principal_obj,
        mock_principal_id,
    ):
        """When include_snapshot=False, adapter.get_packages_snapshot not called."""
        mock_principal_id.return_value = "principal_1"
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")
        mock_tenant.return_value = {"tenant_id": "tenant_1", "adapter_type": "mock"}
        mock_testing_ctx.return_value = None

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [make_package()]}
        mock_fetch_approvals.return_value = {}

        mock_adapter = MagicMock()
        mock_adapter.capabilities.supports_realtime_reporting = True
        mock_adapter.get_packages_snapshot = MagicMock()

        with patch("src.core.tools.media_buy_list.get_adapter", return_value=mock_adapter):
            req = self._make_request(include_snapshot=False)
            _get_media_buys_impl(req, make_tool_context())

        mock_adapter.get_packages_snapshot.assert_not_called()

    @patch("src.core.tools.media_buy_list.get_principal_id_from_context")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list.get_current_tenant")
    @patch("src.core.tools.media_buy_list.get_testing_context")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_snapshot_requested_calls_adapter(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_testing_ctx,
        mock_tenant,
        mock_principal_obj,
        mock_principal_id,
    ):
        """When include_snapshot=True, adapter.get_packages_snapshot is called."""
        mock_principal_id.return_value = "principal_1"
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")
        mock_tenant.return_value = {"tenant_id": "tenant_1", "adapter_type": "mock"}
        mock_testing_ctx.return_value = None

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        pkg = make_package(package_config={"platform_line_item_id": "li_123"})
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [pkg]}
        mock_fetch_approvals.return_value = {}

        snapshot = Snapshot(
            as_of=datetime(2025, 6, 15, tzinfo=UTC),
            impressions=50000,
            spend=100.0,
            staleness_seconds=300,
            delivery_status=DeliveryStatus.delivering,
        )
        mock_adapter = MagicMock()
        mock_adapter.capabilities.supports_realtime_reporting = True
        mock_adapter.get_packages_snapshot.return_value = {"buy_1": {"pkg_1": snapshot}}

        with patch("src.core.tools.media_buy_list.get_adapter", return_value=mock_adapter):
            req = self._make_request(include_snapshot=True)
            response = _get_media_buys_impl(req, make_tool_context())

        mock_adapter.get_packages_snapshot.assert_called_once()
        # The package_refs passed should include the platform_line_item_id
        call_args = mock_adapter.get_packages_snapshot.call_args[0][0]
        assert any("li_123" in ref for ref in call_args)

        # Response should contain the snapshot
        assert response.media_buys[0].packages[0].snapshot is not None

    @patch("src.core.tools.media_buy_list.get_principal_id_from_context")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list.get_current_tenant")
    @patch("src.core.tools.media_buy_list.get_testing_context")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_snapshot_unavailable_when_adapter_lacks_support(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_testing_ctx,
        mock_tenant,
        mock_principal_obj,
        mock_principal_id,
    ):
        """When include_snapshot=True but adapter lacks get_packages_snapshot, mark as unsupported."""
        mock_principal_id.return_value = "principal_1"
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")
        mock_tenant.return_value = {"tenant_id": "tenant_1", "adapter_type": "mock"}
        mock_testing_ctx.return_value = None

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        pkg = make_package()
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [pkg]}
        mock_fetch_approvals.return_value = {}

        mock_adapter = MagicMock()
        mock_adapter.capabilities.supports_realtime_reporting = False

        with patch("src.core.tools.media_buy_list.get_adapter", return_value=mock_adapter):
            req = self._make_request(include_snapshot=True)
            response = _get_media_buys_impl(req, make_tool_context())

        pkg_response = response.media_buys[0].packages[0]
        assert pkg_response.snapshot is None
        assert pkg_response.snapshot_unavailable_reason == SnapshotUnavailableReason.SNAPSHOT_UNSUPPORTED

    def test_context_required(self):
        """Context=None raises ToolError."""
        from fastmcp.exceptions import ToolError

        req = self._make_request()
        with pytest.raises(ToolError, match="Context is required"):
            _get_media_buys_impl(req, None)


class TestGetMediaBuysResponseStructure:
    """Tests for response schema compliance."""

    def test_response_is_serializable(self):
        """GetMediaBuysResponse can be dumped to dict without errors."""
        resp = GetMediaBuysResponse(media_buys=[], errors=None, context=None)
        data = resp.model_dump()
        assert "media_buys" in data
        assert data["media_buys"] == []

    def test_nested_serialization_roundtrip(self):
        """model_dump() recursively serializes all nested models to plain dicts.

        Guards against the Pydantic issue where model_dump() on a parent doesn't
        call custom model_dump() on nested children, leaving Pydantic model instances
        inside the dict instead of plain dicts.
        """
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        resp = GetMediaBuysResponse(
            media_buys=[
                GetMediaBuysMediaBuy(
                    media_buy_id="mb_1",
                    status=MediaBuyStatus.active,
                    currency="USD",
                    total_budget=1000.0,
                    packages=[
                        GetMediaBuysPackage(
                            package_id="pkg_1",
                            creative_approvals=[
                                CreativeApproval(
                                    creative_id="cr_1",
                                    approval_status=ApprovalStatus.approved,
                                ),
                            ],
                            snapshot=Snapshot(
                                as_of=now,
                                impressions=5000.0,
                                spend=100.0,
                                staleness_seconds=900,
                            ),
                        ),
                    ],
                ),
            ],
        )

        data = resp.model_dump()

        # Top level
        assert isinstance(data, dict)
        assert isinstance(data["media_buys"], list)

        # GetMediaBuysMediaBuy should be a dict, not a model instance
        mb = data["media_buys"][0]
        assert isinstance(mb, dict), f"Expected dict, got {type(mb)}"
        assert mb["media_buy_id"] == "mb_1"
        assert mb["status"] == MediaBuyStatus.active

        # GetMediaBuysPackage should be a dict
        assert isinstance(mb["packages"], list)
        pkg = mb["packages"][0]
        assert isinstance(pkg, dict), f"Expected dict, got {type(pkg)}"
        assert pkg["package_id"] == "pkg_1"

        # CreativeApproval should be a dict
        assert isinstance(pkg["creative_approvals"], list)
        approval = pkg["creative_approvals"][0]
        assert isinstance(approval, dict), f"Expected dict, got {type(approval)}"
        assert approval["creative_id"] == "cr_1"
        assert approval["approval_status"] == ApprovalStatus.approved

        # Snapshot should be a dict
        snap = pkg["snapshot"]
        assert isinstance(snap, dict), f"Expected dict, got {type(snap)}"
        assert snap["impressions"] == 5000.0

    def test_media_buy_status_values(self):
        """MediaBuyStatus enum values match AdCP spec strings."""
        assert MediaBuyStatus.pending_activation.value == "pending_activation"
        assert MediaBuyStatus.active.value == "active"
        assert MediaBuyStatus.completed.value == "completed"
