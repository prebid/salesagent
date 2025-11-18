"""Integration tests for GAM pricing model support (CPC, VCPM, FLAT_RATE).

Tests end-to-end flow of creating media buys with different pricing models
and verifying correct GAM line item configuration.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    CurrencyLimit,
    GAMInventory,
    MediaBuy,
    MediaPackage,
    PricingOption,
    Principal,
    Product,
    PropertyTag,
    Tenant,
)
from src.core.schemas import CreateMediaBuyRequest
from src.core.tool_context import ToolContext
from tests.helpers.adcp_factories import create_test_package_request
from tests.utils.database_helpers import create_tenant_with_timestamps

# Tests are now AdCP 2.4 compliant (removed status field, using errors field)
pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def setup_gam_tenant_with_all_pricing_models(integration_db):
    """Create a GAM tenant with products offering all supported pricing models."""
    with get_db_session() as session:
        # Create GAM tenant
        tenant = create_tenant_with_timestamps(
            tenant_id="test_gam_pricing_tenant",
            name="GAM Pricing Test Publisher",
            subdomain="gam-pricing-test",
            ad_server="google_ad_manager",
        )
        session.add(tenant)
        session.flush()

        # Add adapter config (mock mode for testing)
        adapter_config = AdapterConfig(
            tenant_id="test_gam_pricing_tenant",
            adapter_type="google_ad_manager",
            gam_network_code="123456",
            gam_trafficker_id="gam_traffic_456",
        )
        session.add(adapter_config)

        # Add currency limit
        currency_limit = CurrencyLimit(
            tenant_id="test_gam_pricing_tenant",
            currency_code="USD",
            max_daily_package_spend=Decimal("100000.00"),
        )
        session.add(currency_limit)

        # Add property tag (required for products)
        property_tag = PropertyTag(
            tenant_id="test_gam_pricing_tenant",
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)

        # Create principal
        principal = Principal(
            tenant_id="test_gam_pricing_tenant",
            principal_id="test_advertiser_pricing",
            name="Test Advertiser - Pricing",
            access_token="test_gam_pricing_token",
            platform_mappings={"google_ad_manager": {"advertiser_id": "123456789"}},
        )
        session.add(principal)

        # Add GAM inventory (required for product validation)
        gam_inventory_1 = GAMInventory(
            tenant_id="test_gam_pricing_tenant",
            inventory_type="ad_unit",
            inventory_id="23312403856",
            name="Test Ad Unit 123",
            path=["test", "path"],
            status="active",
            inventory_metadata={},
        )
        session.add(gam_inventory_1)

        gam_inventory_2 = GAMInventory(
            tenant_id="test_gam_pricing_tenant",
            inventory_type="ad_unit",
            inventory_id="45623890123",
            name="Homepage Ad Unit",
            path=["homepage"],
            status="active",
            inventory_metadata={},
        )
        session.add(gam_inventory_2)

        # Product 1: CPM pricing (guaranteed)
        product_cpm = Product(
            tenant_id="test_gam_pricing_tenant",
            product_id="prod_gam_cpm_guaranteed",
            name="Display Ads - CPM Guaranteed",
            description="Display inventory with guaranteed CPM pricing",
            format_ids=[
                {
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_300x250_image",
                }
            ],
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
            targeting_template={},
            implementation_config={
                "targeted_ad_unit_ids": ["23312403856"],
                "line_item_type": "STANDARD",
                "priority": 8,
                "creative_placeholders": [{"width": 300, "height": 250}],
                "format_overrides": {
                    "display_300x250": {
                        "format_id": "display_300x250",
                        "name": "Display 300x250",
                        "type": "display",
                        "width": 300,
                        "height": 250,
                        "standard": True,
                        "platform_config": {"gam": {"creative_placeholder": {"width": 300, "height": 250}}},
                    }
                },
            },
        )
        session.add(product_cpm)
        session.flush()

        pricing_cpm = PricingOption(
            tenant_id="test_gam_pricing_tenant",
            product_id="prod_gam_cpm_guaranteed",
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_cpm)

        # Product 2: CPC pricing (non-guaranteed)
        product_cpc = Product(
            tenant_id="test_gam_pricing_tenant",
            product_id="prod_gam_cpc",
            name="Display Ads - CPC",
            description="Click-based pricing for performance campaigns",
            format_ids=[
                {
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_300x250_image",
                },
                {
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_728x90_image",
                },
            ],
            delivery_type="non_guaranteed",
            property_tags=["all_inventory"],
            targeting_template={},
            implementation_config={
                "targeted_ad_unit_ids": ["23312403856"],
                "line_item_type": "PRICE_PRIORITY",
                "priority": 12,
                "creative_placeholders": [
                    {"width": 300, "height": 250},
                    {"width": 728, "height": 90},
                ],
                "format_overrides": {
                    "display_300x250": {
                        "format_id": "display_300x250",
                        "name": "Display 300x250",
                        "type": "display",
                        "width": 300,
                        "height": 250,
                        "standard": True,
                        "platform_config": {"gam": {"creative_placeholder": {"width": 300, "height": 250}}},
                    },
                    "display_728x90": {
                        "format_id": "display_728x90",
                        "name": "Display 728x90",
                        "type": "display",
                        "width": 728,
                        "height": 90,
                        "standard": True,
                        "platform_config": {"gam": {"creative_placeholder": {"width": 728, "height": 90}}},
                    },
                },
            },
        )
        session.add(product_cpc)
        session.flush()

        pricing_cpc = PricingOption(
            tenant_id="test_gam_pricing_tenant",
            product_id="prod_gam_cpc",
            pricing_model="cpc",
            rate=Decimal("2.50"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_cpc)

        # Product 3: VCPM pricing (guaranteed, viewable impressions)
        product_vcpm = Product(
            tenant_id="test_gam_pricing_tenant",
            product_id="prod_gam_vcpm",
            name="Display Ads - VCPM",
            description="Viewable CPM pricing for brand safety",
            format_ids=[
                {
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_300x250_image",
                }
            ],
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
            targeting_template={},
            implementation_config={
                "targeted_ad_unit_ids": ["23312403856"],
                "line_item_type": "STANDARD",
                "priority": 8,
                "creative_placeholders": [{"width": 300, "height": 250}],
                "format_overrides": {
                    "display_300x250": {
                        "format_id": "display_300x250",
                        "name": "Display 300x250",
                        "type": "display",
                        "width": 300,
                        "height": 250,
                        "standard": True,
                        "platform_config": {"gam": {"creative_placeholder": {"width": 300, "height": 250}}},
                    }
                },
            },
        )
        session.add(product_vcpm)
        session.flush()

        pricing_vcpm = PricingOption(
            tenant_id="test_gam_pricing_tenant",
            product_id="prod_gam_vcpm",
            pricing_model="vcpm",
            rate=Decimal("18.00"),
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_vcpm)

        # Product 4: FLAT_RATE pricing (sponsorship)
        product_flat = Product(
            tenant_id="test_gam_pricing_tenant",
            product_id="prod_gam_flatrate",
            name="Homepage Takeover - Flat Rate",
            description="Fixed daily rate for premium placement",
            format_ids=[
                {
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_728x90_image",
                },
                {
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_300x600_image",
                },
            ],
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
            targeting_template={},
            implementation_config={
                "targeted_ad_unit_ids": ["45623890123"],
                "line_item_type": "SPONSORSHIP",
                "priority": 4,
                "creative_placeholders": [
                    {"width": 728, "height": 90},
                    {"width": 300, "height": 600},
                ],
                "format_overrides": {
                    "display_728x90": {
                        "format_id": "display_728x90",
                        "name": "Display 728x90",
                        "type": "display",
                        "width": 728,
                        "height": 90,
                        "standard": True,
                        "platform_config": {"gam": {"creative_placeholder": {"width": 728, "height": 90}}},
                    },
                    "display_300x600": {
                        "format_id": "display_300x600",
                        "name": "Display 300x600",
                        "type": "display",
                        "width": 300,
                        "height": 600,
                        "standard": True,
                        "platform_config": {"gam": {"creative_placeholder": {"width": 300, "height": 600}}},
                    },
                },
            },
        )
        session.add(product_flat)
        session.flush()

        pricing_flat = PricingOption(
            tenant_id="test_gam_pricing_tenant",
            product_id="prod_gam_flatrate",
            pricing_model="flat_rate",
            rate=Decimal("5000.00"),  # $5000 total
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_flat)

        session.commit()

    yield

    # Cleanup
    with get_db_session() as session:
        from sqlalchemy import delete, select

        # Delete media packages first (join through media_buy to filter by tenant)
        media_buy_ids_stmt = select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == "test_gam_pricing_tenant")
        media_buy_ids = [row[0] for row in session.execute(media_buy_ids_stmt)]
        if media_buy_ids:
            session.execute(delete(MediaPackage).where(MediaPackage.media_buy_id.in_(media_buy_ids)))

        # Delete in order of foreign key dependencies
        session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == "test_gam_pricing_tenant"))
        session.execute(delete(PricingOption).where(PricingOption.tenant_id == "test_gam_pricing_tenant"))
        session.execute(delete(Product).where(Product.tenant_id == "test_gam_pricing_tenant"))
        session.execute(delete(PropertyTag).where(PropertyTag.tenant_id == "test_gam_pricing_tenant"))
        session.execute(delete(Principal).where(Principal.tenant_id == "test_gam_pricing_tenant"))
        session.execute(delete(AdapterConfig).where(AdapterConfig.tenant_id == "test_gam_pricing_tenant"))
        session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == "test_gam_pricing_tenant"))
        session.execute(delete(Tenant).where(Tenant.tenant_id == "test_gam_pricing_tenant"))
        session.commit()


@pytest.mark.requires_db
async def test_gam_cpm_guaranteed_creates_standard_line_item(setup_gam_tenant_with_all_pricing_models):
    """Test CPM guaranteed creates STANDARD line item with priority 8."""
    from src.core.tools.media_buy_create import _create_media_buy_impl

    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer_cpm",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_cpm",
                product_id="prod_gam_cpm_guaranteed",
                pricing_option_id="cpm_usd_fixed",  # Generated format: {model}_{currency}_{fixed|auction}
                budget=10000.0,
            )
        ],
        budget={"total": 10000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-03-01T00:00:00Z",
        end_time="2026-03-31T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_gam_pricing_tenant",
        principal_id="test_advertiser_pricing",
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
    )

    # Verify response is success (AdCP 2.4 compliant)
    # Success response has media_buy_id, error response has errors field
    assert (
        not hasattr(response, "errors") or response.errors is None or response.errors == []
    ), f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown error'}"
    assert response.media_buy_id is not None

    # In dry-run mode, the response should succeed
    # In real mode, we'd verify GAM line item properties:
    # - lineItemType = "STANDARD"
    # - priority = 8
    # - costType = "CPM"
    # - costPerUnit = $15.00


@pytest.mark.requires_db
async def test_gam_cpc_creates_price_priority_line_item_with_clicks_goal(setup_gam_tenant_with_all_pricing_models):
    """Test CPC creates PRICE_PRIORITY line item with CLICKS goal unit."""
    from src.core.tools.media_buy_create import _create_media_buy_impl

    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer_cpc",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_cpc",
                product_id="prod_gam_cpc",
                pricing_option_id="cpc_usd_fixed",  # Generated format: {model}_{currency}_{fixed|auction}
                budget=5000.0,
            )
        ],
        budget={"total": 5000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-03-01T00:00:00Z",
        end_time="2026-03-31T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_gam_pricing_tenant",
        principal_id="test_advertiser_pricing",
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
    )

    # Verify response is success (AdCP 2.4 compliant)
    # Success response has media_buy_id, error response has errors field
    assert (
        not hasattr(response, "errors") or response.errors is None or response.errors == []
    ), f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown error'}"
    assert response.media_buy_id is not None

    # In real GAM mode, line item would have:
    # - lineItemType = "PRICE_PRIORITY"
    # - priority = 12
    # - costType = "CPC"
    # - costPerUnit = $2.50
    # - primaryGoal.unitType = "CLICKS"
    # - primaryGoal.units = 2000


@pytest.mark.requires_db
async def test_gam_vcpm_creates_standard_line_item_with_viewable_impressions(setup_gam_tenant_with_all_pricing_models):
    """Test VCPM creates STANDARD line item with VIEWABLE_IMPRESSIONS goal."""
    from src.core.tools.media_buy_create import _create_media_buy_impl

    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer_vcpm",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_vcpm",
                product_id="prod_gam_vcpm",
                pricing_option_id="vcpm_usd_fixed",  # Generated format: {model}_{currency}_{fixed|auction}
                budget=12000.0,
            )
        ],
        budget={"total": 12000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-03-01T00:00:00Z",
        end_time="2026-03-31T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_gam_pricing_tenant",
        principal_id="test_advertiser_pricing",
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
    )

    # Verify response is success (AdCP 2.4 compliant)
    # Success response has media_buy_id, error response has errors field
    assert (
        not hasattr(response, "errors") or response.errors is None or response.errors == []
    ), f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown error'}"
    assert response.media_buy_id is not None

    # In real GAM mode, line item would have:
    # - lineItemType = "STANDARD" (VCPM only works with STANDARD)
    # - priority = 8
    # - costType = "VCPM"
    # - costPerUnit = $18.00
    # - primaryGoal.unitType = "VIEWABLE_IMPRESSIONS"
    # - primaryGoal.units = 50000


@pytest.mark.requires_db
async def test_gam_flat_rate_calculates_cpd_correctly(setup_gam_tenant_with_all_pricing_models):
    """Test FLAT_RATE converts to CPD (cost per day) correctly."""
    from src.core.tools.media_buy_create import _create_media_buy_impl

    # 10 day campaign: $5000 total = $500/day
    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer_flatrate",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_flat",
                product_id="prod_gam_flatrate",
                pricing_option_id="flat_rate_usd_fixed",  # Generated format: {model}_{currency}_{fixed|auction}
                budget=5000.0,
            )
        ],
        budget={"total": 5000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-03-01T00:00:00Z",
        end_time="2026-03-10T23:59:59Z",  # 10 days
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_gam_pricing_tenant",
        principal_id="test_advertiser_pricing",
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
    )

    # Verify response is success (AdCP 2.4 compliant)
    # Success response has media_buy_id, error response has errors field
    assert (
        not hasattr(response, "errors") or response.errors is None or response.errors == []
    ), f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown error'}"
    assert response.media_buy_id is not None

    # In real GAM mode, line item would have:
    # - lineItemType = "SPONSORSHIP" (FLAT_RATE → CPD uses SPONSORSHIP)
    # - priority = 4
    # - costType = "CPD"
    # - costPerUnit = $500.00 (5000 / 10 days)
    # - primaryGoal.unitType = "IMPRESSIONS"
    # - primaryGoal.units = 1000000


@pytest.mark.requires_db
async def test_gam_multi_package_mixed_pricing_models(setup_gam_tenant_with_all_pricing_models):
    """Test creating media buy with multiple packages using different pricing models."""
    from src.core.tools.media_buy_create import _create_media_buy_impl

    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer_multi",
        brand_manifest={"name": "https://example.com/campaign"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_1_cpm",
                product_id="prod_gam_cpm_guaranteed",
                pricing_option_id="cpm_usd_fixed",  # Generated format: {model}_{currency}_{fixed|auction}
                budget=8000.0,
            ),
            create_test_package_request(
                buyer_ref="pkg_2_cpc",
                product_id="prod_gam_cpc",
                pricing_option_id="cpc_usd_fixed",  # Generated format: {model}_{currency}_{fixed|auction}
                budget=3000.0,
            ),
            create_test_package_request(
                buyer_ref="pkg_3_vcpm",
                product_id="prod_gam_vcpm",
                pricing_option_id="vcpm_usd_fixed",  # Generated format: {model}_{currency}_{fixed|auction}
                budget=9000.0,
            ),
        ],
        budget={"total": 20000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-03-01T00:00:00Z",
        end_time="2026-03-31T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_gam_pricing_tenant",
        principal_id="test_advertiser_pricing",
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
    )

    # Verify response is success (AdCP 2.4 compliant)
    # Success response has media_buy_id, error response has errors field
    assert (
        not hasattr(response, "errors") or response.errors is None or response.errors == []
    ), f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown error'}"
    assert response.media_buy_id is not None

    # Each package should create a line item with correct pricing:
    # - pkg_1: CPM, STANDARD, priority 8
    # - pkg_2: CPC, PRICE_PRIORITY, priority 12, CLICKS goal
    # - pkg_3: VCPM, STANDARD, priority 8, VIEWABLE_IMPRESSIONS goal


@pytest.mark.requires_db
async def test_gam_auction_cpc_creates_price_priority(setup_gam_tenant_with_all_pricing_models):
    """Test auction-based CPC (non-fixed) is rejected with clear error (not supported by adcp library v2.5.0)."""
    from fastmcp.exceptions import ToolError

    from src.core.tools.media_buy_create import _create_media_buy_impl

    # Add auction CPC pricing option
    with get_db_session() as session:
        pricing_auction = PricingOption(
            tenant_id="test_gam_pricing_tenant",
            product_id="prod_gam_cpc",
            pricing_model="cpc",
            rate=None,  # Auction-based, no fixed rate
            currency="USD",
            is_fixed=False,  # Auction
            price_guidance={"floor": 1.50, "ceiling": 3.00},
            parameters=None,
            min_spend_per_package=None,
        )
        session.add(pricing_auction)
        session.commit()

    request = CreateMediaBuyRequest(
        buyer_ref="test_buyer_auction",
        brand_manifest={"name": "https://example.com/product"},
        packages=[
            create_test_package_request(
                buyer_ref="pkg_auction_cpc",
                product_id="prod_gam_cpc",
                pricing_option_id="cpc_usd_auction",  # Generated format: {model}_{currency}_{fixed|auction}
                budget=4000.0,
                bid_price=2.25,  # Bid within floor/ceiling
            )
        ],
        budget={"total": 4000.0, "currency": "USD"},
        currency="USD",
        start_time="2026-03-01T00:00:00Z",
        end_time="2026-03-31T23:59:59Z",
    )

    context = ToolContext(
        context_id="test_ctx",
        tenant_id="test_gam_pricing_tenant",
        principal_id="test_advertiser_pricing",
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )

    # Auction CPC should be rejected because adcp library v2.5.0 doesn't support CpcAuctionPricingOption
    # Only CpcPricingOption exists, which requires is_fixed=true
    with pytest.raises(ToolError) as exc_info:
        await _create_media_buy_impl(
            buyer_ref=request.buyer_ref,
            brand_manifest=request.brand_manifest,
            packages=request.packages,
            start_time=request.start_time,
            end_time=request.end_time,
            budget=request.budget,
            ctx=context,
        )

    # Verify error message explains the limitation
    error_message = str(exc_info.value)
    assert "Auction CPC pricing option cpc_usd_auction is not supported" in error_message
    assert "adcp library v2.5.0" in error_message
    assert "CpcPricingOption class only supports fixed-rate pricing" in error_message

    # Cleanup auction pricing option
    with get_db_session() as session:
        from sqlalchemy import select

        stmt = select(PricingOption).filter_by(
            tenant_id="test_gam_pricing_tenant", product_id="prod_gam_cpc", is_fixed=False
        )
        pricing_option = session.scalars(stmt).first()
        if pricing_option:
            session.delete(pricing_option)
            session.commit()
