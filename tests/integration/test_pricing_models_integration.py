"""Integration tests for pricing models (AdCP PR #88).

Tests the full flow: create product with pricing_options → get products → create media buy.
"""

from decimal import Decimal

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, PricingOption, Product, Tenant
from src.core.main import _create_media_buy_impl, _get_products_impl
from src.core.schemas import CreateMediaBuyRequest, GetProductsRequest, Package, PricingModel
from tests.utils.database_helpers import create_tenant_with_timestamps

pytestmark = pytest.mark.requires_db


@pytest.fixture
def setup_tenant_with_pricing_products(integration_db):
    """Create a tenant with products using various pricing models."""
    with get_db_session() as session:
        # Create tenant
        tenant = create_tenant_with_timestamps(
            tenant_id="test_pricing_tenant",
            name="Pricing Test Publisher",
            subdomain="pricing-test",
            ad_server="mock",
        )
        session.add(tenant)
        session.flush()

        # Add currency limit
        currency_limit = CurrencyLimit(
            tenant_id="test_pricing_tenant",
            currency_code="USD",
            max_daily_package_spend=Decimal("50000.00"),
        )
        session.add(currency_limit)

        # Product 1: CPM fixed rate
        product_cpm_fixed = Product(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpm_fixed",
            name="Display Ads - Fixed CPM",
            description="Standard display inventory",
            formats=["display_300x250", "display_728x90"],
            delivery_type="guaranteed",
            targeting_template={},
            implementation_config={},
        )
        session.add(product_cpm_fixed)
        session.flush()

        pricing_cpm_fixed = PricingOption(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpm_fixed",
            pricing_model="cpm",
            rate=Decimal("12.50"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_cpm_fixed)

        # Product 2: CPM auction
        product_cpm_auction = Product(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpm_auction",
            name="Display Ads - Auction CPM",
            description="Programmatic display inventory",
            formats=["display_300x250"],
            delivery_type="non-guaranteed",
            targeting_template={},
            implementation_config={},
        )
        session.add(product_cpm_auction)
        session.flush()

        pricing_cpm_auction = PricingOption(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpm_auction",
            pricing_model="cpm",
            rate=None,
            currency="USD",
            is_fixed=False,
            price_guidance={"floor": 8.0, "p25": 10.0, "p50": 12.0, "p75": 15.0, "p90": 18.0},
        )
        session.add(pricing_cpm_auction)

        # Product 3: CPCV fixed rate with min spend
        product_cpcv = Product(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpcv",
            name="Video Ads - CPCV",
            description="Cost per completed view video inventory",
            formats=["video_instream"],
            delivery_type="non-guaranteed",
            targeting_template={},
            implementation_config={},
        )
        session.add(product_cpcv)
        session.flush()

        pricing_cpcv = PricingOption(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpcv",
            pricing_model="cpcv",
            rate=Decimal("0.35"),
            currency="USD",
            is_fixed=True,
            min_spend_per_package=Decimal("5000.00"),
        )
        session.add(pricing_cpcv)

        # Product 4: Multiple pricing models
        product_multi = Product(
            tenant_id="test_pricing_tenant",
            product_id="prod_multi",
            name="Premium Package - Multiple Models",
            description="Choose your pricing model",
            formats=["display_300x250", "video_instream"],
            delivery_type="non-guaranteed",
            targeting_template={},
            implementation_config={},
        )
        session.add(product_multi)
        session.flush()

        # Add CPM option
        pricing_multi_cpm = PricingOption(
            tenant_id="test_pricing_tenant",
            product_id="prod_multi",
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_multi_cpm)

        # Add CPCV option
        pricing_multi_cpcv = PricingOption(
            tenant_id="test_pricing_tenant",
            product_id="prod_multi",
            pricing_model="cpcv",
            rate=Decimal("0.40"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_multi_cpcv)

        # Add CPP option with demographics
        pricing_multi_cpp = PricingOption(
            tenant_id="test_pricing_tenant",
            product_id="prod_multi",
            pricing_model="cpp",
            rate=Decimal("250.00"),
            currency="USD",
            is_fixed=True,
            parameters={"demographic": "A18-49", "min_points": 5.0},
            min_spend_per_package=Decimal("10000.00"),
        )
        session.add(pricing_multi_cpp)

        session.commit()

    yield

    # Cleanup
    with get_db_session() as session:
        session.query(PricingOption).filter_by(tenant_id="test_pricing_tenant").delete()
        session.query(Product).filter_by(tenant_id="test_pricing_tenant").delete()
        session.query(Tenant).filter_by(tenant_id="test_pricing_tenant").delete()
        session.commit()


@pytest.mark.requires_db
def test_get_products_returns_pricing_options(setup_tenant_with_pricing_products):
    """Test that get_products returns pricing_options for products."""
    request = GetProductsRequest(brief="display ads")

    # Mock context
    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_token"}})()

    tenant = {"tenant_id": "test_pricing_tenant", "name": "Test"}
    principal = type("Principal", (), {"principal_id": "test_principal", "tenant_id": "test_pricing_tenant"})()

    response = _get_products_impl(request, MockContext(), tenant, principal)

    assert response.products is not None
    assert len(response.products) > 0

    # Find the CPM fixed product
    cpm_product = next((p for p in response.products if p.product_id == "prod_cpm_fixed"), None)
    assert cpm_product is not None
    assert cpm_product.pricing_options is not None
    assert len(cpm_product.pricing_options) == 1
    assert cpm_product.pricing_options[0].pricing_model == PricingModel.CPM
    assert cpm_product.pricing_options[0].is_fixed is True
    assert cpm_product.pricing_options[0].rate == 12.50

    # Find the multi-pricing product
    multi_product = next((p for p in response.products if p.product_id == "prod_multi"), None)
    assert multi_product is not None
    assert multi_product.pricing_options is not None
    assert len(multi_product.pricing_options) == 3

    # Verify all three pricing models exist
    pricing_models = {opt.pricing_model for opt in multi_product.pricing_options}
    assert pricing_models == {PricingModel.CPM, PricingModel.CPCV, PricingModel.CPP}


@pytest.mark.requires_db
def test_create_media_buy_with_cpm_fixed_pricing(setup_tenant_with_pricing_products):
    """Test creating media buy with fixed CPM pricing."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_cpm_fixed"],
                pricing_model=PricingModel.CPM,
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_token"}})()

    tenant = {"tenant_id": "test_pricing_tenant", "name": "Test", "config": {"adapters": {"mock": {"enabled": True}}}}
    principal = type(
        "Principal",
        (),
        {
            "principal_id": "test_principal",
            "tenant_id": "test_pricing_tenant",
            "get_adapter_id": lambda self, adapter_type: "test_advertiser",
        },
    )()

    response = _create_media_buy_impl(request, MockContext(), tenant, principal)

    assert response.media_buy_id is not None
    assert response.status == "active"


@pytest.mark.requires_db
def test_create_media_buy_with_cpm_auction_pricing(setup_tenant_with_pricing_products):
    """Test creating media buy with auction CPM pricing."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_cpm_auction"],
                pricing_model=PricingModel.CPM,
                bid_price=15.0,  # Above floor of 8.0
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_token"}})()

    tenant = {"tenant_id": "test_pricing_tenant", "name": "Test", "config": {"adapters": {"mock": {"enabled": True}}}}
    principal = type(
        "Principal",
        (),
        {
            "principal_id": "test_principal",
            "tenant_id": "test_pricing_tenant",
            "get_adapter_id": lambda self, adapter_type: "test_advertiser",
        },
    )()

    response = _create_media_buy_impl(request, MockContext(), tenant, principal)

    assert response.media_buy_id is not None
    assert response.status == "active"


@pytest.mark.requires_db
def test_create_media_buy_auction_bid_below_floor_fails(setup_tenant_with_pricing_products):
    """Test that auction bid below floor price fails."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_cpm_auction"],
                pricing_model=PricingModel.CPM,
                bid_price=5.0,  # Below floor of 8.0
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_token"}})()

    tenant = {"tenant_id": "test_pricing_tenant", "name": "Test", "config": {"adapters": {"mock": {"enabled": True}}}}
    principal = type(
        "Principal",
        (),
        {
            "principal_id": "test_principal",
            "tenant_id": "test_pricing_tenant",
            "get_adapter_id": lambda self, adapter_type: "test_advertiser",
        },
    )()

    with pytest.raises(ValueError) as exc_info:
        _create_media_buy_impl(request, MockContext(), tenant, principal)

    assert "below floor price" in str(exc_info.value)


@pytest.mark.requires_db
def test_create_media_buy_with_cpcv_pricing(setup_tenant_with_pricing_products):
    """Test creating media buy with CPCV pricing."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_cpcv"],
                pricing_model=PricingModel.CPCV,
                budget=8000.0,  # Above min spend of 5000
            )
        ],
        budget={"total": 8000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_token"}})()

    tenant = {"tenant_id": "test_pricing_tenant", "name": "Test", "config": {"adapters": {"mock": {"enabled": True}}}}
    principal = type(
        "Principal",
        (),
        {
            "principal_id": "test_principal",
            "tenant_id": "test_pricing_tenant",
            "get_adapter_id": lambda self, adapter_type: "test_advertiser",
        },
    )()

    response = _create_media_buy_impl(request, MockContext(), tenant, principal)

    assert response.media_buy_id is not None
    assert response.status == "active"


@pytest.mark.requires_db
def test_create_media_buy_below_min_spend_fails(setup_tenant_with_pricing_products):
    """Test that budget below min_spend_per_package fails."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_cpcv"],
                pricing_model=PricingModel.CPCV,
                budget=3000.0,  # Below min spend of 5000
            )
        ],
        budget={"total": 3000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_token"}})()

    tenant = {"tenant_id": "test_pricing_tenant", "name": "Test", "config": {"adapters": {"mock": {"enabled": True}}}}
    principal = type(
        "Principal",
        (),
        {
            "principal_id": "test_principal",
            "tenant_id": "test_pricing_tenant",
            "get_adapter_id": lambda self, adapter_type: "test_advertiser",
        },
    )()

    with pytest.raises(ValueError) as exc_info:
        _create_media_buy_impl(request, MockContext(), tenant, principal)

    assert "below minimum spend" in str(exc_info.value)


@pytest.mark.requires_db
def test_create_media_buy_multi_pricing_choose_cpp(setup_tenant_with_pricing_products):
    """Test creating media buy choosing CPP from multi-pricing product."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_multi"],
                pricing_model=PricingModel.CPP,
                budget=15000.0,  # Above min spend of 10000
            )
        ],
        budget={"total": 15000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_token"}})()

    tenant = {"tenant_id": "test_pricing_tenant", "name": "Test", "config": {"adapters": {"mock": {"enabled": True}}}}
    principal = type(
        "Principal",
        (),
        {
            "principal_id": "test_principal",
            "tenant_id": "test_pricing_tenant",
            "get_adapter_id": lambda self, adapter_type: "test_advertiser",
        },
    )()

    response = _create_media_buy_impl(request, MockContext(), tenant, principal)

    assert response.media_buy_id is not None
    assert response.status == "active"


@pytest.mark.requires_db
def test_create_media_buy_invalid_pricing_model_fails(setup_tenant_with_pricing_products):
    """Test that requesting unavailable pricing model fails."""
    request = CreateMediaBuyRequest(
        promoted_offering="https://example.com/product",
        packages=[
            Package(
                package_id="pkg_1",
                products=["prod_cpm_fixed"],  # Only offers CPM
                pricing_model=PricingModel.CPCV,  # Requesting CPCV
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28",
    )

    class MockContext:
        http_request = type("Request", (), {"headers": {"x-adcp-auth": "test_token"}})()

    tenant = {"tenant_id": "test_pricing_tenant", "name": "Test", "config": {"adapters": {"mock": {"enabled": True}}}}
    principal = type(
        "Principal",
        (),
        {
            "principal_id": "test_principal",
            "tenant_id": "test_pricing_tenant",
            "get_adapter_id": lambda self, adapter_type: "test_advertiser",
        },
    )()

    with pytest.raises(ValueError) as exc_info:
        _create_media_buy_impl(request, MockContext(), tenant, principal)

    assert "does not offer pricing model" in str(exc_info.value)
