"""Integration tests for product deletion functionality.

⚠️ MIGRATION NOTICE: This test has been migrated to tests/integration_v2/ to use the new
pricing_options model. The original file in tests/integration/ is deprecated.
"""

from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select

from src.admin.app import create_app

app, _ = create_app()
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, PricingOption, Principal, Product, Tenant, TenantManagementConfig
from tests.integration_v2.conftest import add_required_setup_data, create_test_product_with_pricing


@pytest.fixture
def client():
    """Flask test client with test configuration."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    app.config["SESSION_COOKIE_HTTPONLY"] = False
    app.config["SESSION_COOKIE_SECURE"] = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_tenant_and_products(integration_db):
    """Create a test tenant with products for deletion tests."""
    with get_db_session() as session:
        # Clean up any existing test data
        try:
            session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == "test_delete"))
            session.execute(delete(PricingOption).where(PricingOption.tenant_id == "test_delete"))
            session.execute(delete(Product).where(Product.tenant_id == "test_delete"))
            session.execute(delete(Principal).where(Principal.tenant_id == "test_delete"))
            session.execute(delete(Tenant).where(Tenant.tenant_id == "test_delete"))
            session.commit()
        except:
            session.rollback()

        # Create test tenant with required setup
        tenant = Tenant(
            tenant_id="test_delete",
            name="Test Delete Tenant",
            subdomain="test-delete",
            ad_server="mock",
            is_active=True,
        )
        session.add(tenant)
        session.commit()

        # Add required setup data (currency limits, property tags)
        add_required_setup_data(session, "test_delete")

        # Create test principal (required for media buys)
        import uuid

        principal = Principal(
            tenant_id="test_delete",
            principal_id="test_principal",
            name="Test Principal",
            platform_mappings={"mock": {"advertiser_id": "test_principal"}},  # Use valid platform key (mock)
            access_token=str(uuid.uuid4()),  # Required field
        )
        session.add(principal)
        session.commit()

        # Create test products using new pricing_options model
        product1 = create_test_product_with_pricing(
            session=session,
            tenant_id="test_delete",
            product_id="test_product_1",
            name="Test Product 1",
            pricing_model="CPM",
            rate="10.00",
            is_fixed=False,
            formats=[{"agent_url": "https://test.com", "id": "display_300x250"}],
        )

        product2 = create_test_product_with_pricing(
            session=session,
            tenant_id="test_delete",
            product_id="test_product_2",
            name="Test Product 2",
            pricing_model="CPM",
            rate="15.00",
            is_fixed=False,
            formats=[{"agent_url": "https://test.com", "id": "video_30s"}],
        )

        # Product with invalid format data (for pattern validation testing)
        product3 = create_test_product_with_pricing(
            session=session,
            tenant_id="test_delete",
            product_id="test_product_invalid",
            name="Test Product Invalid Format",
            pricing_model="CPM",
            rate="20.00",
            is_fixed=False,
            formats='[{"format_id": "test", "type": "invalid_type"}]',  # Invalid type for testing
        )

        session.commit()

        yield {"tenant": tenant, "products": [product1, product2, product3]}

        # Cleanup
        try:
            session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == "test_delete"))
            session.execute(delete(PricingOption).where(PricingOption.tenant_id == "test_delete"))
            session.execute(delete(Product).where(Product.tenant_id == "test_delete"))
            session.execute(delete(Principal).where(Principal.tenant_id == "test_delete"))
            session.execute(delete(Tenant).where(Tenant.tenant_id == "test_delete"))
            session.commit()
        except:
            pass


@pytest.fixture
def authenticated_session(client):
    """Create an authenticated session with super admin privileges."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = "test@example.com"
        sess["email"] = "test@example.com"
        sess["is_super_admin"] = True
        sess["role"] = "super_admin"
    yield sess


@pytest.fixture
def setup_super_admin_config(integration_db):
    """Setup super admin configuration in database."""
    with get_db_session() as session:
        # Clean up existing config
        session.execute(delete(TenantManagementConfig).where(TenantManagementConfig.config_key == "super_admin_emails"))

        # Create super admin config
        config = TenantManagementConfig(
            config_key="super_admin_emails", config_value="test@example.com", description="Test super admin emails"
        )
        session.add(config)
        session.commit()

        yield

        # Cleanup
        session.execute(delete(TenantManagementConfig).where(TenantManagementConfig.config_key == "super_admin_emails"))
        session.commit()


@pytest.mark.requires_db
class TestProductDeletion:
    """Test suite for product deletion functionality."""

    def test_delete_product_success(
        self, client, test_tenant_and_products, authenticated_session, setup_super_admin_config
    ):
        """Test successful product deletion."""
        tenant_id = "test_delete"
        product_id = "test_product_1"

        response = client.delete(f"/tenant/{tenant_id}/products/{product_id}/delete")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert "Test Product 1" in data["message"]

        # Verify product is actually deleted from database
        with get_db_session() as session:
            product = session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)).first()
            assert product is None

    def test_delete_nonexistent_product(
        self, client, test_tenant_and_products, authenticated_session, setup_super_admin_config
    ):
        """Test deletion of non-existent product returns 404."""
        tenant_id = "test_delete"
        product_id = "nonexistent_product"

        response = client.delete(f"/tenant/{tenant_id}/products/{product_id}/delete")

        assert response.status_code == 404
        data = response.get_json()
        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_delete_product_with_active_media_buy(
        self, client, test_tenant_and_products, authenticated_session, setup_super_admin_config
    ):
        """Test deletion of product that has active media buys."""
        tenant_id = "test_delete"
        product_id = "test_product_1"

        # Create an active media buy that references this product
        with get_db_session() as session:
            media_buy = MediaBuy(
                tenant_id=tenant_id,
                media_buy_id="test_buy_1",
                principal_id="test_principal",
                order_name="Test Order",
                advertiser_name="Test Advertiser",
                status="active",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                raw_request={"product_ids": [product_id]},
                budget=1000.0,
            )
            session.add(media_buy)
            session.commit()

        response = client.delete(f"/tenant/{tenant_id}/products/{product_id}/delete")

        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "active media buy" in data["error"].lower()
        assert "test_buy_1" in data["error"]

        # Verify product still exists
        with get_db_session() as session:
            product = session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)).first()
            assert product is not None

    def test_delete_product_with_pending_media_buy(
        self, client, test_tenant_and_products, authenticated_session, setup_super_admin_config
    ):
        """Test deletion of product that has pending media buys."""
        tenant_id = "test_delete"
        product_id = "test_product_2"

        # Create a pending media buy that references this product
        with get_db_session() as session:
            media_buy = MediaBuy(
                tenant_id=tenant_id,
                media_buy_id="test_buy_pending",
                principal_id="test_principal",
                order_name="Test Pending Order",
                advertiser_name="Test Advertiser",
                status="pending",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                raw_request={"product_ids": [product_id]},
                budget=500.0,
            )
            session.add(media_buy)
            session.commit()

        response = client.delete(f"/tenant/{tenant_id}/products/{product_id}/delete")

        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "active media buy" in data["error"].lower()

    def test_delete_product_with_completed_media_buy_allowed(
        self, client, test_tenant_and_products, authenticated_session, setup_super_admin_config
    ):
        """Test deletion of product with completed media buys is allowed."""
        tenant_id = "test_delete"
        product_id = "test_product_2"

        # Create a completed media buy that references this product
        with get_db_session() as session:
            media_buy = MediaBuy(
                tenant_id=tenant_id,
                media_buy_id="test_buy_completed",
                principal_id="test_principal",
                order_name="Test Completed Order",
                advertiser_name="Test Advertiser",
                status="completed",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                raw_request={"product_ids": [product_id]},
                budget=500.0,
            )
            session.add(media_buy)
            session.commit()

        response = client.delete(f"/tenant/{tenant_id}/products/{product_id}/delete")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    def test_delete_product_unauthorized(self, client, test_tenant_and_products):
        """Test product deletion without authentication."""
        tenant_id = "test_delete"
        product_id = "test_product_1"

        response = client.delete(f"/tenant/{tenant_id}/products/{product_id}/delete")

        # Should redirect to login (302) or return 401/403
        assert response.status_code in [302, 401, 403]

    def test_delete_product_wrong_tenant(
        self, client, test_tenant_and_products, authenticated_session, setup_super_admin_config
    ):
        """Test deletion of product from wrong tenant."""
        product_id = "test_product_1"
        wrong_tenant_id = "wrong_tenant"

        response = client.delete(f"/tenant/{wrong_tenant_id}/products/{product_id}/delete")

        assert response.status_code == 404
        data = response.get_json()
        assert "error" in data

    def test_delete_product_validation_error_handling(
        self, client, test_tenant_and_products, authenticated_session, setup_super_admin_config
    ):
        """Test that validation errors are properly handled and reported."""
        tenant_id = "test_delete"
        product_id = "test_product_invalid"

        # This product has invalid format data that might cause validation errors
        response = client.delete(f"/tenant/{tenant_id}/products/{product_id}/delete")

        # The deletion should either succeed (if validation doesn't trigger)
        # or return a proper error message (if validation fails)
        if response.status_code == 400:
            data = response.get_json()
            assert "error" in data
            # Should have improved error message from our fix
            assert "Validation error:" in data["error"] or "Failed to delete product:" in data["error"]
        else:
            # If deletion succeeded, that's also acceptable
            assert response.status_code == 200

    def test_delete_product_with_csrf_token(
        self, client, test_tenant_and_products, authenticated_session, setup_super_admin_config
    ):
        """Test that deletion works without CSRF token (since we removed the requirement)."""
        tenant_id = "test_delete"
        product_id = "test_product_1"

        # Test deletion without CSRF token should work now
        response = client.delete(
            f"/tenant/{tenant_id}/products/{product_id}/delete", headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    def test_delete_product_database_error_handling(
        self, client, test_tenant_and_products, authenticated_session, setup_super_admin_config
    ):
        """Test that database errors are properly handled."""
        tenant_id = "test_delete"
        product_id = "test_product_1"

        # Mock a database error in the product deletion module
        with patch("src.admin.blueprints.products.get_db_session") as mock_session:
            # Mock the context manager to raise an exception
            mock_context = mock_session.return_value.__enter__.return_value
            # SQLAlchemy 2.0 uses scalars() instead of query()
            mock_context.scalars.side_effect = Exception("Database connection failed")

            response = client.delete(f"/tenant/{tenant_id}/products/{product_id}/delete")

            assert response.status_code == 500
            data = response.get_json()
            assert "error" in data
            assert "Failed to delete product" in data["error"]

    def test_delete_multiple_products_different_statuses(
        self, client, test_tenant_and_products, authenticated_session, setup_super_admin_config
    ):
        """Test deleting multiple products with different media buy statuses."""
        tenant_id = "test_delete"

        # Create media buys with different statuses
        with get_db_session() as session:
            # Product 1: No media buys (should delete)
            # Product 2: Active media buy (should not delete)
            active_buy = MediaBuy(
                tenant_id=tenant_id,
                media_buy_id="active_buy",
                principal_id="test_principal",
                order_name="Test Active Order",
                advertiser_name="Test Advertiser",
                status="active",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
                raw_request={"product_ids": ["test_product_2"]},
                budget=1000.0,
            )
            session.add(active_buy)
            session.commit()

        # Try to delete product with no media buys
        response1 = client.delete(f"/tenant/{tenant_id}/products/test_product_1/delete")
        assert response1.status_code == 200

        # Try to delete product with active media buy
        response2 = client.delete(f"/tenant/{tenant_id}/products/test_product_2/delete")
        assert response2.status_code == 400


class TestEnvironmentFirstAuthentication:
    """Test the environment-first authentication approach we implemented."""

    def test_environment_super_admin_check(self):
        """Test that environment variables are checked first for super admin status."""
        from src.admin.utils import is_super_admin

        # Test with environment variable
        with patch.dict("os.environ", {"SUPER_ADMIN_EMAILS": "env-admin@example.com"}):
            # Should return True from environment, even if not in database
            assert is_super_admin("env-admin@example.com") is True
            assert is_super_admin("not-admin@example.com") is False

    def test_database_fallback_super_admin_check(self, integration_db, setup_super_admin_config):
        """Test that database is used as fallback when environment variables are not set."""
        from src.admin.utils import is_super_admin

        # Clear environment variables
        with patch.dict("os.environ", {"SUPER_ADMIN_EMAILS": ""}, clear=False):
            # Should fallback to database configuration
            assert is_super_admin("test@example.com") is True
            assert is_super_admin("not-admin@example.com") is False

    def test_domain_based_super_admin_environment(self):
        """Test domain-based super admin authentication from environment."""
        from src.admin.utils import is_super_admin

        with patch.dict("os.environ", {"SUPER_ADMIN_DOMAINS": "example.com,admin.org"}):
            assert is_super_admin("user@example.com") is True
            assert is_super_admin("admin@admin.org") is True
            assert is_super_admin("user@other.com") is False

    def test_session_caching_optimization(self, client, test_tenant_and_products, setup_super_admin_config):
        """Test that super admin status is cached in session to avoid redundant database calls."""

        # Clear environment variables to force database check
        with patch.dict("os.environ", {"SUPER_ADMIN_EMAILS": "", "SUPER_ADMIN_DOMAINS": ""}, clear=False):
            with client.session_transaction() as sess:
                sess["authenticated"] = True
                sess["user"] = "test@example.com"
                sess["email"] = "test@example.com"
                # Don't set is_super_admin initially to test caching

            tenant_id = "test_delete"

            # First request should check database and cache super admin status
            response = client.get(f"/tenant/{tenant_id}/products/")  # Note trailing slash
            assert response.status_code == 200  # Should succeed

            # Verify session now has is_super_admin cached
            with client.session_transaction() as sess:
                assert sess.get("is_super_admin") is True
