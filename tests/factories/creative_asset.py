"""Pydantic schema factory for CreativeAsset (AdCP type).

Produces valid CreativeAsset objects with all required fields.
Used by creative sync tests instead of hand-crafted dicts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import factory
from adcp.types import LegacyCreativeAsset as CreativeAsset
from adcp.types.domains.core.format_id import FormatReferenceStructuredObject as FormatId

from tests.factories.format import AGENT_URL


def make_legacy_asset_dict(asset_id: str, **fields: object) -> dict:
    """Build a LEGACY (AdCP v1) single-dict asset entry: ``{asset_id: {**fields}}``.

    The v1 shape has NO ``asset_type`` discriminator and is NOT a list — it keys
    each role directly to a flat dict of fields (e.g. ``url``/``width``/``height``,
    ``url_type``, ``content``, ``duration_ms``). The pinned SDK's discriminated
    asset union rejects this shape. The legacy adapter converter that consumed it,
    ``_convert_creative_to_adapter_asset``, is deleted; the live path is
    ``media_buy_create._build_adapter_asset_from_creative``, which reads the ORM
    row rather than a schema object.

    Use this ONLY for legacy-input / negative tests that deliberately exercise the
    old shape. New or valid creative assets must use the AssetSpec mechanism
    (``image_spec``/``text_spec``/``video_spec``/``url_spec``/``asset_spec`` +
    ``build_assets``). Centralising the legacy shape here keeps it out of inline
    test dicts — see #1391.
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


def make_creative_asset_request(**extra: object) -> dict:
    """The same minimal creative, as the WIRE DICT sync_creatives accepts.

    Reuses make_creative_asset_minimal rather than restating its defaults. Returns a dict
    because that is what a transport hands the request DTO.

    PREFER ``CreativeAssetRequestFactory.payload()`` (tests/factories/request.py) for new
    code: it binds ``CreativeAssetRequest`` — the REQUEST model — and carries the OMIT /
    None override contract a negative path needs. This helper still builds the RESPONSE
    model and dumps it, which is the confusion below; its 14 existing callers are
    salesagent-b341x.10's to redirect.

    The per-file ``_make_creative`` helpers build the RESPONSE model (created_date,
    updated_date, principal_id); feeding one of those to a request worked only while the
    request field wrongly pointed at the response model, which is what left seven
    spec-legal fields unsendable and `inputs` unreachable.
    """
    return make_creative_asset_minimal(**extra).model_dump(exclude_none=True, mode="json")


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
    assets = factory.LazyFunction(lambda: named_format_assets("display_300x250_image"))


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


#: The file size a canonical manifest's image states: under the smallest cap any scenario
#: declares (200 KB). ``core/assets/image-asset.json``: ``file_size_bytes`` is "Required by
#: agents that advertise a max_file_size_kb constraint", and the seller refuses a manifest
#: silent about a cap the selected declaration states.
MANIFEST_IMAGE_BYTES = 50 * 1024


def manifest_image_spec(
    role: str = "image_main", *, width: int = 300, height: int = 250, multiple: bool = False
) -> AssetSpec:
    """An image as a canonical manifest carries it: its dimensions and its file size.

    ``multiple=True`` makes it one rendition of an array-valued slot; several such specs for
    one role build the array (:func:`build_assets`).
    """
    return image_spec(role, width=width, height=height, multiple=multiple).with_fields(
        file_size_bytes=MANIFEST_IMAGE_BYTES
    )


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
    """Merge specs into one ``assets`` slot map for a creative payload.

    Specs sharing a role with ``multiple=True`` each append one item to that slot's array, in
    order -- the shape of a slot holding several assets, such as an image rendition set
    (creative-manifest.json: "an array of asset objects (slots with ``min``/``max`` counts")).
    Any other repeated role is replaced by the later spec.
    """
    out: dict = {}
    for spec in specs:
        for role, value in spec.payload().items():
            held = out.get(role)
            out[role] = [*held, *value] if spec.multiple and isinstance(held, list) else value
    return out


def canonical_assets(kind: str) -> dict:
    """The ``assets`` a manifest of canonical *kind* must carry: one asset per REQUIRED slot.

    Read off the pin, not listed here: ``declared_slots_by_kind`` is the seller's own reader
    of each canonical schema's ``slots`` default, so the slot ids and asset types are the
    ones the seller grades against (``image`` -> ``image_main``, ``video_hosted`` ->
    ``video_main``). creative-manifests.mdx § Basic structure: "``assets`` is keyed by
    ``asset_group_id`` from the matched declaration's ``params.slots[]``".

    Only the media types have a builder; a kind requiring anything else raises ``KeyError``
    naming the type, so a scenario cannot silently send an incomplete manifest.
    """
    from src.core.format_satisfaction import declared_slots_by_kind

    builders = {
        "image": manifest_image_spec,
        "video": video_spec,
        "audio": lambda group: asset_spec(group, "audio", url="https://example.com/spot.mp3"),
    }
    return build_assets(
        *(
            builders[str(slot["asset_type"])](str(slot["asset_group_id"]))
            for slot in declared_slots_by_kind()[kind]
            if slot.get("required")
        )
    )


def named_format_assets(
    format_id: str = "display_300x250_image", *, media_url: str | None = None, prompt: str | None = None
) -> dict:
    """The ``assets`` a manifest naming legacy *format_id* must carry: one per REQUIRED asset.

    The deprecated branch's sibling of :func:`canonical_assets`, for a format the committed
    capture carries (``load_reference_formats``) -- the one the harness serves -- so the asset
    ids and types are the ones the seller grades against (``display_300x250_image`` ->
    ``banner_image`` image plus ``click_url`` url). :func:`format_manifest` builds it.
    """
    from src.core.format_cache import load_reference_formats

    (fmt,) = (fmt for fmt in load_reference_formats() if fmt.format_id.id == format_id)
    return format_manifest(fmt, media_url=media_url, prompt=prompt)


def prompt_assets(content: str, *also: AssetSpec, format_id: str = "display_300x250_generative") -> dict:
    """A generative creative's assets: what its named format requires, the prompt carrying *content*.

    The captured generative formats require a ``generation_prompt`` text asset, so a creative
    in one carries it whatever other prompt role (``message``, ``brief``) a test also sends.
    *also* are those further assets; give a prompt role the same *content* and the build's
    prompt is *content* whichever asset production reads it from.
    """
    return {**named_format_assets(format_id, prompt=content), **build_assets(*also)}


def format_manifest(fmt: Any, *, media_url: str | None = None, prompt: str | None = None) -> dict:
    """The ``assets`` that fill *fmt*'s required assets, each keyed by its ``asset_id``.

    core/format.json: each asset's ``asset_id`` "must be used as the key in creative
    manifests", and "Required assets must be provided for a valid creative". Read off the
    format, not listed by the caller, so a manifest cannot drift from the format it names.

    *media_url* replaces the default URL of the image, video or audio asset, for a caller
    whose creatives must differ in what they serve; *prompt* is the content of the text
    asset, for a generative format whose required text asset is the build's prompt.

    A required type with no builder raises ``KeyError`` naming it, so a scenario cannot
    silently send an incomplete manifest.
    """
    media = {} if media_url is None else {"url": media_url}
    builders: dict[str, Callable[[str], AssetSpec]] = {
        "image": lambda asset_id: image_spec(asset_id, **media),
        "url": lambda asset_id: url_spec(asset_id, url="https://example.com/landing"),
        "text": lambda asset_id: text_spec(asset_id, content=prompt or "Test copy"),
        "video": lambda asset_id: video_spec(asset_id, **media),
        "audio": lambda asset_id: asset_spec(asset_id, "audio", url=media_url or "https://example.com/spot.mp3"),
        "html": lambda asset_id: asset_spec(asset_id, "html", content="<div>ad</div>"),
        "vast": lambda asset_id: asset_spec(asset_id, "vast", delivery_type="url", url="https://example.com/vast.xml"),
        "daast": lambda asset_id: asset_spec(
            asset_id, "daast", delivery_type="url", url="https://example.com/daast.xml"
        ),
    }
    return build_assets(
        *(
            builders[str(asset.asset_type)](asset.asset_id)
            for asset in fmt.assets or ()
            if getattr(asset, "item_type", None) == "individual" and asset.required
        )
    )


def assert_assets(stored_assets: Mapping, *specs: AssetSpec) -> None:
    """Assert every spec is present in ``stored_assets`` with its declared fields preserved."""
    for spec in specs:
        spec.assert_in(stored_assets)


def make_test_banner_creative(**overrides: object) -> CreativeAsset:
    """Minimal valid banner CreativeAsset (``c_test_1`` / ``display_300x250_image``).

    Canonical replacement for per-module ``_make_creative_asset`` helpers —
    see #1430 DRY extraction.

    FOR SYNC TESTS, which is why the format must be one the reference agent actually
    serves. This defaulted to ``display_300x250`` — a format that is NOT among those in
    ``tests/fixtures/creative_formats/reference_formats.json``, so every creative built
    here is refused by ``_validation.py`` with ``AdCPFormatNotFoundError`` and comes back
    ``action='failed'``. That was invisible while the harness served a stand-in
    catalogue; it is the real catalogue now, in process and on the wire alike.
    ``CreativeAssetFactory`` above, in this same file, already named the served id.
    """
    defaults: dict = {
        "creative_id": "c_test_1",
        "name": "Test Banner",
        "format_id": FormatId(agent_url=AGENT_URL, id="display_300x250_image"),
        "assets": named_format_assets("display_300x250_image"),
    }
    defaults.update(overrides)
    return make_creative_asset_minimal(**defaults)
