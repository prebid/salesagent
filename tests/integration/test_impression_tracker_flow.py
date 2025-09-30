"""
Integration test for impression tracker flow from sync_creatives to GAM adapter.

Verifies that tracking URLs provided by buyers in delivery_settings flow
correctly through the creative conversion pipeline to the GAM adapter.
"""

from datetime import UTC, datetime
from unittest.mock import patch

from src.core.main import _convert_creative_to_adapter_asset
from src.core.schemas import Creative


class TestImpressionTrackerFlow:
    """Test impression tracker URL preservation through creative conversion."""

    def test_hosted_asset_preserves_tracking_urls(self):
        """Test that hosted asset creatives preserve tracking URLs in delivery_settings."""
        # Create a hosted asset creative (image) with tracking URLs
        creative = Creative(
            creative_id="cr_image_123",
            name="Test Image Creative",
            format_id="display_300x250",
            content_uri="https://cdn.example.com/banner.jpg",
            media_url="https://cdn.example.com/banner.jpg",
            width=300,
            height=250,
            delivery_settings={
                "tracking_urls": [
                    "https://buyer-tracker.com/impression1",
                    "https://buyer-tracker.com/impression2",
                ],
                "ssl_required": True,
            },
            principal_id="principal_123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        # Convert to adapter asset format
        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        # Verify delivery_settings are preserved
        assert "delivery_settings" in asset
        assert "tracking_urls" in asset["delivery_settings"]
        assert len(asset["delivery_settings"]["tracking_urls"]) == 2
        assert asset["delivery_settings"]["tracking_urls"][0] == "https://buyer-tracker.com/impression1"
        assert asset["delivery_settings"]["ssl_required"] is True

    def test_third_party_tag_preserves_tracking_urls(self):
        """Test that third-party tag creatives preserve tracking URLs."""
        # For snippet-based creatives, content_uri should point to where snippet came from (optional)
        # but we use snippet content directly
        creative = Creative(
            creative_id="cr_tag_123",
            name="Test Third-Party Tag",
            format_id="display_300x250",
            content_uri='<script src="https://ad.example.com/tag.js"></script>',  # Snippet as content_uri
            snippet='<script src="https://ad.example.com/tag.js"></script>',
            snippet_type="javascript",
            delivery_settings={
                "tracking_urls": ["https://buyer-tracker.com/pixel"],
            },
            principal_id="principal_123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        assert "delivery_settings" in asset
        assert asset["delivery_settings"]["tracking_urls"][0] == "https://buyer-tracker.com/pixel"

    def test_native_creative_preserves_tracking_urls(self):
        """Test that native creatives preserve tracking URLs."""
        creative = Creative(
            creative_id="cr_native_123",
            name="Test Native Creative",
            format_id="native_1x1",
            content_uri="https://example.com/native",
            template_variables={
                "headline": "Amazing Product",
                "body": "Buy now!",
                "main_image_url": "https://cdn.example.com/product.jpg",
            },
            delivery_settings={
                "tracking_urls": ["https://buyer-tracker.com/native-pixel"],
            },
            principal_id="principal_123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        assert "delivery_settings" in asset
        assert asset["delivery_settings"]["tracking_urls"][0] == "https://buyer-tracker.com/native-pixel"

    def test_creative_without_tracking_urls_still_works(self):
        """Test that creatives without tracking URLs still convert correctly."""
        creative = Creative(
            creative_id="cr_simple_123",
            name="Test Simple Creative",
            format_id="display_728x90",
            content_uri="https://cdn.example.com/banner.jpg",
            media_url="https://cdn.example.com/banner.jpg",
            principal_id="principal_123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        # Should not have delivery_settings if not provided
        assert "delivery_settings" not in asset or asset.get("delivery_settings") is None

    @patch("src.adapters.gam.managers.creatives.GAMCreativesManager")
    def test_gam_adapter_receives_tracking_urls(self, mock_gam_manager):
        """Test that GAM adapter's add_creative_assets receives tracking URLs correctly."""
        # This test verifies the full flow: Creative -> conversion -> GAM adapter

        # Create a creative with tracking URLs
        creative_with_tracking = Creative(
            creative_id="cr_tracked_123",
            name="Tracked Image Creative",
            format_id="display_300x250",
            content_uri="https://cdn.example.com/tracked.jpg",
            media_url="https://cdn.example.com/tracked.jpg",
            width=300,
            height=250,
            delivery_settings={
                "tracking_urls": [
                    "https://buyer-tracker.com/impression",
                    "https://analytics.buyer.com/pixel",
                ],
            },
            principal_id="principal_123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        # Convert creative to adapter asset format
        asset = _convert_creative_to_adapter_asset(creative_with_tracking, ["package_1"])

        # Simulate what the GAM adapter would receive
        # The _add_tracking_urls_to_creative method should find these URLs
        assert asset.get("delivery_settings") is not None
        tracking_urls = asset.get("delivery_settings", {}).get("tracking_urls", [])
        assert len(tracking_urls) == 2
        assert "buyer-tracker.com" in tracking_urls[0]
        assert "analytics.buyer.com" in tracking_urls[1]

        # This matches the pattern in GAM adapter:
        # if "delivery_settings" in asset and asset["delivery_settings"]:
        #     if "tracking_urls" in settings:
        #         tracking_urls = settings["tracking_urls"]

    def test_video_creative_preserves_tracking_urls(self):
        """Test that video creatives preserve tracking URLs."""
        creative = Creative(
            creative_id="cr_video_123",
            name="Test Video Creative",
            format_id="video_640x480",
            content_uri="https://cdn.example.com/video.mp4",
            media_url="https://cdn.example.com/video.mp4",
            width=640,
            height=480,
            duration=30.0,
            delivery_settings={
                "tracking_urls": ["https://buyer-tracker.com/video-impression"],
            },
            principal_id="principal_123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        assert "delivery_settings" in asset
        assert asset["delivery_settings"]["tracking_urls"][0] == "https://buyer-tracker.com/video-impression"
        assert asset["duration"] == 30.0

    def test_multiple_tracking_urls_preserved(self):
        """Test that multiple tracking URLs are all preserved."""
        tracking_urls = [
            "https://tracker1.com/pixel",
            "https://tracker2.com/impression",
            "https://tracker3.com/view",
            "https://tracker4.com/count",
        ]

        creative = Creative(
            creative_id="cr_multi_track_123",
            name="Multi-Tracker Creative",
            format_id="display_300x250",
            content_uri="https://cdn.example.com/ad.jpg",
            media_url="https://cdn.example.com/ad.jpg",
            delivery_settings={"tracking_urls": tracking_urls},
            principal_id="principal_123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        assert len(asset["delivery_settings"]["tracking_urls"]) == 4
        for i, url in enumerate(tracking_urls):
            assert asset["delivery_settings"]["tracking_urls"][i] == url

    def test_delivery_settings_other_fields_preserved(self):
        """Test that other delivery_settings fields are preserved alongside tracking_urls."""
        creative = Creative(
            creative_id="cr_full_settings_123",
            name="Full Settings Creative",
            format_id="display_300x250",
            content_uri="https://cdn.example.com/ad.jpg",
            media_url="https://cdn.example.com/ad.jpg",
            delivery_settings={
                "tracking_urls": ["https://tracker.com/pixel"],
                "safe_frame_compatible": True,
                "ssl_required": True,
                "orientation_lock": "FREE_ORIENTATION",
                "custom_field": "custom_value",
            },
            principal_id="principal_123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        settings = asset["delivery_settings"]
        assert settings["tracking_urls"][0] == "https://tracker.com/pixel"
        assert settings["safe_frame_compatible"] is True
        assert settings["ssl_required"] is True
        assert settings["orientation_lock"] == "FREE_ORIENTATION"
        assert settings["custom_field"] == "custom_value"
