"""Tests for _extract_url_from_assets helper.

Verifies URL extraction priority: top-level url > named asset keys
(main, image, video, creative, content) > first available asset URL.

Beads: salesagent-dmn
"""

from src.core.tools.creatives import _extract_url_from_assets


class TestTopLevelUrl:
    """Top-level url field takes precedence."""

    def test_returns_top_level_url(self):
        assert _extract_url_from_assets({"url": "https://example.com/ad.png"}) == "https://example.com/ad.png"

    def test_top_level_url_beats_assets(self):
        creative = {
            "url": "https://top.com/ad.png",
            "assets": {"main": {"url": "https://asset.com/ad.png"}},
        }
        assert _extract_url_from_assets(creative) == "https://top.com/ad.png"


class TestPriorityKeys:
    """Named asset keys are tried in priority order."""

    def test_main_key(self):
        creative = {"assets": {"main": {"url": "https://example.com/main.png"}}}
        assert _extract_url_from_assets(creative) == "https://example.com/main.png"

    def test_image_key(self):
        creative = {"assets": {"image": {"url": "https://example.com/image.png"}}}
        assert _extract_url_from_assets(creative) == "https://example.com/image.png"

    def test_video_key(self):
        creative = {"assets": {"video": {"url": "https://example.com/video.mp4"}}}
        assert _extract_url_from_assets(creative) == "https://example.com/video.mp4"

    def test_main_beats_image(self):
        creative = {
            "assets": {
                "image": {"url": "https://example.com/image.png"},
                "main": {"url": "https://example.com/main.png"},
            }
        }
        assert _extract_url_from_assets(creative) == "https://example.com/main.png"

    def test_skips_non_dict_asset(self):
        """Non-dict asset values are skipped."""
        creative = {"assets": {"main": "not-a-dict", "image": {"url": "https://example.com/image.png"}}}
        assert _extract_url_from_assets(creative) == "https://example.com/image.png"

    def test_skips_asset_without_url(self):
        creative = {"assets": {"main": {"width": 300}, "image": {"url": "https://example.com/image.png"}}}
        assert _extract_url_from_assets(creative) == "https://example.com/image.png"


class TestFallback:
    """Falls back to first available asset URL."""

    def test_unknown_key_fallback(self):
        creative = {"assets": {"custom_banner": {"url": "https://example.com/banner.png"}}}
        assert _extract_url_from_assets(creative) == "https://example.com/banner.png"

    def test_skips_non_dict_in_fallback(self):
        creative = {"assets": {"banner": "string-value", "ad": {"url": "https://example.com/ad.png"}}}
        assert _extract_url_from_assets(creative) == "https://example.com/ad.png"


class TestNoUrl:
    """Returns None when no URL can be extracted."""

    def test_empty_dict(self):
        assert _extract_url_from_assets({}) is None

    def test_no_url_no_assets(self):
        assert _extract_url_from_assets({"name": "test"}) is None

    def test_empty_assets(self):
        assert _extract_url_from_assets({"assets": {}}) is None

    def test_assets_without_urls(self):
        creative = {"assets": {"main": {"width": 300}, "image": {"height": 250}}}
        assert _extract_url_from_assets(creative) is None
