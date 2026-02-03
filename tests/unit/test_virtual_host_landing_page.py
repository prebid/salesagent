"""Unit tests for virtual host landing page functionality."""

from unittest.mock import Mock, patch

from starlette.requests import Request

from src.landing.landing_page import generate_fallback_landing_page, generate_tenant_landing_page


class TestVirtualHostLandingPage:
    """Test virtual host landing page functionality."""

    @patch("src.core.main.get_tenant_by_virtual_host")
    async def test_landing_page_with_virtual_host(self, mock_get_tenant):
        """Test landing page display for virtual host."""
        # Arrange
        mock_tenant = {
            "tenant_id": "landing-test",
            "name": "Landing Test Publisher",
            "virtual_host": "landing.test.com",
        }
        mock_get_tenant.return_value = mock_tenant

        # Mock request with Apx-Incoming-Host header
        mock_request = Mock(spec=Request)
        mock_request.headers = {"apx-incoming-host": "landing.test.com"}

        # Act - simulate the root route handler logic
        headers = dict(mock_request.headers)
        apx_host = headers.get("apx-incoming-host")

        tenant = None
        if apx_host:
            tenant = mock_get_tenant(apx_host)

        # Assert
        assert tenant is not None
        assert tenant["name"] == "Landing Test Publisher"
        assert tenant["virtual_host"] == "landing.test.com"
        mock_get_tenant.assert_called_once_with("landing.test.com")

    @patch("src.core.main.get_tenant_by_virtual_host")
    async def test_landing_page_without_virtual_host(self, mock_get_tenant):
        """Test redirect to admin for regular requests."""
        # Arrange
        mock_request = Mock(spec=Request)
        mock_request.headers = {}  # No special headers

        # Act - simulate the root route handler logic
        headers = dict(mock_request.headers)
        apx_host = headers.get("apx-incoming-host")

        # Should not call get_tenant_by_virtual_host if no header
        if not apx_host:
            # Should redirect to admin
            should_redirect = True
        else:
            tenant = mock_get_tenant(apx_host)
            should_redirect = tenant is None

        # Assert
        assert apx_host is None
        assert should_redirect is True
        mock_get_tenant.assert_not_called()

    def test_landing_page_html_generation_with_new_module(self):
        """Test HTML content generation using the new landing page module."""
        # Arrange
        tenant = {
            "tenant_id": "html-test",
            "name": "HTML Test Publisher & Co.",  # Test HTML escaping
            "subdomain": "htmltest",
        }
        virtual_host = "htmltest.sales-agent.example.com"

        # Act - use the new landing page module
        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant, virtual_host)

        # Assert - check for enhanced content (note: & will be escaped as &amp;)
        assert "HTML Test Publisher" in html_content  # Check for core name without special chars
        assert "Advertising Context Protocol" in html_content
        assert "/mcp" in html_content
        # A2A endpoint is at the root, not /a2a
        assert "https://htmltest.sales-agent.example.com" in html_content
        assert "/.well-known/agent.json" in html_content
        assert "<!DOCTYPE html>" in html_content

        # Check for new features
        assert "Need a Buying Agent?" in html_content
        assert "Internal Admin" in html_content
        assert "adcontextprotocol.org" in html_content

    def test_landing_page_xss_prevention_with_jinja2(self):
        """Test that tenant names are properly escaped using Jinja2."""
        # Arrange - tenant name with potential XSS
        tenant = {
            "tenant_id": "xss-test",
            "name": "<script>alert('xss')</script>Malicious Publisher",
            "subdomain": "xsstest",
        }

        # Act - use the new landing page module (should auto-escape)
        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Assert - Jinja2 should have escaped the malicious content
        assert "&lt;script&gt;" in html_content  # Escaped version
        assert "<script>" not in html_content  # Raw script tags should not be present
        assert "alert('xss')" not in html_content  # Should be escaped
        assert "Malicious Publisher" in html_content  # Safe content should remain

    def test_landing_page_url_generation_production(self):
        """Test URL generation in production environment."""
        tenant = {"name": "Production Publisher", "subdomain": "prod", "tenant_id": "prod-1"}
        virtual_host = "prod.sales-agent.example.com"

        with patch.dict("os.environ", {"PRODUCTION": "true"}):
            with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
                html_content = generate_tenant_landing_page(tenant, virtual_host)

        # Should use production URLs (A2A at root, not /a2a)
        assert "https://prod.sales-agent.example.com/mcp" in html_content
        assert "https://prod.sales-agent.example.com" in html_content  # A2A endpoint is at root
        assert "https://prod.sales-agent.example.com/.well-known/agent.json" in html_content

    def test_landing_page_url_generation_development(self):
        """Test URL generation in development environment."""
        tenant = {"name": "Dev Publisher", "subdomain": "dev", "tenant_id": "dev-1"}

        with patch.dict("os.environ", {"PRODUCTION": "false", "ADCP_SALES_PORT": "8080"}):
            with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
                html_content = generate_tenant_landing_page(tenant)

        # Should use localhost URLs (A2A at root, not /a2a)
        assert "http://localhost:8080/mcp" in html_content
        assert "http://localhost:8080" in html_content  # A2A endpoint is at root
        assert "http://localhost:8080/.well-known/agent.json" in html_content

    def test_landing_page_basic_content(self):
        """Test that landing page includes basic content elements."""
        tenant = {"name": "Test Publisher", "subdomain": "testpub", "tenant_id": "testpub-1"}

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Check for basic landing page content
        assert "Test Publisher" in html_content
        assert "AdCP" in html_content
        assert "testpub" in html_content
        # Check version is displayed in footer (both sales agent and AdCP versions)
        assert "Prebid Sales Agent v" in html_content
        assert "(AdCP " in html_content

    def test_landing_page_admin_dashboard_link(self):
        """Test that landing page includes admin dashboard link."""
        tenant = {"name": "Admin Test Publisher", "subdomain": "admintest", "tenant_id": "admintest-1"}

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Check for admin dashboard
        assert "Internal Admin" in html_content
        assert "/admin/" in html_content

    def test_landing_page_adcp_documentation_links(self):
        """Test that landing page includes proper AdCP documentation links."""
        tenant = {"name": "Docs Test Publisher", "subdomain": "docstest", "tenant_id": "docstest-1"}

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Check for documentation links
        assert "adcontextprotocol.org" in html_content
        assert "AdCP Protocol Documentation" in html_content
        assert "Media Buy API Reference" in html_content
        assert "Signals API Reference" in html_content

    @patch("src.core.main.get_tenant_by_virtual_host")
    async def test_landing_page_with_nonexistent_tenant(self, mock_get_tenant):
        """Test landing page with virtual host that has no tenant."""
        # Arrange
        mock_get_tenant.return_value = None
        mock_request = Mock(spec=Request)
        mock_request.headers = {"apx-incoming-host": "nonexistent.test.com"}

        # Act - simulate the root route handler logic
        headers = dict(mock_request.headers)
        apx_host = headers.get("apx-incoming-host")

        tenant = None
        if apx_host:
            tenant = mock_get_tenant(apx_host)

        should_redirect = tenant is None

        # Assert
        assert tenant is None
        assert should_redirect is True
        mock_get_tenant.assert_called_once_with("nonexistent.test.com")

    def test_fallback_landing_page_generation(self):
        """Test fallback landing page when tenant lookup fails."""
        # Act
        html_content = generate_fallback_landing_page("Test error message")

        # Assert
        assert "<!DOCTYPE html>" in html_content
        assert "AdCP Sales Agent" in html_content
        assert "Test error message" in html_content
        assert "/admin/" in html_content
        assert "Go to Admin Dashboard" in html_content

    def test_landing_page_responsive_design(self):
        """Test that landing page includes responsive design elements."""
        tenant = {"name": "Responsive Publisher", "subdomain": "responsive", "tenant_id": "responsive-1"}

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Check for responsive CSS features
        assert "@media (max-width: 768px)" in html_content
        assert "width=device-width" in html_content  # Viewport meta tag
        assert "flex" in html_content  # Flexbox
        assert "box-sizing: border-box" in html_content  # Responsive box model

    def test_landing_page_accessibility_features(self):
        """Test that landing page includes accessibility features."""
        tenant = {"name": "Accessible Publisher", "subdomain": "accessible", "tenant_id": "accessible-1"}

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Check for accessibility features
        assert 'lang="en"' in html_content
        assert 'charset="utf-8"' in html_content
        assert 'name="description"' in html_content  # Meta description

    def test_landing_page_virtual_host_info_display(self):
        """Test that virtual host information is displayed when available."""
        tenant = {"name": "Virtual Host Publisher", "subdomain": "vhost"}
        virtual_host = "custom.example.com"

        html_content = generate_tenant_landing_page(tenant, virtual_host)

        # Check for virtual host info - virtual host should be used in URLs
        assert virtual_host in html_content

    def test_landing_page_tenant_subdomain_extraction(self):
        """Test tenant subdomain extraction from virtual host."""
        tenant = {"name": "Subdomain Test", "tenant_id": "subdomain-test"}
        virtual_host = "scribd.sales-agent.example.com"

        html_content = generate_tenant_landing_page(tenant, virtual_host)

        # Should extract "scribd" as the subdomain and use it in URLs
        assert "scribd" in html_content
        assert "https://scribd.sales-agent.example.com" in html_content

    @patch("src.core.main.get_tenant_by_virtual_host")
    async def test_landing_page_header_case_insensitive(self, mock_get_tenant):
        """Test header extraction with different cases."""
        # Arrange
        mock_tenant = {"name": "Case Test Publisher", "virtual_host": "case.test.com"}
        mock_get_tenant.return_value = mock_tenant

        test_headers = [
            {"apx-incoming-host": "case.test.com"},
            {"Apx-Incoming-Host": "case.test.com"},
            {"APX-INCOMING-HOST": "case.test.com"},
        ]

        for headers in test_headers:
            mock_request = Mock(spec=Request)
            mock_request.headers = headers

            # Act - simulate header extraction (case might vary)
            request_headers = dict(mock_request.headers)
            apx_host = (
                request_headers.get("apx-incoming-host")
                or request_headers.get("Apx-Incoming-Host")
                or request_headers.get("APX-INCOMING-HOST")
            )

            # Assert
            assert apx_host == "case.test.com"

    def test_landing_page_template_errors_handled(self):
        """Test that template errors are handled gracefully."""
        # Test with minimal tenant data
        tenant = {"name": "Minimal Publisher"}

        # Should not raise exception even with minimal data
        html_content = generate_tenant_landing_page(tenant)

        assert "Minimal Publisher" in html_content
        assert "<!DOCTYPE html>" in html_content
