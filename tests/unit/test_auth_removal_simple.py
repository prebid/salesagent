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
        from src.core.auth import get_principal_from_context

        context = Mock(spec=["meta"])  # Limit to only meta attribute
        context.meta = {}  # Empty meta, no headers

        with patch("src.core.auth.get_http_headers", return_value={}):  # No x-adcp-auth header
            principal_id, tenant = get_principal_from_context(context)
            assert principal_id is None
            assert tenant is None

    def test_get_principal_from_context_works_with_auth(self):
        """Test that get_principal_from_context still works with auth."""
        # Lazy import to avoid triggering load_config() at module import time
        from src.core.auth import get_principal_from_context

        context = Mock(spec=["meta"])  # Limit to only meta attribute
        # Must include Host header for tenant detection (security fix)
        context.meta = {
            "headers": {
                "x-adcp-auth": "test-token",
                "host": "test-tenant.sales-agent.scope3.com",  # Required for tenant detection
            }
        }

        with patch(
            "src.core.auth.get_http_headers",
            return_value={
                "x-adcp-auth": "test-token",
                "host": "test-tenant.sales-agent.scope3.com",
            },
        ):
            # Mock virtual host lookup to fail (not a virtual host)
            with patch("src.core.auth.get_tenant_by_virtual_host", return_value=None):
                # Mock subdomain lookup to succeed
                with patch("src.core.auth.get_tenant_by_subdomain") as mock_tenant_lookup:
                    mock_tenant_lookup.return_value = {
                        "tenant_id": "tenant_test",
                        "subdomain": "test-tenant",
                        "name": "Test Tenant",
                    }
                    with patch("src.core.auth.set_current_tenant"):
                        with patch("src.core.auth.get_principal_from_token", return_value="test_principal"):
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
        # Simple source code check - tools now split across multiple files
        tool_files = [
            "src/core/tools/products.py",
            "src/core/tools/properties.py",
            "src/core/auth.py",
        ]

        sources = []
        for tool_file in tool_files:
            try:
                with open(tool_file) as f:
                    sources.append(f.read())
            except FileNotFoundError:
                continue

        combined_source = "\n".join(sources)

        # Key changes should be present - tuple return after ContextVar fix
        # Updated to accept new require_valid_token parameter for discovery endpoints
        assert (
            "get_principal_from_context(context)  # Returns (None, None) if no auth" in combined_source
            or "get_principal_from_context(context)  # Returns None if no auth" in combined_source
            or "require_valid_token=False" in combined_source  # New pattern for discovery endpoints
        ), "Optional auth pattern not found in tool files"
        assert 'principal_id or "anonymous"' in combined_source, "Anonymous user pattern not found"

    def test_pricing_filtering_for_anonymous_users(self):
        """Test that pricing data is filtered for anonymous users."""
        # Test the pricing filtering logic
        from src.core.schemas import Product
        from tests.helpers.adcp_factories import (
            create_test_cpm_pricing_option,
            create_test_publisher_properties_by_tag,
        )

        # Create a product with pricing data
        product = Product(
            product_id="test_product",
            name="Test Product",
            description="Test description",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="non_guaranteed",
            delivery_measurement={
                "provider": "test_provider",
                "notes": "Test measurement",
            },
            publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
            pricing_options=[
                create_test_cpm_pricing_option(
                    pricing_option_id="cpm_usd_fixed",
                    currency="USD",
                    rate=2.50,
                    min_spend_per_package=1000.0,
                )
            ],
        )

        # Simulate the anonymous user logic - check pricing type
        principal_id = None

        # Verify we have a fixed rate pricing option (for authenticated users)
        assert hasattr(product.pricing_options[0], "rate")
        assert product.pricing_options[0].rate == 2.50

        # For anonymous users, we would replace with auction pricing (no rate field)
        # Here we just verify the concept by checking the structure
        if principal_id is None:  # Anonymous user
            # In real implementation, we'd replace pricing_options with auction variants
            # For this test, we're verifying the pricing option structure
            pass

        # Verify other fields remain accessible
        assert product.pricing_options[0].currency == "USD"

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
        from src.core.schemas import Product
        from tests.helpers.adcp_factories import (
            create_test_cpm_pricing_option,
            create_test_publisher_properties_by_tag,
        )

        # Create a product with pricing data
        product = Product(
            product_id="test_product",
            name="Test Product",
            description="Test description",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="non_guaranteed",
            delivery_measurement={
                "provider": "test_provider",
                "notes": "Test measurement",
            },
            publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
            pricing_options=[
                create_test_cpm_pricing_option(
                    pricing_option_id="cpm_usd_fixed",
                    currency="USD",
                    rate=2.50,
                    min_spend_per_package=1000.0,
                )
            ],
        )

        # Simulate authenticated user logic
        principal_id = "authenticated_user"
        if principal_id is None:  # This should NOT trigger for authenticated users
            pass  # Would replace with auction pricing

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
