"""Integration tests for inventory profile configuration transitions.

This test suite validates edge cases when switching between custom and profile-based
product configuration. Tests ensure data integrity and correct effective_* property
behavior during various transition scenarios.

Key scenarios:
1. Switching from custom to profile configuration
2. Switching from profile to custom configuration
3. Switching between different profiles
4. Clearing profile without custom config
5. Profile deletion with dependent products
"""

import pytest
from sqlalchemy import delete, select

from src.core.database.database_session import get_db_session
from src.core.database.models import InventoryProfile, Product, Tenant


@pytest.mark.integration
@pytest.mark.requires_db
class TestInventoryProfileTransitions:
    """Test configuration transitions between custom and profile-based products."""

    @pytest.fixture
    def tenant(self, integration_db):
        """Create test tenant. Returns tenant_id string."""
        with get_db_session() as session:
            from tests.utils.database_helpers import create_tenant_with_timestamps

            tenant = create_tenant_with_timestamps(
                tenant_id="test_transitions",
                name="Inventory Profile Transitions Test",
                subdomain="transitions",
                ad_server="mock",
                is_active=True,
            )
            session.add(tenant)
            session.commit()
            tenant_id = tenant.tenant_id

        yield tenant_id

        # Cleanup
        with get_db_session() as session:
            stmt = delete(Tenant).where(Tenant.tenant_id == tenant_id)
            session.execute(stmt)
            session.commit()

    @pytest.fixture
    def profile_a(self, tenant):
        """Create first test profile. Returns profile id."""
        with get_db_session() as session:
            profile = InventoryProfile(
                tenant_id=tenant,
                profile_id="profile_a",
                name="Profile A",
                description="First test profile",
                inventory_config={
                    "ad_units": ["11111", "22222"],
                    "placements": [],
                    "include_descendants": True,
                },
                format_ids=[
                    {"agent_url": "https://example.com", "id": "format_a1"},
                    {"agent_url": "https://example.com", "id": "format_a2"},
                ],
                publisher_properties=[
                    {
                        "publisher_domain": "site-a.com",
                        "property_ids": ["site_a_main"],
                    }
                ],
            )
            session.add(profile)
            session.commit()
            profile_id = profile.id

        yield profile_id

        # Cleanup
        with get_db_session() as session:
            stmt = delete(InventoryProfile).where(InventoryProfile.id == profile_id)
            session.execute(stmt)
            session.commit()

    @pytest.fixture
    def profile_b(self, tenant):
        """Create second test profile. Returns profile id."""
        with get_db_session() as session:
            profile = InventoryProfile(
                tenant_id=tenant,
                profile_id="profile_b",
                name="Profile B",
                description="Second test profile",
                inventory_config={
                    "ad_units": ["33333", "44444"],
                    "placements": [],
                    "include_descendants": True,
                },
                format_ids=[
                    {"agent_url": "https://example.com", "id": "format_b1"},
                    {"agent_url": "https://example.com", "id": "format_b2"},
                ],
                publisher_properties=[
                    {
                        "publisher_domain": "site-b.com",
                        "property_ids": ["site_b_main"],
                    }
                ],
            )
            session.add(profile)
            session.commit()
            profile_id = profile.id

        yield profile_id

        # Cleanup
        with get_db_session() as session:
            stmt = delete(InventoryProfile).where(InventoryProfile.id == profile_id)
            session.execute(stmt)
            session.commit()

    def create_product(self, tenant_id, product_id, name, **kwargs):
        """Helper to create a product with minimal required fields."""
        defaults = {
            "format_ids": [],
            "targeting_template": {},
            "delivery_type": "standard",
            "properties": None,
            "property_tags": ["all_inventory"],
        }
        defaults.update(kwargs)

        with get_db_session() as session:
            product = Product(
                tenant_id=tenant_id,
                product_id=product_id,
                name=name,
                **defaults,
            )
            session.add(product)
            session.commit()

    def test_switching_from_custom_to_profile_uses_profile_config(self, tenant, profile_a):
        """Test switching from custom formats/properties to profile-based.

        Scenario: Product has custom configuration, then switches to profile.
        Expected: effective_* properties return profile data, custom data preserved.
        """
        # Create product with custom formats and properties
        custom_formats = [
            {"agent_url": "https://custom.com", "id": "custom_format_1"},
            {"agent_url": "https://custom.com", "id": "custom_format_2"},
        ]
        custom_properties = [
            {
                "publisher_domain": "custom-site.com",
                "property_ids": ["custom_property"],
            }
        ]

        self.create_product(
            tenant_id=tenant,
            product_id="test_custom_to_profile",
            name="Custom to Profile Test",
            format_ids=custom_formats,
            properties=custom_properties,
            property_tags=None,  # Using properties instead of tags
        )

        # Verify original custom data and then switch to profile
        with get_db_session() as session:
            stmt = select(Product).where(
                Product.tenant_id == tenant,
                Product.product_id == "test_custom_to_profile",
            )
            product = session.scalars(stmt).first()

            assert product.format_ids == custom_formats
            assert product.properties == custom_properties
            assert product.effective_format_ids == custom_formats
            assert product.effective_properties == custom_properties

            # Get profile data for comparison
            stmt = select(InventoryProfile).where(InventoryProfile.id == profile_a)
            profile = session.scalars(stmt).first()
            profile_formats = profile.format_ids
            profile_properties = profile.publisher_properties

            # Set inventory_profile_id to existing profile
            product.inventory_profile_id = profile_a
            session.commit()
            session.refresh(product)
            session.refresh(product.inventory_profile)  # Ensure relationship is loaded

            # Assert effective_formats returns profile data
            assert product.effective_format_ids == profile_formats
            assert product.effective_format_ids != custom_formats

            # Assert effective_properties returns profile data
            assert product.effective_properties == profile_properties
            assert product.effective_properties != custom_properties

            # Assert custom data still exists in database (not deleted)
            assert product.format_ids == custom_formats
            assert product.properties == custom_properties

        # Cleanup
        with get_db_session() as session:
            stmt = delete(Product).where(
                Product.tenant_id == tenant,
                Product.product_id == "test_custom_to_profile",
            )
            session.execute(stmt)
            session.commit()

    def test_switching_from_profile_to_custom_uses_custom_config(self, tenant, profile_a):
        """Test switching from profile-based to custom configuration.

        Scenario: Product uses profile, then switches to custom config.
        Expected: effective_* properties return custom data.
        """
        # Create product with inventory_profile_id set
        self.create_product(
            tenant_id=tenant,
            product_id="test_profile_to_custom",
            name="Profile to Custom Test",
            inventory_profile_id=profile_a,
        )

        with get_db_session() as session:
            stmt = select(Product).where(
                Product.tenant_id == tenant,
                Product.product_id == "test_profile_to_custom",
            )
            product = session.scalars(stmt).first()

            # Get profile data for comparison
            stmt = select(InventoryProfile).where(InventoryProfile.id == profile_a)
            profile = session.scalars(stmt).first()

            # Initially using profile
            assert product.inventory_profile_id == profile_a
            assert product.effective_format_ids == profile.format_ids
            assert product.effective_properties == profile.publisher_properties

            # Clear inventory_profile_id (set to None)
            product.inventory_profile_id = None

            # Set custom formats and properties
            custom_formats = [{"agent_url": "https://new-custom.com", "id": "new_custom_format"}]
            custom_properties = [
                {
                    "publisher_domain": "new-custom-site.com",
                    "property_ids": ["new_custom_property"],
                }
            ]

            product.format_ids = custom_formats
            product.properties = custom_properties
            product.property_tags = None

            session.commit()
            session.refresh(product)

            # Assert effective_formats returns custom data
            assert product.effective_format_ids == custom_formats

            # Assert effective_properties returns custom data
            assert product.effective_properties == custom_properties

        # Cleanup
        with get_db_session() as session:
            stmt = delete(Product).where(
                Product.tenant_id == tenant,
                Product.product_id == "test_profile_to_custom",
            )
            session.execute(stmt)
            session.commit()

    def test_switching_profiles_updates_effective_properties(self, tenant, profile_a, profile_b):
        """Test switching between different inventory profiles.

        Scenario: Product uses profile_a, then switches to profile_b.
        Expected: effective_formats reflects current profile's formats.
        """
        # Create product with profile_a
        self.create_product(
            tenant_id=tenant,
            product_id="test_profile_switch",
            name="Profile Switch Test",
            inventory_profile_id=profile_a,
        )

        with get_db_session() as session:
            stmt = select(Product).where(
                Product.tenant_id == tenant,
                Product.product_id == "test_profile_switch",
            )
            product = session.scalars(stmt).first()

            # Load profiles for comparison
            stmt_a = select(InventoryProfile).where(InventoryProfile.id == profile_a)
            profile_a_obj = session.scalars(stmt_a).first()

            stmt_b = select(InventoryProfile).where(InventoryProfile.id == profile_b)
            profile_b_obj = session.scalars(stmt_b).first()

            # Assert effective_formats returns profile_a formats
            assert product.effective_format_ids == profile_a_obj.format_ids
            assert product.effective_properties == profile_a_obj.publisher_properties

            # Verify we have the expected profile_a data
            profile_a_format_ids = [f["id"] for f in profile_a_obj.format_ids]
            assert "format_a1" in profile_a_format_ids
            assert "format_a2" in profile_a_format_ids

            # Switch product to profile_b
            product.inventory_profile_id = profile_b
            session.commit()
            session.refresh(product)
            session.refresh(product.inventory_profile)  # Reload relationship

            # Assert effective_formats returns profile_b formats
            assert product.effective_format_ids == profile_b_obj.format_ids
            assert product.effective_properties == profile_b_obj.publisher_properties

            # Verify we now have profile_b data
            profile_b_format_ids = [f["id"] for f in profile_b_obj.format_ids]
            assert "format_b1" in profile_b_format_ids
            assert "format_b2" in profile_b_format_ids

            # Verify profile_a data is NOT returned
            current_format_ids = [f["id"] for f in product.effective_format_ids]
            assert "format_a1" not in current_format_ids
            assert "format_a2" not in current_format_ids

        # Cleanup
        with get_db_session() as session:
            stmt = delete(Product).where(
                Product.tenant_id == tenant,
                Product.product_id == "test_profile_switch",
            )
            session.execute(stmt)
            session.commit()

    def test_clearing_profile_without_custom_config_has_sensible_behavior(self, tenant, profile_a):
        """Test clearing profile without setting custom config.

        Scenario: Product uses profile, profile_id cleared, no custom config set.
        Expected: System handles gracefully by synthesizing properties from property_tags.
        """
        # Create product with inventory_profile_id (no custom config)
        self.create_product(
            tenant_id=tenant,
            product_id="test_clear_profile",
            name="Clear Profile Test",
            inventory_profile_id=profile_a,
            format_ids=[],  # Empty custom formats
            properties=None,
            property_tags=["all_inventory"],
        )

        with get_db_session() as session:
            stmt = select(Product).where(
                Product.tenant_id == tenant,
                Product.product_id == "test_clear_profile",
            )
            product = session.scalars(stmt).first()

            # Get profile for comparison
            stmt = select(InventoryProfile).where(InventoryProfile.id == profile_a)
            profile = session.scalars(stmt).first()

            # Initially using profile
            assert product.inventory_profile_id == profile_a
            assert product.effective_format_ids == profile.format_ids

            # Clear inventory_profile_id
            product.inventory_profile_id = None
            session.commit()
            session.refresh(product)

            # Check effective_formats and effective_properties
            # System should return custom config gracefully
            assert product.inventory_profile_id is None

            # effective_formats should return empty list (product.format_ids)
            effective_formats = product.effective_format_ids
            assert isinstance(effective_formats, list)
            assert effective_formats == []

            # effective_properties should synthesize by_tag variant from property_tags
            effective_properties = product.effective_properties
            assert effective_properties is not None
            assert isinstance(effective_properties, list)
            assert len(effective_properties) == 1
            assert effective_properties[0]["selection_type"] == "by_tag"
            assert effective_properties[0]["property_tags"] == ["all_inventory"]

            # Verify system handles gracefully (no exceptions)
            # Product is valid even without profile or custom config
            assert product.product_id == "test_clear_profile"
            assert product.tenant_id == tenant

        # Cleanup
        with get_db_session() as session:
            stmt = delete(Product).where(
                Product.tenant_id == tenant,
                Product.product_id == "test_clear_profile",
            )
            session.execute(stmt)
            session.commit()

    def test_profile_deletion_handles_dependent_products(self, tenant, profile_a):
        """Test profile deletion behavior with dependent products.

        Scenario: Profile has multiple products referencing it, then profile is deleted.
        Expected: Products handle deletion per foreign key constraint (SET NULL).

        Per models.py line 195-198:
        inventory_profile_id: Mapped[int | None] = mapped_column(
            Integer,
            ForeignKey("inventory_profiles.id", ondelete="SET NULL"),
            nullable=True,
        )

        Expected behavior: ondelete="SET NULL" means inventory_profile_id set to NULL.
        """
        # Create multiple products referencing profile
        product_ids = ["test_delete_1", "test_delete_2", "test_delete_3"]

        for product_id in product_ids:
            self.create_product(
                tenant_id=tenant,
                product_id=product_id,
                name=f"Delete Test {product_id}",
                inventory_profile_id=profile_a,
            )

        with get_db_session() as session:
            # Verify products are using profile
            for product_id in product_ids:
                stmt = select(Product).where(
                    Product.tenant_id == tenant,
                    Product.product_id == product_id,
                )
                product = session.scalars(stmt).first()
                assert product is not None
                assert product.inventory_profile_id == profile_a

            # Delete profile
            stmt = delete(InventoryProfile).where(InventoryProfile.id == profile_a)
            session.execute(stmt)
            session.commit()

            # Check what happens to products
            # Expected: ondelete="SET NULL" means inventory_profile_id should be NULL
            for product_id in product_ids:
                stmt = select(Product).where(
                    Product.tenant_id == tenant,
                    Product.product_id == product_id,
                )
                product = session.scalars(stmt).first()

                # Product should still exist (not CASCADE deleted)
                assert product is not None, f"Product {product_id} was deleted (unexpected CASCADE)"

                # inventory_profile_id should be NULL (SET NULL behavior)
                assert product.inventory_profile_id is None, (
                    f"Product {product_id} still has inventory_profile_id "
                    f"(expected SET NULL, got {product.inventory_profile_id})"
                )

        # Cleanup products
        with get_db_session() as session:
            for product_id in product_ids:
                stmt = delete(Product).where(
                    Product.tenant_id == tenant,
                    Product.product_id == product_id,
                )
                session.execute(stmt)
            session.commit()

        # Document actual behavior:
        # ✅ Confirmed: ondelete="SET NULL" behavior works correctly
        # - Products are NOT deleted (no CASCADE)
        # - inventory_profile_id is set to NULL
        # - Products remain valid and functional
        # - Products fall back to custom config (formats, properties, property_tags)
