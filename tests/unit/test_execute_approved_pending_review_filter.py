"""Unit test: execute_approved_media_buy must skip pending_review creatives.

Creatives in pending_review status at buy-approval time are held back from the
ad server upload. They are pushed retroactively when the operator approves them
via approve_creative (prebid#1038).
"""

from unittest.mock import MagicMock, patch

_MODULE = "src.core.tools.media_buy_create"


class TestExecuteApprovedPendingReviewFilter:
    """execute_approved_media_buy skips pending_review creatives."""

    def test_pending_review_creative_not_uploaded(self):
        """A pending_review creative is excluded from the adapter asset list."""
        from src.core.tools.media_buy_create import execute_approved_media_buy

        # UoW chain: first UoW loads data, second updates status
        uow1 = MagicMock()
        uow1.__enter__ = MagicMock(return_value=uow1)
        uow1.__exit__ = MagicMock(return_value=False)
        uow1.session = MagicMock()
        uow1.media_buys = MagicMock()

        uow2 = MagicMock()
        uow2.__enter__ = MagicMock(return_value=uow2)
        uow2.__exit__ = MagicMock(return_value=False)
        uow2.media_buys = MagicMock()

        uow3 = MagicMock()
        uow3.__enter__ = MagicMock(return_value=uow3)
        uow3.__exit__ = MagicMock(return_value=False)
        uow3.media_buys = MagicMock()

        uow_iter = iter([uow1, uow2, uow3])

        # Tenant
        tenant = MagicMock()
        tenant.tenant_id = "t1"
        tenant.ad_server = "mock"

        # Media buy
        from datetime import UTC, datetime, timedelta
        from decimal import Decimal

        mb = MagicMock()
        mb.media_buy_id = "mb_1"
        mb.tenant_id = "t1"
        mb.principal_id = "p1"
        mb.status = "pending_approval"
        mb.order_name = "Test"
        mb.advertiser_name = "Advertiser"
        mb.start_date = datetime.now(UTC).date()
        mb.end_date = (datetime.now(UTC) + timedelta(days=7)).date()
        mb.start_time = None
        mb.end_time = None
        mb.budget = Decimal("1000.00")
        mb.currency = "USD"
        mb.raw_request = {
            "brand": {"domain": "test.com"},
            "start_time": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "end_time": (datetime.now(UTC) + timedelta(days=8)).isoformat(),
            "packages": [{"product_id": "prod_1", "pricing_option_id": "po_1", "budget": 1000.0}],
        }

        # Package
        pkg = MagicMock()
        pkg.package_id = "pkg_1"
        pkg.package_config = {"product_id": "prod_1", "name": "Pkg", "budget": 1000.0, "pricing_model": "CPM"}

        # Creative assignment — status is pending_review
        assignment = MagicMock()
        assignment.creative_id = "cre_pending"
        assignment.package_id = "pkg_1"
        assignment.weight = 100

        pending_creative = MagicMock()
        pending_creative.creative_id = "cre_pending"
        pending_creative.status = "pending_review"

        session = uow1.session
        session.scalars.return_value.first.side_effect = [tenant, mb]
        session.scalars.return_value.all.side_effect = [
            [pkg],  # db_packages
            [assignment],  # assignments
            [pending_creative],  # creatives
        ]

        from src.core.schemas import CreateMediaBuySuccess, Principal

        principal = Principal(principal_id="p1", name="Test", platform_mappings={})
        adapter_response = CreateMediaBuySuccess(
            media_buy_id="gam_order_1",
            packages=[],
        )

        mock_adapter = MagicMock()
        mock_adapter.creatives_manager = MagicMock()
        mock_adapter.orders_manager.approve_order.return_value = True

        with (
            patch("src.core.database.repositories.MediaBuyUoW", side_effect=lambda _: next(uow_iter)),
            patch("src.core.config_loader.set_current_tenant"),
            patch("src.core.config_loader.get_tenant_by_id", return_value={"tenant_id": "t1"}),
            patch(f"{_MODULE}.get_principal_object", return_value=principal),
            patch(f"{_MODULE}._execute_adapter_media_buy_creation", return_value=adapter_response),
            patch(f"{_MODULE}._validate_creatives_before_adapter_call"),
            patch(f"{_MODULE}.get_adapter", return_value=mock_adapter),
        ):
            success, error = execute_approved_media_buy("mb_1", "t1")

        # The pending_review creative must NOT have been uploaded
        mock_adapter.creatives_manager.add_creative_assets.assert_not_called()


class TestPersistAdapterPackageIds:
    """_persist_adapter_package_ids must not overwrite mismatched platform_order_id."""

    def test_refuses_to_overwrite_mismatched_platform_order_id(self):
        from src.core.tools.media_buy_create import _persist_adapter_package_ids

        pkg = MagicMock()
        pkg.package_id = "pkg_1"
        pkg.package_config = {"platform_order_id": "existing_gam_order"}

        repo = MagicMock()
        repo.get_packages.return_value = [pkg]

        _persist_adapter_package_ids(
            repo,
            media_buy_id="mb_1",
            platform_order_id="new_gam_order",
            log_label="TEST",
        )

        assert pkg.package_config["platform_order_id"] == "existing_gam_order"

    def test_writes_platform_order_id_when_unset(self):
        from src.core.tools.media_buy_create import _persist_adapter_package_ids

        pkg = MagicMock()
        pkg.package_id = "pkg_1"
        pkg.package_config = {}

        repo = MagicMock()
        repo.get_packages.return_value = [pkg]

        _persist_adapter_package_ids(
            repo,
            media_buy_id="mb_1",
            platform_order_id="gam_order_1",
        )

        assert pkg.package_config["platform_order_id"] == "gam_order_1"

    def test_refuses_to_overwrite_mismatched_platform_line_item_id(self):
        from src.core.tools.media_buy_create import _persist_adapter_package_ids

        pkg = MagicMock()
        pkg.package_id = "pkg_1"
        pkg.package_config = {"platform_line_item_id": "existing_li"}

        repo = MagicMock()
        repo.get_packages.return_value = [pkg]

        _persist_adapter_package_ids(
            repo,
            media_buy_id="mb_1",
            platform_order_id="gam_order_1",
            platform_line_item_ids={"pkg_1": "new_li"},
        )

        assert pkg.package_config["platform_line_item_id"] == "existing_li"
