"""Tests for _extract_url_from_assets helper.

Verifies URL extraction priority: top-level url > named asset keys
(main, image, video, creative, content) > first available asset URL.

Beads: salesagent-dmn
"""

from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from adcp.types.generated_poc.core.format_id import FormatId

from src.core.tools.creatives import _extract_url_from_assets

_FMT = FormatId(id="banner", agent_url="http://agent.test")


def _make_creative(**extra: object) -> CreativeAsset:
    """Build a minimal CreativeAsset with optional extra fields."""
    defaults: dict = {"creative_id": "test", "name": "test", "format_id": _FMT, "assets": {}}
    defaults.update(extra)
    return CreativeAsset(**defaults)


class TestTopLevelUrl:
    """Top-level url field takes precedence."""

    def test_returns_top_level_url(self):
        assert (
            _extract_url_from_assets(_make_creative(url="https://example.com/ad.png")) == "https://example.com/ad.png"
        )

    def test_top_level_url_beats_assets(self):
        creative = _make_creative(
            url="https://top.com/ad.png",
            assets={"main": {"url": "https://asset.com/ad.png"}},
        )
        assert _extract_url_from_assets(creative) == "https://top.com/ad.png"


class TestPriorityKeys:
    """Named asset keys are tried in priority order."""

    def test_main_key(self):
        creative = _make_creative(assets={"main": {"url": "https://example.com/main.png"}})
        assert _extract_url_from_assets(creative) == "https://example.com/main.png"

    def test_image_key(self):
        creative = _make_creative(assets={"image": {"url": "https://example.com/image.png"}})
        assert _extract_url_from_assets(creative) == "https://example.com/image.png"

    def test_video_key(self):
        creative = _make_creative(assets={"video": {"url": "https://example.com/video.mp4"}})
        assert _extract_url_from_assets(creative) == "https://example.com/video.mp4"

    def test_main_beats_image(self):
        creative = _make_creative(
            assets={
                "image": {"url": "https://example.com/image.png"},
                "main": {"url": "https://example.com/main.png"},
            },
        )
        assert _extract_url_from_assets(creative) == "https://example.com/main.png"


class TestFallback:
    """Falls back to first available asset URL."""

    def test_unknown_key_fallback(self):
        creative = _make_creative(assets={"custom_banner": {"url": "https://example.com/banner.png"}})
        assert _extract_url_from_assets(creative) == "https://example.com/banner.png"


class TestNoUrl:
    """Returns None when no URL can be extracted."""

    def test_no_url_empty_assets(self):
        assert _extract_url_from_assets(_make_creative()) is None

    def test_empty_assets(self):
        assert _extract_url_from_assets(_make_creative(assets={})) is None
