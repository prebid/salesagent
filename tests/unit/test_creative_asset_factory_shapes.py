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
from pydantic import ValidationError

from src.core.schemas.creative import CreativeAssetRequest
from src.core.tools.creatives._assets import _extract_text_from_asset_value, _extract_url_from_asset_value
from tests.factories.creative_asset import assert_assets, build_assets, image_spec, text_spec, url_spec
from tests.factories.request import OMIT, CreativeAssetRequestFactory

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


# ---------------------------------------------------------------------------
# CreativeAssetRequestFactory — the three clauses of its override contract
#
# The baseline is graded against the REQUEST model, which is the whole reason the
# factory is named for it: CreativeAssetFactory (above, and in the same package) binds
# the RESPONSE model, and feeding a response-shaped creative to a request is a defect
# this repo has already had.
# ---------------------------------------------------------------------------


def test_baseline_payload_is_accepted_by_the_request_model():
    """payload() is a conformant creative item — a baseline that is not is not a baseline."""
    assert CreativeAssetRequest.model_validate(CreativeAssetRequestFactory.payload())


def test_omit_removes_the_key_entirely():
    """``assets=OMIT`` is an ABSENT key, not a null one — and the pin then rejects the item."""
    payload = CreativeAssetRequestFactory.payload(assets=OMIT)
    assert "assets" not in payload
    with pytest.raises(ValidationError):
        CreativeAssetRequest.model_validate(payload)


def test_none_keeps_the_key_carrying_null():
    """``format_id=None`` puts the key on the wire carrying null.

    The distinction OMIT draws: the pin reports an absent and a null ``format_id``
    identically ([type=oneOf]), so the call site is the only place that can state which
    of the two the author meant.
    """
    payload = CreativeAssetRequestFactory.payload(format_id=None)
    assert "format_id" in payload
    assert payload["format_id"] is None
    assert "format_id" not in CreativeAssetRequestFactory.payload(format_id=OMIT)


def test_overrides_reach_the_dict_verbatim_where_build_would_normalise():
    """An override lands AFTER model_dump, so a negative path can carry un-normalised bytes.

    ``build()`` routes the same value through the model and gets pydantic's trailing
    slash. That asymmetry is why a malformation is always a payload() override and never
    a build() kwarg — through build() it would raise in the test's own setup.
    """
    raw = "https://creative.test.example.com"
    format_id = {"id": "display_300x250", "agent_url": raw}

    assert CreativeAssetRequestFactory.payload(format_id=format_id)["format_id"]["agent_url"] == raw

    through_model = CreativeAssetRequestFactory.build(format_id=format_id).model_dump(mode="json")
    assert through_model["format_id"]["agent_url"] == f"{raw}/"
