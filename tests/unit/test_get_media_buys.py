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
from pydantic import RootModel, ValidationError

from src.core.resolved_identity import ResolvedIdentity
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


def make_identity(
    tenant_id="tenant_1",
    principal_id="principal_1",
    tenant=None,
    testing_context=None,
):
    """Create a ResolvedIdentity for testing."""
    if tenant is None:
        tenant = {"tenant_id": tenant_id, "adapter_type": "mock"}
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant,
        protocol="mcp",
        testing_context=testing_context,
    )


def make_media_buy(
    media_buy_id="buy_1",
    principal_id="principal_1",
    tenant_id="tenant_1",
    start_date=date(2025, 1, 1),
    end_date=date(2025, 12, 31),
    start_time=None,
    end_time=None,
    budget=Decimal("10000"),
    currency="USD",
    raw_request=None,
    status="active",
    is_paused=False,
):
    buy = MagicMock()
    buy.media_buy_id = media_buy_id
    buy.principal_id = principal_id
    buy.tenant_id = tenant_id
    buy.buyer_ref = None
    buy.start_date = start_date
    buy.end_date = end_date
    buy.start_time = start_time
    buy.end_time = end_time
    buy.budget = budget
    buy.currency = currency
    buy.raw_request = raw_request or {}
    buy.status = status
    buy.is_paused = is_paused
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
    def test_pending_start_when_before_start(self):
        buy = make_media_buy(start_date=date(2099, 1, 1), end_date=date(2099, 12, 31))
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.pending_start

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
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.pending_start

    @pytest.mark.parametrize(
        ("persisted", "expected"),
        [
            ("completed", MediaBuyStatus.completed),
            ("paused", MediaBuyStatus.paused),
            ("rejected", MediaBuyStatus.rejected),
            ("canceled", MediaBuyStatus.canceled),
        ],
    )
    def test_persisted_terminal_status_authoritative_over_flight_window(self, persisted, expected):
        """Regression (salesagent-36d): a buy persisted as a terminal/explicit
        lifecycle status must be reported with that status even when its flight
        window covers today. The persisted MediaBuy.status column is the source
        of truth — terminal states cannot be re-derived from flight dates.
        """
        buy = make_media_buy(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status=persisted,
        )
        assert _compute_status(buy, date(2025, 6, 15)) == expected

    def test_paused_flag_overrides_active_window(self):
        """Regression (salesagent-36d): is_paused True reports paused even when
        the flight window covers today, mirroring _internal_status_for_buy."""
        buy = make_media_buy(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status="active",
            is_paused=True,
        )
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.paused

    def test_approved_buy_in_flight_is_active(self):
        """A buy persisted as the generic 'approved' serving state with a flight
        window covering today is date-refined to active."""
        buy = make_media_buy(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status="approved",
        )
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.active

    def test_active_buy_before_flight_is_pending_start(self):
        """The generic serving state is date-refined: an 'active' buy whose
        flight has not started yet reports pending_start."""
        buy = make_media_buy(
            start_date=date(2099, 1, 1),
            end_date=date(2099, 12, 31),
            status="active",
        )
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.pending_start

    def test_active_buy_past_flight_is_completed(self):
        """The generic serving state is date-refined: an 'active' buy past its
        end date reports completed."""
        buy = make_media_buy(
            start_date=date(2020, 1, 1),
            end_date=date(2020, 12, 31),
            status="active",
        )
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.completed

    def test_pre_serving_persisted_status_maps_to_pending(self):
        """Transitional pre-serving states (draft/pending_approval/...) report
        a pending status, not a date-derived one."""
        buy = make_media_buy(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status="pending_creatives",
        )
        assert _compute_status(buy, date(2025, 6, 15)) == MediaBuyStatus.pending_creatives


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

        result = _resolve_status_filter(StatusFilter([MediaBuyStatus.pending_start]))
        assert result == {MediaBuyStatus.pending_start}


class TestFetchTargetMediaBuys:
    """status_filter applies consistently regardless of which filter key is used."""

    TODAY = date(2025, 6, 15)

    def _run(self, req, buys):
        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = buys
        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        return _fetch_target_media_buys(req, "principal_1", mock_uow, self.TODAY)

    def test_media_buy_ids_with_status_filter_excludes_non_matching(self):
        active = make_media_buy("buy_active", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        completed = make_media_buy("buy_done", start_date=date(2020, 1, 1), end_date=date(2020, 12, 31))
        req = GetMediaBuysRequest(
            media_buy_ids=["buy_active", "buy_done"],
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

    @patch("src.core.tools.media_buy_list.MediaBuyUoW")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_returns_active_media_buy(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_principal_obj,
        mock_uow_cls,
    ):
        """Basic happy path: one active media buy returned."""
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")

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
        response = _get_media_buys_impl(req, identity=make_identity())

        assert len(response.media_buys) == 1
        assert response.media_buys[0].media_buy_id == "buy_active"

    def test_missing_principal_returns_error(self):
        """If principal ID not in identity, return empty list with error."""
        identity = make_identity(principal_id=None)

        req = self._make_request()
        response = _get_media_buys_impl(req, identity=identity)

        assert response.media_buys == []
        assert response.errors is not None
        assert len(response.errors) > 0

    @patch("src.core.tools.media_buy_list.MediaBuyUoW")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_snapshot_not_requested_when_false(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_principal_obj,
        mock_uow_cls,
    ):
        """When include_snapshot=False, adapter.get_packages_snapshot not called."""
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [make_package()]}
        mock_fetch_approvals.return_value = {}

        mock_adapter = MagicMock()
        mock_adapter.capabilities.supports_realtime_reporting = True
        mock_adapter.get_packages_snapshot = MagicMock()

        with patch("src.core.tools.media_buy_list.get_adapter", return_value=mock_adapter):
            req = self._make_request()
            _get_media_buys_impl(req, identity=make_identity(), include_snapshot=False)

        mock_adapter.get_packages_snapshot.assert_not_called()

    @patch("src.core.tools.media_buy_list.MediaBuyUoW")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_snapshot_requested_calls_adapter(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_principal_obj,
        mock_uow_cls,
    ):
        """When include_snapshot=True, adapter.get_packages_snapshot is called."""
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")

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
            req = self._make_request()
            response = _get_media_buys_impl(req, identity=make_identity(), include_snapshot=True)

        mock_adapter.get_packages_snapshot.assert_called_once()
        # The package_refs passed should include the platform_line_item_id
        call_args = mock_adapter.get_packages_snapshot.call_args[0][0]
        assert any("li_123" in ref for ref in call_args)

        # Response should contain the snapshot
        assert response.media_buys[0].packages[0].snapshot is not None

    @patch("src.core.tools.media_buy_list.MediaBuyUoW")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_snapshot_unavailable_when_adapter_lacks_support(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_principal_obj,
        mock_uow_cls,
    ):
        """When include_snapshot=True but adapter lacks get_packages_snapshot, mark as unsupported."""
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        pkg = make_package()
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [pkg]}
        mock_fetch_approvals.return_value = {}

        mock_adapter = MagicMock()
        mock_adapter.capabilities.supports_realtime_reporting = False

        with patch("src.core.tools.media_buy_list.get_adapter", return_value=mock_adapter):
            req = self._make_request()
            response = _get_media_buys_impl(req, identity=make_identity(), include_snapshot=True)

        pkg_response = response.media_buys[0].packages[0]
        assert pkg_response.snapshot is None
        assert pkg_response.snapshot_unavailable_reason == SnapshotUnavailableReason.SNAPSHOT_UNSUPPORTED

    def test_identity_required(self):
        """identity=None raises AdCPAuthenticationError."""
        from src.core.exceptions import AdCPAuthenticationError

        req = self._make_request()
        with pytest.raises(AdCPAuthenticationError, match="Identity is required"):
            _get_media_buys_impl(req, None)


class TestTargetingOverlayRoundTrip:
    """get_media_buys must echo persisted targeting_overlay so callers can
    verify what was stored (storyboard inventory_list_targeting parity).

    Covers: UC-002-MAIN-14a
    """

    def _make_request(self, **kwargs):
        return GetMediaBuysRequest(**kwargs)

    @patch("src.core.tools.media_buy_list.MediaBuyUoW")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_property_list_returned_at_storyboard_path(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_principal_obj,
        mock_uow_cls,
    ):
        """media_buys[0].packages[0].targeting_overlay.property_list.list_id matches input."""
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        pkg = make_package(
            package_config={
                "product_id": "prod_1",
                "targeting_overlay": {
                    "property_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "acme_outdoor_allowlist_v1",
                    },
                },
            }
        )
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [pkg]}
        mock_fetch_approvals.return_value = {}

        req = self._make_request()
        response = _get_media_buys_impl(req, identity=make_identity())

        # Storyboard validation: literal field path must match
        targeting = response.media_buys[0].packages[0].targeting_overlay
        assert targeting is not None
        assert targeting.property_list is not None
        assert targeting.property_list.list_id == "acme_outdoor_allowlist_v1"

    @patch("src.core.tools.media_buy_list.MediaBuyUoW")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_collection_list_returned_at_storyboard_path(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_principal_obj,
        mock_uow_cls,
    ):
        """media_buys[0].packages[0].targeting_overlay.collection_list.list_id matches input."""
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        pkg = make_package(
            package_config={
                "product_id": "prod_1",
                "targeting_overlay": {
                    "collection_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "acme_outdoor_collections_v1",
                    },
                },
            }
        )
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [pkg]}
        mock_fetch_approvals.return_value = {}

        req = self._make_request()
        response = _get_media_buys_impl(req, identity=make_identity())

        targeting = response.media_buys[0].packages[0].targeting_overlay
        assert targeting is not None
        assert targeting.collection_list is not None
        assert targeting.collection_list.list_id == "acme_outdoor_collections_v1"

    @patch("src.core.tools.media_buy_list.MediaBuyUoW")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_both_list_types_returned_together(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_principal_obj,
        mock_uow_cls,
    ):
        """Storyboard's create-with-both-lists step expects both fields back at once."""
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        pkg = make_package(
            package_config={
                "product_id": "prod_1",
                "targeting_overlay": {
                    "property_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "acme_outdoor_allowlist_v1",
                    },
                    "collection_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "acme_outdoor_collections_v1",
                    },
                },
            }
        )
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [pkg]}
        mock_fetch_approvals.return_value = {}

        req = self._make_request()
        response = _get_media_buys_impl(req, identity=make_identity())

        # Round-trip via model_dump (the wire-format path)
        dumped = response.model_dump(exclude_none=True)
        pkg_data = dumped["media_buys"][0]["packages"][0]
        assert pkg_data["targeting_overlay"]["property_list"]["list_id"] == "acme_outdoor_allowlist_v1"
        assert pkg_data["targeting_overlay"]["collection_list"]["list_id"] == "acme_outdoor_collections_v1"

    @patch("src.core.tools.media_buy_list.MediaBuyUoW")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_legacy_targeting_key_fallback(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_principal_obj,
        mock_uow_cls,
    ):
        """Pre-rename data stored under 'targeting' key still rehydrates."""
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        pkg = make_package(
            package_config={
                "product_id": "prod_1",
                "targeting": {  # legacy key
                    "property_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "legacy_v1",
                    },
                },
            }
        )
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [pkg]}
        mock_fetch_approvals.return_value = {}

        req = self._make_request()
        response = _get_media_buys_impl(req, identity=make_identity())

        targeting = response.media_buys[0].packages[0].targeting_overlay
        assert targeting is not None
        assert targeting.property_list.list_id == "legacy_v1"

    @patch("src.core.tools.media_buy_list.MediaBuyUoW")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_no_targeting_overlay_returns_none(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_principal_obj,
        mock_uow_cls,
    ):
        """Packages without persisted targeting return targeting_overlay=None, not an empty Targeting."""
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        pkg = make_package(package_config={"product_id": "prod_1"})  # no targeting at all
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [pkg]}
        mock_fetch_approvals.return_value = {}

        req = self._make_request()
        response = _get_media_buys_impl(req, identity=make_identity())

        assert response.media_buys[0].packages[0].targeting_overlay is None

    @patch("src.core.tools.media_buy_list.MediaBuyUoW")
    @patch("src.core.tools.media_buy_list.get_principal_object")
    @patch("src.core.tools.media_buy_list._fetch_target_media_buys")
    @patch("src.core.tools.media_buy_list._fetch_packages")
    @patch("src.core.tools.media_buy_list._fetch_creative_approvals")
    def test_internal_targeting_fields_not_leaked(
        self,
        mock_fetch_approvals,
        mock_fetch_packages,
        mock_fetch_buys,
        mock_principal_obj,
        mock_uow_cls,
    ):
        """Targeting carries internal fields (had_city_targeting, tenant_id, etc.) — none
        of them may leak into the response. Targeting.model_dump excludes the full set:
        key_value_pairs, tenant_id, created_at, updated_at, metadata, had_city_targeting.
        """
        mock_principal_obj.return_value = MagicMock(principal_id="principal_1")

        buy = make_media_buy(start_date=date(2020, 1, 1), end_date=date(2099, 12, 31))
        pkg = make_package(
            package_config={
                "product_id": "prod_1",
                "targeting_overlay": {
                    "property_list": {
                        "agent_url": "https://gov.example",
                        "list_id": "v1",
                    },
                    # Legacy city targeting triggers had_city_targeting=True via normalizer
                    "geo_city_any_of": ["NYC"],
                    # Each of these must be excluded by Targeting.model_dump
                    "tenant_id": "leaky_tenant_id",
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-02T00:00:00Z",
                    "metadata": {"private": "do_not_leak"},
                    "key_value_pairs": {"aee_segment": "secret"},
                },
            }
        )
        mock_fetch_buys.return_value = [buy]
        mock_fetch_packages.return_value = {"buy_1": [pkg]}
        mock_fetch_approvals.return_value = {}

        req = self._make_request()
        response = _get_media_buys_impl(req, identity=make_identity())

        dumped = response.model_dump(exclude_none=True)
        targeting = dumped["media_buys"][0]["packages"][0]["targeting_overlay"]
        # Full excluded set per Targeting.model_dump + Field(exclude=True)
        excluded_internal_fields = {
            "key_value_pairs",
            "tenant_id",
            "created_at",
            "updated_at",
            "metadata",
            "had_city_targeting",
        }
        leaked = excluded_internal_fields & set(targeting.keys())
        assert not leaked, f"Internal Targeting fields leaked into response: {sorted(leaked)}"
        # property_list still surfaces
        assert targeting["property_list"]["list_id"] == "v1"


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
        assert MediaBuyStatus.pending_start.value == "pending_start"
        assert MediaBuyStatus.active.value == "active"
        assert MediaBuyStatus.completed.value == "completed"


# ---------------------------------------------------------------------------
# Security regression: internal flags must not be in request objects
# ---------------------------------------------------------------------------


class TestGetMediaBuysRequestRejectsInternalFlags:
    """Regression: internal behavior flags must NOT be accepted by GetMediaBuysRequest.

    External callers must never control _impl behavior through the request object.
    Flags like include_snapshot are passed as explicit _impl parameters by transport
    wrappers, not embedded in the request.
    """

    def test_include_snapshot_rejected(self):
        """include_snapshot must NOT be accepted by GetMediaBuysRequest."""
        with pytest.raises(ValidationError):
            GetMediaBuysRequest(include_snapshot=True)

    def test_include_snapshot_false_also_rejected(self):
        """Even include_snapshot=False must be rejected — the field doesn't belong here."""
        with pytest.raises(ValidationError):
            GetMediaBuysRequest(include_snapshot=False)
