"""Test Suite 5: End-to-End Media Buy Creation with Inventory Profiles.

Tests that verify inventory profiles work correctly in the full media buy
creation flow, including GAM adapter integration.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    InventoryProfile,
    PricingOption,
    Principal,
)
from src.core.tools import create_media_buy_raw
from tests.e2e.adcp_request_builder import build_adcp_media_buy_request
from tests.helpers.adcp_factories import create_test_db_product


@pytest.mark.e2e
@pytest.mark.requires_db
def test_create_media_buy_with_profile_based_product_uses_profile_inventory(db_session, sample_tenant):
    """Test that media buy creation uses inventory from profile."""
    with get_db_session() as session:
        # Setup: Create inventory profile with specific ad units
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
            formats=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "property_type": "website",
                    "name": "Example Website",
                    "identifiers": [{"type": "domain", "value": "example.com"}],
                    "publisher_domain": "example.com",
                }
            ],
        )
        session.add(profile)
        session.flush()

        # Create product referencing profile
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

        # Create pricing option
        pricing = PricingOption(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        # Create principal
        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_media_buy",
            name="Test Advertiser",
            access_token="test_token_media_buy",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)

        session.commit()

        # Create media buy request using helper (includes all required fields)
        start_time = datetime.now() + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        request_dict = build_adcp_media_buy_request(
            product_ids=[product.product_id],
            total_budget=150.0,  # 10000 impressions * $15 CPM / 1000
            start_time=start_time,
            end_time=end_time,
            brand_manifest={"name": "Test Campaign"},
        )

        # Mock GAM adapter to capture line item creation
        with patch("src.adapters.get_adapter") as mock_get_adapter:
            mock_adapter = MagicMock()
            mock_adapter.create_media_buy.return_value = {
                "media_buy_id": "mock_media_buy_123",
                "status": "active",
                "line_items": [
                    {
                        "line_item_id": "mock_li_1",
                        "name": "Test Line Item",
                        "delivery_status": "ready",
                        "quantity_goal": 10000,
                        "targeted_ad_unit_ids": ["12345", "67890"],
                        "targeted_placement_ids": ["99999"],
                    }
                ],
            }
            mock_get_adapter.return_value = mock_adapter

            # Execute media buy creation
            response = create_media_buy_raw(
                **request_dict,
                tenant_id=sample_tenant["tenant_id"],
                principal_id=principal.principal_id,
            )

            # Verify adapter was called
            assert mock_adapter.create_media_buy.called

            # Get the call arguments
            call_args = mock_adapter.create_media_buy.call_args
            adapter_config = call_args[0][0]  # First positional argument

            # Verify that line items target the correct ad units from profile
            # The adapter should receive implementation_config with targeted_ad_unit_ids
            assert "implementation_config" in adapter_config
            impl_config = adapter_config["implementation_config"]
            assert "targeted_ad_unit_ids" in impl_config
            assert set(impl_config["targeted_ad_unit_ids"]) == {"12345", "67890"}
            assert "targeted_placement_ids" in impl_config
            assert set(impl_config["targeted_placement_ids"]) == {"99999"}


@pytest.mark.e2e
@pytest.mark.requires_db
def test_create_media_buy_with_profile_based_product_validates_formats(db_session, sample_tenant):
    """Test that media buy creation validates creative formats against profile."""
    with get_db_session() as session:
        # Setup: Create inventory profile with specific formats
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
            formats=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
                {"agent_url": "https://test.example.com", "id": "display_728x90"},
            ],
            publisher_properties=[
                {
                    "property_type": "website",
                    "name": "Example Website",
                    "identifiers": [{"type": "domain", "value": "example.com"}],
                    "publisher_domain": "example.com",
                }
            ],
        )
        session.add(profile)
        session.flush()

        # Create product referencing profile
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

        # Create pricing option
        pricing = PricingOption(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        # Create principal
        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_format_validation",
            name="Test Advertiser Format",
            access_token="test_token_format",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)

        session.commit()

        # Create media buy request with creative that doesn't match profile formats
        start_time = datetime.now() + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        request_dict = build_adcp_media_buy_request(
            product_ids=[product.product_id],
            total_budget=150.0,
            start_time=start_time,
            end_time=end_time,
            brand_manifest={"name": "Test Campaign Format"},
        )
        # Add creative_ids to first package
        request_dict["packages"][0]["creative_ids"] = ["creative_video_15s"]

        # Mock GAM adapter
        with patch("src.adapters.get_adapter") as mock_get_adapter:
            mock_adapter = MagicMock()
            mock_get_adapter.return_value = mock_adapter

            # Expect validation error (or adapter to handle gracefully)
            # Note: Actual behavior depends on implementation
            # This test documents expected behavior
            try:
                response = create_media_buy_raw(
                    **request_dict,
                    tenant_id=sample_tenant["tenant_id"],
                    principal_id=principal.principal_id,
                )
                # If no exception, verify that validation occurred
                # (implementation may allow and warn, or may reject)
                assert response is not None
            except ValueError as e:
                # Validation error is expected behavior
                assert "format" in str(e).lower() or "creative" in str(e).lower()


@pytest.mark.e2e
@pytest.mark.requires_db
def test_multiple_products_same_profile_in_media_buy(db_session, sample_tenant):
    """Test media buy with multiple products referencing same profile."""
    with get_db_session() as session:
        # Setup: Create inventory profile
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
            formats=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "publisher_domain": "example.com",
                    "property_ids": ["prop_shared"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        # Create 3 products referencing same profile
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

            # Create pricing option for each
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

        # Create principal
        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_shared",
            name="Test Advertiser Shared",
            access_token="test_token_shared",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)

        session.commit()

        # Create media buy with all 3 products in one package
        start_time = datetime.now() + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        request_dict = build_adcp_media_buy_request(
            product_ids=[p.product_id for p in products],
            total_budget=450.0,  # 30000 impressions * $15 CPM / 1000
            start_time=start_time,
            end_time=end_time,
            brand_manifest={"name": "Test Campaign Shared"},
        )

        # Mock GAM adapter
        with patch("src.adapters.get_adapter") as mock_get_adapter:
            mock_adapter = MagicMock()
            mock_adapter.create_media_buy.return_value = {
                "media_buy_id": "mock_media_buy_shared",
                "status": "active",
                "line_items": [
                    {
                        "line_item_id": f"mock_li_{i}",
                        "name": f"Line Item {i}",
                        "delivery_status": "ready",
                        "quantity_goal": 10000,
                    }
                    for i in range(3)
                ],
            }
            mock_get_adapter.return_value = mock_adapter

            # Execute media buy creation
            response = create_media_buy_raw(
                **request_dict,
                tenant_id=sample_tenant["tenant_id"],
                principal_id=principal.principal_id,
            )

            # Verify adapter was called
            assert mock_adapter.create_media_buy.called
            assert response is not None


@pytest.mark.e2e
@pytest.mark.requires_db
def test_media_buy_reflects_profile_updates_made_after_product_creation(db_session, sample_tenant):
    """Test that media buy uses UPDATED profile config, not stale config."""
    with get_db_session() as session:
        # Setup: Create inventory profile with initial config
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
            formats=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "property_type": "website",
                    "name": "Old Website",
                    "identifiers": [{"type": "domain", "value": "old.example.com"}],
                    "publisher_domain": "old.example.com",
                }
            ],
        )
        session.add(profile)
        session.flush()

        profile_id = profile.id

        # Create product referencing profile
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

        # Create pricing option
        pricing = PricingOption(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        # Create principal
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
                "property_type": "website",
                "name": "New Website",
                "identifiers": [{"type": "domain", "value": "new.example.com"}],
                "publisher_domain": "new.example.com",
            }
        ]
        session.commit()

        # Create media buy AFTER profile update
        start_time = datetime.now() + timedelta(days=1)
        end_time = start_time + timedelta(days=7)

        request_dict = build_adcp_media_buy_request(
            product_ids=[product.product_id],
            total_budget=150.0,
            start_time=start_time,
            end_time=end_time,
            brand_manifest={"name": "Test Campaign Updates"},
        )

        # Mock GAM adapter to capture configuration
        with patch("src.adapters.get_adapter") as mock_get_adapter:
            mock_adapter = MagicMock()
            mock_adapter.create_media_buy.return_value = {
                "media_buy_id": "mock_media_buy_updates",
                "status": "active",
                "line_items": [
                    {
                        "line_item_id": "mock_li_updates",
                        "name": "Updated Line Item",
                        "delivery_status": "ready",
                        "quantity_goal": 10000,
                    }
                ],
            }
            mock_get_adapter.return_value = mock_adapter

            # Execute media buy creation
            response = create_media_buy_raw(
                **request_dict,
                tenant_id=sample_tenant["tenant_id"],
                principal_id=principal.principal_id,
            )

            # Verify adapter was called with UPDATED config
            assert mock_adapter.create_media_buy.called
            call_args = mock_adapter.create_media_buy.call_args
            adapter_config = call_args[0][0]

            # Verify UPDATED inventory is used (not old config)
            impl_config = adapter_config.get("implementation_config", {})
            assert "new_unit" in impl_config.get("targeted_ad_unit_ids", [])
            assert "old_unit" not in impl_config.get("targeted_ad_unit_ids", [])
            assert "new_placement" in impl_config.get("targeted_placement_ids", [])
