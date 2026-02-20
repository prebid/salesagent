"""Unit test for GAM adapter products_map delivery_type propagation.

Regression test for #1056: CPM guaranteed line items incorrectly created as
STANDARD instead of SPONSORSHIP because delivery_type was not included in
products_map, causing is_guaranteed to always be False downstream.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, patch

from src.adapters.google_ad_manager import GoogleAdManager


def _make_db_session(product_mock):
    """Create a mock db session that handles all scalars() calls in create_media_buy.

    create_media_buy has two `with get_db_session()` blocks:
    1. Products/inventory lookup (scalars calls: Product, ProductInventoryMapping)
    2. AdapterConfig/Tenant lookup (scalars calls: AdapterConfig, Tenant)
    """
    call_count = 0

    def scalars_side_effect(stmt):
        nonlocal call_count
        call_count += 1
        result = Mock()
        if call_count == 1:
            # Product lookup
            result.first.return_value = product_mock
        elif call_count == 2:
            # ProductInventoryMapping lookup
            result.all.return_value = []
        elif call_count == 3:
            # AdapterConfig lookup
            result.first.return_value = None
        elif call_count == 4:
            # Tenant lookup
            result.first.return_value = None
        else:
            result.first.return_value = None
            result.all.return_value = []
        return result

    mock_session = MagicMock()
    mock_session.scalars.side_effect = scalars_side_effect
    return mock_session


def test_products_map_includes_delivery_type():
    """products_map must include delivery_type so line item type selection works.

    Bug: products_map was built as:
        {"product_id": ..., "implementation_config": ...}
    Missing delivery_type meant orders.py got None for product.get("delivery_type"),
    making is_guaranteed always False, selecting wrong line item type.
    """
    # Create a mock adapter
    mock_adapter = Mock(spec=GoogleAdManager)
    mock_adapter.tenant_id = "tenant_test"
    mock_adapter.advertiser_id = "adv_123"
    mock_adapter.trafficker_id = "traff_123"
    mock_adapter.log = Mock()
    mock_adapter.orders_manager = Mock()
    mock_adapter.workflow_manager = Mock()
    mock_adapter._requires_manual_approval = Mock(return_value=False)
    mock_adapter._validate_targeting = Mock(return_value=[])
    mock_adapter._check_order_has_guaranteed_items = Mock(return_value=(False, []))
    mock_adapter._placement_targeting_map = {}
    mock_adapter.targeting_manager = Mock()

    # Create a mock Product with delivery_type = "guaranteed"
    mock_product = Mock()
    mock_product.product_id = "prod_abc"
    mock_product.delivery_type = "guaranteed"
    mock_product.implementation_config = {"targeted_ad_unit_ids": ["12345"]}

    # Create a MediaPackage
    mock_package = Mock()
    mock_package.package_id = "pkg_prod_abc_001"
    mock_package.product_id = "prod_abc"
    mock_package.targeting_overlay = None

    # Create request packages (need real string attributes for Pydantic response construction)
    mock_req_package = Mock()
    mock_req_package.buyer_ref = "buyer_ref_001"
    mock_req_package.package_id = "pkg_prod_abc_001"

    # Create a request
    mock_request = Mock()
    mock_request.buyer_ref = "buyer_test"
    mock_request.packages = [mock_req_package]
    mock_request.get_total_budget = Mock(return_value=10000)

    # Pricing info
    package_pricing_info = {
        "pkg_prod_abc_001": {
            "pricing_model": "cpm",
            "rate": 5.0,
            "currency": "USD",
            "is_fixed": True,
            "bid_price": None,
        }
    }

    # Capture what products_map is passed to create_line_items
    captured_products_map = {}

    def capture_create_line_items(**kwargs):
        captured_products_map.update(kwargs.get("products_map", {}))
        return ["li_001"]

    mock_adapter.orders_manager.create_line_items = Mock(side_effect=capture_create_line_items)
    mock_adapter.orders_manager.create_order = Mock(return_value="order_123")
    mock_adapter.orders_manager.approve_order = Mock(return_value=True)

    mock_session = _make_db_session(mock_product)

    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_db.return_value.__enter__.return_value = mock_session

        GoogleAdManager.create_media_buy(
            mock_adapter,
            request=mock_request,
            packages=[mock_package],
            start_time=datetime(2024, 1, 1, tzinfo=UTC),
            end_time=datetime(2024, 12, 31, tzinfo=UTC),
            package_pricing_info=package_pricing_info,
        )

    # Assert: products_map entry has exactly the keys that orders.py consumes.
    # orders.py reads: implementation_config (line 413), product_id (line 738), delivery_type (line 771).
    # If you add a new .get() in orders.py, add the key here too.
    assert "pkg_prod_abc_001" in captured_products_map, "Package should be in products_map"
    product_entry = captured_products_map["pkg_prod_abc_001"]

    expected_keys = {"product_id", "implementation_config", "delivery_type"}
    assert set(product_entry.keys()) == expected_keys, (
        f"products_map entry has unexpected shape. "
        f"Expected keys: {expected_keys}, got: {set(product_entry.keys())}. "
        f"If orders.py now reads a new field, add it to both google_ad_manager.py and this test."
    )

    # Verify values are correct (not None or wrong type)
    assert product_entry["product_id"] == "prod_abc"
    assert product_entry["delivery_type"] == "guaranteed"
    assert isinstance(product_entry["implementation_config"], dict)
