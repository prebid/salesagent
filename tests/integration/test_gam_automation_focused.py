"""
Integration tests for GAM automatic order activation feature (simplified).

Tests the implementation of Issue #116: automatic activation for non-guaranteed GAM orders.
Focused integration tests using real database connections and minimal mocking.
"""

from datetime import datetime

import pytest
from sqlalchemy import delete, select

from src.adapters.google_ad_manager import GUARANTEED_LINE_ITEM_TYPES, NON_GUARANTEED_LINE_ITEM_TYPES
from src.core.database.database_session import get_db_session
from src.core.database.models import Product, Tenant
from src.core.schemas import MediaPackage, Principal


class TestGAMAutomationBasics:
    """Test basic GAM automation constants and configuration."""

    def test_line_item_type_constants(self):
        """Test that line item type constants are correctly defined."""
        # Test guaranteed types
        assert "STANDARD" in GUARANTEED_LINE_ITEM_TYPES
        assert "SPONSORSHIP" in GUARANTEED_LINE_ITEM_TYPES

        # Test non-guaranteed types
        assert "NETWORK" in NON_GUARANTEED_LINE_ITEM_TYPES
        assert "HOUSE" in NON_GUARANTEED_LINE_ITEM_TYPES
        assert "PRICE_PRIORITY" in NON_GUARANTEED_LINE_ITEM_TYPES
        assert "BULK" in NON_GUARANTEED_LINE_ITEM_TYPES

        # Ensure no overlap
        assert not (GUARANTEED_LINE_ITEM_TYPES & NON_GUARANTEED_LINE_ITEM_TYPES)


@pytest.mark.skip_ci
class TestGAMProductConfiguration:
    """Test database-backed product configuration for automation."""

    @pytest.fixture
    def test_tenant_data(self, integration_db):
        """Create test tenant and products in database."""
        tenant_id = "test_automation_tenant"

        with get_db_session() as db_session:
            # Create test tenant
            test_tenant = Tenant(
                tenant_id=tenant_id,
                name="Test Automation Tenant",
                subdomain="test-auto",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            db_session.add(test_tenant)

            # Non-guaranteed product with automatic activation
            product_auto = Product(
                tenant_id=tenant_id,
                product_id="test_product_auto",
                name="Auto Network Product",
                formats=[{"format_id": "display_300x250", "name": "Display 300x250", "type": "display"}],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=True,
                cpm=2.50,
                # JSONType expects dict, not json.dumps() string
                implementation_config={
                    "line_item_type": "NETWORK",
                    "non_guaranteed_automation": "automatic",
                    "creative_placeholders": [{"width": 300, "height": 250, "expected_creative_count": 1}],
                },
            )

            # Non-guaranteed product requiring confirmation
            product_conf = Product(
                tenant_id=tenant_id,
                product_id="test_product_confirm",
                name="Confirmation House Product",
                formats=[{"format_id": "display_728x90", "name": "Leaderboard 728x90", "type": "display"}],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=True,
                cpm=1.00,
                # JSONType expects dict, not json.dumps() string
                implementation_config={
                    "line_item_type": "HOUSE",
                    "non_guaranteed_automation": "confirmation_required",
                    "creative_placeholders": [{"width": 728, "height": 90, "expected_creative_count": 1}],
                },
            )

            db_session.add_all([product_auto, product_conf])
            db_session.commit()

            # Get IDs before session closes to avoid DetachedInstanceError
            auto_product_id = product_auto.product_id
            conf_product_id = product_conf.product_id

        yield tenant_id, auto_product_id, conf_product_id

        # Cleanup
        with get_db_session() as db_session:
            db_session.execute(delete(Product).where(Product.tenant_id == tenant_id))
            db_session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
            db_session.commit()

    def test_product_automation_config_parsing(self, test_tenant_data):
        """Test that product automation configuration is correctly stored and retrieved."""
        tenant_id, auto_product_id, conf_product_id = test_tenant_data

        with get_db_session() as db_session:
            # Test automatic product
            auto_product = db_session.scalars(
                select(Product).filter_by(tenant_id=tenant_id, product_id=auto_product_id)
            ).first()

            assert auto_product is not None
            # JSONType automatically deserializes, no json.loads() needed
            config = auto_product.implementation_config
            assert config["non_guaranteed_automation"] == "automatic"
            assert config["line_item_type"] == "NETWORK"

            # Test confirmation required product
            conf_product = db_session.scalars(
                select(Product).filter_by(tenant_id=tenant_id, product_id=conf_product_id)
            ).first()

            assert conf_product is not None
            # JSONType automatically deserializes, no json.loads() needed
            config = conf_product.implementation_config
            assert config["non_guaranteed_automation"] == "confirmation_required"
            assert config["line_item_type"] == "HOUSE"


class TestGAMPackageTypes:
    """Test media package type detection and validation."""

    def test_package_delivery_type_mapping(self):
        """Test that MediaPackage delivery types map correctly to automation behavior."""
        # Non-guaranteed package
        non_guaranteed_pkg = MediaPackage(
            package_id="test_network",
            name="Network Package",
            delivery_type="non_guaranteed",
            impressions=10000,
            cpm=2.50,
            format_ids=["display_300x250"],
        )

        assert non_guaranteed_pkg.delivery_type == "non_guaranteed"

        # Guaranteed package
        guaranteed_pkg = MediaPackage(
            package_id="test_standard",
            name="Standard Package",
            delivery_type="guaranteed",
            impressions=50000,
            cpm=5.00,
            format_ids=["display_300x250"],
        )

        assert guaranteed_pkg.delivery_type == "guaranteed"

    def test_principal_configuration(self):
        """Test principal object creation for GAM integration."""
        principal = Principal(
            principal_id="test_advertiser",
            name="Test Advertiser",
            platform_mappings={"google_ad_manager": {"advertiser_id": "123456"}},
        )

        assert principal.principal_id == "test_advertiser"
        assert principal.platform_mappings["google_ad_manager"]["advertiser_id"] == "123456"
        assert principal.get_adapter_id("gam") == "123456"
