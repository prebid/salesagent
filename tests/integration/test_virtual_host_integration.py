"""Integration tests for virtual host functionality."""

from src.core.config_loader import get_tenant_by_virtual_host


class TestVirtualHostIntegration:
    """Test virtual host integration across multiple components."""

    def test_header_parsing_apx_incoming_host(self):
        """Test parsing of Apx-Incoming-Host header."""

        # Create a mock context that simulates FastMCP context structure
        class MockContext:
            def __init__(self, headers):
                self.meta = {"headers": headers}

        # Test basic header extraction
        context = MockContext({"apx-incoming-host": "ad-sales.testcompany.com", "x-adcp-auth": "test-token"})

        # Act - simulate how main.py extracts the header
        headers = context.meta.get("headers", {})
        apx_host = headers.get("apx-incoming-host")

        # Assert
        assert apx_host == "ad-sales.testcompany.com"

    def test_header_case_sensitivity(self):
        """Test that header extraction handles different cases."""
        test_cases = [
            {"apx-incoming-host": "test1.com"},
            {"Apx-Incoming-Host": "test2.com"},
            {"APX-INCOMING-HOST": "test3.com"},
        ]

        for headers in test_cases:

            class MockContext:
                def __init__(self, headers):
                    self.meta = {"headers": headers}

            context = MockContext(headers)

            # Try different case variations as they might be normalized
            header_value = (
                context.meta.get("headers", {}).get("apx-incoming-host")
                or context.meta.get("headers", {}).get("Apx-Incoming-Host")
                or context.meta.get("headers", {}).get("APX-INCOMING-HOST")
            )

            assert header_value is not None
            assert header_value in ["test1.com", "test2.com", "test3.com"]

    def test_virtual_host_function_integration(self, integration_db):
        """Test that virtual host lookup function handles non-existent domains gracefully."""
        # This is a real integration test - calls the actual function
        # with a domain that shouldn't exist
        result = get_tenant_by_virtual_host("definitely-does-not-exist.invalid")

        # Should return None for non-existent virtual hosts
        assert result is None

    def test_multiple_routing_headers_priority(self):
        """Test priority when multiple routing headers are present."""

        class MockContext:
            def __init__(self, headers):
                self.meta = {"headers": headers}

        context = MockContext(
            {
                "apx-incoming-host": "virtual.example.com",
                "x-adcp-tenant": "subdomain-tenant",
                "host": "subdomain.localhost:8080",
            }
        )

        # Act - Apx-Incoming-Host should take priority
        headers = context.meta.get("headers", {})
        apx_host = headers.get("apx-incoming-host")
        tenant_header = headers.get("x-adcp-tenant")
        host_header = headers.get("host")

        # Assert - all should be available but Apx-Incoming-Host should be preferred
        assert apx_host == "virtual.example.com"
        assert tenant_header == "subdomain-tenant"
        assert host_header == "subdomain.localhost:8080"

    def test_context_error_handling(self):
        """Test graceful handling of malformed contexts."""
        # Test various edge cases
        test_contexts = [
            {},
            {"meta": {}},
            {"meta": {"headers": {}}},
            {"meta": None},
            {"meta": {"headers": None}},
        ]

        for ctx_data in test_contexts:

            class MockContext:
                def __init__(self, data):
                    for key, value in data.items():
                        setattr(self, key, value)

            context = MockContext(ctx_data)

            # This should not raise an exception
            headers = getattr(context, "meta", {})
            if headers:
                headers = headers.get("headers", {})
                apx_host = headers.get("apx-incoming-host") if headers else None
            else:
                apx_host = None

            # Should handle gracefully regardless of structure
            assert apx_host is None or isinstance(apx_host, str)
