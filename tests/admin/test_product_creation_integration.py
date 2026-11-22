"""Integration tests for product creation via UI and API."""

import json
from collections.abc import Sequence

import pytest
from sqlalchemy import delete, select
from werkzeug.datastructures import MultiDict

from src.admin.app import create_app

app = create_app()
from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Product, Tenant
from tests.integration.conftest import (
    add_required_setup_data,
    create_test_product_with_pricing,
)
from tests.utils.database_helpers import create_tenant_with_timestamps

#: How base.html renders a flashed error, the one way a page shows the operator a failure it did not answer
#: with an error status.
_ERROR_FLASH = b'class="alert alert-error"'


def _assert_no_error_shown(response) -> None:
    """The page shows no flashed error; a substring such as b"Error" also matches page scripts."""
    at = response.data.find(_ERROR_FLASH)
    assert at == -1, f"The page shows an error: {response.data[at : at + 600]!r}"


def _valid_product_form(product_id: str, name: str, *, description: str, countries: Sequence[str] = ()) -> MultiDict:
    """The add-product form with every field the form requires, plus *countries*.

    One auction CPM pricing option, the property the fixture publisher authorizes
    (``add_required_setup_data``), and one canonical format declaration: core/product.json's
    root anyOf requires a format arm, so the form refuses a product without one.
    """
    return MultiDict(
        [
            ("product_id", product_id),
            ("name", name),
            ("description", description),
            *(("countries", country) for country in countries),
            ("delivery_type", "non_guaranteed"),
            ("pricing_model_0", "cpm_auction"),
            ("currency_0", "USD"),
            ("floor_0", "5.0"),
            ("min_spend_0", "1000"),
            ("property_mode", "property_ids"),
            ("selected_property_ids", "fixture_default_psatest_com"),
            ("format_options", json.dumps([{"format_kind": "image", "params": {"width": 300, "height": 250}}])),
        ]
    )


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

    # Two countries, so the stored column has to be a JSON array of both.
    product_data = _valid_product_form(
        "test_product_json",
        "Test Product JSON",
        description="Test product for JSON encoding",
        countries=["US", "GB"],
    )

    # Send POST request to add product
    response = client.post("/tenant/test_product_tenant/products/add", data=product_data, follow_redirects=True)

    # Check response - should redirect to products list on success
    assert response.status_code == 200, f"Failed to create product: {response.data}"
    # Check that we were redirected to the products list page
    assert b"Products" in response.data
    # Check for error messages
    _assert_no_error_shown(response)

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
    # The required fields only: no countries and no other optional JSON field is submitted.
    product_data = _valid_product_form(
        "test_product_empty", "Test Product Empty JSON", description="Test product with empty JSON fields"
    )

    response = client.post("/tenant/test_product_tenant/products/add", data=product_data, follow_redirects=True)

    assert response.status_code == 200
    _assert_no_error_shown(response)

    # Verify empty arrays/objects are stored correctly
    with get_db_session() as session:
        product = session.scalars(
            select(Product).filter_by(tenant_id="test_product_tenant", product_id="test_product_empty")
        ).first()

        assert product is not None, "The form refused a product carrying every required field"
        # Fields the form did not submit are stored empty, never as a string of JSON.
        assert product.format_ids in [None, []]
        assert product.countries in [None, []]
        assert product.price_guidance in [None, {}]
        assert product.targeting_template in [None, {}]


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
            format_ids=[
                {"id": "display_300x250", "agent_url": "https://test.example.com"},
                {"id": "video_16x9", "agent_url": "https://test.example.com"},
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
    _assert_no_error_shown(response)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
