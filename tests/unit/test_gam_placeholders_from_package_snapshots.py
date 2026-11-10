"""The GAM creative placeholder a package's RECORDED format declarations imply.

WHY A UNIT TEST, against ``tests/CLAUDE.md``'s default of BDD, and this is the epic's own
recorded exception rather than a shortcut: ``salesagent-ol033.8`` states that stage 7 "is the
only stage where a non-BDD test is the right instrument, because an adapter's translation to
a vendor payload is not buyer-visible behaviour". A buyer never sees a
``creativePlaceholder``; GAM does. What a buyer sees of this change -- that a canonical-only
package is placed at all -- is graded by the create scenarios the earlier stages own.

THE SUBJECT is ``GAMOrdersManager.create_line_items``, one level up from the derivation, so
the claim is about the payload GAM is actually asked to create and not about a helper's
return value. The only stub is the GAM SOAP client, which is the one patch target
CLAUDE.md allows ("a system we do not own and a test cannot call -- the ad server").

EVERY EXPECTED SIZE DIFFERS FROM THE SEEDED FALLBACK ON PURPOSE, and here that is the whole
design of the instrument rather than a convention inherited from
``test_gam_creative_placeholder_derivation.py``. The fixtures seed
``implementation_config["creative_placeholders"]`` with 300x250 and declare something else
in the package's snapshot, because 300x250 is precisely what the adapter answered BEFORE this
change: ``managers/orders.py`` fell back to the product's seeded config for a package naming
no ``format_ids``, which is exactly the canonical-only package. So an assertion expecting the
declared size fails with the fallback's size if the snapshot read is dead, which is the
failure mode worth catching -- and it is the one that was live, because a canonical-only
declaration asserting no ``v1_format_ref`` could not be named in ``MediaPackage.format_ids``
at all before the carrier grew ``formats_to_provide``.
"""

import json
from pathlib import Path
from typing import Any

import adcp
import pytest
from adcp.types.domains.core.package_format_snapshot import PackageFormatSnapshot

from src.adapters.gam.managers.orders import GAMOrdersManager
from src.core.exceptions import AdCPCapabilityNotSupportedError
from src.core.format_resolver import package_format_snapshot
from src.core.schemas import MediaPackage
from src.services.gam_product_config_service import _GAM_FAMILY_BY_KIND

#: The slot the GAM config seeder defaults to, and therefore the value this file must never
#: expect from a declaration: it is what a dead snapshot read answers.
SEEDED_FALLBACK = {"width": 300, "height": 250, "expected_creative_count": 1, "is_native": False}


class _RecordingLineItemService:
    """The GAM ``LineItemService``, recording what it was asked to create."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def createLineItems(self, line_items: list[dict[str, Any]]) -> list[dict[str, Any]]:  # noqa: N802
        self.created.extend(line_items)
        return [{"id": 11111111 + index} for index, _ in enumerate(line_items)]


class _StubClientManager:
    """A GAM client that serves only the one service this path calls."""

    def __init__(self, line_item_service: _RecordingLineItemService) -> None:
        self._line_item_service = line_item_service

    def get_service(self, name: str):
        assert name == "LineItemService", f"unexpected GAM service requested: {name}"
        return self._line_item_service


def _snapshot(**document: Any) -> PackageFormatSnapshot:
    """One recorded declaration, built the way production builds it.

    Through ``format_resolver.package_format_snapshot`` -- the function the create path calls
    -- rather than a hand-made stub, because the behaviour under test is that the adapter
    reads ``params`` off the class that function produces. ``params`` on a snapshot arm is the
    generated per-kind MODEL, so a dict stub would answer ``getattr(params, "width")`` with
    nothing: it could make a working derivation look dead, or the reverse.

    Note that the pinned ``PackageFormatSnapshot`` is a sixteen-arm union ALIAS and not a
    model class, which is why it is used here as an annotation and never as a constructor --
    ``model_validate`` on it raises ``AttributeError: 'types.UnionType'``. Production
    validates through a ``TypeAdapter`` for the same reason.
    """
    return package_format_snapshot(document, product_id="prod_1")


def _placed_line_item(package: MediaPackage, impl_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """The single line item GAM was asked to create for *package*.

    ``targeted_ad_unit_ids`` and ``package_pricing_info`` are present because
    ``create_line_items`` refuses a product with no inventory targeting and a package with no
    pricing before it reaches any format work. Both are fixture plumbing and nothing here
    asserts on either.
    """
    from datetime import UTC, datetime

    service = _RecordingLineItemService()
    manager = GAMOrdersManager(_StubClientManager(service), advertiser_id="adv_1", trafficker_id="traf_1")

    config: dict[str, Any] = {"targeted_ad_unit_ids": ["23312403859"]}
    config.update(impl_config or {})

    manager.create_line_items(
        order_id="99887766",
        packages=[package],
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        end_time=datetime(2026, 1, 31, tzinfo=UTC),
        products_map={package.package_id: {"implementation_config": config}},
        package_pricing_info={
            package.package_id: {
                "pricing_model": "cpm",
                "rate": 5.0,
                "currency": "USD",
                "is_fixed": True,
            }
        },
    )

    assert len(service.created) == 1, f"expected one line item, got {len(service.created)}"
    return service.created[0]


def _canonical_only_package(*snapshots: PackageFormatSnapshot) -> MediaPackage:
    """A package whose contract is canonical ONLY -- no deprecated references at all.

    ``format_ids=[]`` is the shape that made this change necessary rather than tidy: a
    declaration asserting no ``v1_format_ref`` has no legal name in that array
    (``registries/v1-canonical-mapping.json`` forbids synthesizing one), so this is what
    reached the adapter and fell through to the product's seeded config.
    """
    return MediaPackage(
        package_id="pkg_1",
        name="Canonical package",
        delivery_type="non_guaranteed",
        cpm=5.0,
        impressions=1000,
        format_ids=[],
        formats_to_provide=list(snapshots),
        product_id="prod_1",
    )


def test_a_canonical_only_package_is_placed_at_the_size_its_snapshot_declares():
    """The recorded declaration's ``params`` decide the placeholder, not the product's config.

    ``core/package.json`` on ``formats_to_provide``: the snapshots are the package's
    "immutable canonical creative contracts" and "the live Product is never substituted for
    the package snapshot". The seeded 300x250 in this fixture IS that substitution, and it is
    what the adapter answered with before.
    """
    line_item = _placed_line_item(
        _canonical_only_package(_snapshot(format_kind="image", params={"width": 970, "height": 250})),
        impl_config={"creative_placeholders": [SEEDED_FALLBACK]},
    )

    assert line_item["creativePlaceholders"] == [
        {"size": {"width": 970, "height": 250}, "expectedCreativeCount": 1, "creativeSizeType": "PIXEL"}
    ]


def test_a_canonical_size_list_becomes_one_placeholder_per_declared_size():
    """``formats/canonical/image.json`` offers ``sizes[]`` beside fixed width/height.

    A package accepting several fixed sizes is several GAM slots, and GAM waits for one
    creative per placeholder -- which is why each is emitted separately rather than merged.
    """
    line_item = _placed_line_item(
        _canonical_only_package(
            _snapshot(
                format_kind="image",
                params={"sizes": [{"width": 728, "height": 90}, {"width": 320, "height": 100}]},
            )
        ),
        impl_config={"creative_placeholders": [SEEDED_FALLBACK]},
    )

    assert line_item["creativePlaceholders"] == [
        {"size": {"width": 728, "height": 90}, "expectedCreativeCount": 1, "creativeSizeType": "PIXEL"},
        {"size": {"width": 320, "height": 100}, "expectedCreativeCount": 1, "creativeSizeType": "PIXEL"},
    ]


def test_a_native_declaration_is_placed_as_a_1x1_native_slot():
    """``native_in_feed`` is the one kind whose slot is the FAMILY rather than its params.

    A GAM native line item takes a 1x1 slot and lets its creative template decide the
    rendered size, which is why ``_GAM_FAMILY_BY_KIND``'s native row and not a declared
    width answers here.
    """
    line_item = _placed_line_item(
        _canonical_only_package(_snapshot(format_kind="native_in_feed", params={})),
        impl_config={"creative_placeholders": [SEEDED_FALLBACK]},
    )

    assert line_item["creativePlaceholders"] == [
        {"size": {"width": 1, "height": 1}, "expectedCreativeCount": 1, "creativeSizeType": "NATIVE"}
    ]


def test_the_snapshot_answers_before_the_packages_deprecated_references():
    """Both arms present: the canonical contract decides, and the legacy name is not consulted.

    The deprecated reference here CARRIES a contradicting size, so this fails if the
    precedence inverts -- which is the whole of E1's "id-regex survives only for legacy
    packages with no declaration". ``core/product-format-declaration.json`` makes the
    canonical arm the closed set and says legacy named formats "MUST be normalized to
    canonical declarations before comparison", so the two are not unioned.
    """
    package = MediaPackage(
        package_id="pkg_1",
        name="Dual-armed package",
        delivery_type="non_guaranteed",
        cpm=5.0,
        impressions=1000,
        format_ids=[
            {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_728x90_image",
                "width": 728,
                "height": 90,
            }
        ],
        formats_to_provide=[_snapshot(format_kind="image", params={"width": 970, "height": 250})],
        product_id="prod_1",
    )

    line_item = _placed_line_item(package, impl_config={"creative_placeholders": [SEEDED_FALLBACK]})

    assert line_item["creativePlaceholders"] == [
        {"size": {"width": 970, "height": 250}, "expectedCreativeCount": 1, "creativeSizeType": "PIXEL"}
    ]


def test_a_declaration_stating_only_a_range_falls_through_to_the_products_config():
    """A range is not a slot size, so the product's seeded slot is the honest answer.

    ``image``, ``video_hosted`` and ``video_vast`` may declare ``min_width``/``max_width``
    instead of a fixed size. Picking an endpoint would invent a slot the seller never
    declared, so the snapshot contributes nothing and the fallback -- the one value an
    operator can see and correct -- answers. This is the one test here that EXPECTS the
    fallback, which is why it names the range explicitly.
    """
    line_item = _placed_line_item(
        _canonical_only_package(_snapshot(format_kind="image", params={"min_width": 300, "max_width": 970})),
        impl_config={"creative_placeholders": [SEEDED_FALLBACK]},
    )

    assert line_item["creativePlaceholders"] == [
        {"size": {"width": 300, "height": 250}, "expectedCreativeCount": 1, "creativeSizeType": "PIXEL"}
    ]


def test_an_audio_declaration_is_refused_rather_than_placed_on_a_display_slot():
    """GAM has no standalone audio line item, so an audio kind cannot be placed at all.

    The SAME refusal the deprecated arm has always raised for the same fact, reached through
    the recorded kind instead of an ``"audio" in format_id`` substring test. Without it the
    canonical path would place this package on the seeded 300x250 DISPLAY slot -- a display
    line item for an audio buy, which is the quiet failure
    ``.claude/rules/patterns/code-patterns.md`` forbids.
    """
    with pytest.raises(AdCPCapabilityNotSupportedError):
        _placed_line_item(
            _canonical_only_package(_snapshot(format_kind="audio_hosted", params={})),
            impl_config={"creative_placeholders": [SEEDED_FALLBACK]},
        )


def test_a_declared_family_the_product_excluded_is_refused():
    """``supported_format_types`` is the product's own statement of what it traffics.

    The gate was previously reachable only inside the ``package.format_ids`` branch, so
    letting the canonical arm answer first would have BYPASSED it for a package carrying
    both arms. This is that gate on the canonical arm, keyed on the family rather than on a
    substring of a format's name.
    """
    with pytest.raises(AdCPCapabilityNotSupportedError):
        _placed_line_item(
            _canonical_only_package(_snapshot(format_kind="video_hosted", params={"width": 640, "height": 480})),
            impl_config={
                "creative_placeholders": [SEEDED_FALLBACK],
                "supported_format_types": ["display"],
            },
        )


def test_a_kind_with_no_gam_family_is_not_refused_and_contributes_no_slot():
    """The four unclassified kinds fall through; they are not refusals.

    GAM makes no claim that it cannot place a composition contract or a seller-defined
    ``custom`` shape, so a refusal would have nothing to cite -- unlike audio, where GAM
    states the opposite. Such a declaration contributes no slot and the product's seeded
    config answers, which is why the fallback size is expected here.
    """
    line_item = _placed_line_item(
        _canonical_only_package(_snapshot(format_kind="custom", params={}, format_shape="display")),
        impl_config={"creative_placeholders": [SEEDED_FALLBACK]},
    )

    assert line_item["creativePlaceholders"] == [
        {"size": {"width": 300, "height": 250}, "expectedCreativeCount": 1, "creativeSizeType": "PIXEL"}
    ]


def _pinned_format_kinds() -> set[str]:
    """Every ``format_kind`` the pinned declaration union discriminates on.

    DERIVED from the schema the application runs against rather than listed here, so a pin
    bump cannot leave this file agreeing with a stale copy of the enum. Read out of the
    installed wheel's ``_schemas`` tree, which is the same source
    ``core/product-format-declaration.json`` is quoted from elsewhere in this change.
    """
    schema = json.loads(
        (Path(adcp.__file__).parent / "_schemas" / "3.2" / "core" / "product-format-declaration.json").read_text()
    )
    kinds: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            declared = node.get("properties", {}).get("format_kind")
            if isinstance(declared, dict):
                if "const" in declared:
                    kinds.add(declared["const"])
                kinds.update(declared.get("enum", []))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return kinds


def test_every_kind_the_gam_family_table_classifies_is_a_kind_the_pin_declares():
    """The hand-written table may not name a format kind that does not exist.

    The table cannot be derived -- a canonical kind's GAM creative type is a judgement about
    a vendor, stated nowhere in the pin -- so what is derived instead is that each row names
    something real. A typo'd key would otherwise sit there classifying nothing, which is the
    failure the deleted eight-member dimension map had: all nine of its constants were absent
    from the pinned catalogue, so it matched nothing and its fallback answered for everything
    (``salesagent-ne2t3``).
    """
    assert set(_GAM_FAMILY_BY_KIND) <= _pinned_format_kinds()


def test_the_kinds_the_table_leaves_unclassified_are_exactly_the_four_recorded_ones():
    """A pin bump that adds a format kind fails HERE rather than being classified by accident.

    The four are deliberate and the table says why: ``agent_placement``,
    ``coordinated_placements`` and ``sponsored_placement`` are composition contracts rather
    than creatives of a GAM type, and ``custom`` is whatever this seller defines. A
    seventeenth kind arriving in the pin is a decision for whoever bumps it, and this
    assertion is how they are told there is one to make.
    """
    assert _pinned_format_kinds() - set(_GAM_FAMILY_BY_KIND) == {
        "agent_placement",
        "coordinated_placements",
        "custom",
        "sponsored_placement",
    }


@pytest.mark.parametrize(
    ("kind", "family"),
    [
        ("image", "display"),
        ("html5", "display"),
        ("native_in_feed", "native"),
        ("video_hosted", "video"),
        ("audio_hosted", "audio"),
        ("custom", None),
    ],
)
def test_the_family_a_kind_traffics_as(kind: str, family: str | None):
    """One reader for the table, so the slot derivation and the adapter agree on a kind."""
    from src.services.gam_product_config_service import gam_format_family

    assert gam_format_family(kind) == family
