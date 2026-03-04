"""Shared fixtures for admin tests."""

from unittest.mock import MagicMock, Mock

import pytest

from src.admin.app import create_app


@pytest.fixture
def test_app():
    """Create a test Flask application."""
    config = {
        "TESTING": True,
        "SECRET_KEY": "test_secret_key",
        "WTF_CSRF_ENABLED": False,
    }
    app = create_app(config)
    return app


@pytest.fixture
def test_client(test_app):
    """Create a test client."""
    return test_app.test_client()


@pytest.fixture
def authenticated_client(test_app):
    """Create an authenticated test client."""
    client = test_app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "test_user@example.com"
        sess["user_name"] = "Test User"
    return client


@pytest.fixture
def super_admin_client(test_app):
    """Create a super admin test client."""
    client = test_app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "admin@example.com"
        sess["user_name"] = "Admin User"
        sess["is_super_admin"] = True
    return client


@pytest.fixture
def tenant_admin_client(test_app):
    """Create a tenant admin test client."""
    client = test_app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "tenant_admin@example.com"
        sess["user_name"] = "Tenant Admin"
        sess["tenant_id"] = "test_tenant"
        sess["is_tenant_admin"] = True
    return client


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    session = MagicMock()
    return session


@pytest.fixture
def mock_tenant():
    """Create a mock tenant object."""
    tenant = Mock()
    tenant.tenant_id = "test_tenant"
    tenant.name = "Test Tenant"
    tenant.subdomain = "test"
    tenant.admin_token = "test_admin_token"
    tenant.slack_webhook_url = "https://hooks.slack.com/test"
    tenant.adapter_config = '{"mock": {"enabled": true}}'
    tenant.max_daily_budget = 10000
    tenant.enable_axe_signals = True
    tenant.auto_approve_format_ids = ["display_300x250"]
    tenant.human_review_required = False
    tenant.policy_settings = '{"enabled": false}'
    return tenant


@pytest.fixture
def mock_principal():
    """Create a mock principal object."""
    principal = Mock()
    principal.principal_id = "test_principal"
    principal.tenant_id = "test_tenant"
    principal.name = "Test Principal"
    principal.access_token = "test_access_token"
    principal.platform_mappings = '{"mock": {"id": "123"}}'
    return principal


@pytest.fixture
def mock_product():
    """Create a mock product object."""
    product = Mock()
    product.product_id = "test_product"
    product.tenant_id = "test_tenant"
    product.name = "Test Product"
    product.description = "Test product description"
    product.price_model = "cpm"
    product.base_price = 10.0
    product.currency = "USD"
    product.min_spend = 100.0
    product.format_ids = '["display", "video"]'
    product.countries = '["US", "CA"]'
    return product


@pytest.fixture
def mock_user():
    """Create a mock user object."""
    user = Mock()
    user.user_id = "test_user"
    user.tenant_id = "test_tenant"
    user.email = "user@example.com"
    user.name = "Test User"
    user.is_admin = False
    user.is_active = True
    return user


@pytest.fixture
def mock_media_buy():
    """Create a mock media buy object."""
    media_buy = Mock()
    media_buy.media_buy_id = "test_media_buy"
    media_buy.tenant_id = "test_tenant"
    media_buy.principal_id = "test_principal"
    media_buy.status = "active"
    media_buy.budget = 5000.0
    media_buy.start_date = "2024-01-01"
    media_buy.end_date = "2024-01-31"
    return media_buy
