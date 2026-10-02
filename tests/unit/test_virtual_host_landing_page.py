"""Unit tests for virtual host landing page functionality.

Everything here calls ``generate_tenant_landing_page`` and asserts on the HTML production
returned. The page takes a tenant row and nothing else: every URL on it is built from the
host that row declares, which is also what the agent card publishes. So a tenant here states
its ``virtual_host`` the way a stored row does, and no test supplies a request host.

WHICH TENANT A HOST RESOLVES IS NOT GRADED HERE. It is graded where it is observable: on
all four transports by
``tests/bdd/features/local-tenant-identification-routes.feature``, and for the routing
function by ``tests/unit/test_domain_routing.py``. A test that mocks
``get_tenant_by_virtual_host`` and re-implements the root handler's header reading in its
own body grades a copy of production that lives in this file — hard rule 5 in
tests/CLAUDE.md, and the "recomputing the expected value from setup" pitfall — so it
cannot fail when production changes.

THAT THE PAGE AND THE CARD AGREE is graded in
``tests/integration/test_landing_page_and_card_agree_on_origin.py``, which drives both
surfaces over HTTP against a real row: only a test holding both answers at once can tell
"the stored origin is published" from "some origin is published".
"""

from unittest.mock import patch

from src.landing.landing_page import generate_fallback_landing_page, generate_tenant_landing_page


class TestVirtualHostLandingPage:
    """Test virtual host landing page functionality."""

    def test_landing_page_html_generation_with_new_module(self):
        """Test HTML content generation through the landing page module."""
        # Arrange
        tenant = {
            "tenant_id": "html-test",
            "name": "HTML Test Publisher & Co.",  # Test HTML escaping
            "subdomain": "htmltest",
            "virtual_host": "htmltest.sales-agent.example.com",
        }

        # Act - use the new landing page module
        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Assert - check for enhanced content (note: & will be escaped as &amp;)
        assert "HTML Test Publisher" in html_content  # Check for core name without special chars
        assert "Advertising Context Protocol" in html_content
        assert "/mcp" in html_content
        # A2A endpoint is at the root, not /a2a
        assert "https://htmltest.sales-agent.example.com" in html_content
        assert "/.well-known/agent-card.json" in html_content
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
            "virtual_host": "xsstest.sales-agent.example.com",
        }

        # Act - use the new landing page module (should auto-escape)
        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Assert - Jinja2 should have escaped the malicious content
        assert "&lt;script&gt;" in html_content  # Escaped version
        assert "<script>" not in html_content  # Raw script tags should not be present
        assert "alert('xss')" not in html_content  # Should be escaped
        assert "Malicious Publisher" in html_content  # Safe content should remain

    def test_landing_page_urls_use_the_stored_host(self):
        """A tenant's own host, with https because it is not localhost."""
        tenant = {
            "name": "Production Publisher",
            "subdomain": "prod",
            "tenant_id": "prod-1",
            "virtual_host": "prod.sales-agent.example.com",
        }

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # A2A is at the root, so the origin itself is one of the published endpoints.
        assert "https://prod.sales-agent.example.com/mcp" in html_content
        assert "https://prod.sales-agent.example.com" in html_content
        assert "https://prod.sales-agent.example.com/.well-known/agent-card.json" in html_content

    def test_landing_page_urls_use_http_for_a_localhost_host(self):
        """A tenant served on localhost publishes http, and keeps its port."""
        tenant = {
            "name": "Dev Publisher",
            "subdomain": "dev",
            "tenant_id": "dev-1",
            "virtual_host": "localhost:8080",
        }

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        assert "http://localhost:8080/mcp" in html_content
        assert "http://localhost:8080" in html_content  # A2A endpoint is at root
        assert "http://localhost:8080/.well-known/agent-card.json" in html_content

    def test_landing_page_basic_content(self):
        """Test that landing page includes basic content elements."""
        tenant = {
            "name": "Test Publisher",
            "subdomain": "testpub",
            "tenant_id": "testpub-1",
            "virtual_host": "testpub.sales-agent.example.com",
        }

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
        tenant = {
            "name": "Admin Test Publisher",
            "subdomain": "admintest",
            "tenant_id": "admintest-1",
            "virtual_host": "admintest.sales-agent.example.com",
        }

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Check for admin dashboard
        assert "Internal Admin" in html_content
        assert "https://admintest.sales-agent.example.com/admin/" in html_content

    def test_landing_page_adcp_documentation_links(self):
        """Test that landing page includes proper AdCP documentation links."""
        tenant = {
            "name": "Docs Test Publisher",
            "subdomain": "docstest",
            "tenant_id": "docstest-1",
            "virtual_host": "docstest.sales-agent.example.com",
        }

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Check for documentation links
        assert "adcontextprotocol.org" in html_content
        assert "AdCP Protocol Documentation" in html_content
        assert "Media Buy API Reference" in html_content
        assert "Signals API Reference" in html_content

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
        tenant = {
            "name": "Responsive Publisher",
            "subdomain": "responsive",
            "tenant_id": "responsive-1",
            "virtual_host": "responsive.sales-agent.example.com",
        }

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Check for responsive CSS features
        assert "@media (max-width: 768px)" in html_content
        assert "width=device-width" in html_content  # Viewport meta tag
        assert "flex" in html_content  # Flexbox
        assert "box-sizing: border-box" in html_content  # Responsive box model

    def test_landing_page_accessibility_features(self):
        """Test that landing page includes accessibility features."""
        tenant = {
            "name": "Accessible Publisher",
            "subdomain": "accessible",
            "tenant_id": "accessible-1",
            "virtual_host": "accessible.sales-agent.example.com",
        }

        with patch("src.core.tenant_status.is_tenant_ad_server_configured", return_value=True):
            html_content = generate_tenant_landing_page(tenant)

        # Check for accessibility features
        assert 'lang="en"' in html_content
        assert 'charset="utf-8"' in html_content
        assert 'name="description"' in html_content  # Meta description

    def test_landing_page_virtual_host_info_display(self):
        """The host the tenant declares is the one the page shows."""
        tenant = {
            "tenant_id": "vhost-1",
            "name": "Virtual Host Publisher",
            "subdomain": "vhost",
            "virtual_host": "custom.example.com",
        }

        html_content = generate_tenant_landing_page(tenant)

        assert "https://custom.example.com" in html_content

    def test_landing_page_template_errors_handled(self):
        """Test that template errors are handled gracefully."""
        # Minimal tenant data: the two fields a row always carries, plus a name
        tenant = {"tenant_id": "minimal-1", "name": "Minimal Publisher", "virtual_host": "minimal.example.com"}

        # Should not raise exception even with minimal data
        html_content = generate_tenant_landing_page(tenant)

        assert "Minimal Publisher" in html_content
        assert "<!DOCTYPE html>" in html_content
