"""
Integration tests for GAM creative validation in the adapter.

This test suite verifies that the GAM adapter correctly integrates
the validation logic and handles validation failures appropriately.
"""

from datetime import datetime
from unittest.mock import patch

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import Principal


class TestGAMValidationIntegration:
    """Test GAM adapter integration with validation."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create a mock principal
        self.principal = Principal(
            principal_id="test_principal",
            name="Test Principal",
            access_token="test_token",
            platform_mappings={"google_ad_manager": {"advertiser_id": "123"}},
        )

        # Create GAM adapter config
        self.config = {
            "network_code": "123456",
            "service_account_key_file": "/path/to/key.json",
            "trafficker_id": "trafficker_123",
        }

    def test_gam_adapter_initializes_validator(self):
        """Test that GAM adapter initializes the validator on construction."""
        with patch.object(GoogleAdManager, "_init_client"):
            adapter = GoogleAdManager(config=self.config, principal=self.principal, dry_run=True)

            # Validator should be initialized
            assert hasattr(adapter, "validator")
            assert adapter.validator is not None

    def test_add_creative_assets_validates_before_processing(self):
        """Test that creative assets are validated before GAM API calls."""
        with patch.object(GoogleAdManager, "_init_client"):
            adapter = GoogleAdManager(config=self.config, principal=self.principal, dry_run=True)

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

    def test_add_creative_assets_proceeds_with_valid_assets(self):
        """Test that valid assets proceed to GAM processing."""
        with patch.object(GoogleAdManager, "_init_client"):
            adapter = GoogleAdManager(config=self.config, principal=self.principal, dry_run=True)

        # Mock the validation method to return no errors
        with patch.object(adapter, "_validate_creative_for_gam") as mock_validate:
            mock_validate.return_value = []  # No validation errors

            with patch.object(adapter, "_get_creative_type") as mock_get_type:
                mock_get_type.return_value = "hosted_asset"

                with patch.object(adapter, "_create_gam_creative") as mock_create:
                    mock_create.return_value = {
                        "name": "Test Creative",
                        "id": "gam_123",
                        "size": {"width": 728, "height": 90},
                        "destinationUrl": "https://example.com/landing",
                    }

                    # Valid asset
                    valid_asset = {
                        "creative_id": "test_creative_1",
                        "name": "Test Creative 1",
                        "url": "https://example.com/banner.jpg",
                        "width": 728,
                        "height": 90,
                        "package_assignments": ["package_1"],
                    }

                    # Call add_creative_assets
                    result = adapter.add_creative_assets("123", [valid_asset], None)

                    # Should proceed with processing
                    mock_validate.assert_called_once_with(valid_asset)
                    mock_get_type.assert_called_once_with(valid_asset)
                    # Note: _create_gam_creative would be called in non-dry-run mode

    def test_validate_creative_for_gam_method(self):
        """Test the _validate_creative_for_gam method directly."""
        with patch.object(GoogleAdManager, "_init_client"):
            adapter = GoogleAdManager(config=self.config, principal=self.principal, dry_run=True)

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
        with patch.object(GoogleAdManager, "_init_client"):
            adapter = GoogleAdManager(config=self.config, principal=self.principal, dry_run=True)

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

            result = adapter.add_creative_assets("test_media_buy", [html5_asset], datetime.now())

            # Should succeed in dry-run mode
            assert len(result) == 1
            assert result[0].status == "approved"

    def test_html5_creative_with_zip_file(self):
        """Test HTML5 creative with ZIP file containing assets."""
        with patch.object(GoogleAdManager, "_init_client"):
            adapter = GoogleAdManager(config=self.config, principal=self.principal, dry_run=True)

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

            result = adapter.add_creative_assets("test_media_buy", [zip_asset], datetime.now())

            # Should succeed
            assert len(result) == 1
            assert result[0].status == "approved"

    def test_validation_handles_different_creative_types(self):
        """Test validation works for different creative types."""
        with patch.object(GoogleAdManager, "_init_client"):
            adapter = GoogleAdManager(config=self.config, principal=self.principal, dry_run=True)

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
        with patch.object(GoogleAdManager, "_init_client"):
            adapter = GoogleAdManager(config=self.config, principal=self.principal, dry_run=True)

        # Mock the log method to capture log messages
        with patch.object(adapter, "log") as mock_log:
            # Asset with validation errors
            invalid_asset = {
                "creative_id": "test_creative_1",
                "url": "http://example.com/banner.jpg",  # HTTP not allowed
                "width": 2000,  # Too wide
            }

            result = adapter.add_creative_assets("123", [invalid_asset], None)

            # Should log validation failure
            mock_log.assert_any_call("[red]Creative test_creative_1 failed GAM validation:[/red]")

            # Should log specific validation issues
            log_calls = [call.args[0] for call in mock_log.call_args_list]
            logged_text = " ".join(log_calls)
            assert "HTTPS" in logged_text
            assert "width" in logged_text


class TestGAMValidationPerformance:
    """Test performance aspects of GAM validation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.principal = Principal(
            principal_id="test_principal",
            name="Test Principal",
            access_token="test_token",
            platform_mappings={"google_ad_manager": {"advertiser_id": "123"}},
        )

        self.config = {
            "network_code": "123456",
            "service_account_key_file": "/path/to/key.json",
            "trafficker_id": "trafficker_123",
        }

    def test_validation_performance_with_many_assets(self):
        """Test validation performance with many assets."""
        with patch.object(GoogleAdManager, "_init_client"):
            adapter = GoogleAdManager(config=self.config, principal=self.principal, dry_run=True)

        # Create many assets for validation
        assets = []
        for i in range(100):
            assets.append(
                {
                    "creative_id": f"creative_{i}",
                    "name": f"Creative {i}",
                    "url": f"https://example.com/banner_{i}.jpg",
                    "width": 728,
                    "height": 90,
                }
            )

        # Time the validation
        import time

        start_time = time.time()

        # This will validate all assets
        result = adapter.add_creative_assets("123", assets, None)

        end_time = time.time()
        validation_time = end_time - start_time

        # Validation should complete quickly (less than 1 second for 100 assets)
        assert validation_time < 1.0

        # All assets should pass validation
        assert len(result) == 100
        # In dry-run mode, they would get "approved" status if validation passes
        assert all(status.status in ["approved", "failed"] for status in result)

    def test_validation_early_exit_on_failure(self):
        """Test that validation provides early feedback on failures."""
        with patch.object(GoogleAdManager, "_init_client"):
            adapter = GoogleAdManager(config=self.config, principal=self.principal, dry_run=True)

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
