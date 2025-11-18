"""Test Suite 4: Inventory Profile Updates Cascade to Products.

Tests that verify when an inventory profile is updated, all products
referencing that profile automatically reflect the new configuration.
"""

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import InventoryProfile, Product
from tests.helpers.adcp_factories import create_test_db_product


@pytest.mark.requires_db
def test_updating_profile_formats_affects_all_products(integration_db, sample_tenant):
    """Test that updating profile formats cascades to all referencing products."""
    with get_db_session() as session:
        # Create profile with initial formats
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_formats",
            name="Test Profile - Formats",
            description="Profile for testing format updates",
            inventory_config={
                "ad_units": ["unit_1", "unit_2"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "format_a"},
                {"agent_url": "https://test.example.com", "id": "format_b"},
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

        profile_id = profile.id

        # Create 3 products referencing this profile
        products = []
        for i in range(3):
            product = create_test_db_product(
                tenant_id=sample_tenant["tenant_id"],
                product_id=f"test_product_formats_{i}",
                name=f"Test Product {i}",
                description=f"Product {i} with inventory profile",
                inventory_profile_id=profile_id,
                # Legacy fields (not used when profile is set)
                format_ids=[],
                is_custom=False,
                countries=["US"],
            )
            session.add(product)
            products.append(product)

        session.commit()

        # Verify initial state - all products return profile's formats
        stmt = select(Product).where(Product.tenant_id == sample_tenant["tenant_id"])
        db_products = session.scalars(stmt).all()
        for product in db_products:
            effective_formats = product.effective_format_ids
            assert len(effective_formats) == 2
            assert {"agent_url": "https://test.example.com", "id": "format_a"} in effective_formats
            assert {"agent_url": "https://test.example.com", "id": "format_b"} in effective_formats

        # Update profile formats
        stmt = select(InventoryProfile).where(InventoryProfile.id == profile_id)
        profile = session.scalars(stmt).first()
        profile.format_ids = [
            {"agent_url": "https://test.example.com", "id": "format_c"},
            {"agent_url": "https://test.example.com", "id": "format_d"},
        ]
        session.commit()

        # Verify cascade - all products now return new formats
        stmt = select(Product).where(Product.tenant_id == sample_tenant["tenant_id"])
        db_products = session.scalars(stmt).all()
        assert len(db_products) == 3

        for product in db_products:
            effective_formats = product.effective_format_ids
            assert len(effective_formats) == 2
            assert {"agent_url": "https://test.example.com", "id": "format_c"} in effective_formats
            assert {"agent_url": "https://test.example.com", "id": "format_d"} in effective_formats
            # Old formats should not be present
            assert {"agent_url": "https://test.example.com", "id": "format_a"} not in effective_formats
            assert {"agent_url": "https://test.example.com", "id": "format_b"} not in effective_formats


@pytest.mark.requires_db
def test_updating_profile_inventory_affects_product_implementation_config(integration_db, sample_tenant):
    """Test that updating profile inventory cascades to product implementation_config."""
    with get_db_session() as session:
        # Create profile with initial inventory
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_inventory",
            name="Test Profile - Inventory",
            description="Profile for testing inventory updates",
            inventory_config={
                "ad_units": ["unit_1", "unit_2"],
                "placements": ["placement_1"],
                "include_descendants": False,
            },
            format_ids=[
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

        profile_id = profile.id

        # Create products referencing this profile
        products = []
        for i in range(2):
            product = create_test_db_product(
                tenant_id=sample_tenant["tenant_id"],
                product_id=f"test_product_inventory_{i}",
                name=f"Test Product Inventory {i}",
                description=f"Product {i} with inventory profile",
                inventory_profile_id=profile_id,
                format_ids=[],
                is_custom=False,
                countries=["US"],
            )
            session.add(product)
            products.append(product)

        session.commit()

        # Verify initial state - implementation_config reflects profile's inventory
        stmt = select(Product).where(Product.tenant_id == sample_tenant["tenant_id"])
        db_products = session.scalars(stmt).all()
        for product in db_products:
            config = product.effective_implementation_config
            assert config is not None
            assert "targeted_ad_unit_ids" in config
            assert set(config["targeted_ad_unit_ids"]) == {"unit_1", "unit_2"}
            assert "targeted_placement_ids" in config
            assert set(config["targeted_placement_ids"]) == {"placement_1"}

        # Update profile inventory
        stmt = select(InventoryProfile).where(InventoryProfile.id == profile_id)
        profile = session.scalars(stmt).first()
        profile.inventory_config = {
            "ad_units": ["unit_3", "unit_4"],
            "placements": ["placement_2", "placement_3"],
            "include_descendants": True,
        }
        session.commit()

        # Verify cascade - all products now have updated implementation_config
        stmt = select(Product).where(Product.tenant_id == sample_tenant["tenant_id"])
        db_products = session.scalars(stmt).all()
        assert len(db_products) == 2

        for product in db_products:
            config = product.effective_implementation_config
            assert config is not None
            assert "targeted_ad_unit_ids" in config
            assert set(config["targeted_ad_unit_ids"]) == {"unit_3", "unit_4"}
            assert "targeted_placement_ids" in config
            assert set(config["targeted_placement_ids"]) == {"placement_2", "placement_3"}
            assert config.get("include_descendants") is True
            # Old inventory should not be present
            assert "unit_1" not in config["targeted_ad_unit_ids"]
            assert "unit_2" not in config["targeted_ad_unit_ids"]
            assert "placement_1" not in config["targeted_placement_ids"]


@pytest.mark.requires_db
def test_updating_profile_properties_affects_all_products(integration_db, sample_tenant):
    """Test that updating profile publisher_properties cascades to all products."""
    with get_db_session() as session:
        # Create profile with initial properties
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_properties",
            name="Test Profile - Properties",
            description="Profile for testing property updates",
            inventory_config={
                "ad_units": ["unit_1"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "property_type": "website",
                    "name": "Original Website",
                    "identifiers": [{"type": "domain", "value": "original.com"}],
                    "publisher_domain": "original.com",
                }
            ],
        )
        session.add(profile)
        session.flush()

        profile_id = profile.id

        # Create products referencing this profile
        products = []
        for i in range(3):
            product = create_test_db_product(
                tenant_id=sample_tenant["tenant_id"],
                product_id=f"test_product_props_{i}",
                name=f"Test Product Props {i}",
                description=f"Product {i} with inventory profile",
                inventory_profile_id=profile_id,
                format_ids=[],
                is_custom=False,
                countries=["US"],
            )
            session.add(product)
            products.append(product)

        session.commit()

        # Verify initial state - all products return profile's properties
        stmt = select(Product).where(Product.tenant_id == sample_tenant["tenant_id"])
        db_products = session.scalars(stmt).all()
        for product in db_products:
            effective_props = product.effective_properties
            assert len(effective_props) == 1
            assert effective_props[0]["publisher_domain"] == "original.com"
            assert effective_props[0]["property_type"] == "website"
            assert effective_props[0]["name"] == "Original Website"

        # Update profile properties
        stmt = select(InventoryProfile).where(InventoryProfile.id == profile_id)
        profile = session.scalars(stmt).first()
        profile.publisher_properties = [
            {
                "property_type": "website",
                "name": "Updated Website",
                "identifiers": [{"type": "domain", "value": "updated.com"}],
                "publisher_domain": "updated.com",
            },
            {
                "property_type": "mobile_app",
                "name": "Second App",
                "identifiers": [{"type": "bundle_id", "value": "com.second.app"}],
                "publisher_domain": "second.com",
                "tags": ["premium"],
            },
        ]
        session.commit()

        # Verify cascade - all products now return new properties
        stmt = select(Product).where(Product.tenant_id == sample_tenant["tenant_id"])
        db_products = session.scalars(stmt).all()
        assert len(db_products) == 3

        for product in db_products:
            effective_props = product.effective_properties
            assert len(effective_props) == 2

            # Check first property
            prop1 = next(p for p in effective_props if p["publisher_domain"] == "updated.com")
            assert prop1 is not None
            assert prop1["property_type"] == "website"
            assert prop1["name"] == "Updated Website"

            # Check second property
            prop2 = next(p for p in effective_props if p["publisher_domain"] == "second.com")
            assert prop2 is not None
            assert prop2["property_type"] == "mobile_app"
            assert prop2["name"] == "Second App"
            assert prop2.get("tags") == ["premium"]

            # Old properties should not be present
            assert not any(p["publisher_domain"] == "original.com" for p in effective_props)
