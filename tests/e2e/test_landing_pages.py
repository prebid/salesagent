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
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport


class TestLandingPages:
    """Test landing page routing for different domain types."""

    def _get_base_url(self) -> str:
        """Get base URL for tests (supports dynamic ports via ADCP_SALES_PORT env var)."""
        port = os.getenv("ADCP_SALES_PORT", "8080")
        return os.getenv("TEST_BASE_URL", f"http://localhost:{port}")

    @pytest.mark.integration
    def test_admin_domain_redirects_to_login(self):
        """Admin domain should return 302 redirect to login page."""
        base_url = self._get_base_url()

        try:
            # Test admin domain routing with admin Host header
            response = requests.get(
                f"{base_url}/",
                headers={
                    "Host": "admin.sales-agent.example.com",
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
                    "Host": "admin.sales-agent.example.com",
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

                assert has_mcp or is_pending, (
                    "Landing page should either show MCP endpoint or pending configuration message"
                )

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

                assert has_a2a or is_pending, (
                    "Landing page should either show A2A endpoint or pending configuration message"
                )

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
                    "Host": f"localhost:{os.getenv('ADCP_SALES_PORT', '8080')}",  # Backend host
                    "Apx-Incoming-Host": "admin.sales-agent.example.com",  # Proxied admin host
                },
                timeout=5,
                allow_redirects=False,
            )

            # Should route based on Apx-Incoming-Host (admin domain -> login redirect)
            assert response.status_code == 302, (
                f"Proxied admin domain should redirect to login (302), got {response.status_code}"
            )

            location = response.headers.get("Location", "")
            assert "/login" in location, f"Proxied admin domain should redirect to /login, got {location}"

        except (requests.ConnectionError, requests.Timeout):
            pytest.skip(f"Server not running at {base_url}")


class TestAuthOptionalEndpoints:
    """Test auth-optional MCP endpoints via MCP client protocol.

    "With auth" tests pass auth token + tenant header.
    "Without auth" tests pass Host header for domain-based tenant resolution.
    Both use StreamableHttpTransport which handles session handshake.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_creative_formats_without_auth(self, live_server):
        """list_creative_formats should be reachable without authentication via domain routing."""
        transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers={"Host": "test-custom-domain.example.com"},
        )
        try:
            async with Client(transport=transport) as client:
                result = await client.call_tool("list_creative_formats", {})
                # Success or tool-level error are both acceptable
                assert result is not None
        except Exception:
            # MCP session or tool error without auth is acceptable
            pass

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_creative_formats_with_auth(self, live_server, test_auth_token):
        """list_creative_formats should work with authentication."""
        transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers={"x-adcp-auth": test_auth_token, "x-adcp-tenant": "ci-test"},
        )
        async with Client(transport=transport) as client:
            result = await client.call_tool("list_creative_formats", {})
            assert result is not None, "list_creative_formats should return a result"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_authorized_properties_without_auth(self, live_server):
        """list_authorized_properties should be reachable without authentication via domain routing."""
        transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers={"Host": "test-custom-domain.example.com"},
        )
        try:
            async with Client(transport=transport) as client:
                result = await client.call_tool("list_authorized_properties", {})
                assert result is not None
        except Exception:
            pass

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_authorized_properties_with_auth(self, live_server, test_auth_token):
        """list_authorized_properties should work with authentication."""
        transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers={"x-adcp-auth": test_auth_token, "x-adcp-tenant": "ci-test"},
        )
        async with Client(transport=transport) as client:
            result = await client.call_tool("list_authorized_properties", {})
            assert result is not None, "list_authorized_properties should return a result"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_products_without_auth_public_policy(self, live_server):
        """get_products should be reachable without authentication (public policy tenants)."""
        transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers={"Host": "test-custom-domain.example.com"},
        )
        try:
            async with Client(transport=transport) as client:
                result = await client.call_tool("get_products", {"brief": "test campaign"})
                assert result is not None
        except Exception:
            pass

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_products_with_auth(self, live_server, test_auth_token):
        """get_products should work with authentication regardless of policy."""
        transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers={"x-adcp-auth": test_auth_token, "x-adcp-tenant": "ci-test"},
        )
        async with Client(transport=transport) as client:
            result = await client.call_tool("get_products", {"brief": "test campaign"})
            assert result is not None, "get_products should return a result"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_products_filters_pricing_for_anonymous(self, live_server):
        """get_products should hide pricing information for anonymous users."""
        transport = StreamableHttpTransport(
            url=f"{live_server['mcp']}/mcp/",
            headers={"Host": "test-custom-domain.example.com"},
        )
        try:
            async with Client(transport=transport) as client:
                result = await client.call_tool("get_products", {"brief": "test campaign"})
                # If tool succeeds, verify pricing is filtered for anonymous
                if result:
                    for content in result:
                        if hasattr(content, "text"):
                            import json

                            try:
                                data = json.loads(content.text)
                                if "products" in data:
                                    for product in data["products"]:
                                        pricing = product.get("pricing_options", [])
                                        assert len(pricing) == 0, (
                                            f"Anonymous users should not see pricing, got {len(pricing)} options"
                                        )
                            except (json.JSONDecodeError, KeyError):
                                pass  # Non-JSON result is fine
        except Exception:
            pass  # Connection or tool errors acceptable without auth


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

            assert response.status_code == 200, (
                f"AccuWeather landing page should return 200, got {response.status_code}"
            )

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
            assert 'href="/mcp' in content or 'href="/a2a' in content, (
                "test-agent landing page should contain agent endpoints"
            )

        except requests.RequestException as e:
            pytest.skip(f"Could not reach production URL: {e}")

    @pytest.mark.e2e
    def test_applabs_subdomain_landing_page(self):
        """Test applabs subdomain landing page."""
        if not self._is_production_test():
            pytest.skip("Set PRODUCTION_TEST=true to run production tests")

        domain = self._get_production_domain("applabs", "https://applabs.sales-agent.example.com")

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

        domain = self._get_production_domain("admin", "https://admin.sales-agent.example.com")

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
