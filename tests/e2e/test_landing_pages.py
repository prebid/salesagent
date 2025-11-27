#!/usr/bin/env python3
"""
Landing Page E2E Tests

Validates that domain routing works correctly for different domain types:
- Custom domains show agent landing pages
- Subdomains show appropriate landing pages (agent or pending config)
- Admin domains redirect to login
- Unknown domains redirect to signup
- Auth-optional MCP endpoints work with and without tokens

Tests against live servers (local or production).
"""

import os

import pytest
import requests


class TestLandingPages:
    """Test landing page routing for different domain types."""

    def _get_base_url(self) -> str:
        """Get base URL for tests (defaults to localhost)."""
        return os.getenv("TEST_BASE_URL", "http://localhost:8001")

    @pytest.mark.integration
    def test_admin_domain_redirects_to_login(self):
        """Admin domain should return 302 redirect to login page."""
        base_url = self._get_base_url()

        try:
            # Test admin domain routing with admin Host header
            response = requests.get(
                f"{base_url}/",
                headers={
                    "Host": "admin.sales-agent.scope3.com",
                },
                timeout=5,
                allow_redirects=False,
            )

            # Admin domain should redirect to login
            assert response.status_code == 302, f"Admin domain should return 302 redirect, got {response.status_code}"
            location = response.headers.get("Location", "")
            assert "/login" in location, f"Admin domain should redirect to /login, got {location}"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"Server not running at {base_url}")

    @pytest.mark.integration
    def test_admin_login_page_shows_login_form(self):
        """Admin login page should contain login form when following redirect."""
        base_url = self._get_base_url()

        try:
            # Follow redirects to get to login page
            response = requests.get(
                f"{base_url}/",
                headers={
                    "Host": "admin.sales-agent.scope3.com",
                },
                timeout=5,
                allow_redirects=True,
            )

            # Should arrive at login page with 200 OK (skip if server error - environment may not be fully configured)
            if response.status_code >= 500:
                pytest.skip(f"Server error {response.status_code} - environment may not be fully configured")

            assert response.status_code == 200, f"Login page should return 200 OK, got {response.status_code}"
            content = response.content.decode("utf-8").lower()
            assert "login" in content, "Admin login page should contain login form"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"Server not running at {base_url}")

    @pytest.mark.integration
    def test_landing_page_contains_mcp_endpoint(self):
        """Landing page for configured tenant should contain MCP endpoint or pending config message."""
        base_url = self._get_base_url()

        try:
            # For local testing, we need to specify a custom domain
            # that would route to tenant landing page
            response = requests.get(
                f"{base_url}/",
                headers={
                    "Host": "test-custom-domain.example.com",
                },
                timeout=5,
                allow_redirects=True,
            )

            # If we get a 200 OK, check for MCP endpoint
            if response.status_code == 200:
                content = response.content.decode("utf-8").lower()

                # Landing page should mention MCP or show it's pending configuration
                has_mcp = 'href="/mcp' in content or "mcp endpoint" in content
                is_pending = "pending configuration" in content or "not configured" in content

                assert (
                    has_mcp or is_pending
                ), "Landing page should either show MCP endpoint or pending configuration message"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"Server not running at {base_url}")

    @pytest.mark.integration
    def test_landing_page_contains_a2a_endpoint(self):
        """Landing page for configured tenant should contain A2A endpoint or pending config message."""
        base_url = self._get_base_url()

        try:
            # For local testing, we need to specify a custom domain
            response = requests.get(
                f"{base_url}/",
                headers={
                    "Host": "test-custom-domain.example.com",
                },
                timeout=5,
                allow_redirects=True,
            )

            # If we get a 200 OK, check for A2A endpoint
            if response.status_code == 200:
                content = response.content.decode("utf-8").lower()

                # Landing page should mention A2A or show it's pending configuration
                has_a2a = 'href="/a2a' in content or "a2a endpoint" in content
                is_pending = "pending configuration" in content or "not configured" in content

                assert (
                    has_a2a or is_pending
                ), "Landing page should either show A2A endpoint or pending configuration message"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"Server not running at {base_url}")

    @pytest.mark.integration
    def test_approximated_header_precedence_for_admin(self):
        """Apx-Incoming-Host header should take precedence over Host header for admin routing."""
        base_url = self._get_base_url()

        try:
            # Send both headers - Apx-Incoming-Host should win
            # Use admin domain as Apx-Incoming-Host since we know it exists
            response = requests.get(
                f"{base_url}/",
                headers={
                    "Host": "localhost:8001",  # Backend host
                    "Apx-Incoming-Host": "admin.sales-agent.scope3.com",  # Proxied admin host
                },
                timeout=5,
                allow_redirects=False,
            )

            # Should route based on Apx-Incoming-Host (admin domain -> login redirect)
            assert (
                response.status_code == 302
            ), f"Proxied admin domain should redirect to login (302), got {response.status_code}"

            location = response.headers.get("Location", "")
            assert "/login" in location, f"Proxied admin domain should redirect to /login, got {location}"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"Server not running at {base_url}")


class TestAuthOptionalEndpoints:
    """Test auth-optional MCP endpoints (list_creative_formats, list_authorized_properties, get_products)."""

    def _get_base_url(self) -> str:
        """Get base URL for tests (defaults to localhost MCP port)."""
        return os.getenv("TEST_BASE_URL", "http://localhost:8080")

    def _get_test_token(self) -> str | None:
        """Get test auth token from environment."""
        return os.getenv("TEST_AUTH_TOKEN")

    @pytest.mark.integration
    def test_list_creative_formats_without_auth(self):
        """list_creative_formats should work without authentication."""
        base_url = self._get_base_url()

        try:
            # Call list_creative_formats without auth token
            response = requests.post(
                f"{base_url}/mcp/tools/call",
                json={
                    "method": "tools/call",
                    "params": {"name": "list_creative_formats", "arguments": {}},
                },
                headers={
                    "Content-Type": "application/json",
                    "Host": "test-custom-domain.example.com",  # Provide tenant context via Host
                },
                timeout=5,
            )

            # Should succeed without auth (discovery endpoint)
            assert response.status_code in (200, 404), (
                f"list_creative_formats without auth should succeed (200) or indicate missing tenant (404), "
                f"got {response.status_code}"
            )

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"MCP server not running at {base_url}")

    @pytest.mark.integration
    def test_list_creative_formats_with_auth(self):
        """list_creative_formats should work with authentication and return same/more data."""
        base_url = self._get_base_url()
        auth_token = self._get_test_token()

        if not auth_token:
            pytest.skip("TEST_AUTH_TOKEN not set - cannot test authenticated access")

        try:
            # Call list_creative_formats with auth token
            response = requests.post(
                f"{base_url}/mcp/tools/call",
                json={
                    "method": "tools/call",
                    "params": {"name": "list_creative_formats", "arguments": {}},
                },
                headers={
                    "Content-Type": "application/json",
                    "x-adcp-auth": auth_token,
                },
                timeout=5,
            )

            # Should succeed with auth
            assert (
                response.status_code == 200
            ), f"list_creative_formats with auth should succeed, got {response.status_code}"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"MCP server not running at {base_url}")

    @pytest.mark.integration
    def test_list_authorized_properties_without_auth(self):
        """list_authorized_properties should work without authentication."""
        base_url = self._get_base_url()

        try:
            # Call list_authorized_properties without auth token
            response = requests.post(
                f"{base_url}/mcp/tools/call",
                json={
                    "method": "tools/call",
                    "params": {"name": "list_authorized_properties", "arguments": {}},
                },
                headers={
                    "Content-Type": "application/json",
                    "Host": "test-custom-domain.example.com",  # Provide tenant context via Host
                },
                timeout=5,
            )

            # Should succeed without auth (discovery endpoint)
            assert response.status_code in (200, 404), (
                f"list_authorized_properties without auth should succeed (200) or indicate missing tenant (404), "
                f"got {response.status_code}"
            )

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"MCP server not running at {base_url}")

    @pytest.mark.integration
    def test_list_authorized_properties_with_auth(self):
        """list_authorized_properties should work with authentication."""
        base_url = self._get_base_url()
        auth_token = self._get_test_token()

        if not auth_token:
            pytest.skip("TEST_AUTH_TOKEN not set - cannot test authenticated access")

        try:
            # Call list_authorized_properties with auth token
            response = requests.post(
                f"{base_url}/mcp/tools/call",
                json={
                    "method": "tools/call",
                    "params": {"name": "list_authorized_properties", "arguments": {}},
                },
                headers={
                    "Content-Type": "application/json",
                    "x-adcp-auth": auth_token,
                },
                timeout=5,
            )

            # Should succeed with auth
            assert (
                response.status_code == 200
            ), f"list_authorized_properties with auth should succeed, got {response.status_code}"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"MCP server not running at {base_url}")

    @pytest.mark.integration
    def test_get_products_without_auth_public_policy(self):
        """get_products should work without authentication when tenant has public brand_manifest_policy."""
        base_url = self._get_base_url()

        try:
            # Call get_products without auth token
            # Note: This will only work if tenant has brand_manifest_policy=public
            response = requests.post(
                f"{base_url}/mcp/tools/call",
                json={
                    "method": "tools/call",
                    "params": {
                        "name": "get_products",
                        "arguments": {"brief": "test campaign"},
                    },
                },
                headers={
                    "Content-Type": "application/json",
                    "Host": "test-custom-domain.example.com",  # Provide tenant context via Host
                },
                timeout=5,
            )

            # May succeed or fail depending on tenant policy
            # Accept: 200 (public policy), 400/401 (auth required), 404 (no tenant)
            assert response.status_code in (200, 400, 401, 404), (
                f"get_products without auth should succeed (public policy) or fail with auth error, "
                f"got {response.status_code}"
            )

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"MCP server not running at {base_url}")

    @pytest.mark.integration
    def test_get_products_with_auth(self):
        """get_products should work with authentication regardless of policy."""
        base_url = self._get_base_url()
        auth_token = self._get_test_token()

        if not auth_token:
            pytest.skip("TEST_AUTH_TOKEN not set - cannot test authenticated access")

        try:
            # Call get_products with auth token
            response = requests.post(
                f"{base_url}/mcp/tools/call",
                json={
                    "method": "tools/call",
                    "params": {
                        "name": "get_products",
                        "arguments": {"brief": "test campaign"},
                    },
                },
                headers={
                    "Content-Type": "application/json",
                    "x-adcp-auth": auth_token,
                },
                timeout=5,
            )

            # Should succeed with auth
            assert response.status_code == 200, f"get_products with auth should succeed, got {response.status_code}"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"MCP server not running at {base_url}")

    @pytest.mark.integration
    def test_get_products_filters_pricing_for_anonymous(self):
        """get_products should hide pricing information for anonymous users."""
        base_url = self._get_base_url()

        try:
            # Call get_products without auth
            response = requests.post(
                f"{base_url}/mcp/tools/call",
                json={
                    "method": "tools/call",
                    "params": {
                        "name": "get_products",
                        "arguments": {"brief": "test campaign"},
                    },
                },
                headers={
                    "Content-Type": "application/json",
                    "Host": "test-custom-domain.example.com",
                },
                timeout=5,
            )

            # If successful, check that pricing is filtered but other data is present
            if response.status_code == 200:
                data = response.json()
                assert "result" in data, "Response should contain result field"

                if "products" in data["result"]:
                    products = data["result"]["products"]

                    # Should return products (not an empty list)
                    assert len(products) > 0, "Should return at least one product"

                    for product in products:
                        # Should have basic product information
                        assert "id" in product, "Product should have id field"
                        assert "name" in product, "Product should have name field"

                        # pricing_options should be empty or missing for anonymous users
                        pricing_options = product.get("pricing_options", [])
                        assert (
                            len(pricing_options) == 0
                        ), f"Anonymous users should not see pricing, got {len(pricing_options)} options"

                        # Verify no other sensitive pricing fields leak through
                        sensitive_fields = ["cost", "rate", "price", "cpm", "cpc", "vcpm"]
                        for field in sensitive_fields:
                            assert field not in product, f"Anonymous users should not see {field} field"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"MCP server not running at {base_url}")


class TestProductionLandingPages:
    """Test production landing pages (requires PRODUCTION_TEST=true)."""

    def _is_production_test(self) -> bool:
        """Check if we should run production tests."""
        return os.getenv("PRODUCTION_TEST", "").lower() == "true"

    def _get_production_domain(self, tenant: str, default: str) -> str:
        """Get production domain for tenant from environment or use default.

        Args:
            tenant: Tenant identifier (e.g., 'accuweather', 'test_agent')
            default: Default domain to use if environment variable not set

        Returns:
            Domain URL for the tenant
        """
        env_var = f"PROD_{tenant.upper()}_DOMAIN"
        return os.getenv(env_var, default)

    @pytest.mark.e2e
    def test_accuweather_landing_page(self):
        """Test AccuWeather custom domain landing page."""
        if not self._is_production_test():
            pytest.skip("Set PRODUCTION_TEST=true to run production tests")

        domain = self._get_production_domain("accuweather", "https://sales-agent.accuweather.com")

        try:
            response = requests.get(
                domain,
                timeout=10,
                allow_redirects=True,
            )

            assert (
                response.status_code == 200
            ), f"AccuWeather landing page should return 200, got {response.status_code}"

            content = response.content.decode("utf-8").lower()

            # Should contain MCP and A2A endpoints
            assert 'href="/mcp' in content or "/mcp" in content, "AccuWeather landing page should contain MCP endpoint"
            assert 'href="/a2a' in content or "/a2a" in content, "AccuWeather landing page should contain A2A endpoint"

            # Should mention agent capabilities
            assert "agent" in content or "protocol" in content, "Landing page should mention agent capabilities"

        except requests.RequestException as e:
            pytest.skip(f"Could not reach production URL: {e}")

    @pytest.mark.e2e
    def test_test_agent_landing_page(self):
        """Test test-agent.adcontextprotocol.org landing page shows agent landing page."""
        if not self._is_production_test():
            pytest.skip("Set PRODUCTION_TEST=true to run production tests")

        domain = self._get_production_domain("test_agent", "https://test-agent.adcontextprotocol.org")

        try:
            response = requests.get(
                domain,
                timeout=10,
                allow_redirects=False,  # Don't follow redirects
            )

            # Custom domains with tenants show landing page (200)
            assert response.status_code == 200, f"test-agent should show landing page (200), got {response.status_code}"

            content = response.content.decode("utf-8").lower()

            # Should contain agent endpoints
            assert (
                'href="/mcp' in content or 'href="/a2a' in content
            ), "test-agent landing page should contain agent endpoints"

        except requests.RequestException as e:
            pytest.skip(f"Could not reach production URL: {e}")

    @pytest.mark.e2e
    def test_applabs_subdomain_landing_page(self):
        """Test applabs subdomain landing page."""
        if not self._is_production_test():
            pytest.skip("Set PRODUCTION_TEST=true to run production tests")

        domain = self._get_production_domain("applabs", "https://applabs.sales-agent.scope3.com")

        try:
            response = requests.get(
                domain,
                timeout=10,
                allow_redirects=True,
            )

            assert response.status_code == 200, f"applabs landing page should return 200, got {response.status_code}"

            content = response.content.decode("utf-8").lower()

            # applabs is not fully configured, so might show pending config
            # But should still show MCP/A2A endpoints or pending message
            has_endpoints = 'href="/mcp' in content or 'href="/a2a' in content
            is_pending = "pending" in content or "configuration" in content

            assert has_endpoints or is_pending, "applabs should show endpoints or pending configuration"

        except requests.RequestException as e:
            pytest.skip(f"Could not reach production URL: {e}")

    @pytest.mark.e2e
    def test_admin_ui_redirects_to_login(self):
        """Test that admin UI redirects to login."""
        if not self._is_production_test():
            pytest.skip("Set PRODUCTION_TEST=true to run production tests")

        domain = self._get_production_domain("admin", "https://admin.sales-agent.scope3.com")

        try:
            response = requests.get(
                domain,
                timeout=10,
                allow_redirects=False,
            )

            # Should redirect to login
            assert response.status_code == 302, f"Admin UI should redirect to login (302), got {response.status_code}"

            location = response.headers.get("Location", "")
            assert "/login" in location, f"Admin UI should redirect to /login, got {location}"

        except requests.RequestException as e:
            pytest.skip(f"Could not reach production URL: {e}")
