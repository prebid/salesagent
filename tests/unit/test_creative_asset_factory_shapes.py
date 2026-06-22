"""Both AdCP 3.1 asset shapes from the AssetSpec mechanism parse cleanly under the adcp 5.7 SDK.

AdCP 3.1 (creative-manifest) lets a slot value be EITHER a single asset object (individual
slots) OR a list of asset objects (multi-count slots). The AssetSpec mechanism owns that
shape decision (``multiple=False`` -> single object, ``multiple=True`` -> one-element list)
so tests never hand-roll asset shapes (#1391). This pins that both forms are valid: each
``build_assets`` payload parses into a CreativeAsset, the production extractors read the
round-tripped value, and ``assert_assets`` verifies the SAME spec against the stored assets.

Part of #1391 SDK 5.7 creative-asset-shape migration.
"""

import pytest
from adcp.types import CreativeAsset

from src.core.tools.creatives._assets import _extract_text_from_asset_value, _extract_url_from_asset_value
from tests.factories.creative_asset import assert_assets, build_assets, image_spec, text_spec, url_spec

_FORMAT = {"id": "display_300x250", "agent_url": "http://agent.test"}


def _parse(assets: dict) -> CreativeAsset:
    return CreativeAsset(creative_id="c", name="n", format_id=_FORMAT, assets=assets)


@pytest.mark.parametrize("multiple", [False, True], ids=["single-object", "list"])
def test_image_both_shapes_parse_and_extract_url(multiple):
    spec = image_spec("hero", multiple=multiple)
    creative = _parse(build_assets(spec))
    assert _extract_url_from_asset_value(creative.assets["hero"]) == "https://example.com/banner.png"
    assert_assets(creative.model_dump(mode="json")["assets"], spec)


@pytest.mark.parametrize("multiple", [False, True], ids=["single-object", "list"])
def test_text_both_shapes_parse_and_extract_content(multiple):
    spec = text_spec("message", content="hello", multiple=multiple)
    creative = _parse(build_assets(spec))
    assert _extract_text_from_asset_value(creative.assets["message"]) == "hello"
    assert_assets(creative.model_dump(mode="json")["assets"], spec)


@pytest.mark.parametrize("multiple", [False, True], ids=["single-object", "list"])
def test_url_both_shapes_parse_and_extract_url(multiple):
    spec = url_spec("click_url", url="https://example.com/landing", url_type="clickthrough", multiple=multiple)
    creative = _parse(build_assets(spec))
    assert _extract_url_from_asset_value(creative.assets["click_url"]) == "https://example.com/landing"
    assert_assets(creative.model_dump(mode="json")["assets"], spec)
