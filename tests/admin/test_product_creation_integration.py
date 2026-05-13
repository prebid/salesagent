"""Integration tests for product creation via UI and API."""

import pytest
from sqlalchemy import delete, select

from src.admin.app import create_app

app = create_app()
from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Product, Tenant
from tests.integration.conftest import (
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
    from werkzeug.datastructures import MultiDict

    # Note: Formats are now fetched from creative agents via AdCP protocol (not local DB)
    # This test focuses on JSON encoding of countries and other JSON fields
    # Pricing uses indexed form fields (pricing_model_0, rate_0, etc.) per parse_pricing_options_from_form()
    product_data = MultiDict(
        [
            ("product_id", "test_product_json"),
            ("name", "Test Product JSON"),
            ("description", "Test product for JSON encoding"),
            ("countries", "US"),  # First country
            ("countries", "GB"),  # Second country
            ("delivery_type", "non_guaranteed"),  # Required field
            # Indexed pricing option fields (auction CPM with floor price)
            ("pricing_model_0", "cpm_auction"),
            ("currency_0", "USD"),
            ("floor_0", "5.0"),
            ("min_spend_0", "1000"),
            ("property_mode", "none"),  # Bypass property tag validation — this test is about JSON encoding
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
    assert b"Error" not in response.data


@pytest.fixture
def authenticated_admin(client, test_tenant, integration_db):
    """Create a tenant-admin User via factory and seed an authenticated session.

    Returns the test_tenant for chained-fixture convenience.
    """
    from src.core.database.models import Tenant, User
    from tests.factories import UserFactory
    from tests.helpers.managed_tenant_api import bind_factories_to_session

    with bind_factories_to_session() as session:
        existing = session.scalars(select(User).filter_by(email="test@example.com")).first()
        if not existing:
            # Re-load the tenant inside the factory's session so UserFactory's
            # tenant SubFactory uses a session-bound parent rather than the
            # detached instance from the test_tenant fixture's closed session.
            attached_tenant = session.scalars(select(Tenant).filter_by(tenant_id=test_tenant.tenant_id)).one()
            UserFactory(
                tenant=attached_tenant,
                email="test@example.com",
                name="Test User",
                role="admin",
                is_active=True,
            )

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
    return test_tenant


@pytest.mark.requires_db
def test_add_product_without_property_returns_validation_error_not_500(client, authenticated_admin, integration_db):
    """Regression for #335: clicking Save without selecting a property
    must render the form again with a flash error, NOT a 500.

    The storefront iframe submits the form without ever selecting a
    publisher property (because the embedded tenant had no publishers —
    see #336). The user reported "Internal Server Error" instead of the
    validation message the server-side code at
    ``src/admin/blueprints/products.py:1031-1033`` was supposed to flash.

    This test posts the minimum payload the storefront sends:
    name + pricing, no ``selected_property_tags``, no ``property_mode``
    override.
    """
    from werkzeug.datastructures import MultiDict

    # Minimum form payload — name + one pricing option, no property selection.
    # property_mode is intentionally omitted; the route defaults to "tags",
    # then sees no ``selected_property_tags`` and should flash + re-render.
    product_data = MultiDict(
        [
            ("name", "Storefront Repro Product"),
            ("description", "Reproduces #335"),
            ("delivery_type", "non_guaranteed"),
            ("pricing_model_0", "cpm_auction"),
            ("currency_0", "USD"),
            ("floor_0", "5.0"),
        ]
    )

    response = client.post(
        "/tenant/test_product_tenant/products/add",
        data=product_data,
        follow_redirects=False,
    )

    # Must NOT be a 500 — the route is expected to render the form again
    # with a validation flash, status 200.
    assert response.status_code != 500, (
        f"Product save returned 500 instead of a validation error. Body: {response.data[:500]!r}"
    )
    assert response.status_code == 200, f"Expected 200 (re-rendered form), got {response.status_code}"

    # The flash should include the property-tag validation message.
    body = response.get_data(as_text=True)
    assert "Please select at least one property tag" in body, (
        f"Expected property-tag validation message in re-rendered form; body did not contain it. "
        f"First 500 chars: {body[:500]!r}"
    )

    # No product row should have been created.
    with get_db_session() as session:
        leaked = session.scalars(
            select(Product).filter_by(tenant_id="test_product_tenant", name="Storefront Repro Product")
        ).first()
        assert leaked is None, "Validation failed but a Product row was persisted anyway"


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "case_name, form_data",
    [
        ("only_name", [("name", "Bare Product")]),
        ("name_and_pricing_only", [("name", "Repro2"), ("pricing_model_0", "cpm_auction"), ("currency_0", "USD")]),
        (
            "invalid_pricing_rate",
            [
                ("name", "Repro3"),
                ("pricing_model_0", "cpm_fixed"),
                ("currency_0", "USD"),
                ("rate_0", "not-a-number"),
            ],
        ),
        (
            "invalid_property_mode",
            [
                ("name", "Repro4"),
                ("pricing_model_0", "cpm_auction"),
                ("currency_0", "USD"),
                ("floor_0", "5.0"),
                ("property_mode", "bogus_unknown_mode"),
            ],
        ),
        (
            "property_ids_mode_no_selection",
            [
                ("name", "Repro5"),
                ("pricing_model_0", "cpm_auction"),
                ("currency_0", "USD"),
                ("floor_0", "5.0"),
                ("property_mode", "property_ids"),
            ],
        ),
    ],
)
def test_add_product_malformed_inputs_never_return_500(
    client, authenticated_admin, integration_db, case_name, form_data
):
    """Regression for #335: every malformed product-create POST should
    surface a validation error (200 with flash) or a structured error,
    NEVER a raw 500. Parametrized over scenarios that bypass the
    client-side validator (which the storefront iframe may not fire)."""
    from werkzeug.datastructures import MultiDict

    response = client.post(
        "/tenant/test_product_tenant/products/add",
        data=MultiDict(form_data),
        follow_redirects=False,
    )

    assert response.status_code != 500, f"[{case_name}] Product save returned 500. Body: {response.data[:500]!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
