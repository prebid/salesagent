"""Shared MagicMock builders for tests exercising execute_approved_media_buy.

`execute_approved_media_buy` opens 3 UoWs and walks tenant/media_buy/packages/products.
Tests across `test_execute_approved_status_update.py` and
`test_b2_background_approval_polling.py` need the same ORM mocks; this module is
the single source of truth so the duplication guard stays green.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock


def make_mock_media_buy(*, media_buy_id: str = "mb_test_001", status: str = "pending_approval") -> MagicMock:
    """Build a mock MediaBuy ORM object with the minimal fields the function reads."""
    mb = MagicMock()
    mb.media_buy_id = media_buy_id
    mb.tenant_id = "tenant_1"
    mb.principal_id = "principal_1"
    mb.status = status
    mb.order_name = "Test Order"
    mb.advertiser_name = "Test Advertiser"
    mb.start_date = datetime.now(UTC).date()
    mb.end_date = (datetime.now(UTC) + timedelta(days=7)).date()
    mb.start_time = datetime.now(UTC)
    mb.end_time = datetime.now(UTC) + timedelta(days=7)
    mb.budget = Decimal("5000.00")
    mb.currency = "USD"
    mb.raw_request = {
        "brand": {"domain": "testbrand.com"},
        "start_time": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "end_time": (datetime.now(UTC) + timedelta(days=8)).isoformat(),
        "packages": [{"product_id": "prod_1", "pricing_option_id": "po_1", "budget": 5000.0}],
    }
    return mb


def make_mock_tenant(*, ad_server: str = "mock") -> MagicMock:
    """Build a mock Tenant ORM object."""
    tenant = MagicMock()
    tenant.tenant_id = "tenant_1"
    tenant.name = "Test Tenant"
    tenant.subdomain = "test"
    tenant.ad_server = ad_server
    tenant.virtual_host = None
    return tenant


def make_mock_package(*, media_buy_id: str = "mb_test_001") -> MagicMock:
    """Build a mock MediaPackage DB object."""
    pkg = MagicMock()
    pkg.package_id = "pkg_001"
    pkg.media_buy_id = media_buy_id
    pkg.package_config = {"product_id": "prod_1", "name": "Test Package", "budget": 5000.0, "pricing_model": "CPM"}
    return pkg


def make_mock_product() -> MagicMock:
    """Build a mock Product ORM object with a CPM pricing option."""
    product = MagicMock()
    product.product_id = "prod_1"
    product.name = "Test Product"
    product.delivery_type = "non_guaranteed"
    product.format_ids = [{"agent_url": "https://example.com/formats", "format_id": "fmt_1", "id": "fmt_1"}]

    pricing_option = MagicMock()
    pricing_option.pricing_model = "CPM"
    pricing_option.rate = Decimal("10.00")
    pricing_option.currency = "USD"
    pricing_option.is_fixed = True
    pricing_option.root = pricing_option
    product.pricing_options = [pricing_option]
    return product
