"""The canonical slot vocabulary this seller reads off the pinned declaration union.

``src.core.format_satisfaction.declared_slots_by_kind`` DERIVES its map by walking the
sixteen arms of ``core/product-format-declaration.json``'s discriminated union and reading
each arm's ``params.slots`` field DEFAULT. A derivation has a failure mode a hand-written
list does not: if codegen reshapes the union so the walk yields nothing, the map comes back
EMPTY and every caller reads that as "no kind declares slots" -- which is indistinguishable
from a correct answer at the call site and makes the create-path asset gate fall back to
guessing by naming convention again. So the POPULATION is asserted here, not just a sample
of it.

Pure functions over the pinned wheel, so: unit. Nothing here touches a database, a
transport or a request. What the gate does with the vocabulary is buyer-visible and graded
by ``BR-UC-FORMAT-SATISFACTION.feature``'s canonical-asset scenarios on every transport.
"""

from __future__ import annotations

import pytest
from adcp.types.domains.core.canonical_format_kind import CanonicalFormatKind

from src.core.format_satisfaction import canonical_slot_asset_ids, declared_slots_by_kind
from src.core.helpers.creative_helpers import MEDIA_ASSET_TYPES, SIZED_ASSET_TYPES


def test_every_pinned_canonical_kind_is_in_the_derived_map() -> None:
    """The map's keys ARE the pinned kind enum -- no kind missing, none invented.

    The guard against a silently empty derivation. ``CanonicalFormatKind`` is generated
    from the same schema as the declaration union, so the two agreeing is the only evidence
    that the walk over the union's arms found the arms.
    """
    assert set(declared_slots_by_kind()) == {kind.value for kind in CanonicalFormatKind}


def test_the_declaration_names_image_mains_id_rather_than_a_naming_convention() -> None:
    """``image`` declares exactly one media slot, and it is ``image_main``.

    ``formats/canonical/image.json``'s ``slots`` default: ``image_main`` (image),
    ``headline``/``body_text``/``primary_text``/``cta`` (text), ``landing_page_url`` (url).
    The literal is written out rather than recomputed from the declaration, because a value
    derived from the subject cannot contradict it -- this asserts what the PIN says the id
    is, which is the whole claim. ``banner_image``, this repo's own long-standing fallback
    guess, is not it.
    """
    assert canonical_slot_asset_ids("image", MEDIA_ASSET_TYPES) == {"image_main"}
    assert "banner_image" not in canonical_slot_asset_ids("image", MEDIA_ASSET_TYPES)


def test_the_asset_type_filter_selects_within_one_kinds_slots() -> None:
    """One kind's slots partition by asset_type, and the filter is what picks a lane.

    The same ``image`` declaration answers a different set for ``url``. Asserted because
    the gate asks for media assets while the clickthrough reader beside it will ask for url
    assets, and a filter that ignored its argument would answer both with the same ids.
    """
    assert canonical_slot_asset_ids("image", frozenset({"url"})) == {"landing_page_url"}
    assert canonical_slot_asset_ids("image", frozenset({"text"})) == {
        "headline",
        "body_text",
        "primary_text",
        "cta",
    }


def test_a_kind_whose_params_require_a_member_still_declares_its_slots() -> None:
    """The hole the field-default read exists to close, stated as a test.

    Two arms require a ``params`` member -- ``coordinated_placements.params.components`` and
    ``seller_rendered_stateful_display.params.states`` -- so a declaration stating only its
    kind is REFUSED for them. The first version of this derivation validated such a
    declaration and dropped the refusal, which cost ``seller_rendered_stateful_display``
    all four of its media slots with a log line nobody reads. A field default is stated by
    the schema whether or not a sibling member is required.
    """
    assert canonical_slot_asset_ids("seller_rendered_stateful_display", MEDIA_ASSET_TYPES) == {
        "state_canvases",
        "imagery",
        "logo",
        "video_main",
    }


def test_sized_slots_are_the_subset_that_can_carry_dimensions() -> None:
    """``audio_hosted`` declares two media slots and only one of them has dimensions.

    The distinction the gate acts on: it demands width and height, and an audio asset has
    neither. A seller that treated every media slot as sized would read a companion image's
    dimensions onto an audio creative, or demand dimensions of the audio itself.
    """
    assert canonical_slot_asset_ids("audio_hosted", MEDIA_ASSET_TYPES) == {"audio_main", "companion_image"}
    assert canonical_slot_asset_ids("audio_hosted", SIZED_ASSET_TYPES) == {"companion_image"}


@pytest.mark.parametrize("kind", ["", "image_main", "display_300x250", "IMAGE"])
def test_a_kind_the_pin_does_not_declare_answers_no_slots(kind: str) -> None:
    """An unrecognised kind contributes nothing, and does not raise.

    ``Creative.format_kind`` is a plain nullable string column, so a row can carry a value
    the current pin does not declare -- a kind retired by a spec bump, or a legacy id
    mistaken for one. The gate must then fall through to its other candidate sources rather
    than fail the request, which is what an empty answer gives it. The cases are a sample of
    the shapes that have actually appeared: empty, a SLOT id, a legacy format id, and the
    right kind in the wrong case.
    """
    assert canonical_slot_asset_ids(kind, MEDIA_ASSET_TYPES) == frozenset()
