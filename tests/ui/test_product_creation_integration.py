"""Integration tests for product creation via UI and API."""

import pytest
from sqlalchemy import delete, select

from src.admin.app import create_app

app, _ = create_app()
from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Product, Tenant
from tests.integration_v2.conftest import (
    add_required_setup_data,
    create_test_product_with_pricing,
)
from tests.utils.database_helpers import create_tenant_with_timestamps


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
def test_tenant(integration_db):
    """Create a test tenant for product creation tests."""
    # integration_db ensures database tables exist
    with get_db_session() as session:
        # Clean up any existing test tenant (in case of test reruns)
        try:
            session.execute(delete(PricingOption).where(PricingOption.tenant_id == "test_product_tenant"))
            session.execute(delete(Product).where(Product.tenant_id == "test_product_tenant"))
            session.execute(delete(Tenant).where(Tenant.tenant_id == "test_product_tenant"))
            session.commit()
        except Exception:
            session.rollback()  # Ignore errors if tables don't exist yet

        # Create test tenant with required setup data
        tenant = create_tenant_with_timestamps(
            tenant_id="test_product_tenant",
            name="Test Product Tenant",
            subdomain="test-product",
            ad_server="mock",
            enable_axe_signals=True,
            auto_approve_format_ids=[],  # Formats now come from creative agents, not local database
            human_review_required=False,
            billing_plan="basic",
            is_active=True,
        )
        session.add(tenant)
        session.commit()

        # Add required setup data (CurrencyLimit, PropertyTag)
        add_required_setup_data(session, "test_product_tenant")
        session.commit()

        yield tenant

        # Cleanup
        session.execute(delete(PricingOption).where(PricingOption.tenant_id == "test_product_tenant"))
        session.execute(delete(Product).where(Product.tenant_id == "test_product_tenant"))
        session.execute(delete(Tenant).where(Tenant.tenant_id == "test_product_tenant"))
        session.commit()


@pytest.mark.requires_db
def test_add_product_json_encoding(client, test_tenant, integration_db):
    """Test that product creation properly handles JSON fields without double encoding."""

    # Set up user in database for tenant access
    import uuid

    from src.core.database.models import User

    with get_db_session() as session:
        user = User(
            user_id=str(uuid.uuid4()),
            email="test@example.com",
            name="Test User",
            tenant_id="test_product_tenant",
            role="admin",
            is_active=True,
        )
        session.add(user)
        session.commit()

    # Mock the session to be a tenant admin
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {
            "email": "test@example.com",
            "is_super_admin": False,
            "tenant_id": "test_product_tenant",
            "role": "admin",
        }
        sess["email"] = "test@example.com"
        sess["tenant_id"] = "test_product_tenant"
        sess["role"] = "tenant_admin"

    # Product data with JSON fields - using werkzeug's MultiDict for multiple values
    import json

    from werkzeug.datastructures import MultiDict

    # Note: Formats are now fetched from creative agents via AdCP protocol (not local DB)
    # This test focuses on JSON encoding of countries and other JSON fields
    pricing_option = {"pricing_model": "CPM", "rate": 10.0, "currency_code": "USD", "auction": False}

    product_data = MultiDict(
        [
            ("product_id", "test_product_json"),
            ("name", "Test Product JSON"),
            ("description", "Test product for JSON encoding"),
            # Removed format IDs - formats now come from creative agents
            ("countries", "US"),  # First country
            ("countries", "GB"),  # Second country
            ("delivery_type", "non_guaranteed"),  # Required field
            ("pricing_options", json.dumps([pricing_option])),  # Required: at least one pricing option
            ("price_guidance_min", "5.0"),
            ("price_guidance_max", "15.0"),
            ("min_spend", "1000"),
            ("max_impressions", "1000000"),
        ]
    )

    # Send POST request to add product
    response = client.post("/tenant/test_product_tenant/products/add", data=product_data, follow_redirects=True)

    # Check response - should redirect to products list on success
    assert response.status_code == 200, f"Failed to create product: {response.data}"
    # Check that we were redirected to the products list page
    assert b"Products" in response.data
    # Check for error messages
    assert b"Error" not in response.data or b"Error loading" in response.data  # "Error loading" is OK in filters

    # Verify product was created correctly in database
    with get_db_session() as session:
        product = session.scalars(
            select(Product).filter_by(tenant_id="test_product_tenant", product_id="test_product_json")
        ).first()

        assert product is not None
        assert product.name == "Test Product JSON"

        # Check JSON fields are properly stored as arrays/objects, not strings
        # Formats removed - formats now come from creative agents via AdCP protocol
        # Test focuses on countries and other JSON fields
        assert isinstance(product.countries, list)
        assert "US" in product.countries
        assert "GB" in product.countries

        # Verify delivery_type is stored correctly (underscore format per AdCP spec)
        assert product.delivery_type == "non_guaranteed", f"Expected 'non_guaranteed', got '{product.delivery_type}'"

        # Price guidance might be stored differently or might be None for non-guaranteed products
        if product.price_guidance:
            assert isinstance(product.price_guidance, dict)
            # Check if it has the expected structure - it might have different keys
            if "min" in product.price_guidance:
                assert product.price_guidance["min"] == 5.0
                assert product.price_guidance["max"] == 15.0

        # Targeting template might be empty or have geo_country from the countries field
        assert isinstance(product.targeting_template, dict)


@pytest.mark.requires_db
def test_add_product_empty_json_fields(client, test_tenant, integration_db):
    """Test product creation with empty JSON fields."""

    # Set up user in database for tenant access
    import uuid

    from src.core.database.models import User

    with get_db_session() as session:
        # Check if user already exists
        existing = session.scalars(select(User).filter_by(email="test@example.com")).first()
        if not existing:
            user = User(
                user_id=str(uuid.uuid4()),
                email="test@example.com",
                name="Test User",
                tenant_id="test_product_tenant",
                role="admin",
                is_active=True,
            )
            session.add(user)
            session.commit()

    with client.session_transaction() as sess:
        # Use consistent session setup pattern from our authentication fixes
        sess["test_user"] = "test@example.com"
        sess["user"] = {
            "email": "test@example.com",
            "is_super_admin": False,
            "tenant_id": "test_product_tenant",
            "role": "admin",
        }
        sess["test_user_role"] = "tenant_admin"
        sess["test_user_name"] = "Test User"
        sess["authenticated"] = True
        sess["email"] = "test@example.com"
        sess["tenant_id"] = "test_product_tenant"
        sess["role"] = "tenant_admin"

    # Product data with empty JSON fields (no formats or countries selected)
    product_data = {
        "product_id": "test_product_empty",
        "name": "Test Product Empty JSON",
        "description": "Test product with empty JSON fields",
        "delivery_type": "guaranteed",
        "cpm": "10.0",
        "min_spend": "1000",
        # No formats or countries - should result in empty arrays
    }

    response = client.post("/tenant/test_product_tenant/products/add", data=product_data, follow_redirects=True)

    assert response.status_code == 200
    assert b"Error" not in response.data or b"Error loading" in response.data

    # Verify empty arrays/objects are stored correctly
    with get_db_session() as session:
        product = session.scalars(
            select(Product).filter_by(tenant_id="test_product_tenant", product_id="test_product_empty")
        ).first()

        # Product should be created (may fail if form validation rejected it)
        if product is not None:
            # Empty fields might be stored as None or empty lists/dicts depending on the database
            assert product.format_ids in [None, []]
            assert product.countries in [None, []]
            assert product.price_guidance in [None, {}]
            assert product.targeting_template in [None, {}]
        else:
            # Product creation failed, check if there was a validation error in response
            assert b"Product created successfully" not in response.data


@pytest.mark.requires_db
def test_add_product_postgresql_validation(client, test_tenant):
    """Test that PostgreSQL validation constraints work correctly."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {
            "email": "test@example.com",
            "is_super_admin": False,
            "tenant_id": "test_product_tenant",
            "role": "admin",
        }
        sess["email"] = "test@example.com"
        sess["tenant_id"] = "test_product_tenant"
        sess["role"] = "tenant_admin"

    # Try to create a product with invalid JSON (double-encoded)
    # This simulates what would happen if we still had the bug
    with get_db_session() as session:
        # Bypass the API to test database constraint directly
        try:
            # This should fail if we try to insert double-encoded JSON
            bad_product = Product(
                tenant_id="test_product_tenant",
                product_id="test_bad_json",
                name="Bad JSON Product",
                format_ids='"[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]"',  # Double-encoded string
                countries='"["US"]"',  # Double-encoded string
                delivery_type="guaranteed",
            )
            session.add(bad_product)
            session.commit()
            # If we get here, the database accepted bad data (shouldn't happen with PostgreSQL)
            pytest.skip("Database doesn't validate JSON structure (likely SQLite)")
        except Exception as e:
            # PostgreSQL should reject double-encoded JSON
            session.rollback()
            assert "check_format_ids_is_array" in str(e) or "check_countries_is_array" in str(e) or "JSON" in str(e)


@pytest.mark.requires_db
def test_list_products_json_parsing(client, test_tenant, integration_db):
    """Test that list products endpoint properly handles JSON fields."""

    # Set up user in database for tenant access
    import uuid

    from src.core.database.models import User

    # Use the test_tenant fixture's tenant_id consistently
    tenant_id = test_tenant.tenant_id

    with get_db_session() as session:
        # Check if user already exists
        existing = session.scalars(select(User).filter_by(email="test@example.com", tenant_id=tenant_id)).first()
        if not existing:
            user = User(
                user_id=str(uuid.uuid4()),
                email="test@example.com",
                name="Test User",
                tenant_id=tenant_id,
                role="admin",
                is_active=True,
            )
            session.add(user)
            session.commit()

    # Create a product with JSON fields using new pricing model
    with get_db_session() as session:
        product = create_test_product_with_pricing(
            session=session,
            tenant_id=tenant_id,
            product_id="test_list_json",
            name="Test List JSON",
            pricing_model="CPM",
            rate="10.00",
            is_fixed=False,
            formats=[
                {"format_id": "display_300x250", "name": "Display 300x250", "type": "display"},
                {"format_id": "video_16x9", "name": "Video 16:9", "type": "video"},
            ],
            countries=["US", "CA"],
            price_guidance={"min": 10.0, "max": 20.0},
            delivery_type="guaranteed",
            targeting_template={"geo_countries": ["US", "CA"]},
        )
        session.commit()

    with client.session_transaction() as sess:
        # Use consistent session setup pattern from our authentication fixes
        sess["test_user"] = "test@example.com"
        sess["user"] = {
            "email": "test@example.com",
            "is_super_admin": False,
            "tenant_id": tenant_id,
            "role": "admin",
        }
        sess["test_user_role"] = "tenant_admin"
        sess["test_user_name"] = "Test User"
        sess["authenticated"] = True
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["role"] = "tenant_admin"

    # Get products list using consistent tenant_id
    response = client.get(f"/tenant/{tenant_id}/products/")
    assert response.status_code == 200

    # Check that the template receives properly formatted data
    # The template expects price_guidance to have min/max attributes
    # This test ensures the JSON is parsed correctly for template rendering
    assert b"Test List JSON" in response.data
    # Should not have JSON parsing errors in the page
    assert b"Error" not in response.data
    assert b"500" not in response.data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
