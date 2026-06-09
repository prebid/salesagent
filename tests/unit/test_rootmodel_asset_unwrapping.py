"""Tests for RootModel asset unwrapping in creative asset helpers.

SDK 5.7 wraps creative asset slot values in Assets (a RootModel) containing
list[AssetVariant] items. The extraction helpers must unwrap these layers
to reach the concrete typed asset (ImageAsset, TextAsset, etc.).

These tests verify that both _extract_url_from_asset_value and
_extract_text_from_asset_value handle:
  1. SDK 5.7 RootModel-wrapped assets
  2. Plain dicts (backward compat)
  3. None/empty gracefully

beads: salesagent-6xt9
"""

from adcp.types import CreativeAsset

from src.core.tools.creatives._assets import _extract_text_from_asset_value, _extract_url_from_asset_value
from tests.factories.creative_asset import build_assets, image_spec, text_spec, video_spec


def _build_creative_with_assets(assets_dict: dict) -> CreativeAsset:
    """Build a CreativeAsset with the given assets dict.

    The SDK parses the raw dict into Assets RootModel wrappers automatically.
    """
    return CreativeAsset(
        creative_id="c1",
        name="test",
        format_id={"id": "f1", "agent_url": "http://test"},
        assets=assets_dict,
    )


class TestExtractUrlFromAssetValueRootModel:
    """_extract_url_from_asset_value handles SDK 5.7 RootModel-wrapped assets."""

    def test_rootmodel_image_asset_extracts_url(self):
        """URL is extracted from an ImageAsset wrapped in Assets RootModel."""
        ca = _build_creative_with_assets(
            build_assets(image_spec("img", url="https://example.com/image.png", multiple=True))
        )
        asset = ca.assets["img"]

        # Confirm this is actually a RootModel wrapper (not a plain dict)
        assert hasattr(asset, "root"), "SDK should wrap asset in Assets RootModel"

        result = _extract_url_from_asset_value(asset)
        assert result == "https://example.com/image.png"

    def test_rootmodel_video_asset_extracts_url(self):
        """URL is extracted from a VideoAsset wrapped in Assets RootModel."""
        ca = _build_creative_with_assets(
            build_assets(
                video_spec(
                    "vid", url="https://example.com/video.mp4", width=1920, height=1080, multiple=True, duration=30.0
                )
            )
        )
        asset = ca.assets["vid"]
        assert hasattr(asset, "root")

        result = _extract_url_from_asset_value(asset)
        assert result == "https://example.com/video.mp4"


class TestExtractUrlFromAssetValueDict:
    """_extract_url_from_asset_value handles plain dicts (backward compat)."""

    def test_plain_dict_with_url(self):
        """Plain dict with 'url' key returns the URL."""
        result = _extract_url_from_asset_value({"url": "https://example.com/img.png"})
        assert result == "https://example.com/img.png"

    def test_plain_dict_without_url(self):
        """Plain dict without 'url' key returns None."""
        result = _extract_url_from_asset_value({"content": "text only"})
        assert result is None


class TestExtractUrlFromAssetValueEdgeCases:
    """_extract_url_from_asset_value handles None/empty gracefully."""

    def test_none_returns_none(self):
        """None input returns None."""
        result = _extract_url_from_asset_value(None)
        assert result is None

    def test_empty_dict_returns_none(self):
        """Empty dict returns None."""
        result = _extract_url_from_asset_value({})
        assert result is None


class TestExtractTextFromAssetValueRootModel:
    """_extract_text_from_asset_value handles SDK 5.7 RootModel-wrapped assets."""

    def test_rootmodel_text_asset_extracts_content(self):
        """Text content is extracted from a TextAsset wrapped in Assets RootModel."""
        ca = _build_creative_with_assets(build_assets(text_spec("message", content="Hello world", multiple=True)))
        asset = ca.assets["message"]

        # Confirm this is actually a RootModel wrapper
        assert hasattr(asset, "root"), "SDK should wrap asset in Assets RootModel"

        result = _extract_text_from_asset_value(asset)
        assert result == "Hello world"


class TestExtractTextFromAssetValueDict:
    """_extract_text_from_asset_value handles plain dicts (backward compat)."""

    def test_plain_dict_with_content(self):
        """Plain dict with 'content' key returns the content."""
        result = _extract_text_from_asset_value({"content": "Hello from dict"})
        assert result == "Hello from dict"

    def test_plain_dict_with_text(self):
        """Plain dict with 'text' key returns the text."""
        result = _extract_text_from_asset_value({"text": "Hello from text key"})
        assert result == "Hello from text key"

    def test_plain_dict_without_text_or_content(self):
        """Plain dict with neither key returns None."""
        result = _extract_text_from_asset_value({"url": "https://example.com"})
        assert result is None


class TestExtractTextFromAssetValueEdgeCases:
    """_extract_text_from_asset_value handles None/empty gracefully."""

    def test_none_returns_none(self):
        """None input returns None."""
        result = _extract_text_from_asset_value(None)
        assert result is None

    def test_empty_dict_returns_none(self):
        """Empty dict returns None."""
        result = _extract_text_from_asset_value({})
        assert result is None
