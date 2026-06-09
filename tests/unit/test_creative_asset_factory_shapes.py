"""Both AdCP 3.1 asset shapes from the factory parse cleanly under the adcp 5.7 SDK.

AdCP 3.1 (creative-manifest) lets a slot value be EITHER a single asset object (individual
slots) OR a list of asset objects (multi-count slots). The factory provides both forms so
tests never hand-roll asset shapes (#1391). This pins that both forms are valid: they parse
into a CreativeAsset and the production extractors read them.

Part of #1391 SDK 5.7 creative-asset-shape migration.
"""

import pytest
from adcp.types import CreativeAsset

from src.core.tools.creatives._assets import _extract_text_from_asset_value, _extract_url_from_asset_value
from tests.factories.creative_asset import (
    make_image_asset,
    make_image_assets,
    make_text_asset,
    make_text_assets,
    make_url_asset,
    make_url_assets,
)

_FORMAT = {"id": "display_300x250", "agent_url": "http://agent.test"}


def _parse(assets: dict) -> CreativeAsset:
    return CreativeAsset(creative_id="c", name="n", format_id=_FORMAT, assets=assets)


@pytest.mark.parametrize(
    "assets",
    [
        make_image_asset("hero"),  # single-object
        make_image_assets("hero"),  # list
    ],
)
def test_image_both_shapes_parse_and_extract_url(assets):
    creative = _parse(assets)
    assert _extract_url_from_asset_value(creative.assets["hero"]) == "https://example.com/banner.png"


@pytest.mark.parametrize(
    "assets",
    [
        make_text_asset("message", "hello"),  # single-object
        make_text_assets("message", "hello"),  # list
    ],
)
def test_text_both_shapes_parse_and_extract_content(assets):
    creative = _parse(assets)
    assert _extract_text_from_asset_value(creative.assets["message"]) == "hello"


@pytest.mark.parametrize(
    "assets",
    [
        make_url_asset("click_url", url="https://example.com/landing", url_type="clickthrough"),  # single-object
        make_url_assets("click_url", url="https://example.com/landing", url_type="clickthrough"),  # list
    ],
)
def test_url_both_shapes_parse_and_extract_url(assets):
    creative = _parse(assets)
    assert _extract_url_from_asset_value(creative.assets["click_url"]) == "https://example.com/landing"
