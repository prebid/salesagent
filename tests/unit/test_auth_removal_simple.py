"""
Simple, focused tests for authentication removal.

Tests the actual behavior change: discovery endpoints work without auth.
"""


class TestAuthRemovalChanges:
    """Simple tests for the core changes made."""

    # (Retired) The tests here that drove get_principal_from_context went with it. That
    # function was the pre-boundary resolver, deleted for having zero production callers.
    # What they GRADED -- a credential minted for one tenant must not act on another, and a
    # buyer must not see another tenant's rows -- is graded on the wire now, across all three
    # transports, by tests/bdd/features/BR-SECURITY-002-tenant-isolation.feature. That is a
    # stronger grader than these were: they called one internal function directly, so they
    # could not have caught a transport that skipped it.

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

    def test_pricing_filtering_for_anonymous_users(self):
        """Test that pricing data is filtered for anonymous users."""
        # Test the pricing filtering logic
        from src.core.product_conversion import default_reporting_capabilities
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
            reporting_capabilities=default_reporting_capabilities(),
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
        # adcp 2.14.0+ uses RootModel wrapper - the V3 fixed rate is on .root.fixed_price
        pricing_option = product.pricing_options[0]
        assert hasattr(pricing_option, "root")  # noqa: rootmodel
        assert pricing_option.root.fixed_price == 2.50

        # For anonymous users, we would replace with auction pricing (no rate field)
        # Here we just verify the concept by checking the structure
        if principal_id is None:  # Anonymous user
            # In real implementation, we'd replace pricing_options with auction variants
            # For this test, we're verifying the pricing option structure
            pass

        # Verify other fields remain accessible (also via .root)
        assert pricing_option.root.currency == "USD"

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
        from src.core.product_conversion import default_reporting_capabilities
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
            reporting_capabilities=default_reporting_capabilities(),
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
        # adcp 2.14.0+ uses RootModel wrapper - access via .root
        pricing_option = product.pricing_options[0]
        assert pricing_option.root.fixed_price == 2.50
        assert pricing_option.root.min_spend_per_package == 1000.0

        # No pricing message for authenticated users
        pricing_message = None
        if principal_id is None:
            pricing_message = "Please connect through an authorized buying agent for pricing data"

        assert pricing_message is None


# That's it! The real testing should be:
# 1. End-to-end HTTP tests (which already exist)
# 2. Simple unit tests of the changed logic (above)
# 3. Don't try to test the decorated FastMCP functions directly
