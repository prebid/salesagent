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
