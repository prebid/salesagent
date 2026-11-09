"""What a legacy format spec DECLARES, as this seller reads it.

``core/format.json``'s ``assets`` is a DISCRIMINATED union — 45 generated arm classes at
adcp 6.6.0, one per asset type. Every reader in ``src.core.helpers.creative_helpers`` used
to open its format-spec branch by narrowing to one of them::

    from adcp.types import ImageFormatAsset as Assets
    if not isinstance(asset_spec, Assets):
        continue

and the alias is the lie: ``ImageFormatAsset`` IS ``format.Assets``, the IMAGE arm, and the
siblings are not subclasses of it. So the guard admitted image slots only, and the url
readers' format-spec branches — which need ``asset_type == "url"`` — were structurally
unreachable for any spec whatsoever, leaving a hardcoded list of guessed asset-id names as
the only live path. ``core/assets/url-asset.json`` calls that list's behaviour out by name:
a receiver with neither a ``url_type`` nor a format-side ``role`` "MUST NOT silently pick a
mechanism".

These tests are the instrument for the reader itself. BOTH slot shapes are fixtured,
deliberately: ``adcp.utils.get_format_assets`` returns the spec's ``assets`` list
unchanged, so a slot is a typed arm when the spec was validated and a plain dict when it
arrived as a Mapping, and a dict-only fixture would not show that the typed arms are
admitted (which is the whole defect) while a typed-only one would not show the dict branch
exists.

Pure functions over the pinned wheel, so: unit. What the create path then DOES with a
resolved clickthrough is buyer-visible and graded by BR-UC-002's canonical-clickthrough
scenarios on every transport.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.helpers.creative_helpers import (
    declared_slots,
    extract_click_url,
    extract_impression_tracker_url,
    extract_media_url_and_dimensions,
)
from tests.factories.format import _ASSET_CLASS_MAP, FormatFactory, make_asset

#: Deliberately named by nothing but the format under test: no entry or alias of
#: ``core/asset-group-vocabulary.json``, and no member of ``MEDIA_ASSET_FALLBACK_IDS``. A
#: slot id some other lookup also answers would let it pass for a declaration read.
DECLARED_ONLY = "the_slot_the_format_named"
CLICK_URL = "https://advertiser.example.com/landing"
TRACKER_URL = "https://measure.example.com/imp.gif"
VIDEO_URL = "https://cdn.example.com/spot.mp4"


def _url_slot(asset_id: str, role: str | None) -> Any:
    """One typed url slot, optionally carrying ``url-asset-requirements.role``."""
    return _ASSET_CLASS_MAP["url"](
        asset_id=asset_id, required=True, requirements=None if role is None else {"role": role}
    )


def _spec(*slots: Any) -> Any:
    """A VALIDATED spec: ``Format`` coerces each slot into its typed union arm."""
    return FormatFactory(assets=list(slots))


def _raw_spec(*slots: dict[str, Any]) -> dict[str, Any]:
    """An UNVALIDATED spec — a plain Mapping, which ``get_format_assets`` reads verbatim.

    Not ``_spec`` with dict slots: ``Format`` validates its ``assets`` list, so a dict
    handed to the factory comes back as a typed arm and would grade the typed branch while
    claiming to grade this one. The shape only survives when the whole spec is a Mapping.
    """
    return {"assets": [{"item_type": "individual", **slot} for slot in slots]}


class TestDeclaredSlots:
    @pytest.mark.parametrize("asset_type", ["image", "video", "audio", "url"])
    def test_every_declared_asset_type_is_visible_not_only_image(self, asset_type: str) -> None:
        """The reader sees the arm that arrived, whatever type it discriminates to.

        Parametrized over the four types the readers in this module act on. Under the old
        image-arm guard the ``image`` case passed and the other three returned nothing —
        which is the defect, and why one combined fixture would have hidden it behind a
        non-empty result.
        """
        spec = _spec(make_asset(asset_type, DECLARED_ONLY))

        assert declared_slots(spec) == [(DECLARED_ONLY, asset_type, None)]

    def test_a_slot_that_arrived_as_a_plain_dict_is_read_too(self) -> None:
        """``get_format_assets`` hands back a Mapping's list verbatim, so dicts reach here."""
        spec = _raw_spec({"asset_id": DECLARED_ONLY, "asset_type": "url"})

        assert declared_slots(spec) == [(DECLARED_ONLY, "url", None)]

    def test_a_spec_with_no_assets_declares_no_slots(self) -> None:
        """A flexible format carries none, and ``None`` is not an error."""
        assert declared_slots(None) == []
        assert declared_slots(_spec()) == []

    def test_a_slot_missing_its_discriminator_is_dropped(self) -> None:
        """The one thing the old class guard legitimately did, kept."""
        spec = _raw_spec({"asset_id": DECLARED_ONLY})

        assert declared_slots(spec) == []


class TestClickthroughFromTheDeclaration:
    @pytest.mark.parametrize("role", ["clickthrough", "landing_page"])
    def test_a_declared_clickthrough_role_resolves_the_click_url(self, role: str) -> None:
        """Both roles the pin maps to the ``clickthrough`` mechanism.

        ``core/assets/url-asset.json``: "When ``url_type`` is absent, receivers SHOULD fall
        back to the format's ``url-asset-requirements.role`` per this mapping:
        clickthrough/landing_page -> ``clickthrough``".

        The asset is keyed :data:`DECLARED_ONLY`, so a guess list cannot answer this: the
        seller was TOLD which slot is the destination, by the format, in the field the pin
        names for it.
        """
        spec = _spec(make_asset("image", "banner_image"), _url_slot(DECLARED_ONLY, role))
        creative = {"assets": {DECLARED_ONLY: {"asset_type": "url", "url": CLICK_URL}}}

        assert extract_click_url(creative, spec) == CLICK_URL

    def test_the_role_is_read_when_requirements_arrived_as_a_dict(self) -> None:
        """Same declaration, unvalidated shape. Covers the Mapping branch of the read."""
        spec = _raw_spec({"asset_id": DECLARED_ONLY, "asset_type": "url", "requirements": {"role": "clickthrough"}})
        creative = {"assets": {DECLARED_ONLY: {"asset_type": "url", "url": CLICK_URL}}}

        assert extract_click_url(creative, spec) == CLICK_URL

    def test_a_url_slot_declaring_no_role_resolves_nothing_from_the_declaration(self) -> None:
        """No role is the pin's third case, where it forbids a guess.

        The control for the two above: it is the ROLE being read, not merely the presence of
        a url slot. No other rung can reach this asset either — its id is in no registry
        entry — so the answer is None and the url is not invented into a destination.
        """
        spec = _spec(_url_slot(DECLARED_ONLY, None))
        creative = {"assets": {DECLARED_ONLY: {"asset_type": "url", "url": CLICK_URL}}}

        assert extract_click_url(creative, spec) is None

    def test_a_third_party_tracker_role_gets_no_mechanism(self) -> None:
        """The pin gives that role NO safe fallback, so nothing may be invoked from it.

        "third_party_tracker -> no safe fallback (mechanism is integration-specific -- DV/IAS
        ship both pixel and script forms -- receivers MAY reject or warn)." A mapping that
        guessed pixel here would fire a script-only vendor's URL as an image and measure
        nothing, which is the failure the pin is naming.
        """
        spec = _spec(_url_slot(DECLARED_ONLY, "third_party_tracker"))
        creative = {"assets": {DECLARED_ONLY: {"asset_type": "url", "url": TRACKER_URL}}}

        assert extract_click_url(creative, spec) is None
        assert extract_impression_tracker_url(creative, spec) is None


class TestTrackerFromTheDeclaration:
    @pytest.mark.parametrize("role", ["impression_tracker", "click_tracker"])
    def test_a_declared_tracker_role_resolves_the_pixel(self, role: str) -> None:
        """Both roles the pin maps to ``tracker_pixel``."""
        spec = _spec(_url_slot(DECLARED_ONLY, role))
        creative = {"assets": {DECLARED_ONLY: {"asset_type": "url", "url": TRACKER_URL}}}

        assert extract_impression_tracker_url(creative, spec) == TRACKER_URL

    def test_a_viewability_role_is_a_script_and_is_not_served_as_a_pixel(self) -> None:
        """``viewability_tracker -> tracker_script``, which the impression reader must not take.

        The pin's parenthetical is the reason: "OMID and equivalent verification SDKs require
        a <script> tag -- firing them as a pixel produces no measurement". A reader that
        collapsed every tracker role onto the pixel mechanism would ship a measurable-looking
        buy that measures nothing.
        """
        spec = _spec(_url_slot(DECLARED_ONLY, "viewability_tracker"))
        creative = {"assets": {DECLARED_ONLY: {"asset_type": "url", "url": TRACKER_URL}}}

        assert extract_impression_tracker_url(creative, spec) is None


class TestMediaFromTheDeclaration:
    @pytest.mark.parametrize("asset_type", ["video", "audio"])
    def test_a_legacy_non_image_media_slot_resolves_from_its_own_declaration(self, asset_type: str) -> None:
        """A legacy VIDEO or AUDIO creative reads its media asset off its own spec.

        Under the image-arm guard these two fell through to ``MEDIA_ASSET_FALLBACK_IDS``, so
        a legacy video creative was readable only if it happened to key its asset
        ``video_file``, ``video`` or ``main``. :data:`DECLARED_ONLY` is in none of them.
        """
        spec = _spec(make_asset(asset_type, DECLARED_ONLY))
        creative = {"assets": {DECLARED_ONLY: {"asset_type": asset_type, "url": VIDEO_URL}}}

        url, _width, _height = extract_media_url_and_dimensions(creative, spec, None)

        assert url == VIDEO_URL

    def test_an_image_slot_still_contributes_its_dimensions(self) -> None:
        """The case the old guard did serve, kept green: sized types carry width/height."""
        spec = _spec(make_asset("image", DECLARED_ONLY))
        creative = {"assets": {DECLARED_ONLY: {"asset_type": "image", "url": CLICK_URL, "width": 300, "height": 250}}}

        assert extract_media_url_and_dimensions(creative, spec, None) == (CLICK_URL, 300, 250)
