"""Integration tests for pricing models (AdCP PR #88).

Tests the full flow: create product with pricing_options → get products → create media buy.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, PricingOption, Principal, Product, PropertyTag, Tenant
from src.core.schema_adapters import GetProductsRequest
from src.core.schemas import CreateMediaBuyRequest, PricingModel
from src.core.tool_context import ToolContext
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.products import _get_products_impl
from tests.helpers.adcp_factories import create_test_package_request
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

        # Add property tag (required for products)
        property_tag = PropertyTag(
            tenant_id="test_pricing_tenant",
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)

        # Add currency limit
        currency_limit = CurrencyLimit(
            tenant_id="test_pricing_tenant",
            currency_code="USD",
            max_daily_package_spend=Decimal("50000.00"),
        )
        session.add(currency_limit)

        # Add principal for authentication
        principal = Principal(
            tenant_id="test_pricing_tenant",
            principal_id="test_advertiser",
            name="Test Advertiser",
            access_token="test_token",
            platform_mappings={"mock": {"advertiser_id": "mock_adv_123"}},
        )
        session.add(principal)

        # Product 1: CPM fixed rate
        product_cpm_fixed = Product(
            tenant_id="test_pricing_tenant",
            product_id="prod_cpm_fixed",
            name="Display Ads - Fixed CPM",
            description="Standard display inventory",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
            ],
            delivery_type="guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
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
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="non_guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
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
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_instream"}],
            delivery_type="non_guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
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
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_instream"},
            ],
            delivery_type="non_guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
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
        from sqlalchemy import delete, select

        from src.core.database.models import MediaBuy, MediaPackage

        # Delete in correct order to respect foreign keys
        # 1. Delete child records first (MediaPackage references MediaBuy)
        # Note: MediaPackage doesn't have tenant_id directly, must filter by media_buy_id
        tenant_media_buy_ids = session.scalars(
            select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == "test_pricing_tenant")
        ).all()
        if tenant_media_buy_ids:
            session.execute(delete(MediaPackage).where(MediaPackage.media_buy_id.in_(tenant_media_buy_ids)))
        session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == "test_pricing_tenant"))

        # 2. Delete product-related records
        session.execute(delete(PricingOption).where(PricingOption.tenant_id == "test_pricing_tenant"))
        session.execute(delete(Product).where(Product.tenant_id == "test_pricing_tenant"))
        session.execute(delete(PropertyTag).where(PropertyTag.tenant_id == "test_pricing_tenant"))

        # 3. Delete principal and tenant records
        session.execute(delete(Principal).where(Principal.tenant_id == "test_pricing_tenant"))
        session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == "test_pricing_tenant"))
        session.execute(delete(Tenant).where(Tenant.tenant_id == "test_pricing_tenant"))
        session.commit()


@pytest.mark.requires_db
async def test_get_products_returns_pricing_options(setup_tenant_with_pricing_products):
    """Test that get_products returns pricing_options for products."""
    request = GetProductsRequest(brief="display ads", brand_manifest={"name": "Test Brand"})

    # Create context
    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_pricing_tenant",
        principal_id="test_advertiser",
        tool_name="get_products",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    response = await _get_products_impl(request, context)

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
async def test_create_media_buy_with_cpm_fixed_pricing(setup_tenant_with_pricing_products):
    """Test creating media buy with fixed CPM pricing."""
    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_1",
                product_id="prod_cpm_fixed",
                pricing_option_id="cpm_usd_fixed",  # Format: {model}_{currency}_{fixed|auction}
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-02-01T00:00:00Z",
        end_time="2026-02-28T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_pricing_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    response = await _create_media_buy_impl(
        buyer_ref=request.buyer_ref,
        brand_manifest=request.brand_manifest,
        packages=request.packages,
        start_time=request.start_time,
        end_time=request.end_time,
        budget=request.budget,
        ctx=context,
        context=None,
    )

    # Verify response is success (AdCP 2.4 compliant)
    # Success response has media_buy_id, error response has errors field
    assert (
        not hasattr(response, "errors") or response.errors is None or response.errors == []
    ), f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown error'}"
    assert response.media_buy_id is not None


@pytest.mark.requires_db
async def test_create_media_buy_with_cpm_auction_pricing(setup_tenant_with_pricing_products):
    """Test creating media buy with auction CPM pricing."""
    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_1",
                product_id="prod_cpm_auction",
                pricing_option_id="cpm_usd_auction",  # Format: {model}_{currency}_{fixed|auction}
                bid_price=15.0,  # Above floor of 8.0
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-02-01T00:00:00Z",
        end_time="2026-02-28T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_pricing_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    response = await _create_media_buy_impl(
        buyer_ref=request.buyer_ref,
        brand_manifest=request.brand_manifest,
        packages=request.packages,
        start_time=request.start_time,
        end_time=request.end_time,
        budget=request.budget,
        context=None,
        ctx=context,
    )

    # Verify response is success (AdCP 2.4 compliant)
    # Success response has media_buy_id, error response has errors field
    assert (
        not hasattr(response, "errors") or response.errors is None or response.errors == []
    ), f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown error'}"
    assert response.media_buy_id is not None


@pytest.mark.requires_db
async def test_create_media_buy_auction_bid_below_floor_fails(setup_tenant_with_pricing_products):
    """Test that auction bid below floor price fails."""
    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_1",
                product_id="prod_cpm_auction",
                pricing_option_id="cpm_usd_auction",  # Format: {model}_{currency}_{fixed|auction}
                bid_price=5.0,  # Below floor of 8.0
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-02-01T00:00:00Z",
        end_time="2026-02-28T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_pricing_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    # AdCP 2.4 spec: Errors are returned in response.errors, not raised as exceptions
    response = await _create_media_buy_impl(
        buyer_ref=request.buyer_ref,
        brand_manifest=request.brand_manifest,
        packages=request.packages,
        start_time=request.start_time,
        end_time=request.end_time,
        budget=request.budget,
        ctx=context,
        context=None,
    )

    # Check for errors in response (AdCP 2.4 compliant)
    assert response.errors is not None and len(response.errors) > 0, "Expected errors in response"
    error_messages = " ".join(str(e) for e in response.errors)
    assert "below floor price" in error_messages.lower() or "floor" in error_messages.lower()


@pytest.mark.requires_db
async def test_create_media_buy_with_cpcv_pricing(setup_tenant_with_pricing_products):
    """Test creating media buy with CPCV pricing."""
    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_1",
                product_id="prod_cpcv",
                pricing_option_id="cpcv_usd_fixed",  # Format: {model}_{currency}_{fixed|auction}
                budget=8000.0,  # Above min spend of 5000
            )
        ],
        budget={"total": 8000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-02-01T00:00:00Z",
        end_time="2026-02-28T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_pricing_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    response = await _create_media_buy_impl(
        buyer_ref=request.buyer_ref,
        brand_manifest=request.brand_manifest,
        packages=request.packages,
        start_time=request.start_time,
        end_time=request.end_time,
        budget=request.budget,
        ctx=context,
        context=None,
    )

    # Verify response is success (AdCP 2.4 compliant)
    # Success response has media_buy_id, error response has errors field
    assert (
        not hasattr(response, "errors") or response.errors is None or response.errors == []
    ), f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown error'}"
    assert response.media_buy_id is not None


@pytest.mark.requires_db
async def test_create_media_buy_below_min_spend_fails(setup_tenant_with_pricing_products):
    """Test that budget below min_spend_per_package fails."""
    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_1",
                product_id="prod_cpcv",
                pricing_option_id="cpcv_usd_fixed",  # Format: {model}_{currency}_{fixed|auction}
                budget=3000.0,  # Below min spend of 5000
            )
        ],
        budget={"total": 3000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-02-01T00:00:00Z",
        end_time="2026-02-28T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_pricing_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    # AdCP 2.4 spec: Errors are returned in response.errors, not raised as exceptions
    response = await _create_media_buy_impl(
        buyer_ref=request.buyer_ref,
        brand_manifest=request.brand_manifest,
        packages=request.packages,
        start_time=request.start_time,
        end_time=request.end_time,
        budget=request.budget,
        context=None,
        ctx=context,
    )

    # Check for errors in response (AdCP 2.4 compliant)
    assert response.errors is not None and len(response.errors) > 0, "Expected errors in response"
    error_messages = " ".join(str(e) for e in response.errors)
    assert "below minimum spend" in error_messages.lower() or "minimum" in error_messages.lower()


@pytest.mark.requires_db
async def test_create_media_buy_multi_pricing_choose_cpp(setup_tenant_with_pricing_products):
    """Test creating media buy choosing CPP from multi-pricing product."""
    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_1",
                product_id="prod_multi",
                pricing_option_id="cpp_usd_fixed",  # Format: {model}_{currency}_{fixed|auction}
                budget=15000.0,  # Above min spend of 10000
            )
        ],
        budget={"total": 15000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-02-01T00:00:00Z",
        end_time="2026-02-28T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_pricing_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    response = await _create_media_buy_impl(
        buyer_ref=request.buyer_ref,
        brand_manifest=request.brand_manifest,
        packages=request.packages,
        start_time=request.start_time,
        end_time=request.end_time,
        budget=request.budget,
        ctx=context,
        context=None,
    )

    # Verify response is success (AdCP 2.4 compliant)
    # Success response has media_buy_id, error response has errors field
    assert (
        not hasattr(response, "errors") or response.errors is None or response.errors == []
    ), f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown error'}"
    assert response.media_buy_id is not None


@pytest.mark.requires_db
async def test_create_media_buy_invalid_pricing_model_fails(setup_tenant_with_pricing_products):
    """Test that requesting unavailable pricing model fails."""
    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_1",
                product_id="prod_cpm_fixed",  # Only offers CPM
                pricing_option_id="cpcv_usd_fixed",  # Requesting CPCV (should fail)
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-02-01T00:00:00Z",
        end_time="2026-02-28T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_pricing_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    # AdCP 2.4 spec: Errors are returned in response.errors, not raised as exceptions
    response = await _create_media_buy_impl(
        buyer_ref=request.buyer_ref,
        brand_manifest=request.brand_manifest,
        packages=request.packages,
        start_time=request.start_time,
        end_time=request.end_time,
        budget=request.budget,
        ctx=context,
        context=None,
    )

    # Check for errors in response (AdCP 2.4 compliant)
    assert response.errors is not None and len(response.errors) > 0, "Expected errors in response"
    error_messages = " ".join(str(e) for e in response.errors)
    assert "does not offer pricing model" in error_messages.lower() or "pricing" in error_messages.lower()
