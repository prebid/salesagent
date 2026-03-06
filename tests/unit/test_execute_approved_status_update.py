"""Unit test: execute_approved_media_buy must update status to 'active' after adapter success.

Bug: salesagent-mckm
Root cause: execute_approved_media_buy returns (True, None) after successful adapter
execution but never sets media_buy.status = 'active' in the database.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from src.core.schemas import CreateMediaBuySuccess, Principal


def _make_mock_media_buy():
    """Build a mock MediaBuy ORM object with minimal fields for execute_approved_media_buy."""
    mb = MagicMock()
    mb.media_buy_id = "mb_test_001"
    mb.tenant_id = "tenant_1"
    mb.principal_id = "principal_1"
    mb.status = "pending_approval"
    mb.order_name = "Test Order"
    mb.advertiser_name = "Test Advertiser"
    mb.start_date = datetime.now(UTC).date()
    mb.end_date = (datetime.now(UTC) + timedelta(days=7)).date()
    mb.start_time = datetime.now(UTC)
    mb.end_time = datetime.now(UTC) + timedelta(days=7)
    mb.budget = Decimal("5000.00")
    mb.currency = "USD"
    mb.raw_request = {
        "buyer_ref": "test-buyer",
        "brand": {"domain": "testbrand.com"},
        "start_time": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "end_time": (datetime.now(UTC) + timedelta(days=8)).isoformat(),
        "packages": [
            {
                "product_id": "prod_1",
                "pricing_option_id": "po_1",
                "buyer_ref": "pkg-1",
                "budget": 5000.0,
            }
        ],
    }
    return mb


def _make_mock_tenant():
    """Build a mock Tenant ORM object."""
    tenant = MagicMock()
    tenant.tenant_id = "tenant_1"
    tenant.name = "Test Tenant"
    tenant.subdomain = "test"
    tenant.ad_server = "mock"
    tenant.virtual_host = None
    return tenant


def _make_mock_package():
    """Build a mock MediaPackage DB object."""
    pkg = MagicMock()
    pkg.package_id = "pkg_001"
    pkg.media_buy_id = "mb_test_001"
    pkg.package_config = {
        "product_id": "prod_1",
        "name": "Test Package",
        "budget": 5000.0,
        "pricing_model": "CPM",
    }
    return pkg


def _make_mock_product():
    """Build a mock Product ORM object."""
    product = MagicMock()
    product.product_id = "prod_1"
    product.name = "Test Product"
    product.delivery_type = "non_guaranteed"
    product.format_ids = [
        {
            "agent_url": "https://example.com/formats",
            "format_id": "fmt_1",
            "id": "fmt_1",
        }
    ]

    # Set up pricing option
    pricing_option = MagicMock()
    pricing_option.pricing_model = "CPM"
    pricing_option.rate = Decimal("10.00")
    pricing_option.currency = "USD"
    pricing_option.is_fixed = True
    pricing_option.root = pricing_option  # Self-reference for getattr(po, "root", po)
    product.pricing_options = [pricing_option]

    return product


class TestExecuteApprovedStatusUpdate:
    """execute_approved_media_buy must set status='active' after adapter success."""

    def test_status_updated_to_active_after_adapter_success(self):
        """After successful adapter execution, media_buy.status must be 'active'.

        This is the regression test for salesagent-mckm: the function returns
        (True, None) but never updates the status field.
        """
        # -- Arrange --
        tenant = _make_mock_tenant()
        media_buy = _make_mock_media_buy()
        db_package = _make_mock_package()
        product = _make_mock_product()

        principal = Principal(
            principal_id="principal_1",
            name="Test Principal",
            platform_mappings={},
        )

        adapter_response = CreateMediaBuySuccess(
            media_buy_id="mb_test_001",
            buyer_ref="test-buyer",
            packages=[],
        )

        # Mock adapter with no orders_manager (skip order approval)
        mock_adapter = MagicMock()
        mock_adapter.orders_manager = None

        # Set up three UoW instances the function opens:
        # 1. Load tenant, media_buy, packages, products
        # 2. Handle creative uploads
        # 3. Update media buy status to 'active' (the fix)
        mock_session_1 = MagicMock()
        mock_session_2 = MagicMock()
        mock_session_3 = MagicMock()

        # Session 1 scalars: tenant, media_buy, packages, product
        session_1_scalars = [
            MagicMock(first=MagicMock(return_value=tenant)),
            MagicMock(first=MagicMock(return_value=media_buy)),
            MagicMock(all=MagicMock(return_value=[db_package])),
            MagicMock(first=MagicMock(return_value=product)),
        ]
        mock_session_1.scalars = MagicMock(side_effect=session_1_scalars)

        # Session 2: creative assignments returns empty
        mock_session_2.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))

        # Build mock UoWs — each call to MediaBuyUoW() returns the next one
        mock_uow_1 = MagicMock()
        mock_uow_1.__enter__ = MagicMock(return_value=mock_uow_1)
        mock_uow_1.__exit__ = MagicMock(return_value=None)
        mock_uow_1.session = mock_session_1
        mock_uow_1.media_buys = MagicMock()

        mock_uow_2 = MagicMock()
        mock_uow_2.__enter__ = MagicMock(return_value=mock_uow_2)
        mock_uow_2.__exit__ = MagicMock(return_value=None)
        mock_uow_2.session = mock_session_2
        mock_uow_2.media_buys = MagicMock()

        # UoW 3 uses update_status on the repository — track it was called
        mock_repo_3 = MagicMock()
        mock_uow_3 = MagicMock()
        mock_uow_3.__enter__ = MagicMock(return_value=mock_uow_3)
        mock_uow_3.__exit__ = MagicMock(return_value=None)
        mock_uow_3.session = mock_session_3
        mock_uow_3.media_buys = mock_repo_3

        uow_iter = iter([mock_uow_1, mock_uow_2, mock_uow_3])

        with (
            patch("src.core.database.repositories.MediaBuyUoW", side_effect=lambda _: next(uow_iter)),
            patch("src.core.config_loader.set_current_tenant"),
            patch(
                "src.core.config_loader.get_tenant_by_id",
                return_value={"tenant_id": "tenant_1", "adapter_type": "mock"},
            ),
            patch("src.core.auth.get_principal_object", return_value=principal),
            patch(
                "src.core.tools.media_buy_create._execute_adapter_media_buy_creation",
                return_value=adapter_response,
            ),
            patch("src.core.tools.media_buy_create._validate_creatives_before_adapter_call"),
            patch("src.core.helpers.adapter_helpers.get_adapter", return_value=mock_adapter),
        ):
            from src.core.tools.media_buy_create import execute_approved_media_buy

            success, error = execute_approved_media_buy("mb_test_001", "tenant_1")

        # -- Assert --
        assert success is True, f"Expected success but got error: {error}"
        assert error is None

        # THE KEY ASSERTION: update_status must be called with 'active'
        mock_repo_3.update_status.assert_called_once_with("mb_test_001", "active")
