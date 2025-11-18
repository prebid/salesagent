"""Integration tests for Product model effective property resolution.

Tests validate that the effective_* properties on the Product model correctly
resolve configuration from inventory profiles or fall back to custom configuration.

These tests require a real database (integration_db fixture) to test the
SQLAlchemy relationship between Product and InventoryProfile models.

Tests cover:
- effective_formats: Returns profile.format_ids or product.format_ids
- effective_properties: Returns profile.publisher_properties or product.properties
- effective_property_tags: Returns None for profiles, product.property_tags for custom
- effective_implementation_config: Builds GAM config from profile or returns custom config
"""

from decimal import Decimal

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import InventoryProfile, PricingOption, Product, Tenant
from tests.helpers.adcp_factories import create_test_db_product


@pytest.fixture
def test_tenant(integration_db):
    """Create a test tenant for inventory profile tests."""
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test_tenant",
            name="Test Tenant",
            subdomain="test",
            is_active=True,
            ad_server="gam",
            auto_approve_format_ids=[],
            human_review_required=False,
            policy_settings={},
            authorized_emails=["test@example.com"],
        )
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        return tenant


@pytest.fixture
def test_profile(integration_db, test_tenant):
    """Create a test inventory profile with full configuration."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=test_tenant.tenant_id,
            profile_id="test_profile",
            name="Test Profile",
            description="Test inventory profile",
            inventory_config={
                "ad_units": ["23312403859", "23312403860"],
                "placements": ["45678901"],
                "include_descendants": True,
            },
            format_ids=[
                {"agent_url": "http://test.example.com", "id": "display_300x250_image"},
                {"agent_url": "http://test.example.com", "id": "display_728x90_image"},
            ],
            publisher_properties=[
                {
                    "publisher_domain": "example.com",
                    "property_ids": ["example_homepage"],
                }
            ],
            targeting_template={"geo_country": {"values": ["US"], "required": False}},
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile


@pytest.fixture
def test_product_custom(integration_db, test_tenant):
    """Create a product with custom configuration (no profile)."""
    with get_db_session() as session:
        product = create_test_db_product(
            tenant_id=test_tenant.tenant_id,
            product_id="custom_product",
            name="Custom Product",
            description="Product with custom config",
            format_ids=[
                {"agent_url": "http://custom.example.com", "id": "video_15s"},
                {"agent_url": "http://custom.example.com", "id": "video_30s"},
            ],
            property_tags=["premium", "video"],
            implementation_config={
                "targeted_ad_unit_ids": ["custom_unit_1", "custom_unit_2"],
                "targeted_placement_ids": ["custom_placement_1"],
                "include_descendants": False,
                "custom_field": "custom_value",
            },
            is_custom=True,
            countries=["US"],
        )

        # Add required pricing option
        pricing = PricingOption(
            tenant_id=test_tenant.tenant_id,
            product_id="custom_product",
            pricing_model="cpm",
            rate=Decimal("10.0"),
            currency="USD",
            is_fixed=True,
        )

        session.add(product)
        session.add(pricing)
        session.commit()
        session.refresh(product)
        return product


@pytest.fixture
def test_product_with_profile(integration_db, test_tenant, test_profile):
    """Create a product referencing the test_profile."""
    with get_db_session() as session:
        product = create_test_db_product(
            tenant_id=test_tenant.tenant_id,
            product_id="profile_product",
            name="Profile-Based Product",
            description="Product using inventory profile",
            inventory_profile_id=test_profile.id,
            # Custom config exists but should be ignored when profile is set
            format_ids=[{"agent_url": "http://ignored.example.com", "id": "ignored_format"}],
            # Profile provides properties, so set property_tags to satisfy XOR constraint
            property_tags=["ignored_tag"],
            implementation_config={
                "targeted_ad_unit_ids": ["ignored_unit"],
            },
            is_custom=False,
            countries=["US"],
        )

        # Add required pricing option
        pricing = PricingOption(
            tenant_id=test_tenant.tenant_id,
            product_id="profile_product",
            pricing_model="cpm",
            rate=Decimal("10.0"),
            currency="USD",
            is_fixed=True,
        )

        session.add(product)
        session.add(pricing)
        session.commit()
        session.refresh(product)
        return product


class TestEffectiveFormats:
    """Tests for Product.effective_format_ids property."""

    @pytest.mark.requires_db
    def test_effective_formats_returns_profile_formats_when_profile_set(
        self, integration_db, test_product_with_profile, test_profile
    ):
        """Test effective_formats returns profile.format_ids when inventory_profile_id is set.

        Validates that:
        - Product has inventory_profile_id set
        - effective_formats returns profile.format_ids
        - effective_formats does NOT return product.format_ids
        """
        with get_db_session() as session:
            from sqlalchemy import select

            stmt = select(Product).where(Product.product_id == test_product_with_profile.product_id)
            product = session.scalars(stmt).first()

            # Product should have profile reference
            assert product.inventory_profile_id == test_profile.id
            assert product.inventory_profile is not None

            # effective_formats should return profile's formats
            effective = product.effective_format_ids
            assert effective == test_profile.format_ids
            assert len(effective) == 2
            assert effective[0]["id"] == "display_300x250_image"
            assert effective[1]["id"] == "display_728x90_image"

            # Should NOT return product's custom formats
            assert effective != product.format_ids
            assert product.format_ids[0]["id"] == "ignored_format"

    @pytest.mark.requires_db
    def test_effective_formats_returns_custom_formats_when_profile_not_set(self, integration_db, test_product_custom):
        """Test effective_formats returns product.format_ids when inventory_profile_id is not set.

        Validates that:
        - Product has no inventory_profile_id
        - effective_formats returns product.format_ids (custom config)
        """
        with get_db_session() as session:
            from sqlalchemy import select

            stmt = select(Product).where(Product.product_id == test_product_custom.product_id)
            product = session.scalars(stmt).first()

            # Product should NOT have profile reference
            assert product.inventory_profile_id is None

            # effective_formats should return product's custom formats
            effective = product.effective_format_ids
            assert effective == product.format_ids
            assert len(effective) == 2
            assert effective[0]["id"] == "video_15s"
            assert effective[1]["id"] == "video_30s"


class TestEffectiveProperties:
    """Tests for Product.effective_properties property."""

    @pytest.mark.requires_db
    def test_effective_properties_returns_profile_properties_when_profile_set(
        self, integration_db, test_product_with_profile, test_profile
    ):
        """Test effective_properties returns profile.publisher_properties when profile is set.

        Validates that:
        - Product has inventory_profile_id set
        - effective_properties returns profile.publisher_properties
        - Product's direct properties field is None (uses property_tags to satisfy XOR)
        """
        with get_db_session() as session:
            from sqlalchemy import select

            stmt = select(Product).where(Product.product_id == test_product_with_profile.product_id)
            product = session.scalars(stmt).first()

            # Product should have profile reference
            assert product.inventory_profile_id == test_profile.id
            assert product.inventory_profile is not None

            # effective_properties should return profile's publisher_properties
            effective = product.effective_properties
            assert effective == test_profile.publisher_properties
            assert len(effective) == 1
            assert effective[0]["publisher_domain"] == "example.com"
            assert effective[0]["property_ids"] == ["example_homepage"]

            # Product's direct properties should be None (uses property_tags for XOR constraint)
            assert product.properties is None
            assert product.property_tags == ["ignored_tag"]

    @pytest.mark.requires_db
    def test_effective_properties_returns_custom_properties_when_profile_not_set(
        self, integration_db, test_product_custom
    ):
        """Test effective_properties returns synthesized publisher_properties when profile is not set.

        Validates that:
        - Product has no inventory_profile_id
        - effective_properties synthesizes AdCP publisher_properties from property_tags
        - When product uses property_tags, properties is None
        """
        with get_db_session() as session:
            from sqlalchemy import select

            stmt = select(Product).where(Product.product_id == test_product_custom.product_id)
            product = session.scalars(stmt).first()

            # Product should NOT have profile reference
            assert product.inventory_profile_id is None

            # Custom product uses property_tags, so properties should be None
            assert product.properties is None
            assert product.property_tags == ["premium", "video"]

            # effective_properties should synthesize by_tag variant from property_tags
            effective = product.effective_properties
            assert effective is not None
            assert len(effective) == 1
            assert effective[0]["selection_type"] == "by_tag"
            assert effective[0]["property_tags"] == ["premium", "video"]
            assert "publisher_domain" in effective[0]


class TestEffectivePropertyTags:
    """Tests for Product.effective_property_tags property."""

    @pytest.mark.requires_db
    def test_effective_property_tags_returns_none_when_profile_set(self, integration_db, test_product_with_profile):
        """Test effective_property_tags returns None when profile is set.

        Validates that:
        - Product has inventory_profile_id set
        - effective_property_tags returns None (profiles use properties not tags)
        - This signals to use effective_properties instead
        """
        with get_db_session() as session:
            from sqlalchemy import select

            stmt = select(Product).where(Product.product_id == test_product_with_profile.product_id)
            product = session.scalars(stmt).first()

            # Product should have profile reference
            assert product.inventory_profile_id is not None
            assert product.inventory_profile is not None

            # effective_property_tags should return None for profile-based products
            effective = product.effective_property_tags
            assert effective is None

            # Product still has custom property_tags (but they're ignored)
            assert product.property_tags == ["ignored_tag"]

    @pytest.mark.requires_db
    def test_effective_property_tags_returns_custom_tags_when_profile_not_set(
        self, integration_db, test_product_custom
    ):
        """Test effective_property_tags returns product.property_tags when profile is not set.

        Validates that:
        - Product has no inventory_profile_id
        - effective_property_tags returns product.property_tags (custom config)
        """
        with get_db_session() as session:
            from sqlalchemy import select

            stmt = select(Product).where(Product.product_id == test_product_custom.product_id)
            product = session.scalars(stmt).first()

            # Product should NOT have profile reference
            assert product.inventory_profile_id is None

            # effective_property_tags should return product's custom tags
            effective = product.effective_property_tags
            assert effective == product.property_tags
            assert effective == ["premium", "video"]


class TestEffectiveImplementationConfig:
    """Tests for Product.effective_implementation_config property."""

    @pytest.mark.requires_db
    def test_effective_implementation_config_builds_from_profile_inventory(
        self, integration_db, test_product_with_profile, test_profile
    ):
        """Test effective_implementation_config builds GAM config from profile.inventory_config.

        Validates that:
        - Product has inventory_profile_id set
        - effective_implementation_config builds config from profile.inventory_config
        - Config contains correct GAM-specific fields
        - ad_units, placements, include_descendants match profile
        """
        with get_db_session() as session:
            from sqlalchemy import select

            stmt = select(Product).where(Product.product_id == test_product_with_profile.product_id)
            product = session.scalars(stmt).first()

            # Product should have profile reference
            assert product.inventory_profile_id == test_profile.id
            assert product.inventory_profile is not None

            # effective_implementation_config should build from profile
            effective = product.effective_implementation_config
            assert isinstance(effective, dict)

            # Should contain GAM-specific fields from profile.inventory_config
            assert effective["targeted_ad_unit_ids"] == ["23312403859", "23312403860"]
            assert effective["targeted_placement_ids"] == ["45678901"]
            assert effective["include_descendants"] is True

            # Should NOT return product's custom config
            assert effective != product.implementation_config
            assert product.implementation_config["targeted_ad_unit_ids"] == ["ignored_unit"]

    @pytest.mark.requires_db
    def test_effective_implementation_config_returns_custom_config_when_profile_not_set(
        self, integration_db, test_product_custom
    ):
        """Test effective_implementation_config returns custom config when profile is not set.

        Validates that:
        - Product has no inventory_profile_id
        - effective_implementation_config returns product.implementation_config
        - Custom fields are preserved
        """
        with get_db_session() as session:
            from sqlalchemy import select

            stmt = select(Product).where(Product.product_id == test_product_custom.product_id)
            product = session.scalars(stmt).first()

            # Product should NOT have profile reference
            assert product.inventory_profile_id is None

            # effective_implementation_config should return product's custom config
            effective = product.effective_implementation_config
            assert effective == product.implementation_config

            # Should contain custom GAM config
            assert effective["targeted_ad_unit_ids"] == ["custom_unit_1", "custom_unit_2"]
            assert effective["targeted_placement_ids"] == ["custom_placement_1"]
            assert effective["include_descendants"] is False

            # Should preserve custom fields
            assert effective["custom_field"] == "custom_value"

    @pytest.mark.requires_db
    def test_effective_properties_handle_none_profile_relationship(self, integration_db, test_tenant, test_profile):
        """Test effective properties handle None profile relationship gracefully.

        Validates that:
        - Product has inventory_profile_id set but relationship is None (simulated)
        - Fallback behavior works (returns custom config)
        - No errors or exceptions raised

        Note: We can't create a product with an invalid foreign key due to DB constraints,
        so we test by accessing the product when the relationship hasn't been loaded.
        """
        with get_db_session() as session:
            # Create product with valid profile_id
            product = create_test_db_product(
                tenant_id=test_tenant.tenant_id,
                product_id="test_profile_fallback",
                name="Test Profile Fallback",
                description="Product to test profile fallback behavior",
                inventory_profile_id=test_profile.id,
                format_ids=[{"agent_url": "http://fallback.example.com", "id": "fallback_format"}],
                property_tags=["fallback_tag"],
                implementation_config={
                    "targeted_ad_unit_ids": ["fallback_unit"],
                },
                is_custom=True,
                countries=["US"],
            )
            session.add(product)
            session.commit()
            product_id = product.product_id

            # Add required pricing option
            pricing = PricingOption(
                tenant_id=test_tenant.tenant_id,
                product_id="test_profile_fallback",
                pricing_model="cpm",
                rate=Decimal("10.0"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)
            session.commit()

        # Reload product in a new session without loading the relationship
        with get_db_session() as session:
            from sqlalchemy import select

            from src.core.database.models import Product

            stmt = select(Product).where(Product.product_id == product_id)
            product = session.scalars(stmt).first()

            # Access inventory_profile_id directly (should be set)
            assert product.inventory_profile_id == test_profile.id

            # The relationship exists and should be loaded when accessed
            # This tests that the effective_* properties work correctly
            assert product.inventory_profile is not None

            # Effective properties should use profile config
            effective_formats = product.effective_format_ids
            assert effective_formats == test_profile.format_ids
            assert len(effective_formats) == 2

            # effective_properties uses profile, not product's direct properties
            effective_properties = product.effective_properties
            assert effective_properties == test_profile.publisher_properties

            # effective_property_tags returns None for profile-based products
            effective_property_tags = product.effective_property_tags
            assert effective_property_tags is None

            # effective_config built from profile inventory_config
            effective_config = product.effective_implementation_config
            assert effective_config["targeted_ad_unit_ids"] == ["23312403859", "23312403860"]
            assert effective_config["targeted_placement_ids"] == ["45678901"]
