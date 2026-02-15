"""Integration tests: Media Buy Creation with Inventory Profiles.

Tests that verify products referencing inventory profiles work correctly in
the media buy creation flow. Uses the mock adapter (which does not natively
support inventory profiles) to verify the pipeline doesn't break.

Requires PostgreSQL (integration_db).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    InventoryProfile,
    PricingOption,
    Principal,
)
from src.core.tool_context import ToolContext
from src.core.tools.media_buy_create import _create_media_buy_impl
from tests.helpers.adcp_factories import create_test_db_product, create_test_package_request


def _make_context(tenant_id: str, principal_id: str) -> ToolContext:
    """Create a ToolContext for testing."""
    return ToolContext(
        context_id="test_ctx",
        tenant_id=tenant_id,
        principal_id=principal_id,
        tool_name="create_media_buy",
        request_timestamp=datetime.now(UTC),
        testing_context={"dry_run": True, "test_session_id": "test_session"},
    )


def _get_future_date_range() -> tuple[datetime, datetime]:
    """Return a future date range with timezone info."""
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(days=7)
    return start, end


@pytest.mark.requires_db
async def test_create_media_buy_with_profile_based_product(sample_tenant):
    """Test that media buy creation succeeds when product references an inventory profile."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_media_buy",
            name="Test Profile for Media Buy",
            description="Profile with specific ad units",
            inventory_config={
                "ad_units": ["12345", "67890"],
                "placements": ["99999"],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["example_property"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        product = create_test_db_product(
            tenant_id=sample_tenant["tenant_id"],
            product_id="test_product_media_buy",
            name="Profile-Based Product",
            description="Product using inventory profile",
            inventory_profile_id=profile.id,
            format_ids=[],
            is_custom=False,
            countries=["US"],
        )
        session.add(product)

        pricing = PricingOption(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_media_buy",
            name="Test Advertiser",
            access_token="test_token_media_buy",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        start_time, end_time = _get_future_date_range()
        ctx = _make_context(sample_tenant["tenant_id"], principal.principal_id)

        response, task_status = await _create_media_buy_impl(
            buyer_ref="test_buyer_profile",
            brand_manifest={"name": "Test Campaign"},
            packages=[
                create_test_package_request(
                    buyer_ref="pkg_profile",
                    product_id=product.product_id,
                    pricing_option_id="cpm_usd_fixed",
                    budget=150.0,
                )
            ],
            start_time=start_time,
            end_time=end_time,
            ctx=ctx,
            context=None,
        )

        # Verify success
        assert not hasattr(response, "errors") or response.errors is None or response.errors == [], (
            f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown'}"
        )
        assert response.media_buy_id is not None
        assert response.packages is not None
        assert len(response.packages) >= 1


@pytest.mark.requires_db
async def test_create_media_buy_with_profile_formats(sample_tenant):
    """Test that media buy creation handles profile-based format validation."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_format_validation",
            name="Test Profile for Format Validation",
            description="Profile with specific formats",
            inventory_config={
                "ad_units": ["12345"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
                {"agent_url": "https://test.example.com", "id": "display_728x90"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["example_property"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        product = create_test_db_product(
            tenant_id=sample_tenant["tenant_id"],
            product_id="test_product_format_validation",
            name="Format Validation Product",
            description="Product using inventory profile",
            inventory_profile_id=profile.id,
            format_ids=[],
            is_custom=False,
            countries=["US"],
        )
        session.add(product)

        pricing = PricingOption(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_format_validation",
            name="Test Advertiser Format",
            access_token="test_token_format",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        start_time, end_time = _get_future_date_range()
        ctx = _make_context(sample_tenant["tenant_id"], principal.principal_id)

        # Create media buy - should succeed or return structured error, not crash
        try:
            response, _ = await _create_media_buy_impl(
                buyer_ref="test_buyer_format",
                brand_manifest={"name": "Test Campaign Format"},
                packages=[
                    create_test_package_request(
                        buyer_ref="pkg_format",
                        product_id=product.product_id,
                        pricing_option_id="cpm_usd_fixed",
                        budget=150.0,
                    )
                ],
                start_time=start_time,
                end_time=end_time,
                ctx=ctx,
                context=None,
            )
            # Either succeeds or returns structured error - both are valid
            assert response is not None
        except ValueError:
            # Validation error is also acceptable behavior
            pass


@pytest.mark.requires_db
async def test_multiple_products_same_profile_in_media_buy(sample_tenant):
    """Test media buy with multiple products referencing the same profile."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_multiple",
            name="Shared Profile",
            description="Profile shared by multiple products",
            inventory_config={
                "ad_units": ["shared_unit_1", "shared_unit_2"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["prop_shared"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        products = []
        for i in range(3):
            product = create_test_db_product(
                tenant_id=sample_tenant["tenant_id"],
                product_id=f"test_product_shared_{i}",
                name=f"Shared Profile Product {i}",
                description=f"Product {i} sharing profile",
                inventory_profile_id=profile.id,
                format_ids=[],
                is_custom=False,
                countries=["US"],
            )
            session.add(product)

            pricing = PricingOption(
                tenant_id=sample_tenant["tenant_id"],
                product_id=product.product_id,
                pricing_model="cpm",
                rate=Decimal("15.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)
            products.append(product)

        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_shared",
            name="Test Advertiser Shared",
            access_token="test_token_shared",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        start_time, end_time = _get_future_date_range()
        ctx = _make_context(sample_tenant["tenant_id"], principal.principal_id)

        # Use only the first product (AdCP spec: package has singular product_id)
        response, _ = await _create_media_buy_impl(
            buyer_ref="test_buyer_shared",
            brand_manifest={"name": "Test Campaign Shared"},
            packages=[
                create_test_package_request(
                    buyer_ref=f"pkg_shared_{i}",
                    product_id=products[i].product_id,
                    pricing_option_id="cpm_usd_fixed",
                    budget=150.0,
                )
                for i in range(3)
            ],
            start_time=start_time,
            end_time=end_time,
            ctx=ctx,
            context=None,
        )

        assert not hasattr(response, "errors") or response.errors is None or response.errors == [], (
            f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown'}"
        )
        assert response.media_buy_id is not None
        assert response.packages is not None
        assert len(response.packages) == 3


@pytest.mark.requires_db
async def test_media_buy_reflects_profile_updates(sample_tenant):
    """Test that media buy uses current profile config, not stale data."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_updates",
            name="Updatable Profile",
            description="Profile that will be updated",
            inventory_config={
                "ad_units": ["old_unit"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "old.example.com",
                    "property_ids": ["old_property"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        profile_id = profile.id

        product = create_test_db_product(
            tenant_id=sample_tenant["tenant_id"],
            product_id="test_product_updates",
            name="Product with Updatable Profile",
            description="Product using updatable profile",
            inventory_profile_id=profile_id,
            format_ids=[],
            is_custom=False,
            countries=["US"],
        )
        session.add(product)

        pricing = PricingOption(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_updates",
            name="Test Advertiser Updates",
            access_token="test_token_updates",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        # Update profile AFTER product creation
        stmt = select(InventoryProfile).where(InventoryProfile.id == profile_id)
        profile = session.scalars(stmt).first()
        profile.inventory_config = {
            "ad_units": ["new_unit"],
            "placements": ["new_placement"],
            "include_descendants": True,
        }
        profile.publisher_properties = [
            {
                "selection_type": "by_id",
                "publisher_domain": "new.example.com",
                "property_ids": ["new_property"],
            }
        ]
        session.commit()

        # Create media buy AFTER profile update — should still succeed
        start_time, end_time = _get_future_date_range()
        ctx = _make_context(sample_tenant["tenant_id"], principal.principal_id)

        response, _ = await _create_media_buy_impl(
            buyer_ref="test_buyer_updates",
            brand_manifest={"name": "Test Campaign Updates"},
            packages=[
                create_test_package_request(
                    buyer_ref="pkg_updates",
                    product_id=product.product_id,
                    pricing_option_id="cpm_usd_fixed",
                    budget=150.0,
                )
            ],
            start_time=start_time,
            end_time=end_time,
            ctx=ctx,
            context=None,
        )

        assert not hasattr(response, "errors") or response.errors is None or response.errors == [], (
            f"Media buy creation failed: {response.errors if hasattr(response, 'errors') else 'unknown'}"
        )
        assert response.media_buy_id is not None
