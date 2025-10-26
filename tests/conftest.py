"""
Global pytest configuration and fixtures for all tests.

This file provides fixtures available to all test modules.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import database fixtures for all tests
from tests.conftest_db import *  # noqa: F401,F403

# Note: Environment variables are now set via fixtures to avoid global pollution
# See test_environment fixture below for configuration
# Import fixtures modules
from tests.fixtures import (
    CreativeFactory,
    MediaBuyFactory,
    MockAdapter,
    MockDatabase,
    MockGeminiService,
    MockOAuthProvider,
    PrincipalFactory,
    ProductFactory,
    RequestBuilder,
    ResponseBuilder,
    TargetingBuilder,
    TenantFactory,
)

# ============================================================================
# Database Fixtures
# ============================================================================


@pytest.fixture
def mock_db():
    """Provide a mock database connection."""
    return MockDatabase()


@pytest.fixture
def mock_db_with_data(mock_db):
    """Provide a mock database with sample data."""
    # Add sample tenant
    tenant = TenantFactory.create()
    mock_db.set_query_result("SELECT.*FROM tenants", [tenant])

    # Add sample principal
    principal = PrincipalFactory.create(tenant_id=tenant["tenant_id"])
    mock_db.set_query_result("SELECT.*FROM principals", [principal])

    # Add sample products
    products = ProductFactory.create_batch(3, tenant_id=tenant["tenant_id"])
    mock_db.set_query_result("SELECT.*FROM products", products)

    return mock_db


@pytest.fixture
def test_db_path():
    """Provide a temporary test database path."""
    # Create a unique temporary file for each test
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        try:
            os.unlink(db_path)
        except Exception:
            pass  # Ignore cleanup errors


@pytest.fixture(autouse=True, scope="function")
def test_environment(monkeypatch, request):
    """Configure test environment variables without global pollution."""
    # Set testing flags
    monkeypatch.setenv("ADCP_TESTING", "true")
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")  # Enable test mode for auth

    # Check if this is an integration test that needs the database
    is_integration_test = "integration" in str(request.fspath)
    database_url = os.environ.get("DATABASE_URL")

    # Check if this is a unittest.TestCase (which manages its own DATABASE_URL)
    # Pytest calls these as "UnitTestCase" in the node hierarchy
    is_unittest_class = (
        hasattr(request, "cls") and request.cls is not None and issubclass(request.cls, unittest.TestCase)
    )

    # IMPORTANT: Unit tests should NEVER use real database connections
    # Remove database-related env vars UNLESS:
    # 1. This is an integration test with DATABASE_URL set (for integration_db fixture), OR
    # 2. This is a unittest.TestCase class (manages its own DATABASE_URL in setUpClass)
    should_preserve_db = is_integration_test and (database_url or is_unittest_class)

    if not should_preserve_db:
        if "DATABASE_URL" in os.environ:
            monkeypatch.delenv("DATABASE_URL", raising=False)
        if "TEST_DATABASE_URL" in os.environ:
            monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    # Set test API keys and credentials
    monkeypatch.setenv("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", "test_key_for_mocking"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", os.environ.get("GOOGLE_CLIENT_ID", "test_client_id"))
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", os.environ.get("GOOGLE_CLIENT_SECRET", "test_client_secret"))
    monkeypatch.setenv("SUPER_ADMIN_EMAILS", os.environ.get("SUPER_ADMIN_EMAILS", "test@example.com"))

    yield

    # Cleanup: Reset engine to ensure clean state for next test
    # This prevents test isolation issues from module-level state
    try:
        from src.core.database.database_session import reset_engine

        reset_engine()
    except Exception:
        # Ignore errors during cleanup (e.g., if module not yet loaded)
        pass


# NOTE: db_session fixture is now imported from conftest_db.py
# This fixture is deprecated in favor of the conftest_db version
# which properly returns SQLAlchemy Session objects


# ============================================================================
# Factory Fixtures
# ============================================================================


@pytest.fixture
def tenant_factory():
    """Provide tenant factory."""
    return TenantFactory


@pytest.fixture
def principal_factory():
    """Provide principal factory."""
    return PrincipalFactory


@pytest.fixture
def product_factory():
    """Provide product factory."""
    return ProductFactory


@pytest.fixture
def media_buy_factory():
    """Provide media buy factory."""
    return MediaBuyFactory


@pytest.fixture
def creative_factory():
    """Provide creative factory."""
    return CreativeFactory


@pytest.fixture
def sample_tenant():
    """Provide a sample tenant."""
    return TenantFactory.create(tenant_id="test_tenant", name="Test Publisher", subdomain="test")


@pytest.fixture
def sample_principal(sample_tenant):
    """Provide a sample principal."""
    return PrincipalFactory.create(
        tenant_id=sample_tenant["tenant_id"],
        principal_id="test_principal",
        name="Test Advertiser",
        access_token="test_token_123",
    )


@pytest.fixture
def sample_products(sample_tenant):
    """Provide sample products."""
    return ProductFactory.create_batch(3, tenant_id=sample_tenant["tenant_id"])


# ============================================================================
# Mock Service Fixtures
# ============================================================================


@pytest.fixture
def mock_adapter():
    """Provide a mock ad server adapter."""
    return MockAdapter()


@pytest.fixture
def mock_gemini():
    """Provide a mock Gemini AI service."""
    return MockGeminiService()


@pytest.fixture
def mock_oauth():
    """Provide a mock OAuth provider."""
    return MockOAuthProvider()


@pytest.fixture
def mock_gemini_env(mock_gemini):
    """Mock Gemini environment."""
    with patch("google.generativeai.configure"):
        with patch("google.generativeai.GenerativeModel") as mock_model:
            mock_model.return_value = mock_gemini
            yield mock_gemini


@pytest.fixture
def mock_gemini_client():
    """Mock google.generativeai client for AI-powered tests.

    This fixture patches the Gemini API to return deterministic responses
    without requiring GEMINI_API_KEY. Use this for testing AI orchestration logic.
    """
    with patch("google.generativeai.configure") as mock_configure:
        with patch("google.generativeai.GenerativeModel") as MockModel:
            # Create mock model instance
            mock_model = MagicMock()

            # Default response for test scenarios
            mock_response = MagicMock()
            mock_response.text = json.dumps(
                {"delay_seconds": None, "should_accept": True, "should_reject": False, "creative_actions": []}
            )

            # Sync and async versions
            mock_model.generate_content.return_value = mock_response

            async def async_generate(*args, **kwargs):
                return mock_response

            mock_model.generate_content_async = async_generate

            MockModel.return_value = mock_model

            yield mock_model


@pytest.fixture
def mock_gemini_test_scenarios():
    """Mock Gemini with realistic test scenario responses.

    Returns a fixture that accepts scenario name and returns appropriate JSON response.
    """
    scenarios = {
        "delay_10s": json.dumps({"delay_seconds": 10, "should_accept": True, "should_reject": False}),
        "reject_budget": json.dumps(
            {"should_reject": True, "rejection_reason": "Budget too high for this campaign", "should_accept": False}
        ),
        "hitl_approve": json.dumps({"simulate_hitl": True, "hitl_delay_minutes": 2, "hitl_outcome": "approve"}),
        "creative_approve": json.dumps({"creative_actions": [{"creative_index": 0, "action": "approve"}]}),
        "creative_reject": json.dumps(
            {"creative_actions": [{"creative_index": 0, "action": "reject", "reason": "Missing click URL"}]}
        ),
        "creative_ask_field": json.dumps(
            {"creative_actions": [{"creative_index": 0, "action": "ask_for_field", "field": "click_tracker"}]}
        ),
    }

    with patch("google.generativeai.configure"):
        with patch("google.generativeai.GenerativeModel") as MockModel:
            mock_model = MagicMock()

            def generate_content(prompt, **kwargs):
                # Parse prompt to determine which scenario to return
                response = MagicMock()
                prompt_str = str(prompt)

                # Extract just the message part (between quotes after "Their message:")
                message = ""
                if 'Their message: "' in prompt_str:
                    start = prompt_str.find('Their message: "') + len('Their message: "')
                    end = prompt_str.find('"', start)
                    message = prompt_str[start:end].lower()
                else:
                    message = prompt_str.lower()

                # Priority order matters - check most specific patterns first
                if "wait" in message and "seconds" in message:
                    response.text = scenarios["delay_10s"]
                elif "human in the loop" in message or "hitl" in message or "simulate human" in message:
                    response.text = scenarios["hitl_approve"]
                elif "ask for" in message or ("request" in message and "field" in message):
                    response.text = scenarios["creative_ask_field"]
                elif "reject" in message:
                    # Check if it's creative rejection or budget rejection
                    if "url" in message or "missing" in message:
                        response.text = scenarios["creative_reject"]
                    elif "budget" in message:
                        response.text = scenarios["reject_budget"]
                    else:
                        response.text = scenarios["creative_reject"]
                elif "approve" in message:
                    response.text = scenarios["creative_approve"]
                else:
                    # Default: accept without special instructions
                    response.text = json.dumps({"should_accept": True, "should_reject": False})

                return response

            mock_model.generate_content = generate_content

            async def async_generate(prompt, **kwargs):
                return generate_content(prompt, **kwargs)

            mock_model.generate_content_async = async_generate

            MockModel.return_value = mock_model

            yield mock_model


@pytest.fixture
def mock_gemini_product_recommendations():
    """Mock Gemini for product recommendation testing."""
    with patch("google.generativeai.configure"):
        with patch("google.generativeai.GenerativeModel") as MockModel:
            mock_model = MagicMock()
            mock_response = MagicMock()
            mock_response.text = json.dumps(
                {
                    "product_id": "ai_generated_product",
                    "name": "AI-Optimized Display Package",
                    "formats": ["display_300x250", "display_728x90"],
                    "delivery_type": "guaranteed",
                    "cpm": 12.50,
                    "countries": ["US", "CA"],
                    "targeting_template": {"device_targets": {"device_types": ["desktop", "mobile"]}},
                    "implementation_config": {},
                }
            )

            mock_model.generate_content.return_value = mock_response

            async def async_generate(*args, **kwargs):
                return mock_response

            mock_model.generate_content_async = async_generate

            MockModel.return_value = mock_model

            yield mock_model


@pytest.fixture
def mock_gemini_policy_checker():
    """Mock Gemini for policy checking tests."""
    with patch("google.generativeai.configure"):
        with patch("google.generativeai.GenerativeModel") as MockModel:
            mock_model = MagicMock()

            def generate_policy_response(prompt, **kwargs):
                response = MagicMock()

                # Parse prompt to determine policy status
                prompt_lower = prompt.lower() if isinstance(prompt, str) else ""

                if any(word in prompt_lower for word in ["alcohol", "tobacco", "gambling", "predatory"]):
                    response.text = json.dumps(
                        {
                            "status": "restricted",
                            "reason": "Contains age-restricted content",
                            "restrictions": ["Requires age verification"],
                            "warnings": [],
                        }
                    )
                elif any(word in prompt_lower for word in ["prohibited", "illegal", "dangerous"]):
                    response.text = json.dumps(
                        {
                            "status": "blocked",
                            "reason": "Contains prohibited content",
                            "restrictions": [],
                            "warnings": [],
                        }
                    )
                else:
                    response.text = json.dumps(
                        {"status": "allowed", "reason": None, "restrictions": [], "warnings": []}
                    )

                return response

            async def async_policy_response(prompt, **kwargs):
                return generate_policy_response(prompt, **kwargs)

            mock_model.generate_content_async = async_policy_response

            MockModel.return_value = mock_model

            yield mock_model


# ============================================================================
# Builder Fixtures
# ============================================================================


@pytest.fixture
def request_builder():
    """Provide request builder."""
    return RequestBuilder()


@pytest.fixture
def response_builder():
    """Provide response builder."""
    return ResponseBuilder()


@pytest.fixture
def targeting_builder():
    """Provide targeting builder."""
    return TargetingBuilder()


# ============================================================================
# Authentication Fixtures
# ============================================================================


@pytest.fixture
def auth_headers(sample_principal):
    """Provide authentication headers."""
    return {"x-adcp-auth": sample_principal["access_token"]}


@pytest.fixture
def admin_session():
    """Provide admin session data."""
    return {
        "user": {"email": "admin@example.com", "name": "Admin User", "role": "super_admin"},
        "authenticated": True,
        "role": "super_admin",
        "email": "admin@example.com",
        "name": "Admin User",
    }


@pytest.fixture
def tenant_admin_session(sample_tenant):
    """Provide tenant admin session data."""
    return {
        "user": {"email": "tenant.admin@example.com", "name": "Tenant Admin", "role": "tenant_admin"},
        "authenticated": True,
        "role": "tenant_admin",
        "tenant_id": sample_tenant["tenant_id"],
        "email": "tenant.admin@example.com",
        "name": "Tenant Admin",
    }


# ============================================================================
# Flask App Fixtures
# ============================================================================


@pytest.fixture
def flask_app():
    """Provide Flask test app."""
    # Mock database before importing admin app
    with patch("src.core.database.database_session.get_db_session") as mock_get_session:
        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = None
        mock_session.query.return_value.filter_by.return_value.all.return_value = []
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_session.query.return_value.filter.return_value.all.return_value = []
        mock_session.query.return_value.all.return_value = []
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=None)
        mock_get_session.return_value = mock_session

        # Mock inventory service database session
        with patch("src.services.gam_inventory_service.db_session") as mock_inv_session:
            mock_inv_session.query.return_value.filter.return_value.all.return_value = []
            mock_inv_session.close = MagicMock()
            mock_inv_session.remove = MagicMock()

            from src.admin.app import create_app

            app, _ = create_app()
            app.config["TESTING"] = True
            app.config["SECRET_KEY"] = "test-secret-key"
            return app


@pytest.fixture
def flask_client(flask_app):
    """Provide Flask test client."""
    return flask_app.test_client()


@pytest.fixture
def authenticated_client(flask_client, admin_session):
    """Provide authenticated Flask client."""
    with flask_client.session_transaction() as sess:
        sess.update(admin_session)
    return flask_client


# ============================================================================
# MCP Context Fixtures
# ============================================================================


@pytest.fixture
def mcp_context(auth_headers):
    """Provide MCP context object."""

    class MockContext:
        def __init__(self, headers):
            self.headers = headers

        def get_header(self, name, default=None):
            return self.headers.get(name, default)

    return MockContext(auth_headers)


# ============================================================================
# Test Data Fixtures
# ============================================================================


@pytest.fixture
def sample_media_buy_request():
    """Provide sample media buy request."""
    return {
        "product_ids": ["prod_1", "prod_2"],
        "total_budget": 10000.0,
        "flight_start_date": "2025-02-01",
        "flight_end_date": "2025-02-28",
        "targeting_overlay": {"geo_country_any_of": ["US"], "device_type_any_of": ["desktop", "mobile"]},
    }


@pytest.fixture
def sample_creative_content():
    """Provide sample creative content."""
    return {
        "headline": "Test Advertisement",
        "body": "This is a test ad for automated testing.",
        "cta_text": "Learn More",
        "image_url": "https://example.com/test-ad.jpg",
        "click_url": "https://example.com/landing",
        "advertiser": "Test Company",
    }


@pytest.fixture
def fixture_data_path():
    """Provide path to fixture data directory."""
    return Path(__file__).parent / "fixtures" / "data"


@pytest.fixture
def load_fixture_json():
    """Provide function to load fixture JSON files."""

    def _load(filename):
        fixture_path = Path(__file__).parent / "fixtures" / "data" / filename
        with open(fixture_path) as f:
            return json.load(f)

    return _load


# ============================================================================
# Cleanup Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def cleanup_env():
    """Clean up environment after each test."""
    # Store original env
    original_env = os.environ.copy()

    yield

    # Restore original env (but keep testing flags)
    test_vars = [
        "ADCP_TESTING",
        "DATABASE_URL",
        "GEMINI_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "SUPER_ADMIN_EMAILS",
    ]

    for key in list(os.environ.keys()):
        if key not in original_env and key not in test_vars:
            del os.environ[key]


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singleton instances between tests."""
    # Reset any singleton instances that might carry state
    yield

    # Add any singleton reset logic here


# ============================================================================
# Performance Fixtures
# ============================================================================


@pytest.fixture
def benchmark(request):
    """Simple benchmark fixture for performance testing."""
    import time

    start_time = time.time()

    yield

    duration = time.time() - start_time
    print(f"\n{request.node.name} took {duration:.3f}s")

    # Mark as slow if > 5 seconds
    if duration > 5:
        request.node.add_marker(pytest.mark.slow)


# ============================================================================
# Pytest Hooks
# ============================================================================


def pytest_collection_modifyitems(config, items):
    """Modify test collection to skip certain tests in CI."""
    # Note: requires_server tests are now supported via proper mcp_server fixture
    # No longer skipping these tests in CI
    pass
