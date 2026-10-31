"""The GAM creative placeholders a product's format declarations imply.

WHY A UNIT TEST, against ``tests/CLAUDE.md``'s default of BDD. The subject --
``GAMProductConfigService.generate_default_config`` -- is a pure function: format
declarations in, an ``implementation_config`` document out, with no session, no transport
and no identity anywhere in its signature. The bar names that case, and the behaviour
under test is a derivation over EIGHT distinct input shapes (canonical fixed size,
canonical size list, canonical native, both arms together, the legacy entry's own
parameters, the legacy id's embedded size, an undeclarable declaration, a duplicate
size). A feature file enumerating eight input documents against one output document is a
unit test wearing a scenario, and it would grade the same function through more
machinery.

The claim this file does NOT make, because it cannot: that the admin create path and
``_create_media_buy_impl`` pass a product's ``format_options`` in. That is a call-site
claim about two modules this file does not touch, and it belongs to an admin-path test.

EVERY EXPECTED SIZE HERE DIFFERS FROM 300x250 ON PURPOSE. 300x250 is the function's
fallback, which until this change was the ONLY value it ever produced -- so an assertion
expecting 300x250 passes whether the derivation works or is dead, and proves nothing. The
one test that does expect the fallback (``test_a_declaration_stating_no_size...``) asserts
the warning beside it, so the fallback cannot be reached silently.
"""

import logging

import pytest

from src.services.gam_product_config_service import GAMProductConfigService

CREATIVE_AGENT = "https://creative.adcontextprotocol.org"


def _placeholders(**kwargs) -> list[dict]:
    """The ``creative_placeholders`` the service seeds for a non-guaranteed product.

    Through ``generate_default_config`` rather than the private derivation, because the
    seeded document is what the GAM adapter reads (``managers/orders.py`` falls back to
    ``impl_config["creative_placeholders"]`` for a package naming no ``format_ids``), and
    because the delivery-type branch is where the key was consumed.
    """
    config = GAMProductConfigService.generate_default_config("non_guaranteed", **kwargs)
    return config["creative_placeholders"]


def test_a_canonical_image_declaration_sets_the_dimensions_it_declares():
    """``format_options`` is read, and its ``params`` decide the placeholder.

    The defect this pins: the derivation consulted the deprecated arm ALONE, so a
    product declaring its creative shape canonically -- the AdCP 3.2 authoring path --
    got the 300x250 fallback contradicting its own declaration.
    """
    assert _placeholders(format_options=[{"format_kind": "image", "params": {"width": 970, "height": 250}}]) == [
        {"width": 970, "height": 250, "expected_creative_count": 1, "is_native": False}
    ]


def test_a_canonical_size_list_becomes_one_placeholder_per_accepted_size():
    """``formats/canonical/image.json`` offers ``sizes[]`` beside fixed ``width``/``height``.

    A GAM placeholder is one accepted slot size, so a declaration accepting two sizes is
    two placeholders -- not one, and not the first of them.
    """
    assert _placeholders(
        format_options=[
            {"format_kind": "image", "params": {"sizes": [{"width": 728, "height": 90}, {"width": 970, "height": 250}]}}
        ]
    ) == [
        {"width": 728, "height": 90, "expected_creative_count": 1, "is_native": False},
        {"width": 970, "height": 250, "expected_creative_count": 1, "is_native": False},
    ]


def test_a_canonical_native_declaration_is_a_one_by_one_native_placeholder():
    """``native_in_feed`` takes GAM's native convention, not its ``main_image_sizes``.

    ``managers/orders.py`` renders ``is_native`` as ``creativeSizeType: "NATIVE"`` and
    pairs a native unit with a 1x1 size, because the creative template decides the
    rendered size. ``main_image_sizes`` is the native image ASSET's size and would be the
    wrong value for the slot.
    """
    assert _placeholders(
        format_options=[
            {"format_kind": "native_in_feed", "params": {"main_image_sizes": [{"width": 1200, "height": 628}]}}
        ]
    ) == [{"width": 1, "height": 1, "expected_creative_count": 1, "is_native": True}]


def test_a_legacy_entry_carries_its_own_dimensions_and_they_are_read():
    """The key defect: the entries say ``id``, the derivation asked for ``format_id``.

    ``static/js/format-template-picker.js`` emits ``{agent_url, id: "display_image",
    width, height}`` for a parameterized display selection and
    ``_parse_format_entries`` stores exactly that. Reading ``format_id`` yielded ``""``
    for every entry, so no branch matched and every GAM product got the fallback whatever
    the operator picked.
    """
    assert _placeholders(
        formats=[{"agent_url": CREATIVE_AGENT, "id": "display_image", "width": 970, "height": 250}]
    ) == [{"width": 970, "height": 250, "expected_creative_count": 1, "is_native": False}]


def test_a_legacy_format_id_states_its_size_in_the_id_and_that_resolves():
    """A fixed-size legacy id needs no stored parameters.

    728x90 rather than 300x250 so the assertion cannot be satisfied by the fallback. This
    is also what replaces the eight-member hardcoded dimension map, whose keys
    (``display_300x250``, ``display_320x100``, ``display_300x50``) match no id in
    ``tests/fixtures/creative_formats/reference_formats.json``.
    """
    assert _placeholders(formats=[{"agent_url": CREATIVE_AGENT, "id": "display_728x90_image"}]) == [
        {"width": 728, "height": 90, "expected_creative_count": 1, "is_native": False}
    ]


@pytest.mark.parametrize(
    "agent_url",
    [f"{CREATIVE_AGENT}./", f"{CREATIVE_AGENT}/a/../", f"{CREATIVE_AGENT}/mcp"],
    ids=["root-label-dot", "dot-segment", "mounted-path"],
)
def test_a_respelled_reference_agent_still_states_the_size_in_its_id(agent_url):
    """The reference agent in another spelling is the same agent, so its id still upgrades.

    The SDK upgrades a size-encoded id only for the reference agent, and recognizes it by a
    comparison applying four of the eight steps of docs/reference/url-canonicalization and
    the whole path. Handed the raw stored entry, each of these spellings produced no
    placeholder at all.
    """
    assert _placeholders(formats=[{"agent_url": agent_url, "id": "display_728x90_image"}]) == [
        {"width": 728, "height": 90, "expected_creative_count": 1, "is_native": False}
    ]


def test_a_bare_string_format_entry_still_resolves():
    """The docstring's second storage pattern, kept working rather than assumed gone."""
    assert _placeholders(formats=["display_728x90_image"]) == [
        {"width": 728, "height": 90, "expected_creative_count": 1, "is_native": False}
    ]


def test_the_canonical_arm_decides_when_a_product_carries_both_arms():
    """``format_options[]`` is the closed set; the deprecated arm is a projection of it.

    ``core/product-format-declaration.json`` (AdCP 3.2.3): "``format_options[]`` is the
    closed set of accepted formats for this product", and legacy named formats "MUST be
    normalized to canonical declarations before comparison". So the two arms are not
    unioned -- that would double-count a product that declares one shape twice -- and the
    canonical one answers.
    """
    assert _placeholders(
        format_options=[{"format_kind": "image", "params": {"width": 970, "height": 250}}],
        formats=[{"agent_url": CREATIVE_AGENT, "id": "display_728x90_image"}],
    ) == [{"width": 970, "height": 250, "expected_creative_count": 1, "is_native": False}]


def test_one_size_reached_by_two_routes_is_one_placeholder():
    """A duplicate placeholder makes GAM expect two creatives for one slot.

    ``expected_creative_count`` is 1 per placeholder, so emitting 728x90 twice tells GAM
    to wait for two creatives at that size -- the "X out of Y expected" state
    ``managers/orders.py`` already filters for on the media-buy side.
    """
    assert _placeholders(
        formats=[
            {"agent_url": CREATIVE_AGENT, "id": "display_728x90_image"},
            {"agent_url": CREATIVE_AGENT, "id": "display_image", "width": 728, "height": 90},
        ]
    ) == [{"width": 728, "height": 90, "expected_creative_count": 1, "is_native": False}]


def test_a_declaration_stating_no_size_falls_back_and_says_so(caplog):
    """A range is not a size, and the fallback is announced rather than silent.

    ``video_hosted`` declares ``min_width``/``max_width`` and no fixed pair. Picking an
    endpoint would be the same invention as the 640x480 constant this change removed, so
    the seed stays at the fallback -- and the operator is told, because a silent 300x250
    is exactly how this defect survived.
    """
    with caplog.at_level(logging.WARNING):
        placeholders = _placeholders(
            format_options=[{"format_kind": "video_hosted", "params": {"min_width": 1080, "min_height": 1920}}]
        )

    assert placeholders == [{"width": 300, "height": 250, "expected_creative_count": 1, "is_native": False}]
    assert "video_hosted" in caplog.text


def test_a_product_declaring_no_format_at_all_keeps_the_fallback():
    """Neither arm: the pre-existing default, which nothing here changes."""
    assert _placeholders() == [{"width": 300, "height": 250, "expected_creative_count": 1, "is_native": False}]


@pytest.mark.parametrize("delivery_type", ["guaranteed", "non_guaranteed"])
def test_the_derivation_is_the_same_on_both_delivery_branches(delivery_type):
    """The placeholders come from the formats, and the delivery type decides nothing else.

    Both branches assigned ``creative_placeholders`` through the same call, and a
    regression that wired only one of them would otherwise pass every test above.
    """
    config = GAMProductConfigService.generate_default_config(
        delivery_type, format_options=[{"format_kind": "image", "params": {"width": 970, "height": 250}}]
    )
    assert config["creative_placeholders"] == [
        {"width": 970, "height": 250, "expected_creative_count": 1, "is_native": False}
    ]
