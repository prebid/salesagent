"""Integration tests that automatically validate all links in HTML pages.

These tests catch broken links that would otherwise only be discovered in production.
They work by:
1. Fetching pages from the admin UI
2. Parsing all <a href>, <img src>, <link href>, <script src> attributes
3. Validating that each internal link returns a valid HTTP status
4. Reporting any broken links with line numbers for easy debugging

This would have caught the creative review 404 issue (PR #421) before production.
"""

import pytest

from tests.integration.link_validator import LinkValidator, format_broken_links_report

pytestmark = pytest.mark.integration


class TestLinkValidation:
    """Test that all links in HTML pages are valid."""

    def test_dashboard_links_are_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test that all links on the tenant dashboard are valid.

        This test would have caught the creative review 404 issue where the
        creatives blueprint wasn't registered but links pointed to it.
        """
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        # Fetch dashboard page
        dashboard_url = f"/tenant/{tenant_id}"
        broken_links = validator.validate_page(dashboard_url)

        # Assert no broken links
        assert not broken_links, format_broken_links_report(broken_links, dashboard_url)

    def test_settings_page_links_are_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test that all links on the tenant settings page are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        # Fetch settings page
        settings_url = f"/tenant/{tenant_id}/settings"
        broken_links = validator.validate_page(settings_url)

        # Assert no broken links
        assert not broken_links, format_broken_links_report(broken_links, settings_url)

    def test_products_page_links_are_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test that all links on the products page are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        # Fetch products page
        products_url = f"/tenant/{tenant_id}/products/"
        broken_links = validator.validate_page(products_url)

        # Assert no broken links
        assert not broken_links, format_broken_links_report(broken_links, products_url)

    def test_principals_page_links_are_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test that all links on the principals page are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        # Fetch principals page
        principals_url = f"/tenant/{tenant_id}/principals"
        broken_links = validator.validate_page(principals_url)

        # Assert no broken links
        assert not broken_links, format_broken_links_report(broken_links, principals_url)

    def test_media_buys_page_links_are_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test that all links on the media buys page are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        # Fetch media buys page
        media_buys_url = f"/tenant/{tenant_id}/media-buys"
        broken_links = validator.validate_page(media_buys_url)

        # Assert no broken links
        assert not broken_links, format_broken_links_report(broken_links, media_buys_url)

    def test_workflows_page_links_are_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test that all links on the workflows page are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        # Fetch workflows page
        workflows_url = f"/tenant/{tenant_id}/workflows"
        broken_links = validator.validate_page(workflows_url)

        # Assert no broken links
        assert not broken_links, format_broken_links_report(broken_links, workflows_url)

    def test_inventory_page_links_are_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test that all links on the inventory page are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        # Fetch inventory page
        inventory_url = f"/tenant/{tenant_id}/inventory"
        broken_links = validator.validate_page(inventory_url)

        # Assert no broken links (allow 501 for unimplemented routes)
        assert not broken_links, format_broken_links_report(broken_links, inventory_url)

    def test_authorized_properties_page_links_are_valid(
        self, authenticated_admin_session, test_tenant_with_data
    ):
        """Test that all links on the authorized properties page are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        # Fetch authorized properties page
        props_url = f"/tenant/{tenant_id}/authorized-properties"
        broken_links = validator.validate_page(props_url)

        # Assert no broken links
        assert not broken_links, format_broken_links_report(broken_links, props_url)

    def test_property_tags_page_links_are_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test that all links on the property tags page are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        # Fetch property tags page
        tags_url = f"/tenant/{tenant_id}/property-tags"
        broken_links = validator.validate_page(tags_url)

        # Assert no broken links
        assert not broken_links, format_broken_links_report(broken_links, tags_url)

    def test_creative_review_page_links_are_valid(
        self, authenticated_admin_session, test_tenant_with_data
    ):
        """Test that all links on the creative review page are valid.

        This specifically tests the route that was broken in PR #421 - the
        creative review page that was returning 404 because the creatives
        blueprint wasn't registered.
        """
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        # Fetch creative review page (this was the broken link!)
        review_url = f"/tenant/{tenant_id}/creatives/review"
        response = authenticated_admin_session.get(review_url, follow_redirects=True)

        # Verify page loads successfully
        assert response.status_code == 200, f"Creative review page returned {response.status_code}"

        # Validate all links on the page
        broken_links = validator.validate_response(response, current_page=review_url)

        # Assert no broken links
        assert not broken_links, format_broken_links_report(broken_links, review_url)


class TestLinkValidatorUtility:
    """Test the LinkValidator utility itself."""

    def test_validates_internal_links(self, authenticated_admin_session):
        """Test that internal links are validated."""
        validator = LinkValidator(authenticated_admin_session)

        # Test valid internal link
        assert validator.should_validate_link("/tenant/123/products")
        assert validator.should_validate_link("../settings")
        assert validator.should_validate_link("./review")

    def test_skips_external_links(self, authenticated_admin_session):
        """Test that external links are skipped."""
        validator = LinkValidator(authenticated_admin_session)

        # Test external links (should skip)
        assert not validator.should_validate_link("https://google.com")
        assert not validator.should_validate_link("http://example.com")
        assert not validator.should_validate_link("//cdn.example.com/script.js")

    def test_skips_special_links(self, authenticated_admin_session):
        """Test that special links are skipped."""
        validator = LinkValidator(authenticated_admin_session)

        # Test special links (should skip)
        assert not validator.should_validate_link("javascript:void(0)")
        assert not validator.should_validate_link("mailto:user@example.com")
        assert not validator.should_validate_link("tel:+1234567890")
        assert not validator.should_validate_link("#section-anchor")
        assert not validator.should_validate_link("data:image/png;base64,...")

    def test_normalizes_relative_urls(self, authenticated_admin_session):
        """Test that relative URLs are normalized correctly."""
        validator = LinkValidator(authenticated_admin_session)

        # Test relative URL normalization
        assert validator.normalize_url("./products", "/tenant/123/settings") == "/tenant/123/products"
        assert validator.normalize_url("../settings", "/tenant/123/products/") == "/tenant/123/settings"
        assert validator.normalize_url("/absolute/path", "/any/page") == "/absolute/path"

    def test_detects_broken_links(self, authenticated_admin_session):
        """Test that broken links are detected."""
        validator = LinkValidator(authenticated_admin_session)

        # Test broken link detection
        is_valid, status_code, error = validator.validate_link("/this-route-does-not-exist-xyz123")
        assert not is_valid
        assert status_code == 404

    def test_accepts_valid_links(self, authenticated_admin_session):
        """Test that valid links are accepted."""
        validator = LinkValidator(authenticated_admin_session)

        # Test valid link detection
        is_valid, status_code, error = validator.validate_link("/health")
        assert is_valid
        assert status_code in (200, 302, 304)

    def test_formats_broken_links_report(self):
        """Test that broken links are formatted correctly."""
        broken_links = [
            {
                "url": "/tenant/123/broken",
                "normalized_url": "/tenant/123/broken",
                "tag": "a",
                "line": 42,
                "type": "href",
                "status_code": 404,
                "error": "Status 404",
            },
            {
                "url": "../also-broken",
                "normalized_url": "/tenant/123/also-broken",
                "tag": "a",
                "line": 43,
                "type": "href",
                "status_code": 500,
                "error": "Status 500",
            },
        ]

        report = format_broken_links_report(broken_links, "/tenant/123/page")

        # Verify report contains key information
        assert "/tenant/123/page" in report
        assert "/tenant/123/broken" in report
        assert "/tenant/123/also-broken" in report
        assert "line 42" in report
        assert "line 43" in report
        assert "[404]" in report
        assert "[500]" in report
        assert "2 broken links" in report
