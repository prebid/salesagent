"""Which creatives owe the ad server a width and a height.

The create-path asset gate demanded both of every creative, whatever its kind. An audio
asset has neither -- ``formats/canonical/audio_hosted.json`` declares ``audio_main`` as
``asset_type: "audio"``, and ``SIZED_ASSET_TYPES`` is ``{image, video}`` for exactly that
reason -- so a canonical ``audio_hosted`` creative resolved its URL, correctly produced no
dimensions, and was refused VALIDATION_ERROR however correctly its manifest was authored.

The answer is a property of the SLOT the media URL came from, which is the distinction
these tests exist to hold. It is NOT a property of the kind: ``audio_hosted`` declares
``companion_image`` as well, so ``canonical_slot_asset_ids("audio_hosted",
SIZED_ASSET_TYPES)`` answers ``{"companion_image"}`` and a kind-level predicate would still
demand dimensions of an audio-only manifest -- it would look like a fix and change nothing.

Read through ``read_creative_to_traffic``, the one reader of a stored creative for
trafficking: a creative that owes dimensions and states none is refused ``NO_DIMENSIONS``.
Pure functions over the pinned wheel, so: unit. The buyer-visible outcome -- a buy for a
canonical audio creative is accepted -- is graded by BR-UC-002 on every transport.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.creative_to_traffic import TrafficRefusal, TrafficRefusalReason, read_creative_to_traffic
from src.core.format_satisfaction import canonical_slot_asset_ids
from src.core.helpers.creative_helpers import SIZED_ASSET_TYPES
from src.core.traffic_carrier import CreativeToTraffic, MediaRepresentation, TrafficSize

AUDIO_URL = "https://cdn.example.com/spot.mp3"
IMAGE_URL = "https://cdn.example.com/banner.png"


def _manifest(**assets: Any) -> dict[str, Any]:
    return {"assets": dict(assets)}


def _read(creative_data: dict[str, Any], kind: str) -> CreativeToTraffic | TrafficRefusal:
    """The creative as the reader reads it, with no format spec and no declaration stating a size."""
    return read_creative_to_traffic("cr", creative_data, kind, format_spec=None, declarations=())


def _refused_for_dimensions(creative_data: dict[str, Any], kind: str) -> bool:
    return _read(creative_data, kind) == TrafficRefusal("cr", TrafficRefusalReason.NO_DIMENSIONS)


class TestWhatIsOwed:
    @pytest.mark.parametrize("kind", ["audio_hosted"])
    def test_an_audio_only_manifest_owes_no_dimensions(self, kind: str) -> None:
        """The bug, as the gate sees it: a URL is found and dimensions are correctly absent.

        Parametrized over the kinds whose declaration names an audio media slot, which is
        ``audio_hosted`` alone at this pin — ``audio_vast`` and ``audio_daast`` declare a
        ``vast``/``daast`` tag instead, which ``MEDIA_ASSET_TYPES`` does not cover, so they
        fail the gate's URL check before any dimension is considered. That is a separate
        defect and is tracked separately. Spelled as a parametrization rather than a bare
        test so that a pin adding an audio-slot kind extends it here.
        """
        manifest = _manifest(audio_main={"asset_type": "audio", "url": AUDIO_URL})

        read = _read(manifest, kind)

        assert isinstance(read, CreativeToTraffic), read
        assert (read.representation, read.sizes) == (MediaRepresentation(asset_type="audio", url=AUDIO_URL), ())

    def test_an_image_manifest_still_owes_them(self) -> None:
        """The control. A gate that stopped demanding dimensions entirely passes the test
        above and fails this one, which is the only thing separating a fix from a deletion.
        """
        assert _refused_for_dimensions(_manifest(image_main={"asset_type": "image", "url": IMAGE_URL}), "image")

    def test_a_creative_with_no_resolvable_media_still_owes_them(self) -> None:
        """Absence is not an exemption: the caller's URL check is what must fail, not this.

        Were this True-by-default wrong, a creative with no media asset at all would be
        excused its dimensions and refused only for its URL — a thinner refusal than the
        buyer needs.
        """
        assert _read(_manifest(), "image") == TrafficRefusal("cr", TrafficRefusalReason.NO_SERVING_CONTENT)

    def test_a_root_level_url_owes_them_because_it_names_no_slot(self) -> None:
        """The legacy shape: ``data["url"]`` with no assets map. Nothing says its type."""
        assert _refused_for_dimensions({"url": IMAGE_URL}, "image")

    def test_the_companion_image_supplies_dimensions_without_making_them_owed(self) -> None:
        """An ``audio_hosted`` manifest carrying both slots: URL from one, size from the other.

        This is the case that proves the predicate reads the slot the URL CAME FROM rather
        than "did any sized slot resolve". Both answers are visible at once here: the
        dimensions are found, AND they are not owed.
        """
        manifest = _manifest(
            audio_main={"asset_type": "audio", "url": AUDIO_URL},
            companion_image={"asset_type": "image", "url": IMAGE_URL, "width": 300, "height": 250},
        )

        read = _read(manifest, "audio_hosted")

        assert isinstance(read, CreativeToTraffic), read
        assert (read.representation, read.sizes) == (
            MediaRepresentation(asset_type="audio", url=AUDIO_URL),
            (TrafficSize(width=300, height=250),),
        )

    def test_the_kinds_own_sized_slots_are_not_the_answer(self) -> None:
        """Stated as an assertion because it is the trap, not a footnote.

        ``audio_hosted`` DOES declare a sized slot. A predicate written as "does this kind
        declare one" is therefore True for it and demands dimensions of an audio-only
        manifest — the same refusal, from a fix that looks correct. If this ever becomes
        false the shortcut starts passing and someone will take it.
        """
        assert canonical_slot_asset_ids("audio_hosted", SIZED_ASSET_TYPES) == frozenset({"companion_image"})
