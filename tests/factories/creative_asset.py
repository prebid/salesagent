"""Pydantic schema factory for CreativeAsset (AdCP type).

Produces valid CreativeAsset objects with all required fields.
Used by creative sync tests instead of hand-crafted dicts.
"""

from __future__ import annotations

import factory
from adcp.types import CreativeAsset, FormatId

from tests.factories.format import AGENT_URL

# SDK 5.7: CreativeAsset.assets values must be lists of discriminated-union
# asset models with asset_type tag. Use this constant instead of inline dicts.
DEFAULT_IMAGE_ASSETS: dict = {
    "banner": [
        {
            "asset_type": "image",
            "asset_id": "banner",
            "item_type": "individual",
            "required": True,
            "url": "https://example.com/banner.png",
            "width": 300,
            "height": 250,
        }
    ]
}


def make_image_assets(
    asset_id: str = "banner",
    url: str = "https://example.com/banner.png",
    width: int = 300,
    height: int = 250,
) -> dict:
    """Build SDK 5.7 discriminated-union image assets dict with custom values."""
    return {
        asset_id: [
            {
                "asset_type": "image",
                "asset_id": asset_id,
                "item_type": "individual",
                "required": True,
                "url": url,
                "width": width,
                "height": height,
            }
        ]
    }


def make_video_assets(
    asset_id: str = "video",
    url: str = "https://example.com/video.mp4",
    width: int = 640,
    height: int = 360,
    **extra: object,
) -> dict:
    """Build SDK 5.7 discriminated-union video assets dict with custom values."""
    entry: dict = {
        "asset_type": "video",
        "asset_id": asset_id,
        "item_type": "individual",
        "required": True,
        "url": url,
        "width": width,
        "height": height,
    }
    entry.update(extra)
    return {asset_id: [entry]}


def make_text_assets(asset_id: str, content: str) -> dict:
    """Build SDK 5.7 discriminated-union text assets dict."""
    return {
        asset_id: [
            {
                "asset_type": "text",
                "asset_id": asset_id,
                "item_type": "individual",
                "required": True,
                "content": content,
            }
        ]
    }


def make_legacy_asset_dict(asset_id: str, **fields: object) -> dict:
    """Build a LEGACY (AdCP v1) single-dict asset entry: ``{asset_id: {**fields}}``.

    The v1 shape has NO ``asset_type`` discriminator and is NOT a list — it keys
    each role directly to a flat dict of fields (e.g. ``url``/``width``/``height``,
    ``url_type``, ``content``, ``duration_ms``). This is the shape the legacy
    adapter converter (``_convert_creative_to_adapter_asset``) consumes, and the
    shape that SDK 5.7's discriminated union rejects.

    Use this ONLY for legacy-input / negative tests that deliberately exercise the
    old shape. New or valid creative assets must use ``make_image_assets()`` /
    ``make_video_assets()`` / ``make_text_assets()`` (the SDK 5.7 list shape).
    Centralising the legacy shape here keeps it out of inline test dicts — see #1391.
    """
    return {asset_id: dict(fields)}


def make_legacy_image_assets(
    asset_id: str = "banner",
    url: str = "https://example.com/banner.png",
    width: int = 300,
    height: int = 250,
) -> dict:
    """Legacy v1 image-asset dict (no asset_type, not a list) for legacy/negative tests."""
    return make_legacy_asset_dict(asset_id, url=url, width=width, height=height)


def make_creative_asset_minimal(**extra: object) -> CreativeAsset:
    """Build a minimal CreativeAsset with optional extra fields.

    Shared helper for unit tests that need a bare-bones CreativeAsset
    (e.g. test_build_creative_data, test_extract_url_from_assets).
    """
    defaults: dict = {
        "creative_id": "test",
        "name": "test",
        "format_id": FormatId(id="banner", agent_url="http://agent.test"),
        "assets": {},
    }
    defaults.update(extra)
    return CreativeAsset(**defaults)


class CreativeAssetFactory(factory.Factory):
    """Factory for AdCP CreativeAsset Pydantic models.

    Produces valid objects with all required fields:
    creative_id, name, format_id, assets.
    """

    class Meta:
        model = CreativeAsset

    creative_id = factory.Sequence(lambda n: f"c_{n:04d}")
    name = factory.Sequence(lambda n: f"Test Creative {n}")
    format_id = factory.LazyFunction(lambda: FormatId(id="display_300x250_image", agent_url=AGENT_URL))
    assets = factory.LazyFunction(lambda: dict(DEFAULT_IMAGE_ASSETS))
