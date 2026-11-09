"""Test Suite 4: Inventory Profile Updates Cascade to Products.

Tests that verify when an inventory profile is updated, all products
referencing that profile automatically reflect the new configuration.
"""

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import InventoryProfile, InventoryProfilePublisherSelection, Product
from tests.factories.product import authorized_refs
from tests.factories.publisher import held_selections
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
            publisher_selections=held_selections(
                session,
                InventoryProfilePublisherSelection,
                sample_tenant["tenant_id"],
                [
                    {
                        "property_type": "website",
                        "name": "Example Website",
                        "identifiers": [{"type": "domain", "value": "example.com"}],
                        "publisher_domain": "example.com",
                    }
                ],
            ),
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
            effective_formats = list(product.format_contract.format_ids)
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
            effective_formats = list(product.format_contract.format_ids)
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
            publisher_selections=held_selections(
                session,
                InventoryProfilePublisherSelection,
                sample_tenant["tenant_id"],
                [
                    {
                        "property_type": "website",
                        "name": "Example Website",
                        "identifiers": [{"type": "domain", "value": "example.com"}],
                        "publisher_domain": "example.com",
                    }
                ],
            ),
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
    """Test that updating a profile's publisher selections cascades to all products."""
    tenant_id = sample_tenant["tenant_id"]
    with get_db_session() as session:
        # Create profile with an initial selection
        profile = InventoryProfile(
            tenant_id=tenant_id,
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
            publisher_selections=held_selections(
                session,
                InventoryProfilePublisherSelection,
                tenant_id,
                [{"publisher_domain": "original.com", "selection_type": "all"}],
            ),
        )
        session.add(profile)
        session.flush()

        profile_id = profile.id

        # Create products referencing this profile
        for i in range(3):
            product = create_test_db_product(
                tenant_id=tenant_id,
                product_id=f"test_product_props_{i}",
                name=f"Test Product Props {i}",
                description=f"Product {i} with inventory profile",
                inventory_profile_id=profile_id,
                format_ids=[],
                is_custom=False,
                countries=["US"],
            )
            session.add(product)

        session.commit()

        authorized = authorized_refs("original.com", "updated.com", "second.com")

        # Verify initial state - all products sell the profile's publisher
        stmt = select(Product).where(Product.tenant_id == tenant_id)
        for product in session.scalars(stmt).all():
            assert product.resolve_publisher_properties(authorized) == [
                {"publisher_domain": "original.com", "selection_type": "all"}
            ]

        # Update the profile's selection
        stmt = select(InventoryProfile).where(InventoryProfile.id == profile_id)
        profile = session.scalars(stmt).first()
        profile.publisher_selections = held_selections(
            session,
            InventoryProfilePublisherSelection,
            tenant_id,
            [
                {"publisher_domain": "updated.com", "selection_type": "all"},
                {"publisher_domain": "second.com", "property_ids": ["second_com"], "selection_type": "by_id"},
            ],
        )
        session.commit()

        # Verify cascade - all products now sell the new publishers, and not the old one
        stmt = select(Product).where(Product.tenant_id == tenant_id)
        db_products = session.scalars(stmt).all()
        assert len(db_products) == 3
        for product in db_products:
            session.refresh(product)
            assert product.resolve_publisher_properties(authorized) == [
                {"publisher_domain": "updated.com", "selection_type": "all"},
                {"publisher_domain": "second.com", "property_ids": ["second_com"], "selection_type": "by_id"},
            ]
