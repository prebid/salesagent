"""Unit tests for what GAM create_media_buy derives per package from the products it loads.

Regression test for #1056: CPM guaranteed line items incorrectly created as
STANDARD instead of SPONSORSHIP because delivery_type was not included in
products_map, causing is_guaranteed to always be False downstream.

Regression tests for #1516: the product's native_style_id must reach the creatives
manager keyed by package, as the GAM creative template every native creative of that
package is built against.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, patch

from src.adapters.google_ad_manager import GoogleAdManager


def _make_db_session(*products):
    """A db session stand-in answering create_media_buy's scalars() calls in the order it makes them.

    create_media_buy has two `with get_db_session()` blocks. The first reads, per package,
    the Product and then its ProductInventoryMapping rows; the second reads AdapterConfig
    and then Tenant once. The answers are consumed in that order; everything after them
    is empty.
    """
    answers = []
    for product in products:
        answers.append(("first", product))  # Product lookup
        answers.append(("all", []))  # ProductInventoryMapping lookup
    pending = iter(answers)

    def scalars_side_effect(stmt):
        kind, value = next(pending, ("first", None))  # AdapterConfig, Tenant, anything later
        result = Mock()
        result.first.return_value = value if kind == "first" else None
        result.all.return_value = value if kind == "all" else []
        return result

    mock_session = MagicMock()
    mock_session.scalars.side_effect = scalars_side_effect
    return mock_session


def _mock_product(product_id: str, implementation_config: dict) -> Mock:
    """A Product row stand-in carrying the three attributes create_media_buy reads off it."""
    product = Mock()
    product.product_id = product_id
    product.delivery_type = "guaranteed"
    product.implementation_config = implementation_config
    return product


def _create_media_buy_for(*products: Mock) -> tuple[Mock, dict]:
    """Run GoogleAdManager.create_media_buy over a stand-in adapter, one package per product.

    Package ids are ``pkg_<product_id>_001``. Returns the adapter stand-in -- its per-order
    maps (``_placement_targeting_map``, ``_native_template_ids``) are what create_media_buy
    fills -- and the products_map the orders manager received.
    """
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
    mock_adapter._native_template_ids = {}
    mock_adapter._order_name_template = None
    mock_adapter._line_item_name_template = None
    mock_adapter.targeting_manager = Mock()

    packages = []
    request_packages = []
    package_pricing_info = {}
    for product in products:
        package_id = f"pkg_{product.product_id}_001"

        media_package = Mock()
        media_package.package_id = package_id
        media_package.product_id = product.product_id
        media_package.targeting_overlay = None
        packages.append(media_package)

        # Request packages need real string attributes for Pydantic response construction
        request_package = Mock()
        request_package.package_id = package_id
        request_packages.append(request_package)

        package_pricing_info[package_id] = {
            "pricing_model": "cpm",
            "rate": 5.0,
            "currency": "USD",
            "is_fixed": True,
            "bid_price": None,
        }

    mock_request = Mock()
    mock_request.packages = request_packages
    mock_request.get_total_budget = Mock(return_value=10000)

    # Capture what products_map is passed to create_line_items; one line item id per package
    captured_products_map: dict = {}

    def capture_create_line_items(**kwargs):
        captured_products_map.update(kwargs.get("products_map", {}))
        return [f"li_{index:03d}" for index in range(1, len(products) + 1)]

    mock_adapter.orders_manager.create_line_items = Mock(side_effect=capture_create_line_items)
    mock_adapter.orders_manager.create_order = Mock(return_value="order_123")
    mock_adapter.orders_manager.approve_order = Mock(return_value=True)

    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_db.return_value.__enter__.return_value = _make_db_session(*products)

        GoogleAdManager.create_media_buy(
            mock_adapter,
            request=mock_request,
            packages=packages,
            start_time=datetime(2024, 1, 1, tzinfo=UTC),
            end_time=datetime(2024, 12, 31, tzinfo=UTC),
            package_pricing_info=package_pricing_info,
        )

    return mock_adapter, captured_products_map


def test_products_map_includes_delivery_type():
    """products_map must include delivery_type so line item type selection works.

    Bug: products_map was built as:
        {"product_id": ..., "implementation_config": ...}
    Missing delivery_type meant orders.py got None for product.get("delivery_type"),
    making is_guaranteed always False, selecting wrong line item type.
    """
    _adapter, captured_products_map = _create_media_buy_for(
        _mock_product("prod_abc", {"targeted_ad_unit_ids": ["12345"]})
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


def test_native_style_id_is_collected_only_for_packages_whose_product_names_one():
    """Each product's native_style_id reaches the creatives manager keyed by its package (GH #1516).

    The value lives in a JSON column and may arrive as a number; the manager compares
    template ids as strings, so the adapter normalizes once, here. A product with no
    native_style_id contributes no entry, so the manager refuses its native creatives
    instead of inventing a template.
    """
    adapter, _products_map = _create_media_buy_for(
        _mock_product("prod_native", {"targeted_ad_unit_ids": ["12345"], "native_style_id": 987654}),
        _mock_product("prod_display", {"targeted_ad_unit_ids": ["12345"]}),
    )

    assert adapter._native_template_ids == {"pkg_prod_native_001": "987654"}


def test_add_creative_assets_forwards_the_native_template_map():
    """The per-order maps built by create_media_buy reach the creatives manager by keyword."""
    adapter = Mock(spec=GoogleAdManager)
    adapter.creatives_manager = Mock()
    adapter._requires_manual_approval = Mock(return_value=False)
    adapter._placement_targeting_map = {"plc_1": "targeting_1"}
    adapter._native_template_ids = {"pkg_prod_abc_001": "987654"}
    today = datetime(2024, 1, 1, tzinfo=UTC)
    assets = [{"creative_id": "cr_1", "package_assignments": ["pkg_prod_abc_001"]}]

    GoogleAdManager.add_creative_assets(adapter, "9000001", assets, today)

    adapter.creatives_manager.add_creative_assets.assert_called_once_with(
        "9000001",
        assets,
        today,
        placement_targeting_map={"plc_1": "targeting_1"},
        native_template_ids={"pkg_prod_abc_001": "987654"},
    )
