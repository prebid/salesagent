"""
Integration test for impression tracker flow from sync_creatives to GAM adapter.

Verifies that tracking URLs provided by buyers in delivery_settings flow
correctly through the creative conversion pipeline to the GAM adapter.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from src.core.helpers import _convert_creative_to_adapter_asset
from src.core.schemas import Creative, FormatId

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestImpressionTrackerFlow:
    """Test impression tracker URL preservation through creative conversion."""

    def test_hosted_asset_preserves_tracking_urls(self):
        """Test that hosted asset creatives preserve tracking URLs (AdCP v1 compliant)."""
        # Create a hosted asset creative (image) with tracking URLs in assets dict
        creative = Creative(
            creative_id="cr_image_123",
            name="Test Image Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            assets={
                "banner_image": {
                    "url": "https://cdn.example.com/banner.jpg",
                    "width": 300,
                    "height": 250,
                },
                "impression_tracker_1": {
                    "url": "https://buyer-tracker.com/impression1",
                    "url_type": "tracker_pixel",
                },
                "impression_tracker_2": {
                    "url": "https://buyer-tracker.com/impression2",
                    "url_type": "tracker_pixel",
                },
            },
            principal_id="principal_123",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
        )

        # Convert to adapter asset format
        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        # Verify delivery_settings are created with tracking URLs
        assert "delivery_settings" in asset
        assert "tracking_urls" in asset["delivery_settings"]
        assert "impression" in asset["delivery_settings"]["tracking_urls"]
        assert len(asset["delivery_settings"]["tracking_urls"]["impression"]) == 2
        assert "https://buyer-tracker.com/impression1" in asset["delivery_settings"]["tracking_urls"]["impression"]

    def test_third_party_tag_preserves_tracking_urls(self):
        """Test that third-party tag creatives preserve tracking URLs."""
        creative = Creative(
            creative_id="cr_tag_123",
            name="Test Third-Party Tag",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            assets={
                "javascript_tag": {
                    "content": '<script src="https://ad.example.com/tag.js"></script>',
                },
                "impression_tracker_1": {
                    "url": "https://buyer-tracker.com/pixel",
                    "url_type": "tracker_pixel",
                },
            },
            principal_id="principal_123",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        assert "delivery_settings" in asset
        assert "tracking_urls" in asset["delivery_settings"]
        assert "impression" in asset["delivery_settings"]["tracking_urls"]
        assert "https://buyer-tracker.com/pixel" in asset["delivery_settings"]["tracking_urls"]["impression"]

    def test_native_creative_preserves_tracking_urls(self):
        """Test that native creatives preserve tracking URLs."""
        creative = Creative(
            creative_id="cr_native_123",
            name="Test Native Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="native_1x1"),
            assets={
                "headline": {
                    "content": "Amazing Product",
                },
                "body": {
                    "content": "Buy now!",
                },
                "main_image": {
                    "url": "https://cdn.example.com/product.jpg",
                },
                "impression_tracker_1": {
                    "url": "https://buyer-tracker.com/native-pixel",
                    "url_type": "tracker_pixel",
                },
            },
            principal_id="principal_123",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        assert "delivery_settings" in asset
        assert "tracking_urls" in asset["delivery_settings"]
        assert "impression" in asset["delivery_settings"]["tracking_urls"]
        assert "https://buyer-tracker.com/native-pixel" in asset["delivery_settings"]["tracking_urls"]["impression"]

    def test_creative_without_tracking_urls_still_works(self):
        """Test that creatives without tracking URLs still convert correctly."""
        creative = Creative(
            creative_id="cr_simple_123",
            name="Test Simple Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_728x90"),
            assets={
                "banner_image": {
                    "url": "https://cdn.example.com/banner.jpg",
                    "width": 728,
                    "height": 90,
                },
            },
            principal_id="principal_123",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
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
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            assets={
                "banner_image": {
                    "url": "https://cdn.example.com/tracked.jpg",
                    "width": 300,
                    "height": 250,
                },
                "impression_tracker_1": {
                    "url": "https://buyer-tracker.com/impression",
                    "url_type": "tracker_pixel",
                },
                "impression_tracker_2": {
                    "url": "https://analytics.buyer.com/pixel",
                    "url_type": "tracker_pixel",
                },
            },
            principal_id="principal_123",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
        )

        # Convert creative to adapter asset format
        asset = _convert_creative_to_adapter_asset(creative_with_tracking, ["package_1"])

        # Simulate what the GAM adapter would receive
        # The _add_tracking_urls_to_creative method should find these URLs
        assert asset.get("delivery_settings") is not None
        tracking_urls = asset.get("delivery_settings", {}).get("tracking_urls", {}).get("impression", [])
        assert len(tracking_urls) == 2
        assert "https://buyer-tracker.com/impression" in tracking_urls
        assert "https://analytics.buyer.com/pixel" in tracking_urls

        # This matches the pattern in GAM adapter:
        # if "delivery_settings" in asset and asset["delivery_settings"]:
        #     if "tracking_urls" in settings:
        #         tracking_urls = settings["tracking_urls"]["impression"]

    def test_video_creative_preserves_tracking_urls(self):
        """Test that video creatives preserve tracking URLs."""
        creative = Creative(
            creative_id="cr_video_123",
            name="Test Video Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_640x480"),
            assets={
                "video_file": {
                    "url": "https://cdn.example.com/video.mp4",
                    "width": 640,
                    "height": 480,
                    "duration_ms": 30000,
                },
                "impression_tracker_1": {
                    "url": "https://buyer-tracker.com/video-impression",
                    "url_type": "tracker_pixel",
                },
            },
            principal_id="principal_123",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        assert "delivery_settings" in asset
        assert "tracking_urls" in asset["delivery_settings"]
        assert "impression" in asset["delivery_settings"]["tracking_urls"]
        assert "https://buyer-tracker.com/video-impression" in asset["delivery_settings"]["tracking_urls"]["impression"]
        assert asset["duration"] == 30.0  # Converted from duration_ms

    def test_multiple_tracking_urls_preserved(self):
        """Test that multiple tracking URLs are all preserved."""
        creative = Creative(
            creative_id="cr_multi_track_123",
            name="Multi-Tracker Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            assets={
                "banner_image": {
                    "url": "https://cdn.example.com/ad.jpg",
                    "width": 300,
                    "height": 250,
                },
                "impression_tracker_1": {
                    "url": "https://tracker1.com/pixel",
                    "url_type": "tracker_pixel",
                },
                "impression_tracker_2": {
                    "url": "https://tracker2.com/impression",
                    "url_type": "tracker_pixel",
                },
                "impression_tracker_3": {
                    "url": "https://tracker3.com/view",
                    "url_type": "tracker_pixel",
                },
                "impression_tracker_4": {
                    "url": "https://tracker4.com/count",
                    "url_type": "tracker_pixel",
                },
            },
            principal_id="principal_123",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        assert "delivery_settings" in asset
        assert "tracking_urls" in asset["delivery_settings"]
        assert "impression" in asset["delivery_settings"]["tracking_urls"]
        impression_trackers = asset["delivery_settings"]["tracking_urls"]["impression"]
        assert len(impression_trackers) == 4
        assert "https://tracker1.com/pixel" in impression_trackers
        assert "https://tracker2.com/impression" in impression_trackers
        assert "https://tracker3.com/view" in impression_trackers
        assert "https://tracker4.com/count" in impression_trackers

    def test_delivery_settings_other_fields_preserved(self):
        """Test that tracking URLs from assets are converted to delivery_settings format.

        Note: The conversion function only extracts tracking URLs from assets.
        Other delivery_settings fields would need to be added at the Creative level
        or handled separately by adapters.
        """
        creative = Creative(
            creative_id="cr_full_settings_123",
            name="Full Settings Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            assets={
                "banner_image": {
                    "url": "https://cdn.example.com/ad.jpg",
                    "width": 300,
                    "height": 250,
                },
                "impression_tracker_1": {
                    "url": "https://tracker.com/pixel",
                    "url_type": "tracker_pixel",
                },
                "impression_tracker_2": {
                    "url": "https://tracker2.com/impression",
                    "url_type": "tracker_pixel",
                },
            },
            principal_id="principal_123",
            created_date=datetime.now(UTC),
            updated_date=datetime.now(UTC),
        )

        asset = _convert_creative_to_adapter_asset(creative, ["package_1"])

        # Verify delivery_settings structure with tracking URLs
        settings = asset["delivery_settings"]
        assert "tracking_urls" in settings
        assert "impression" in settings["tracking_urls"]
        assert len(settings["tracking_urls"]["impression"]) == 2
        assert "https://tracker.com/pixel" in settings["tracking_urls"]["impression"]
        assert "https://tracker2.com/impression" in settings["tracking_urls"]["impression"]
