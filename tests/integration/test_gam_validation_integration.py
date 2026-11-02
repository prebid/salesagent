"""
Integration tests for GAM creative validation in the adapter.

This test suite verifies that the GAM adapter correctly integrates
the validation logic and handles validation failures appropriately.
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import Principal
from tests.helpers.gam_client import gam_line_item as _line_item
from tests.helpers.gam_client import stub_gam_client_manager

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _build_adapter(principal, config, *, line_items=()):
    """Build the GAM adapter over a stand-in GAM client.

    The order's line items are supplied BY THE TEST — see
    ``tests/helpers/gam_client.py`` for why they must not come from the adapter's own
    unreachable fabrication branch (GH #2245).
    """
    with patch("src.adapters.google_ad_manager.GAMClientManager") as client_manager:
        client_manager.return_value = stub_gam_client_manager(line_items=line_items)
        return GoogleAdManager(
            config=config,
            principal=principal,
            network_code=config["network_code"],
            advertiser_id=principal.platform_mappings["google_ad_manager"]["advertiser_id"],
            trafficker_id=config["trafficker_id"],
            tenant_id="test_tenant",
        )


class TestGAMValidationIntegration:
    """Test GAM adapter integration with validation.

    Two tests were removed from this class because neither could fail.

    ``test_gam_adapter_initializes_validator`` asserted ``hasattr(adapter, "validator")``
    and ``is not None`` against an unconditional ``self.validator = GAMValidator()`` in
    ``__init__`` — it graded an assignment, i.e. that the constructor ran, which every
    other test here already requires.

    ``test_add_creative_assets_proceeds_with_valid_assets`` stubbed
    ``_validate_creative_for_gam``, ``_get_creative_type`` AND ``_create_gam_creative``,
    then asserted that two of those stubs had been called. Every production step on the
    path was replaced, so it graded the mocks. What it claimed to cover — a valid asset
    reaching GAM — is graded by the two HTML5 tests and the concept pair below, which
    drive the real type detection and the real creative build against a stand-in client.
    """

    def setup_method(self):
        """Set up test fixtures."""
        # Create a mock principal
        self.principal = Principal(
            principal_id="test_principal",
            name="Test Principal",
            platform_mappings={"google_ad_manager": {"advertiser_id": "123"}},
        )

        # Create GAM adapter config
        self.config = {
            "network_code": "123456",
            "service_account_key_file": "/path/to/key.json",
            "trafficker_id": "trafficker_123",
        }

    def test_add_creative_assets_validates_before_processing(self):
        """Test that creative assets are validated before GAM API calls."""
        adapter = _build_adapter(self.principal, self.config)

        # Mock the validation method to return validation errors
        with patch.object(adapter, "_validate_creative_for_gam") as mock_validate:
            mock_validate.return_value = ["Width exceeds GAM limit", "HTTPS required"]

            # Asset that would fail validation
            invalid_asset = {
                "creative_id": "test_creative_1",
                "url": "http://example.com/oversized.jpg",  # HTTP and oversized
                "width": 2000,  # Too wide
                "height": 90,
            }

            # Call add_creative_assets
            result = adapter.add_creative_assets("123", [invalid_asset], None)

            # Should return failed status without calling GAM API
            assert len(result) == 1
            assert result[0].creative_id == "test_creative_1"
            assert result[0].status == "failed"

            # Validation should have been called
            mock_validate.assert_called_once_with(invalid_asset)

    def test_validate_creative_for_gam_method(self):
        """Test the _validate_creative_for_gam method directly."""
        adapter = _build_adapter(self.principal, self.config)

        # Test with invalid asset
        invalid_asset = {
            "url": "http://example.com/banner.jpg",  # HTTP not allowed
            "width": 2000,  # Too wide
            "snippet": "<script>eval('code')</script>",  # Dangerous JS
        }

        issues = adapter._validate_creative_for_gam(invalid_asset)

        # Should return validation issues
        assert len(issues) > 0
        assert any("HTTPS" in issue for issue in issues)
        assert any("width" in issue for issue in issues)
        assert any("eval" in issue for issue in issues)

        # Test with valid asset
        valid_asset = {
            "url": "https://example.com/banner.jpg",
            "width": 728,
            "height": 90,
        }

        issues = adapter._validate_creative_for_gam(valid_asset)

        # Should return no issues
        assert issues == []

    def test_html5_creative_type_detection_and_creation(self):
        """Test that HTML5 creatives are detected and handled correctly."""
        # 970x250 comes from the asset's format string, so the order's line item must
        # accept that size for the creative to reach GAM.
        adapter = _build_adapter(
            self.principal,
            self.config,
            line_items=[_line_item("test_package", sizes=((970, 250),))],
        )

        # Test HTML5 creative detection by file extension
        html5_asset = {
            "creative_id": "html5_creative_1",
            "name": "HTML5 Banner",
            "format": "display_970x250",
            "media_url": "https://example.com/creative.html",
            "click_url": "https://example.com/landing",
            "package_assignments": ["test_package"],
        }

        # Check that it's detected as HTML5
        creative_type = adapter._get_creative_type(html5_asset)
        assert creative_type == "html5"

        # Test HTML5 creative creation
        with patch.object(adapter, "_validate_creative_for_gam") as mock_validate:
            mock_validate.return_value = []  # No validation errors

            result = adapter.add_creative_assets("90210", [html5_asset], datetime.now())

            # Should succeed in dry-run mode
            assert len(result) == 1
            assert result[0].status == "approved"

    def test_html5_creative_with_zip_file(self):
        """Test HTML5 creative with ZIP file containing assets."""
        # "html5_interactive" carries no dimensions, so the size check falls back to
        # 300x250 and the line item must accept that.
        adapter = _build_adapter(
            self.principal,
            self.config,
            line_items=[_line_item("test_package", sizes=((300, 250),))],
        )

        zip_asset = {
            "creative_id": "html5_zip_1",
            "name": "HTML5 Interactive Banner",
            "format": "html5_interactive",
            "media_url": "https://example.com/creative.zip",
            "click_url": "https://example.com/landing",
            "backup_image_url": "https://example.com/backup.jpg",
            "package_assignments": ["test_package"],
        }

        # Should be detected as HTML5
        creative_type = adapter._get_creative_type(zip_asset)
        assert creative_type == "html5"

        # Test creation with validation
        with patch.object(adapter, "_validate_creative_for_gam") as mock_validate:
            mock_validate.return_value = []  # No validation errors

            result = adapter.add_creative_assets("90210", [zip_asset], datetime.now())

            # Should succeed
            assert len(result) == 1
            assert result[0].status == "approved"

    def test_approved_creative_carries_seller_side_concept_from_order(self):
        """Approved creatives carry a GAM-Order-derived seller-side concept (#1506).

        AdCP exposes concept_id/concept_name read-only on list_creatives but carries
        no concept on sync_creatives, so there is no protocol writer. GAM has no
        first-class creative group, so the adapter falls back to the GAM Order as the
        closest native grouping. The value is namespaced (``gam-order-<id>``) and
        tagged with ``concept_source`` so it stays distinguishable from a future
        buyer-supplied concept.
        """
        adapter = _build_adapter(
            self.principal,
            self.config,
            line_items=[_line_item("test_package", sizes=((970, 250),))],
        )

        asset = {
            "creative_id": "html5_creative_1",
            "name": "HTML5 Banner",
            "format": "display_970x250",
            "media_url": "https://example.com/creative.html",
            "click_url": "https://example.com/landing",
            "package_assignments": ["test_package"],
        }

        with patch.object(adapter, "_validate_creative_for_gam", return_value=[]):
            result = adapter.add_creative_assets("789", [asset], datetime.now())

        assert len(result) == 1
        status = result[0]
        assert status.status == "approved"
        assert status.concept_id == "gam-order-789"
        assert status.concept_name == "GAM Order 789"
        assert status.concept_source == "gam_order"

    def test_failed_creative_carries_no_concept_enrichment(self):
        """A creative that fails GAM validation is never pushed, so it gets no concept (#1506)."""
        adapter = _build_adapter(self.principal, self.config)

        invalid_asset = {
            "creative_id": "bad_creative_1",
            "url": "http://example.com/oversized.jpg",
            "width": 2000,
            "height": 90,
        }

        with patch.object(adapter, "_validate_creative_for_gam", return_value=["Width exceeds GAM limit"]):
            result = adapter.add_creative_assets("789", [invalid_asset], datetime.now())

        assert len(result) == 1
        status = result[0]
        assert status.status == "failed"
        assert status.concept_id is None
        assert status.concept_name is None
        assert status.concept_source is None

    def test_validation_handles_different_creative_types(self):
        """Test validation works for different creative types."""
        adapter = _build_adapter(self.principal, self.config)

        # Test third-party tag validation
        third_party_asset = {
            "snippet": "<script src='http://unsafe.com/script.js'></script>",
            "snippet_type": "javascript",
        }

        issues = adapter._validate_creative_for_gam(third_party_asset)
        assert any("Script source must use HTTPS" in issue for issue in issues)

        # Test VAST validation
        vast_asset = {"snippet_type": "vast_xml"}  # Missing snippet and URL

        issues = adapter._validate_creative_for_gam(vast_asset)
        assert any("VAST creative requires either 'snippet' or 'url'" in issue for issue in issues)

        # Test native validation (when we add it)
        native_asset = {"template_variables": {"headline": "Test Ad", "image_url": "https://example.com/img.jpg"}}

        issues = adapter._validate_creative_for_gam(native_asset)
        # Should be valid for basic native structure
        assert issues == []

    def test_validation_logging_on_failure(self):
        """Test that validation failures are properly logged."""
        # Asset with validation errors
        invalid_asset = {
            "creative_id": "test_creative_1",
            "url": "http://example.com/banner.jpg",  # HTTP not allowed
            "width": 2000,  # Too wide
            "height": 90,
            "package_assignments": ["mock_package"],  # Assign to mock package
        }

        with patch("src.adapters.google_ad_manager.GAMClientManager"):
            # Mock the log method before creating adapter so it gets the mocked version
            with patch.object(GoogleAdManager, "log") as mock_log:
                adapter = GoogleAdManager(
                    config=self.config,
                    principal=self.principal,
                    network_code=self.config["network_code"],
                    advertiser_id=self.principal.platform_mappings["google_ad_manager"]["advertiser_id"],
                    trafficker_id=self.config["trafficker_id"],
                    tenant_id="test_tenant",
                )

                result = adapter.add_creative_assets("123", [invalid_asset], None)

                # Check that validation error was detected
                assert result[0].status == "failed"

                # The log assertion that stood here was wrapped in ``if mock_log.called:``,
                # so it passed whenever the log had NOT been written — the one outcome it
                # existed to catch. Removed rather than unwrapped: the manager captures
                # ``self.log`` at construction, so what reaches it is an implementation
                # detail of the wiring, and the failed status above is the buyer-visible
                # fact.


class TestGAMValidationPerformance:
    """Test performance aspects of GAM validation.

    ``test_validation_performance_with_many_assets`` was removed. Its two assertions were
    a wall-clock ``validation_time < 1.0`` over a path whose every GAM call is a stand-in
    (so it measured neither validation nor production cost, and was flaky by
    construction), and ``all(status.status in ["approved", "failed"])``, which admits
    every value that field can take on that path. Neither could fail.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.principal = Principal(
            principal_id="test_principal",
            name="Test Principal",
            platform_mappings={"google_ad_manager": {"advertiser_id": "123"}},
        )

        self.config = {
            "network_code": "123456",
            "service_account_key_file": "/path/to/key.json",
            "trafficker_id": "trafficker_123",
        }

    def test_validation_early_exit_on_failure(self):
        """Test that validation provides early feedback on failures."""
        adapter = _build_adapter(self.principal, self.config)

        # Mix of valid and invalid assets
        assets = [
            {  # Invalid - HTTP
                "creative_id": "invalid_1",
                "name": "Invalid Creative 1",
                "url": "http://example.com/banner.jpg",
                "width": 728,
                "height": 90,
            },
            {  # Valid
                "creative_id": "valid_1",
                "name": "Valid Creative 1",
                "url": "https://example.com/banner.jpg",
                "width": 728,
                "height": 90,
            },
            {  # Invalid - oversized
                "creative_id": "invalid_2",
                "name": "Invalid Creative 2",
                "url": "https://example.com/banner.jpg",
                "width": 2000,
                "height": 90,
            },
        ]

        result = adapter.add_creative_assets("123", assets, None)

        # Should process all assets and identify failures
        assert len(result) == 3

        # Check specific results
        results_by_id = {r.creative_id: r.status for r in result}
        assert results_by_id["invalid_1"] == "failed"
        assert results_by_id["valid_1"] == "approved"  # In dry-run mode
        assert results_by_id["invalid_2"] == "failed"
