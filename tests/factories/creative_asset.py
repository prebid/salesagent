"""Pydantic schema factory for CreativeAsset (AdCP type).

Produces valid CreativeAsset objects with all required fields.
Used by creative sync tests instead of hand-crafted dicts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

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


def make_url_assets(asset_id: str, url: str, url_type: str | None = None) -> dict:
    """Build an SDK 5.7 LIST-shape url asset slot ``{asset_id: [{asset_type: url, ...}]}``.

    For multi-count url slots (format ``slots[].max > 1``). For an individual url slot use
    the single-object form :func:`make_url_asset`.
    """
    entry: dict = {
        "asset_type": "url",
        "asset_id": asset_id,
        "item_type": "individual",
        "required": True,
        "url": url,
    }
    if url_type is not None:
        entry["url_type"] = url_type
    return {asset_id: [entry]}


# ---------------------------------------------------------------------------
# Single-object (flat) helpers — AdCP 3.1 individual-slot shape
#
# AdCP 3.1 (creative-manifest) lets a slot value be EITHER a single asset object
# (individual slots) OR a list of asset objects (multi-count slots, ``slots[].max > 1``).
# The adcp 5.7 SDK accepts and round-trips both. These helpers emit the single-object
# shape (just the typed asset fields — no asset_id/item_type/required, which belong to the
# format's requirements, not the manifest value). Use the ``*_assets`` (plural) helpers
# above for the list shape. Centralising both keeps the shapes out of inline test dicts (#1391).
# ---------------------------------------------------------------------------


def make_image_asset(
    asset_id: str = "banner",
    url: str = "https://example.com/banner.png",
    width: int = 300,
    height: int = 250,
) -> dict:
    """AdCP 3.1 single-object image asset for an individual slot: ``{asset_id: {asset_type: image, ...}}``."""
    return {asset_id: {"asset_type": "image", "url": url, "width": width, "height": height}}


def make_text_asset(asset_id: str, content: str) -> dict:
    """AdCP 3.1 single-object text asset for an individual slot: ``{asset_id: {asset_type: text, content}}``."""
    return {asset_id: {"asset_type": "text", "content": content}}


def make_url_asset(asset_id: str, url: str, url_type: str | None = None) -> dict:
    """AdCP 3.1 single-object url asset for an individual slot: ``{asset_id: {asset_type: url, url, [url_type]}}``."""
    asset: dict = {"asset_type": "url", "url": url}
    if url_type is not None:
        asset["url_type"] = url_type
    return {asset_id: asset}


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


# ---------------------------------------------------------------------------
# AssetSpec — one mechanism for BUILDING the mock and VERIFYING the result
#
# Declare an asset once as an AssetSpec, then use the SAME spec to build the
# request payload (``.payload()`` / ``build_assets``) and to assert the stored or
# returned value (``.assert_in()`` / ``assert_assets``). The spec owns the AdCP 3.1
# shape decision (single object for an individual slot; a list for a multi-count
# slot) and the comparison, so step/test code never indexes ``[0]``, never unwraps a
# RootModel, and never re-implements containment. Add the asset once — build and
# verify both flow through it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetSpec:
    """A single creative asset, used to both build and verify a creative's assets.

    ``multiple=False`` emits the AdCP 3.1 single-object shape (individual slot);
    ``multiple=True`` emits a one-element list (multi-count slot). Verification is
    shape-agnostic (accepts object or list) and uses field containment, because
    production enriches the stored object with null-default fields.
    """

    role: str
    asset_type: str
    fields: Mapping
    multiple: bool = False

    def _object(self) -> dict:
        return {"asset_type": self.asset_type, **dict(self.fields)}

    def payload(self) -> dict:
        """The ``{role: value}`` slot map for a creative's ``assets`` field."""
        obj = self._object()
        return {self.role: [obj] if self.multiple else obj}

    def assert_in(self, stored_assets: Mapping) -> None:
        """Assert this asset is present in ``stored_assets`` with its declared fields preserved."""
        assert self.role in stored_assets, f"asset '{self.role}' missing from {list(stored_assets)}"
        value = stored_assets[self.role]
        obj = value[0] if isinstance(value, list) else value
        assert isinstance(obj, Mapping), f"asset '{self.role}' is not an object: {obj!r}"
        for key, expected in self._object().items():
            assert obj.get(key) == expected, f"asset '{self.role}'.{key}: expected {expected!r}, got {obj.get(key)!r}"

    def with_fields(self, **extra: object) -> AssetSpec:
        """Return a copy with additional/overridden typed fields (e.g. asset-level provenance)."""
        return AssetSpec(self.role, self.asset_type, {**dict(self.fields), **extra}, self.multiple)


def image_spec(
    role: str = "image",
    *,
    url: str = "https://example.com/banner.png",
    width: int = 300,
    height: int = 250,
    multiple: bool = False,
) -> AssetSpec:
    """AssetSpec for an image asset."""
    return AssetSpec(role, "image", {"url": url, "width": width, "height": height}, multiple)


def text_spec(role: str, *, content: str, multiple: bool = False) -> AssetSpec:
    """AssetSpec for a text asset."""
    return AssetSpec(role, "text", {"content": content}, multiple)


def url_spec(role: str, *, url: str, url_type: str | None = None, multiple: bool = False) -> AssetSpec:
    """AssetSpec for a url asset."""
    fields = {"url": url} if url_type is None else {"url": url, "url_type": url_type}
    return AssetSpec(role, "url", fields, multiple)


def video_spec(
    role: str = "video",
    *,
    url: str = "https://example.com/video.mp4",
    width: int = 640,
    height: int = 360,
    multiple: bool = False,
    **fields: object,
) -> AssetSpec:
    """AssetSpec for a video asset (extra typed fields, e.g. duration, via kwargs)."""
    return AssetSpec(role, "video", {"url": url, "width": width, "height": height, **fields}, multiple)


def asset_spec(role: str, asset_type: str, *, multiple: bool = False, **fields: object) -> AssetSpec:
    """Generic AssetSpec for any asset_type (audio, vast, html, css, markdown, catalog, ...).

    Use the typed constructors (image_spec/text_spec/url_spec/video_spec) for the common
    types; use this for the long tail so no test hand-rolls an asset dict.
    """
    return AssetSpec(role, asset_type, dict(fields), multiple)


def build_assets(*specs: AssetSpec) -> dict:
    """Merge specs into one ``assets`` slot map for a creative payload."""
    out: dict = {}
    for spec in specs:
        out.update(spec.payload())
    return out


def assert_assets(stored_assets: Mapping, *specs: AssetSpec) -> None:
    """Assert every spec is present in ``stored_assets`` with its declared fields preserved."""
    for spec in specs:
        spec.assert_in(stored_assets)
