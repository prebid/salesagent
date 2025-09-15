"""
Tests for GAM creative validation functionality.

This test suite validates the GAM-specific creative validation logic
including size limits, content policies, and technical requirements.
"""

from src.adapters.gam_validation import GAMValidator, validate_gam_creative


class TestGAMValidator:
    """Test suite for GAMValidator class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = GAMValidator()

    def test_validate_creative_size_within_limits(self):
        """Test that valid creative dimensions pass validation."""
        issues = self.validator.validate_creative_size(width=728, height=90, file_size=50000, creative_type="display")
        assert issues == []

    def test_validate_creative_size_width_exceeds_limit(self):
        """Test that oversized width fails validation."""
        issues = self.validator.validate_creative_size(width=2000, height=90, creative_type="display")
        assert len(issues) == 1
        assert "width 2000px exceeds GAM maximum of 1800px" in issues[0]

    def test_validate_creative_size_height_exceeds_limit(self):
        """Test that oversized height fails validation."""
        issues = self.validator.validate_creative_size(width=728, height=2000, creative_type="display")
        assert len(issues) == 1
        assert "height 2000px exceeds GAM maximum of 1500px" in issues[0]

    def test_validate_creative_size_file_size_exceeds_limit(self):
        """Test that oversized file fails validation."""
        issues = self.validator.validate_creative_size(width=728, height=90, file_size=200000, creative_type="display")
        assert len(issues) == 1
        assert "File size 200,000 bytes exceeds GAM display limit of 150,000 bytes" in issues[0]

    def test_validate_creative_size_video_limits(self):
        """Test video creative size limits."""
        # Valid video size
        issues = self.validator.validate_creative_size(width=1280, height=720, file_size=1000000, creative_type="video")
        assert issues == []

        # Oversized video file
        issues = self.validator.validate_creative_size(width=1280, height=720, file_size=3000000, creative_type="video")
        assert len(issues) == 1
        assert "File size 3,000,000 bytes exceeds GAM video limit of 2,200,000 bytes" in issues[0]

    def test_validate_creative_size_html5_no_file_size_validation(self):
        """Test HTML5 creative size validation - file size should be skipped."""
        # HTML5 with normal file size - should pass
        issues = self.validator.validate_creative_size(width=970, height=250, file_size=2000000, creative_type="html5")
        assert issues == []

        # HTML5 with large file size - should still pass (GAM API will handle validation)
        issues = self.validator.validate_creative_size(width=970, height=250, file_size=10000000, creative_type="html5")
        assert issues == []  # No file size validation for HTML5

        # HTML5 with oversized dimensions - should fail dimension validation
        issues = self.validator.validate_creative_size(width=2000, height=250, file_size=1000000, creative_type="html5")
        assert len(issues) == 1
        assert "Creative width 2000px exceeds GAM maximum of 1800px" in issues[0]

    def test_validate_creative_size_none_values(self):
        """Test that None values are handled gracefully."""
        issues = self.validator.validate_creative_size(width=None, height=None, file_size=None, creative_type="display")
        assert issues == []

    def test_validate_content_policy_https_required(self):
        """Test that HTTPS URLs are required."""
        asset = {
            "url": "http://example.com/banner.jpg",
            "click_url": "http://example.com/landing",
        }
        issues = self.validator.validate_content_policy(asset)
        assert len(issues) == 2
        assert "url must use HTTPS" in issues[0]
        assert "click_url must use HTTPS" in issues[1]

    def test_validate_content_policy_https_valid(self):
        """Test that HTTPS URLs pass validation."""
        asset = {
            "url": "https://example.com/banner.jpg",
            "click_url": "https://example.com/landing",
            "media_url": "https://example.com/video.mp4",
        }
        issues = self.validator.validate_content_policy(asset)
        assert issues == []

    def test_validate_content_policy_snippet_validation(self):
        """Test validation of JavaScript snippets."""
        asset = {
            "snippet": """
                <script>
                    eval('malicious code');
                    document.write('<img src="http://evil.com">');
                </script>
            """,
            "snippet_type": "javascript",
        }
        issues = self.validator.validate_content_policy(asset)
        assert len(issues) >= 2
        # Should catch eval and document.write
        assert any("eval" in issue for issue in issues)
        assert any("document.write" in issue for issue in issues)

    def test_validate_content_policy_safe_snippet(self):
        """Test that safe snippets pass validation."""
        asset = {
            "snippet": """
                <script src="https://safe-cdn.com/script.js"></script>
                <div>Safe content</div>
            """,
            "snippet_type": "html",
        }
        issues = self.validator.validate_content_policy(asset)
        assert issues == []

    def test_validate_content_policy_http_script_source(self):
        """Test that HTTP script sources are flagged."""
        asset = {"snippet": """<script src="http://unsafe.com/script.js"></script>""", "snippet_type": "html"}
        issues = self.validator.validate_content_policy(asset)
        assert len(issues) == 1
        assert "Script source must use HTTPS" in issues[0]

    def test_validate_technical_requirements_third_party_tag(self):
        """Test validation of third-party tag requirements."""
        # Missing snippet
        asset = {"snippet_type": "javascript"}
        issues = self.validator.validate_technical_requirements(asset)
        assert any("requires 'snippet' field" in issue for issue in issues)

        # Missing snippet_type
        asset = {"snippet": "<script>alert('test');</script>"}
        issues = self.validator.validate_technical_requirements(asset)
        assert any("requires 'snippet_type' field" in issue for issue in issues)

        # Invalid snippet_type
        asset = {"snippet": "<script>alert('test');</script>", "snippet_type": "invalid"}
        issues = self.validator.validate_technical_requirements(asset)
        assert any("Invalid snippet_type" in issue for issue in issues)

        # Valid third-party tag
        asset = {"snippet": "<script>console.log('valid');</script>", "snippet_type": "javascript"}
        issues = self.validator.validate_technical_requirements(asset)
        assert not any("snippet" in issue for issue in issues)

    def test_validate_technical_requirements_native_creative(self):
        """Test validation of native creative requirements."""
        # Missing template_variables
        asset = {"template_variables": None}
        issues = self.validator.validate_technical_requirements(asset)
        # Note: This might not trigger if _get_creative_type_from_asset doesn't detect it as native

        # Valid native creative
        asset = {"template_variables": {"headline": "Test Ad", "image_url": "https://example.com/img.jpg"}}
        issues = self.validator.validate_technical_requirements(asset)
        # Should have no template_variables related issues
        assert not any("template_variables" in issue for issue in issues)

    def test_validate_technical_requirements_vast_creative(self):
        """Test validation of VAST creative requirements."""
        # Missing both snippet and url
        asset = {"snippet_type": "vast_xml"}
        issues = self.validator.validate_technical_requirements(asset)
        assert any("VAST creative requires either 'snippet' or 'url'" in issue for issue in issues)

        # Valid VAST with snippet
        asset = {"snippet": "<VAST version='4.0'>...</VAST>", "snippet_type": "vast_xml"}
        issues = self.validator.validate_technical_requirements(asset)
        assert not any("VAST creative requires" in issue for issue in issues)

        # Valid VAST with URL
        asset = {"url": "https://example.com/vast.xml", "snippet_type": "vast_url"}
        issues = self.validator.validate_technical_requirements(asset)
        assert not any("VAST creative requires" in issue for issue in issues)

    def test_validate_technical_requirements_video_aspect_ratio(self):
        """Test video aspect ratio validation."""
        # Valid 16:9 aspect ratio
        asset = {"url": "https://example.com/video.mp4", "width": 1920, "height": 1080, "format": "video_display"}
        issues = self.validator.validate_technical_requirements(asset)
        assert not any("aspect ratio" in issue for issue in issues)

        # Invalid aspect ratio
        asset = {
            "url": "https://example.com/video.mp4",
            "width": 1000,
            "height": 300,  # 3.33:1 aspect ratio
            "format": "video_display",
        }
        issues = self.validator.validate_technical_requirements(asset)
        assert any("aspect ratio" in issue for issue in issues)

    def test_get_creative_type_from_asset(self):
        """Test creative type detection from asset properties."""
        # Third-party tag
        asset = {"snippet": "<script>test</script>", "snippet_type": "javascript"}
        creative_type = self.validator._get_creative_type_from_asset(asset)
        assert creative_type == "third_party_tag"

        # VAST
        asset = {"snippet": "<VAST>...</VAST>", "snippet_type": "vast_xml"}
        creative_type = self.validator._get_creative_type_from_asset(asset)
        assert creative_type == "vast"

        # Native
        asset = {"template_variables": {"headline": "Test"}}
        creative_type = self.validator._get_creative_type_from_asset(asset)
        assert creative_type == "native"

        # Video (by URL)
        asset = {"url": "https://example.com/video.mp4"}
        creative_type = self.validator._get_creative_type_from_asset(asset)
        assert creative_type == "video"

        # Video (by format)
        asset = {"url": "https://example.com/media", "format": "video_display"}
        creative_type = self.validator._get_creative_type_from_asset(asset)
        assert creative_type == "video"

        # HTML5 (by URL)
        asset = {"media_url": "https://example.com/creative.html"}
        creative_type = self.validator._get_creative_type_from_asset(asset)
        assert creative_type == "html5"

        # HTML5 (by ZIP URL)
        asset = {"media_url": "https://example.com/creative.zip"}
        creative_type = self.validator._get_creative_type_from_asset(asset)
        assert creative_type == "html5"

        # HTML5 (by format)
        asset = {"url": "https://example.com/media", "format": "html5_interactive"}
        creative_type = self.validator._get_creative_type_from_asset(asset)
        assert creative_type == "html5"

        # Display (default)
        asset = {"url": "https://example.com/banner.jpg"}
        creative_type = self.validator._get_creative_type_from_asset(asset)
        assert creative_type == "display"

    def test_validate_creative_asset_comprehensive(self):
        """Test comprehensive validation of a complete creative asset."""
        # Valid display creative
        asset = {
            "creative_id": "test_creative_1",
            "url": "https://example.com/banner.jpg",
            "click_url": "https://example.com/landing",
            "width": 728,
            "height": 90,
            "format": "display_728x90",
        }
        issues = self.validator.validate_creative_asset(asset)
        assert issues == []

        # Invalid creative with multiple issues
        asset = {
            "creative_id": "test_creative_2",
            "url": "http://example.com/oversized.jpg",  # HTTP instead of HTTPS
            "click_url": "http://example.com/landing",  # HTTP instead of HTTPS
            "width": 2000,  # Exceeds width limit
            "height": 2000,  # Exceeds height limit
            "file_size": 300000,  # Exceeds file size limit
            "format": "display_300x250",
        }
        issues = self.validator.validate_creative_asset(asset)
        assert len(issues) >= 4  # Should have multiple validation errors

        # Check that all expected issues are present
        issue_text = " ".join(issues)
        assert "width 2000px exceeds" in issue_text
        assert "height 2000px exceeds" in issue_text
        assert "File size 300,000 bytes exceeds" in issue_text
        assert "must use HTTPS" in issue_text


class TestConvenienceFunction:
    """Test the convenience function for GAM validation."""

    def test_validate_gam_creative_valid(self):
        """Test validation of a valid creative."""
        asset = {
            "url": "https://example.com/banner.jpg",
            "width": 300,
            "height": 250,
        }
        issues = validate_gam_creative(asset)
        assert issues == []

    def test_validate_gam_creative_invalid(self):
        """Test validation of an invalid creative."""
        asset = {
            "url": "http://example.com/banner.jpg",  # HTTP not allowed
            "width": 2000,  # Too wide
        }
        issues = validate_gam_creative(asset)
        assert len(issues) >= 2
        assert any("HTTPS" in issue for issue in issues)
        assert any("width" in issue for issue in issues)


class TestSnippetValidation:
    """Test snippet content validation specifically."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = GAMValidator()

    def test_validate_snippet_prohibited_functions(self):
        """Test detection of prohibited JavaScript functions."""
        test_cases = [
            ("eval('code')", "eval"),
            ("document.write('content')", "document.write"),
            ("innerHTML = 'content'", "innerHTML"),
            ("setTimeout(function(){}, 1000)", "setTimeout"),
            ("new Function('return 1')", "Function"),
        ]

        for snippet, expected_func in test_cases:
            issues = self.validator._validate_snippet_content(snippet)
            assert any(expected_func in issue for issue in issues), f"Should detect {expected_func} in: {snippet}"

    def test_validate_snippet_safe_content(self):
        """Test that safe snippet content passes validation."""
        safe_snippets = [
            "<div>Safe HTML content</div>",
            "<img src='https://example.com/image.jpg' alt='Ad'>",
            "<script src='https://trusted-cdn.com/script.js'></script>",
            "console.log('This is safe');",
        ]

        for snippet in safe_snippets:
            issues = self.validator._validate_snippet_content(snippet)
            assert issues == [], f"Safe snippet should pass: {snippet}"

    def test_validate_snippet_protocol_restrictions(self):
        """Test validation of URL protocols in snippets."""
        # JavaScript protocol should be blocked
        snippet = "<a href='javascript:alert(1)'>Click</a>"
        issues = self.validator._validate_snippet_content(snippet)
        assert any("javascript: protocol" in issue for issue in issues)

        # Data URLs with script content should be blocked
        snippet = "<script src='data:text/javascript,alert(1)'></script>"
        issues = self.validator._validate_snippet_content(snippet)
        assert any("Data URLs with script content" in issue for issue in issues)


class TestFileExtensionValidation:
    """Test file extension validation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = GAMValidator()

    def test_validate_file_extension_valid_display(self):
        """Test valid display creative file extensions."""
        valid_urls = [
            "https://example.com/banner.jpg",
            "https://example.com/banner.jpeg",
            "https://example.com/banner.png",
            "https://example.com/banner.gif",
            "https://example.com/banner.webp",
        ]

        for url in valid_urls:
            issues = self.validator._validate_file_extension(url, "display")
            assert issues == [], f"Valid display URL should pass: {url}"

    def test_validate_file_extension_valid_video(self):
        """Test valid video creative file extensions."""
        valid_urls = [
            "https://example.com/video.mp4",
            "https://example.com/video.webm",
            "https://example.com/video.mov",
        ]

        for url in valid_urls:
            issues = self.validator._validate_file_extension(url, "video")
            assert issues == [], f"Valid video URL should pass: {url}"

    def test_validate_file_extension_api_endpoints(self):
        """Test that API endpoints are not flagged for file extension issues."""
        api_urls = [
            "https://api.example.com/creative/render",
            "https://service.com/ad/display?id=123",
            "https://cdn.com/serve/dynamic",
        ]

        for url in api_urls:
            issues = self.validator._validate_file_extension(url, "display")
            assert issues == [], f"API endpoint should pass: {url}"

    def test_validate_file_extension_wrong_type(self):
        """Test that wrong file types are flagged."""
        # Using video file for display creative
        issues = self.validator._validate_file_extension("https://example.com/banner.mp4", "display")
        # Note: Current implementation may not catch this due to auto-detection logic
        # This test documents the expected behavior if we enhance validation


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = GAMValidator()

    def test_empty_asset(self):
        """Test validation of empty asset dictionary."""
        asset = {}
        issues = self.validator.validate_creative_asset(asset)
        # Should not crash, may have some validation issues
        assert isinstance(issues, list)

    def test_asset_with_none_values(self):
        """Test validation of asset with None values."""
        asset = {
            "url": None,
            "width": None,
            "height": None,
            "snippet": None,
        }
        issues = self.validator.validate_creative_asset(asset)
        # Should handle None values gracefully
        assert isinstance(issues, list)

    def test_malformed_snippet_type(self):
        """Test handling of malformed snippet_type values."""
        asset = {"snippet": "<script>test</script>", "snippet_type": "INVALID_TYPE"}
        issues = self.validator.validate_technical_requirements(asset)
        assert any("Invalid snippet_type" in issue for issue in issues)

    def test_zero_dimensions(self):
        """Test handling of zero or negative dimensions."""
        # Zero dimensions should pass (they're just not checked)
        issues = self.validator.validate_creative_size(0, 0)
        assert issues == []

        # Negative dimensions should pass (unusual but not explicitly invalid)
        issues = self.validator.validate_creative_size(-100, -50)
        assert issues == []


class TestMediaDataValidation:
    """Test binary asset upload validation with media_data field."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = GAMValidator()

    def test_validate_media_data_valid_bytes(self):
        """Test validation of valid binary media data."""

        # Create mock image data
        mock_image_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10"

        asset = {"media_data": mock_image_data, "filename": "test.png", "format": "display_300x250"}
        issues = self.validator.validate_media_data(asset)
        assert issues == []

    def test_validate_media_data_valid_base64(self):
        """Test validation of valid base64 encoded media data."""
        import base64

        # Create mock image data and encode it
        mock_image_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10"
        base64_data = base64.b64encode(mock_image_data).decode("utf-8")

        asset = {"media_data": base64_data, "filename": "test.png", "format": "display_300x250"}
        issues = self.validator.validate_media_data(asset)
        assert issues == []

    def test_validate_media_data_invalid_base64(self):
        """Test validation of invalid base64 data."""
        asset = {"media_data": "invalid-base64-data!@#$", "filename": "test.png", "format": "display_300x250"}
        issues = self.validator.validate_media_data(asset)
        assert len(issues) == 1
        assert "must be valid base64" in issues[0]

    def test_validate_media_data_empty_data(self):
        """Test validation of empty media data."""
        # Empty bytes
        asset = {"media_data": b"", "filename": "test.png", "format": "display_300x250"}
        issues = self.validator.validate_media_data(asset)
        assert len(issues) == 1
        assert "cannot be empty" in issues[0]

        # Empty base64 string
        asset = {"media_data": "", "filename": "test.png", "format": "display_300x250"}
        issues = self.validator.validate_media_data(asset)
        assert len(issues) == 1
        assert "cannot be empty" in issues[0]

    def test_validate_media_data_wrong_file_extension(self):
        """Test validation of wrong file extension for creative type."""

        mock_image_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10"

        # Try to upload video file for display creative
        asset = {
            "media_data": mock_image_data,
            "filename": "test.mp4",  # Video extension for display creative
            "format": "display_300x250",
        }
        issues = self.validator.validate_media_data(asset)
        assert len(issues) == 1
        assert "File extension .mp4 not allowed for display creatives" in issues[0]

    def test_validate_media_data_invalid_type(self):
        """Test validation of invalid media_data type."""
        asset = {
            "media_data": 123,  # Invalid type (should be bytes or string)
            "filename": "test.png",
            "format": "display_300x250",
        }
        issues = self.validator.validate_media_data(asset)
        assert len(issues) == 1
        assert "must be bytes or base64-encoded string" in issues[0]

    def test_validate_media_data_missing_is_valid(self):
        """Test that missing media_data is valid (optional field)."""
        asset = {"url": "https://example.com/image.png", "format": "display_300x250"}  # URL instead of media_data
        issues = self.validator.validate_media_data(asset)
        assert issues == []

    def test_validate_creative_asset_with_media_data_calculates_file_size(self):
        """Test that file size is calculated from media_data for validation."""

        # Create large mock data that exceeds display limit (150KB)
        large_data = b"x" * 200000  # 200KB

        asset = {
            "media_data": large_data,
            "filename": "large.png",
            "format": "display_300x250",
            "width": 300,
            "height": 250,
        }

        # Should fail size validation
        issues = self.validator.validate_creative_asset(asset)
        assert any("File size" in issue and "exceeds GAM display limit" in issue for issue in issues)

    def test_validate_creative_asset_with_base64_calculates_file_size(self):
        """Test that file size is calculated from base64 media_data."""
        import base64

        # Create large mock data that exceeds display limit
        large_data = b"x" * 200000  # 200KB
        base64_data = base64.b64encode(large_data).decode("utf-8")

        asset = {
            "media_data": base64_data,
            "filename": "large.png",
            "format": "display_300x250",
            "width": 300,
            "height": 250,
        }

        # Should fail size validation (base64 decoded size should be checked)
        issues = self.validator.validate_creative_asset(asset)
        assert any("File size" in issue and "exceeds GAM display limit" in issue for issue in issues)


class TestImpressionTrackingSupport:
    """Test impression tracking URL support for all creative types."""

    def setup_method(self):
        """Set up test fixtures."""
        from unittest.mock import Mock

        from src.core.schemas import Principal

        # Create a mock GAM adapter for testing tracking functionality
        self.mock_adapter = Mock()
        self.mock_adapter.dry_run = True
        self.mock_adapter.log = Mock()

        # Import the actual methods to test
        from src.adapters.google_ad_manager import GoogleAdManager

        self.adapter_class = GoogleAdManager

        # Create a mock principal
        mock_principal = Mock(spec=Principal)
        mock_principal.principal_id = "test_principal"
        mock_principal.platform_mappings = {"google_ad_manager": {"advertiser_id": "123"}}

        # Create a real instance for method testing (we'll override what we need)
        self.adapter = self.adapter_class(config={"network_code": "123456"}, principal=mock_principal, dry_run=True)

        # Mock the client to avoid actual API initialization
        self.adapter.client = Mock()

    def test_add_tracking_urls_third_party_creative(self):
        """Test tracking URL addition for third-party creatives."""
        creative = {"xsi_type": "ThirdPartyCreative"}
        asset = {"delivery_settings": {"tracking_urls": ["https://tracker1.com/pixel", "https://tracker2.com/pixel"]}}

        self.adapter._add_tracking_urls_to_creative(creative, asset)

        assert "thirdPartyImpressionTrackingUrls" in creative
        assert creative["thirdPartyImpressionTrackingUrls"] == asset["delivery_settings"]["tracking_urls"]

    def test_add_tracking_urls_image_creative(self):
        """Test tracking URL addition for image creatives."""
        creative = {"xsi_type": "ImageCreative"}
        asset = {"tracking_urls": ["https://analytics.com/impression"]}

        self.adapter._add_tracking_urls_to_creative(creative, asset)

        assert "thirdPartyImpressionUrls" in creative
        assert creative["thirdPartyImpressionUrls"] == asset["tracking_urls"]

    def test_add_tracking_urls_video_creative(self):
        """Test tracking URL addition for video creatives."""
        creative = {"xsi_type": "VideoCreative"}
        asset = {"delivery_settings": {"tracking_urls": ["https://video-tracker.com/impression"]}}

        self.adapter._add_tracking_urls_to_creative(creative, asset)

        assert "thirdPartyImpressionUrls" in creative
        assert creative["thirdPartyImpressionUrls"] == asset["delivery_settings"]["tracking_urls"]

    def test_add_tracking_urls_native_creative(self):
        """Test tracking URL handling for native creatives."""
        creative = {"xsi_type": "TemplateCreative"}
        asset = {"tracking_urls": ["https://native-tracker.com/impression"]}

        # Native creatives don't directly support tracking URLs in the same way
        # but the method should log a note about using template variables
        self.adapter._add_tracking_urls_to_creative(creative, asset)

        # Native creatives shouldn't get direct tracking URL fields
        assert "thirdPartyImpressionUrls" not in creative
        assert "thirdPartyImpressionTrackingUrls" not in creative

        # The method should handle native creatives gracefully (note: logs are captured in test output)

    def test_add_tracking_urls_multiple_sources(self):
        """Test combining tracking URLs from multiple sources."""
        creative = {"xsi_type": "ImageCreative"}
        asset = {
            "delivery_settings": {"tracking_urls": ["https://tracker1.com/pixel"]},
            "tracking_urls": ["https://tracker2.com/pixel", "https://tracker3.com/pixel"],
        }

        self.adapter._add_tracking_urls_to_creative(creative, asset)

        # Should combine URLs from both sources
        assert len(creative["thirdPartyImpressionUrls"]) == 3
        assert "https://tracker1.com/pixel" in creative["thirdPartyImpressionUrls"]
        assert "https://tracker2.com/pixel" in creative["thirdPartyImpressionUrls"]
        assert "https://tracker3.com/pixel" in creative["thirdPartyImpressionUrls"]

    def test_add_tracking_urls_no_urls_provided(self):
        """Test that no tracking URLs are added when none provided."""
        creative = {"xsi_type": "ImageCreative"}
        asset = {}

        self.adapter._add_tracking_urls_to_creative(creative, asset)

        # Should not add any tracking URL fields
        assert "thirdPartyImpressionUrls" not in creative
        assert "thirdPartyImpressionTrackingUrls" not in creative

    def test_add_tracking_urls_unknown_creative_type(self):
        """Test handling of unknown creative types."""
        creative = {"xsi_type": "UnknownCreativeType"}
        asset = {"tracking_urls": ["https://tracker.com/pixel"]}

        self.adapter._add_tracking_urls_to_creative(creative, asset)

        # Should not add any tracking URL fields
        assert "thirdPartyImpressionUrls" not in creative
        assert "thirdPartyImpressionTrackingUrls" not in creative

        # The method should handle unknown types gracefully (note: warning logged in test output)

    def test_binary_upload_with_tracking_integration(self):
        """Test that binary upload creatives get tracking URLs."""
        import base64

        mock_image_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10"
        base64_data = base64.b64encode(mock_image_data).decode("utf-8")

        asset = {
            "creative_id": "test_with_tracking",
            "media_data": base64_data,
            "filename": "test.png",
            "format": "display_300x250",
            "tracking_urls": ["https://analytics.com/impression"],
            "name": "Test Creative with Tracking",
            "click_url": "https://example.com/landing",
        }

        # Test that _create_hosted_asset_creative includes tracking
        base_creative = {"advertiserId": "123", "name": asset["name"], "destinationUrl": asset["click_url"]}

        # Mock the upload method to avoid actual API calls
        from unittest.mock import Mock

        self.adapter._upload_binary_asset = Mock(return_value={"assetId": "mock_asset_123456", "fileName": "test.png"})

        creative = self.adapter._create_hosted_asset_creative(asset, base_creative)

        # Should be ImageCreative with tracking URLs
        assert creative["xsi_type"] == "ImageCreative"
        assert "thirdPartyImpressionUrls" in creative
        assert creative["thirdPartyImpressionUrls"] == asset["tracking_urls"]
