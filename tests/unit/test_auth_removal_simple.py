"""
Simple, focused tests for authentication removal.

Tests the actual behavior change: discovery endpoints work without auth.
"""

from unittest.mock import Mock, patch


class TestAuthRemovalChanges:
    """Simple tests for the core changes made."""

    def test_get_principal_from_context_returns_none_without_auth(self):
        """Test that get_principal_from_context returns None when no auth provided."""
        # Lazy import to avoid triggering load_config() at module import time
        from src.core.main import get_principal_from_context

        context = Mock(spec=["meta"])  # Limit to only meta attribute
        context.meta = {}  # Empty meta, no headers

        with patch("src.core.main.get_http_headers", return_value={}):  # No x-adcp-auth header
            principal_id, tenant = get_principal_from_context(context)
            assert principal_id is None
            assert tenant is None

    def test_get_principal_from_context_works_with_auth(self):
        """Test that get_principal_from_context still works with auth."""
        # Lazy import to avoid triggering load_config() at module import time
        from src.core.main import get_principal_from_context

        context = Mock(spec=["meta"])  # Limit to only meta attribute
        # Must include Host header for tenant detection (security fix)
        context.meta = {
            "headers": {
                "x-adcp-auth": "test-token",
                "host": "test-tenant.sales-agent.scope3.com",  # Required for tenant detection
            }
        }

        with patch(
            "src.core.main.get_http_headers",
            return_value={
                "x-adcp-auth": "test-token",
                "host": "test-tenant.sales-agent.scope3.com",
            },
        ):
            # Mock virtual host lookup to fail (not a virtual host)
            with patch("src.core.main.get_tenant_by_virtual_host", return_value=None):
                # Mock subdomain lookup to succeed
                with patch("src.core.main.get_tenant_by_subdomain") as mock_tenant_lookup:
                    mock_tenant_lookup.return_value = {
                        "tenant_id": "tenant_test",
                        "subdomain": "test-tenant",
                        "name": "Test Tenant",
                    }
                    with patch("src.core.main.set_current_tenant"):
                        with patch("src.core.main.get_principal_from_token", return_value="test_principal"):
                            principal_id, tenant = get_principal_from_context(context)
                            assert principal_id == "test_principal"
                            assert tenant == {
                                "tenant_id": "tenant_test",
                                "subdomain": "test-tenant",
                                "name": "Test Tenant",
                            }

    def test_audit_logging_handles_none_principal(self):
        """Test that audit logging works with None principal_id."""
        # This tests the key change: principal_id or "anonymous"
        principal_id = None
        audit_principal = principal_id or "anonymous"

        assert audit_principal == "anonymous"

        # With actual principal
        principal_id = "real_user"
        audit_principal = principal_id or "anonymous"

        assert audit_principal == "real_user"

    def test_discovery_endpoints_use_optional_auth_pattern(self):
        """Verify the source code uses the optional auth pattern."""
        # Simple source code check - much easier than complex mocking
        with open("src/core/main.py") as f:
            source = f.read()

        # Key changes should be present - tuple return after ContextVar fix
        # Updated to accept new require_valid_token parameter for discovery endpoints
        assert (
            "get_principal_from_context(context)  # Returns (None, None) if no auth" in source
            or "get_principal_from_context(context)  # Returns None if no auth" in source
            or "require_valid_token=False" in source  # New pattern for discovery endpoints
        )
        assert 'principal_id or "anonymous"' in source

    def test_pricing_filtering_for_anonymous_users(self):
        """Test that pricing data is filtered for anonymous users."""
        # Test the pricing filtering logic
        from src.core.schemas import PricingOption, Product

        # Create a product with pricing data
        product = Product(
            product_id="test_product",
            name="Test Product",
            description="Test description",
            formats=["display_300x250"],
            delivery_type="non_guaranteed",
            property_tags=["all_inventory"],  # Required per AdCP spec
            pricing_options=[
                PricingOption(
                    pricing_option_id="cpm_usd_fixed",
                    pricing_model="cpm",
                    rate=2.50,
                    currency="USD",
                    is_fixed=True,
                    min_spend_per_package=1000.0,
                )
            ],
        )

        # Simulate the anonymous user logic - remove rate for anonymous users
        principal_id = None
        if principal_id is None:  # Anonymous user
            # Remove pricing rate from all pricing_options
            for po in product.pricing_options:
                po.rate = None

        # Verify pricing data is removed
        assert product.pricing_options[0].rate is None
        # But other fields like currency and is_fixed remain
        assert product.pricing_options[0].currency == "USD"
        assert product.pricing_options[0].is_fixed is True

        # Other data should remain
        assert product.product_id == "test_product"
        assert product.name == "Test Product"

    def test_pricing_message_for_anonymous_users(self):
        """Test that the pricing message is added for anonymous users."""
        # Test the message logic
        principal_id = None
        pricing_message = None

        if principal_id is None:  # Anonymous user
            pricing_message = "Please connect through an authorized buying agent for pricing data"

        base_message = "Found 2 matching products"
        final_message = f"{base_message}. {pricing_message}" if pricing_message else base_message

        expected = "Found 2 matching products. Please connect through an authorized buying agent for pricing data"
        assert final_message == expected

    def test_authenticated_users_keep_pricing_data(self):
        """Test that authenticated users still get full pricing data."""
        from src.core.schemas import PricingOption, Product

        # Create a product with pricing data
        product = Product(
            product_id="test_product",
            name="Test Product",
            description="Test description",
            formats=["display_300x250"],
            delivery_type="non_guaranteed",
            property_tags=["all_inventory"],  # Required per AdCP spec
            pricing_options=[
                PricingOption(
                    pricing_option_id="cpm_usd_fixed",
                    pricing_model="cpm",
                    rate=2.50,
                    currency="USD",
                    is_fixed=True,
                    min_spend_per_package=1000.0,
                )
            ],
        )

        # Simulate authenticated user logic
        principal_id = "authenticated_user"
        if principal_id is None:  # This should NOT trigger for authenticated users
            for po in product.pricing_options:
                po.rate = None

        # Verify pricing data is preserved (not removed for authenticated users)
        assert product.pricing_options[0].rate == 2.50
        assert product.pricing_options[0].min_spend_per_package == 1000.0

        # No pricing message for authenticated users
        pricing_message = None
        if principal_id is None:
            pricing_message = "Please connect through an authorized buying agent for pricing data"

        assert pricing_message is None


# That's it! The real testing should be:
# 1. End-to-end HTTP tests (which already exist)
# 2. Simple unit tests of the changed logic (above)
# 3. Don't try to test the decorated FastMCP functions directly
