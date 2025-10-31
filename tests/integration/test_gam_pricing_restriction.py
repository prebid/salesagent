"""Integration test for GAM pricing model restrictions (AdCP PR #88).

Tests that GAM adapter properly enforces CPM-only restriction.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    CurrencyLimit,
    MediaBuy,
    MediaPackage,
    PricingOption,
    Principal,
    Product,
    PropertyTag,
    Tenant,
)
from src.core.schemas import CreateMediaBuyRequest, Package, PricingModel
from src.core.tool_context import ToolContext
from tests.utils.database_helpers import create_tenant_with_timestamps

# Tests are now AdCP 2.4 compliant (removed status field, using errors field)
pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def setup_gam_tenant_with_non_cpm_product(integration_db):
    """Create a GAM tenant with a product offering non-CPM pricing."""
    with get_db_session() as session:
        # Create GAM tenant
        tenant = create_tenant_with_timestamps(
            tenant_id="test_gam_tenant",
            name="GAM Test Publisher",
            subdomain="gam-test",
            ad_server="google_ad_manager",
        )
        session.add(tenant)
        session.flush()

        # Add adapter config (mock mode for testing)
        adapter_config = AdapterConfig(
            tenant_id="test_gam_tenant",
            adapter_type="google_ad_manager",
            gam_network_code="123456",
            gam_trafficker_id="987654",
        )
        session.add(adapter_config)

        # Add currency limit
        currency_limit = CurrencyLimit(
            tenant_id="test_gam_tenant",
            currency_code="USD",
            max_daily_package_spend=Decimal("50000.00"),
        )
        session.add(currency_limit)

        # Add property tag (required for products)
        property_tag = PropertyTag(
            tenant_id="test_gam_tenant",
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)

        # Create principal
        principal = Principal(
            tenant_id="test_gam_tenant",
            principal_id="test_advertiser",
            name="Test Advertiser",
            access_token="test_gam_token",
            platform_mappings={"google_ad_manager": {"advertiser_id": "987654321"}},
        )
        session.add(principal)

        # Create product with CPCV pricing (not supported by GAM)
        product = Product(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_cpcv",
            name="Video Ads - CPCV",
            description="Video inventory with CPCV pricing",
            formats=["video_instream"],
            delivery_type="non_guaranteed",
            property_tags=["all_inventory"],
            targeting_template={},
            implementation_config={},
        )
        session.add(product)
        session.flush()

        # Add CPCV pricing option
        pricing_cpcv = PricingOption(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_cpcv",
            pricing_model="cpcv",
            rate=Decimal("0.40"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_cpcv)

        # Create product with CPM pricing (supported by GAM)
        product_cpm = Product(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_cpm",
            name="Display Ads - CPM",
            description="Display inventory with CPM pricing",
            formats=["display_300x250"],
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
            targeting_template={},
            implementation_config={},
        )
        session.add(product_cpm)
        session.flush()

        # Add CPM pricing option
        pricing_cpm = PricingOption(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_cpm",
            pricing_model="cpm",
            rate=Decimal("12.50"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_cpm)

        # Create product with multiple pricing models including non-CPM
        product_multi = Product(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_multi",
            name="Premium Package",
            description="Multiple pricing models (some unsupported)",
            formats=["display_300x250", "video_instream"],
            delivery_type="non_guaranteed",
            property_tags=["all_inventory"],
            targeting_template={},
            implementation_config={},
        )
        session.add(product_multi)
        session.flush()

        # Add CPM (supported)
        pricing_multi_cpm = PricingOption(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_multi",
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_multi_cpm)

        # Add CPP (not supported by GAM)
        pricing_multi_cpp = PricingOption(
            tenant_id="test_gam_tenant",
            product_id="prod_gam_multi",
            pricing_model="cpp",
            rate=Decimal("250.00"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters={"demographic": "A18-49"},
            min_spend_per_package=None,
        )
        session.add(pricing_multi_cpp)

        session.commit()

    yield

    # Cleanup
    with get_db_session() as session:
        from sqlalchemy import delete, select

        # Delete media packages first (join through media_buy to filter by tenant)
        media_buy_ids_stmt = select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == "test_gam_tenant")
        media_buy_ids = [row[0] for row in session.execute(media_buy_ids_stmt)]
        if media_buy_ids:
            session.execute(delete(MediaPackage).where(MediaPackage.media_buy_id.in_(media_buy_ids)))

        # Delete in order of foreign key dependencies
        session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == "test_gam_tenant"))
        session.execute(delete(PricingOption).where(PricingOption.tenant_id == "test_gam_tenant"))
        session.execute(delete(Product).where(Product.tenant_id == "test_gam_tenant"))
        session.execute(delete(PropertyTag).where(PropertyTag.tenant_id == "test_gam_tenant"))
        session.execute(delete(Principal).where(Principal.tenant_id == "test_gam_tenant"))
        session.execute(delete(AdapterConfig).where(AdapterConfig.tenant_id == "test_gam_tenant"))
        session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == "test_gam_tenant"))
        session.execute(delete(Tenant).where(Tenant.tenant_id == "test_gam_tenant"))
        session.commit()


@pytest.mark.requires_db
async def test_gam_rejects_cpcv_pricing_model(setup_gam_tenant_with_non_cpm_product):
    """Test that GAM adapter rejects CPCV pricing model with clear error."""
    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            Package(
                package_id="pkg_1",
                product_id="prod_gam_cpcv",  # Use product_id instead of products array
                pricing_model=PricingModel.CPCV,  # Not supported by GAM
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
        tenant_id="test_gam_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    from src.core.tools.media_buy_create import _create_media_buy_impl

    # GAM adapter rejects unsupported pricing models during validation
    # In dry_run mode, this manifests as media_buy_id=None which triggers ToolError
    with pytest.raises(Exception) as exc_info:
        await _create_media_buy_impl(
            buyer_ref=request.buyer_ref,
            brand_manifest=request.brand_manifest,
            packages=request.packages,
            start_time=request.start_time,
            end_time=request.end_time,
            budget=request.budget,
            context=context,
        )

    error_msg = str(exc_info.value).lower()
    # Check error indicates CPCV/pricing model rejection or media_buy_id failure
    assert (
        "cpcv" in error_msg
        or "pricing" in error_msg
        or "not supported" in error_msg
        or "media_buy_id" in error_msg
        or "gam" in error_msg
    ), f"Expected pricing/GAM error, got: {error_msg}"


@pytest.mark.requires_db
async def test_gam_accepts_cpm_pricing_model(setup_gam_tenant_with_non_cpm_product):
    """Test that GAM adapter accepts CPM pricing model."""
    from src.core.tools.media_buy_create import _create_media_buy_impl

    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            Package(
                package_id="pkg_1",
                product_id="prod_gam_cpm",  # Use product_id instead of products array
                pricing_model=PricingModel.CPM,  # Supported by GAM
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
        tenant_id="test_gam_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    # This should succeed
    response = await _create_media_buy_impl(
        buyer_ref=request.buyer_ref,
        brand_manifest=request.brand_manifest,
        packages=request.packages,
        start_time=request.start_time,
        end_time=request.end_time,
        budget=request.budget,
        context=context,
    )

    # Verify response (AdCP 2.4 compliant)
    assert response.media_buy_id is not None
    # Success = no errors (or empty errors list)
    assert response.errors == [] or response.errors is None


@pytest.mark.requires_db
async def test_gam_rejects_cpp_from_multi_pricing_product(setup_gam_tenant_with_non_cpm_product):
    """Test that GAM adapter rejects CPP when buyer chooses it from multi-pricing product."""
    from src.core.tools.media_buy_create import _create_media_buy_impl

    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            Package(
                package_id="pkg_1",
                product_id="prod_gam_multi",  # Use product_id instead of products array
                pricing_model=PricingModel.CPP,  # Not supported by GAM
                budget=15000.0,
            )
        ],
        budget={"total": 15000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-02-01T00:00:00Z",
        end_time="2026-02-28T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_gam_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    # GAM adapter rejects unsupported pricing models during validation
    # In dry_run mode, this manifests as media_buy_id=None which triggers ToolError
    with pytest.raises(Exception) as exc_info:
        response = await _create_media_buy_impl(
            buyer_ref=request.buyer_ref,
            brand_manifest=request.brand_manifest,
            packages=request.packages,
            start_time=request.start_time,
            end_time=request.end_time,
            budget=request.budget,
            context=context,
        )

    # Check error message indicates CPP/pricing model rejection
    error_msg = str(exc_info.value).lower()
    assert "cpp" in error_msg or "pricing" in error_msg or "not supported" in error_msg or "media_buy_id" in error_msg


@pytest.mark.requires_db
async def test_gam_accepts_cpm_from_multi_pricing_product(setup_gam_tenant_with_non_cpm_product):
    """Test that GAM adapter accepts CPM when buyer chooses it from multi-pricing product."""
    from src.core.tools.media_buy_create import _create_media_buy_impl

    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            Package(
                package_id="pkg_1",
                product_id="prod_gam_multi",  # Use product_id instead of products array
                pricing_model=PricingModel.CPM,  # Supported by GAM
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
        tenant_id="test_gam_tenant",
        principal_id="test_advertiser",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    # This should succeed - buyer chose CPM from multi-option product
    response = await _create_media_buy_impl(
        buyer_ref=request.buyer_ref,
        brand_manifest=request.brand_manifest,
        packages=request.packages,
        start_time=request.start_time,
        end_time=request.end_time,
        budget=request.budget,
        context=context,
    )

    # Verify response (AdCP 2.4 compliant)
    assert response.media_buy_id is not None
    # Success = no errors (or empty errors list)
    assert response.errors == [] or response.errors is None
